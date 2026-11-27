"""观察员：只读数据，产出 Finding。

它不写计划、不出题。查够了就交接；如果模型直接给了规划员的动作，
把这条动作原样带给规划员，不浪费一次调用。
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator

import client.services.plan_prompts as plan_prompts
import client.services.plan_tools as plan_tools
from client.services.plan_tools import ToolError
from shared.domain.models.agents import ROLE_LABELS, AgentRole, Finding

from . import budget, protocol
from .findings import PLANNER_ACTIONS, READ_TOOLS, from_tool_result

logger = logging.getLogger(__name__)

ROLE = AgentRole.OBSERVER.value
ROLE_LABEL = ROLE_LABELS[ROLE]


class Observer:
    def __init__(self, *, db, space_id: str, llm, goal: str, today: str) -> None:
        self.db = db
        self.space_id = space_id
        self.llm = llm
        self.messages = plan_prompts.observer_messages(goal, today)
        self.findings: list[Finding] = []
        self.steps: list[dict] = []
        self.llm_calls = 0
        # 模型在观察阶段直接给出的规划动作，交给规划员当第一条动作
        self.pending: tuple[str, dict, str] | None = None
        self.handoff_reason = ""

    async def run(self) -> AsyncIterator[protocol.AgentEvent]:
        calls: dict[str, int] = {}
        while (
            self.llm_calls < budget.MAX_OBSERVER_CALLS
            and len(self.steps) < budget.MAX_OBSERVER_STEPS
        ):
            yield protocol.AgentEvent(
                "thinking",
                {
                    "step": len(self.steps) + 1,
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

            if tool == "finish_observation":
                self.handoff_reason = str(args.get("summary") or "观察结束")
                yield self._handoff()
                return

            if tool in PLANNER_ACTIONS:
                # 不判错：把它当成「观察结束」的信号，动作留给规划员执行
                self.pending = (tool, args, reply)
                self.handoff_reason = "模型直接给出了规划动作，已转交规划员"
                yield self._handoff()
                return

            if tool not in READ_TOOLS or tool not in plan_tools.TOOLS:
                self.messages.append({"role": "assistant", "content": reply})
                self.messages.append(
                    {
                        "role": "user",
                        "content": plan_prompts.feedback_message(
                            tool,
                            {
                                "ok": False,
                                "error": "观察员只能调用 get_progress / list_weak_points / "
                                "list_wrong_answers / finish_observation。",
                            },
                        ),
                    }
                )
                continue

            if calls.get(tool, 0) >= budget.MAX_SAME_TOOL_CALLS:
                self.messages.append({"role": "assistant", "content": reply})
                self.messages.append(
                    {
                        "role": "user",
                        "content": plan_prompts.feedback_message(
                            tool,
                            {
                                "ok": False,
                                "error": "这个工具已经查过了，结果在上文。"
                                "请换一个工具，或 finish_observation。",
                            },
                        ),
                    }
                )
                continue

            calls[tool] = calls.get(tool, 0) + 1
            begin = time.monotonic()
            ok = True
            try:
                result = await plan_tools.TOOLS[tool](self.db, space_id=self.space_id, **args)
            except ToolError as exc:
                result, ok = {"ok": False, "error": str(exc)}, False
            except Exception as exc:  # noqa: BLE001 - 工具故障要让模型换路，不是整轮失败
                logger.exception("观察工具失败：%s", tool)
                result, ok = {
                    "ok": False,
                    "error": protocol.explain(str(exc) or exc.__class__.__name__),
                }, False

            elapsed_ms = int((time.monotonic() - begin) * 1000)
            brief = protocol.brief(tool, result, ok)
            step = plan_prompts.step_meta(tool, args, result, elapsed_ms, ok, brief)
            self.steps.append(step)

            yield protocol.AgentEvent(
                "step",
                {
                    "index": len(self.steps),
                    "role": ROLE,
                    "role_label": ROLE_LABEL,
                    "tool": tool,
                    "label": plan_tools.TOOL_LABELS.get(tool, tool),
                    "args": args,
                    "ok": ok,
                    "brief": brief,
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
                    "brief": brief,
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

            self.messages.append({"role": "assistant", "content": reply})
            self.messages.append(
                {"role": "user", "content": plan_prompts.feedback_message(tool, result)}
            )

        self.handoff_reason = self.handoff_reason or "观察预算用尽"
        yield self._handoff()

    def _handoff(self) -> protocol.AgentEvent:
        return protocol.AgentEvent(
            "handoff",
            {},
            persist={
                "role": ROLE,
                "action": "handoff",
                "tool": None,
                "args": {},
                "ok": True,
                "brief": f"观察完成：{len(self.findings)} 条事实（{self.handoff_reason}）",
                "result": {
                    "findings": len(self.findings),
                    "steps": len(self.steps),
                    "reason": self.handoff_reason,
                    "pending": self.pending[0] if self.pending else None,
                },
                "elapsed_ms": 0,
            },
        )