"""学习教练的入口（M8）。

M7 时这里是一个 6 步工具循环；现在循环被拆成了四个角色：
观察员查数据、规划员写计划、审核员核对数字、记录员写入。
调度与预算在 `client/services/agents/coordinator.py`，这里只保留
接口层需要的东西：`MAX_STEPS`、`parse_action` 与 `sse_plan`。

保留 `build_llm` 的模块级引用，是为了测试仍能 `plan_agent.build_llm = fake`
替换模型——入口不吞掉这个缝。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from shared.infrastructure.llm.openai_compat import build_llm

from client.services.agents import coordinator
from client.services.agents.budget import MAX_TOOL_STEPS
from client.services.agents.protocol import parse_action

MAX_STEPS = MAX_TOOL_STEPS

__all__ = ["MAX_STEPS", "build_llm", "parse_action", "sse_plan"]


async def sse_plan(payload: dict) -> AsyncIterator[str]:
    """规划 SSE。自己开 Session 的是协调员，这里只做转发。"""
    async for chunk in coordinator.run_coach(
        payload, llm_factory=lambda: build_llm()
    ):
        yield chunk