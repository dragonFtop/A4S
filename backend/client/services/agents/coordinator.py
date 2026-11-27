"""协调员：把观察 → 规划 → 审核 → 写入串成一次可回放的运行。

它是唯一知道「预算还剩多少、现在轮到谁」的地方。角色之间不直接说话，
一切都经过这里：事件转成 SSE，步骤写进 `agent_steps`，最终结论写进
`agent_runs`。这样一次运行事后能完整重放，而不是只剩一份计划正文。

出口只有四个：
- `plan` + `done`：审核通过并已写入；
- `no_plan`：规划员没写出来，或审核两次都没过；
- `no_data`：资料与练习都是 0，没有排计划的依据；
- `error`：模型/写库故障。
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from datetime import datetime

from shared.config import settings
from shared.domain.models.agents import (
    ROLE_LABELS,
    AgentKind,
    AgentRole,
    AgentRun,
    Proposal,
    RunStatus,
    Verdict,
    new_id,
)
from shared.infrastructure.db.session import SessionLocal
from shared.infrastructure.llm.openai_compat import build_llm
from shared.repositories.agents import SqlAlchemyAgentRunRepository
from shared.repositories.sqlalchemy_repo import SqlAlchemyStudyPlanRepository
from shared.repositories.stats_repo import StatsRepository
from client.services.plan_tools import ToolError

from . import budget, protocol
from .observer import Observer
from .planner import Planner
from .verifier import verify
from .writer import Writer

logger = logging.getLogger(__name__)

COACH_ROLES = (
    AgentRole.OBSERVER,
    AgentRole.PLANNER,
    AgentRole.VERIFIER,
    AgentRole.WRITER,
)


class _Recorder:
    """一次运行的记账员：步骤落库、预算计数、时间线素材。"""

    def __init__(self, repo: SqlAlchemyAgentRunRepository, run_id: str) -> None:
        self.repo = repo
        self.run_id = run_id
        self.seq = 0
        self.llm_calls = 0
        self.handoffs = 0
        self.steps: list[dict] = []

    def persist(self, payload: dict) -> None:
        self.seq += 1
        self.repo.append_step(self.run_id, seq=self.seq, **payload)
        if payload.get("action") == "handoff":
            self.handoffs += 1

    def note_step(self, event: protocol.AgentEvent) -> None:
        """把一条 step 事件同时收进 meta.steps，历史详情与实时视图才一致。"""
        data = event.data
        self.steps.append(
            {
                "role": data.get("role"),
                "role_label": data.get("role_label"),
                "tool": data.get("tool"),
                "label": data.get("label"),
                "args": data.get("args") or {},
                "ok": data.get("ok", True),
                "brief": data.get("brief", ""),
                "elapsed_ms": data.get("elapsed_ms", 0),
                "result": (event.persist or {}).get("result") or {},
            }
        )

    async def pipe(
        self,
        events: AsyncIterator[protocol.AgentEvent],
        *,
        push: tuple[str, ...] = ("thinking", "step"),
    ) -> AsyncIterator[str]:
        async for event in events:
            if event.persist is not None:
                self.persist(event.persist)
            if event.name == "thinking":
                self.llm_calls += 1
            if event.name == "step":
                self.note_step(event)
            if event.name in push:
                yield protocol.event(event.name, event.data)


def _verdict_brief(verdict: Verdict) -> str:
    if verdict.passed:
        suffix = f"，{len(verdict.warnings)} 条提示" if verdict.warnings else ""
        return f"审核通过{suffix}"
    first = verdict.reasons[0] if verdict.reasons else "未通过"
    return f"打回：{first}"


async def run_coach(
    payload: dict, *, llm_factory=None
) -> AsyncIterator[str]:
    db = SessionLocal()
    started = time.monotonic()
    goal = (payload.get("goal") or "").strip()[:200]
    run_id: str | None = None
    repo: SqlAlchemyAgentRunRepository | None = None
    rec: _Recorder | None = None
    finished = False

    try:
        if not settings.llm_configured:
            yield protocol.event(
                "error", {"detail": "尚未配置大模型 API Key，请先在设置页填写"}
            )
            return

        space_id = payload["space_id"]
        stats = StatsRepository(db, space_id).summary()
        if stats["documents"]["total"] == 0 and stats["practice"]["sessions"] == 0:
            yield protocol.event(
                "no_data",
                {"detail": "还没有资料和练习记录，先导入一份资料或做一套题，计划才有依据。"},
            )
            return

        llm = (llm_factory or build_llm)()
        today = datetime.now().astimezone().strftime("%Y-%m-%d")

        run_id = new_id()
        repo = SqlAlchemyAgentRunRepository(db)
        repo.start(
            AgentRun(
                id=run_id,
                space_id=space_id,
                kind=AgentKind.COACH.value,
                goal=goal,
                model=settings.llm_model,
                budget_steps=budget.MAX_TOOL_STEPS,
            )
        )
        rec = _Recorder(repo, run_id)

        yield protocol.event(
            "start",
            {
                "steps_max": budget.MAX_TOOL_STEPS,
                "goal": goal,
                "today": today,
                "run_id": run_id,
                "roles": [ROLE_LABELS[role.value] for role in COACH_ROLES],
            },
        )

        observer = Observer(
            db=db, space_id=space_id, llm=llm, goal=goal, today=today
        )
        async for chunk in rec.pipe(observer.run()):
            yield chunk

        planner = Planner(
            db=db,
            space_id=space_id,
            llm=llm,
            goal=goal,
            today=today,
            findings=observer.findings,
            pending=observer.pending,
        )
        async for chunk in rec.pipe(planner.run()):
            yield chunk

        proposal: Proposal | None = planner.proposal
        verdict: Verdict | None = None
        attempt = 0

        while proposal is not None:
            verdict = verify(proposal, observer.findings)
            rec.persist(
                {
                    "role": AgentRole.VERIFIER.value,
                    "action": "verdict",
                    "tool": None,
                    "args": {"attempt": attempt + 1},
                    "ok": verdict.passed,
                    "brief": _verdict_brief(verdict),
                    "result": verdict.as_dict(),
                    "elapsed_ms": 0,
                }
            )
            if verdict.passed:
                break
            if attempt >= budget.MAX_VERIFIER_RETRIES:
                break
            attempt += 1
            planner.revise(verdict)
            async for chunk in rec.pipe(planner.run()):
                yield chunk
            proposal = planner.proposal

        elapsed = round(time.monotonic() - started, 1)

        if proposal is None or verdict is None or not verdict.passed:
            reasons = verdict.reasons if verdict else ["规划员在预算内没有写出计划"]
            elapsed_ms = int((time.monotonic() - started) * 1000)
            repo.finish(
                run_id,
                status=RunStatus.ABORTED.value,
                steps_count=len(rec.steps),
                llm_calls=rec.llm_calls,
                handoffs=rec.handoffs,
                summary={"reasons": reasons, "plan_id": None},
                verdict=verdict.as_dict() if verdict else {},
                elapsed_ms=elapsed_ms,
            )
            finished = True
            yield protocol.event(
                "no_plan",
                {
                    "detail": (
                        "规划员在预算内没有写出计划。下面是已经查到的结论，可以再试一次。"
                        if proposal is None
                        else f"审核没有通过：{reasons[0]}"
                    ),
                    "steps": rec.steps,
                    "reasons": reasons,
                    "run_id": run_id,
                    "elapsed_seconds": elapsed,
                },
            )
            return

        meta = {
            "steps": rec.steps,
            "stats": {
                "documents": stats["documents"]["ready"],
                "sessions": stats["practice"]["finished"],
                "avg_score": round(float(stats["practice"]["avg_score"]), 1),
                "wrong": stats["wrong"]["total"],
            },
            "model": settings.llm_model,
            "generated_at": datetime.now().astimezone().isoformat(),
            "run_id": run_id,
            "agents": {
                "roles": [role.value for role in COACH_ROLES],
                "llm_calls": rec.llm_calls,
                "handoffs": rec.handoffs,
                "verdict": verdict.as_dict(),
            },
        }

        writer = Writer(db, space_id=space_id, goal=goal)
        commit_begin = time.monotonic()
        try:
            saved, commit_ms = writer.commit(proposal, meta)
        except ToolError as exc:
            detail = str(exc)
            commit_ms = int((time.monotonic() - commit_begin) * 1000)
            rec.persist(
                {
                    "role": AgentRole.WRITER.value,
                    "action": "tool",
                    "tool": "save_plan",
                    "args": {"chars": len(proposal.markdown)},
                    "ok": False,
                    "brief": f"写入失败：{detail}",
                    "result": {"ok": False, "error": detail},
                    "elapsed_ms": commit_ms,
                }
            )
            elapsed_ms = int((time.monotonic() - started) * 1000)
            repo.finish(
                run_id,
                status=RunStatus.FAILED.value,
                steps_count=len(rec.steps),
                llm_calls=rec.llm_calls,
                handoffs=rec.handoffs,
                summary={"plan_id": None},
                verdict=verdict.as_dict(),
                error=detail,
                elapsed_ms=elapsed_ms,
            )
            finished = True
            yield protocol.event("error", {"detail": detail, "run_id": run_id})
            return

        rec.persist(
            {
                "role": AgentRole.WRITER.value,
                "action": "tool",
                "tool": "save_plan",
                "args": {"chars": len(proposal.markdown)},
                "ok": True,
                "brief": f"写下计划（{saved['chars']} 字）",
                "result": {"plan_id": saved["plan_id"], "chars": saved["chars"]},
                "elapsed_ms": commit_ms,
            }
        )

        SqlAlchemyStudyPlanRepository(db).update_meta(
            saved["plan_id"],
            {"elapsed_seconds": elapsed, "steps_count": len(rec.steps)},
            space_id,
        )

        elapsed_ms = int((time.monotonic() - started) * 1000)
        repo.finish(
            run_id,
            status=RunStatus.COMPLETED.value,
            steps_count=len(rec.steps),
            llm_calls=rec.llm_calls,
            handoffs=rec.handoffs,
            summary={
                "plan_id": saved["plan_id"],
                "chars": saved["chars"],
                "steps": len(rec.steps),
            },
            verdict=verdict.as_dict(),
            elapsed_ms=elapsed_ms,
        )
        finished = True

        yield protocol.event(
            "plan",
            {
                "plan_id": saved["plan_id"],
                "markdown": proposal.markdown.strip(),
                "chars": saved["chars"],
                "steps": len(rec.steps),
                "elapsed_seconds": elapsed,
                "run_id": run_id,
            },
        )
        yield protocol.event(
            "done",
            {"plan_id": saved["plan_id"], "steps": len(rec.steps), "run_id": run_id},
        )
    except Exception as exc:  # noqa: BLE001 - 供应商/数据库故障要原样转给人
        logger.exception("学习教练运行失败")
        detail = protocol.explain(str(exc) or exc.__class__.__name__)
        if run_id and repo is not None and not finished:
            try:
                repo.finish(
                    run_id,
                    status=RunStatus.FAILED.value,
                    steps_count=len(rec.steps) if rec else 0,
                    llm_calls=rec.llm_calls if rec else 0,
                    handoffs=rec.handoffs if rec else 0,
                    error=detail,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                )
            except Exception:  # noqa: BLE001 - 收尾失败不能盖住原始错误
                logger.exception("运行账本收尾失败")
            finished = True
        yield protocol.event("error", {"detail": detail, "run_id": run_id})
    finally:
        if run_id and repo is not None and not finished:
            try:
                repo.finish(
                    run_id,
                    status=RunStatus.ABORTED.value,
                    steps_count=len(rec.steps) if rec else 0,
                    llm_calls=rec.llm_calls if rec else 0,
                    handoffs=rec.handoffs if rec else 0,
                    error="运行被中断",
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                )
            except Exception:  # noqa: BLE001
                logger.exception("中断收尾失败")
        db.close()