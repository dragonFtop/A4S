"""记录员：唯一能写入的角色。

审核员通过之后，只有这里能碰 `study_plans`。把写入收成一个小类，
是为了让「谁能写」在代码里一眼可见——不是靠提示词自律。
"""

from __future__ import annotations

import time

import client.services.plan_tools as plan_tools
from shared.domain.models.agents import Proposal


class Writer:
    def __init__(self, db, *, space_id: str, goal: str = "") -> None:
        self.db = db
        self.space_id = space_id
        self.goal = goal

    def commit(self, proposal: Proposal, meta: dict) -> tuple[dict, int]:
        """落库并返回 (保存结果, 耗时毫秒)。失败抛 ToolError，由协调员翻译。"""
        begin = time.monotonic()
        saved = plan_tools.save_plan(
            self.db,
            space_id=self.space_id,
            markdown=proposal.markdown,
            goal=self.goal or proposal.goal,
            meta=meta,
        )
        return saved, int((time.monotonic() - begin) * 1000)