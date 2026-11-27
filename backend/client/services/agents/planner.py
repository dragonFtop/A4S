"""规划员：基于观察员交来的事实写计划，提交审核。

它只做两件事：必要时出一套题、写出 plan 正文并 propose。
真正的写入不在这里——审核通过后由记录员执行。
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator

import client.services.plan_prompts as plan_prompts
import client.services.plan_tools as plan_tools
from client.services.plan_tools import ToolError
from shared.domain.models.agents import ROLE_LABELS, AgentRole, Finding, Proposal, Verdict

from . import budget, protocol
from .findings import READ_TOOLS, facts_digest, from_tool_result

logger = logging.getLogger(__name__)

ROLE = AgentRole.PLANNER.value
ROLE_LABEL = ROLE_LABELS[ROLE]


class Planner:
    def __init__(
        self,
        *,
        db,
        space_id: str,
        llm,
        goal: str,
        today: str,
        findings: list[Finding],
        pending: tuple[str, dict, str] | None = None,
        step_offset: int = 0,
    ) -> None:
        self.db = db
        self.space_id = space_id
        self.llm = llm
        self.goal = goal
        self.findings = findings
        self.messages = plan_prompts.planner_messages(goal, today, facts_digest(findings))
        self.proposal: Proposal | None = None
        self.steps: list[dict] = []
        self.llm_calls = 0
        self.step_offset = step_offset
        self._pending = pending
        self._practice_calls = 0

    async def run(self) -> AsyncIterator[protocol.AgentEvent]:
        while self.llm_calls < budget.MAX_PLANNER_CALLS and self.proposal is None:
            reply = ""
            if self._pending is not None:
                tool, args, reply = self._pending
                self._pending = None
            else:
                yield protocol.AgentEvent(
                    "thinking",
                    {
                        "step": min(
                            self.step_offset + len(self.steps) + 1, budget.MAX_TOOL_STEPS
                        ),
                        "steps_max": budget.MAX_TOOL_STEPS,
                        "role": ROLE,
                        "role_label": ROLE_LABEL,
                    },
                )
                reply = await self.llm.chat(self.messages, temperature=0.2)
                self.llm_calls += 1
                action = protocol.parse_action(reply)
                if action is None:
                    self.messages.append({"role": "assistant", "content": reply})
                    self.messages.append(
                        {"role": "user", "content": plan_prompts.correction_message(reply)}
                    )
                    continue
                tool, args = action

            if tool in ("propose_plan", "save_plan"):
                proposal = Proposal(
                    kind="study_plan",
                    payload={
                        "markdown": str(args.get("markdown") or ""),
                        "goal": str(args.get("goal") or ""),
                    },
                )
                self.proposal = proposal
                yield protocol.AgentEvent(
                    "propose",
                    {},
                    persist={
                        "role": ROLE,
                        "action": "propose",
                        "tool": tool,
                        "args": {"chars": len(proposal.markdown)},
                        "ok": True,
                        "brief": f"提交计划（{len(proposal.markdown)} 字）",
                        "result": proposal.as_dict(),
                        "elapsed_ms": 0,
                    },
                )
                return

            if tool == "generate_practice":
                if reply:
                    self.messages.append({"role": "assistant", "content": reply})
                if self._practice_calls >= budget.MAX_PRACTICE_CALLS:
                    self.messages.append(
                        {
                            "role": "user",
                            "content": plan_prompts.feedback_message(
                                tool,
                                {"ok": False, "error": "出题最多一次，直接 propose_plan。"},
                            ),
                        }
                    )
                    continue
                self._practice_calls += 1
                result, ok, elapsed_ms = await self._run_practice(args)
                step = plan_prompts.step_meta(tool, args, result, elapsed_ms, ok,
                                              protocol.brief(tool, result, ok))
                self.steps.append(step)
                yield protocol.AgentEvent(
                    "step",
                    {
                        "index": self.step_offset + len(self.steps),
                        "role": ROLE,
                        "role_label": ROLE_LABEL,
                        "tool": tool,
                        "label": plan_tools.TOOL_LABELS.get(tool, tool),
                        "args": args,
                        "ok": ok,
                        "brief": step["brief"],
                        "preview": protocol.preview(result),
                        "elapsed_ms": elapsed_ms,
                        "session_id": result.get("session_id") if isinstance(result, dict) else None,
                    },
                    persist={
                        "role": ROLE,
                        "action": "tool",
                        "tool": tool,
                        "args": args,
                        "ok": ok,
                        "brief": step["brief"],
                        "result": step["result"],
                        "elapsed_ms": elapsed_ms,
                    },
                )
                if ok:
                    for finding in from_tool_result(tool, result):
                        self.findings.append(finding)
                        yield protocol.AgentEvent(
                            "finding",
                            {},
                            persist={
                                "role": ROLE,
                                "action": "finding",
                                "tool": finding.kind.value,
                                "args": {},
                                "ok": True,
                                "brief": finding.claim,
                                "result": finding.as_dict(),
                                "elapsed_ms": 0,
                            },
                        )
                self.messages.append(
                    {"role": "user", "content": plan_prompts.feedback_message(tool, result)}
                )
                continue

            # 观察阶段的工具、或未定义的动作：一律退回，不执行
            if reply:
                self.messages.append({"role": "assistant", "content": reply})
            if tool in READ_TOOLS:
                error = "观察阶段已结束，数据在上面的事实里。请直接 propose_plan。"
            else:
                error = f"规划员没有名为 {tool} 的动作；可用 generate_practice / propose_plan。"
            self.messages.append(
                {"role": "user", "content": plan_prompts.feedback_message(tool, {"ok": False, "error": error})}
            )

    def revise(self, verdict: Verdict) -> None:
        """审核打回：清掉旧提案，把理由作为下一条用户消息。"""
        self.proposal = None
        self.messages.append(
            {"role": "user", "content": plan_prompts.verdict_message(verdict.reasons)}
        )

    async def _run_practice(self, args: dict) -> tuple[dict, bool, int]:
        begin = time.monotonic()
        try:
            result = await plan_tools.TOOLS["generate_practice"](
                self.db, space_id=self.space_id, **args
            )
            return result, True, int((time.monotonic() - begin) * 1000)
        except ToolError as exc:
            return {"ok": False, "error": str(exc)}, False, int((time.monotonic() - begin) * 1000)
        except Exception as exc:  # noqa: BLE001 - 出题失败要能退化成纯阅读计划
            logger.exception("规划员出题失败")
            return {
                "ok": False,
                "error": protocol.explain(str(exc) or exc.__class__.__name__),
            }, False, int((time.monotonic() - begin) * 1000)