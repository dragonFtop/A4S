"""多智能体运行记录仓储（M8）。

一次运行 = 一行 `agent_runs` + N 行 `agent_steps`。这里只做读写与互转，
预算控制、角色调度在 `client/services/agents/coordinator.py`。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.domain.models.agents import AgentRun, AgentStep, RunStatus
from shared.infrastructure.db.models import AgentRunORM, AgentStepORM


def _to_step(row: AgentStepORM) -> AgentStep:
    return AgentStep(
        id=row.id,
        run_id=row.run_id,
        seq=row.seq,
        role=row.role,
        action=row.action,
        tool=row.tool,
        args=row.args or {},
        ok=bool(row.ok),
        brief=row.brief or "",
        result=row.result,
        elapsed_ms=row.elapsed_ms,
        created_at=row.created_at,
    )


def _to_run(row: AgentRunORM, steps: list[AgentStep] | None = None) -> AgentRun:
    return AgentRun(
        id=row.id,
        space_id=row.space_id,
        kind=row.kind,
        goal=row.goal or "",
        trigger=row.trigger,
        model=row.model or "",
        status=row.status,
        budget_steps=row.budget_steps,
        steps_count=row.steps_count,
        llm_calls=row.llm_calls,
        handoffs=row.handoffs,
        summary=row.summary or {},
        verdict=row.verdict or {},
        error=row.error or "",
        elapsed_ms=row.elapsed_ms,
        created_at=row.created_at,
        finished_at=row.finished_at,
        steps=steps or [],
    )


class SqlAlchemyAgentRunRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ---------- 写 ----------

    def start(self, run: AgentRun) -> AgentRun:
        row = AgentRunORM(
            id=run.id,
            space_id=run.space_id,
            kind=run.kind,
            goal=run.goal,
            trigger=run.trigger,
            model=run.model,
            status=RunStatus.RUNNING.value,
            budget_steps=run.budget_steps,
            summary=run.summary or {},
            verdict=run.verdict or {},
            created_at=run.created_at,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return _to_run(row)

    def append_step(
        self,
        run_id: str,
        *,
        seq: int,
        role: str,
        action: str,
        tool: str | None = None,
        args: dict | None = None,
        ok: bool = True,
        brief: str = "",
        result: dict | None = None,
        elapsed_ms: int = 0,
    ) -> AgentStep:
        row = AgentStepORM(
            run_id=run_id,
            seq=seq,
            role=role,
            action=action,
            tool=tool,
            args=args or {},
            ok=ok,
            brief=brief,
            result=result,
            elapsed_ms=elapsed_ms,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return _to_step(row)

    def finish(
        self,
        run_id: str,
        *,
        status: str,
        steps_count: int,
        llm_calls: int,
        handoffs: int = 0,
        summary: dict | None = None,
        verdict: dict | None = None,
        error: str = "",
        elapsed_ms: int = 0,
    ) -> None:
        row = self.db.get(AgentRunORM, run_id)
        if row is None:
            return
        row.status = status
        row.steps_count = steps_count
        row.llm_calls = llm_calls
        row.handoffs = handoffs
        if summary is not None:
            row.summary = summary
        if verdict is not None:
            row.verdict = verdict
        row.error = error
        row.elapsed_ms = elapsed_ms
        row.finished_at = datetime.now(timezone.utc)
        self.db.commit()

    # ---------- 读 ----------

    def get(self, run_id: str, space_id: str | None = None) -> AgentRun | None:
        row = self.db.get(AgentRunORM, run_id)
        if row is None:
            return None
        if space_id is not None and row.space_id != space_id:
            return None
        steps = (
            self.db.execute(
                select(AgentStepORM)
                .where(AgentStepORM.run_id == run_id)
                .order_by(AgentStepORM.seq.asc())
            )
            .scalars()
            .all()
        )
        return _to_run(row, [_to_step(item) for item in steps])

    def list(self, space_id: str, limit: int = 20) -> list[AgentRun]:
        rows = (
            self.db.execute(
                select(AgentRunORM)
                .where(AgentRunORM.space_id == space_id)
                .order_by(AgentRunORM.created_at.desc())
                .limit(max(1, min(limit, 100)))
            )
            .scalars()
            .all()
        )
        return [_to_run(row) for row in rows]

    def next_seq(self, run_id: str) -> int:
        """步骤序号。运行内自增，跨运行不复用。"""
        current = self.db.execute(
            select(func.max(AgentStepORM.seq)).where(AgentStepORM.run_id == run_id)
        ).scalar()
        return int(current or 0) + 1

    def count_steps(self, run_id: str) -> int:
        count = self.db.execute(
            select(func.count(AgentStepORM.id)).where(AgentStepORM.run_id == run_id)
        ).scalar()
        return int(count or 0)