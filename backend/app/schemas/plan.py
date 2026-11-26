"""学习计划的请求与出参模型（M7）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PlanIn(BaseModel):
    # 可选的补充目标：不填就只看数据决定
    goal: str = Field(default="", max_length=200)


class PlanStepOut(BaseModel):
    tool: str
    label: str
    args: dict = Field(default_factory=dict)
    ok: bool = True
    elapsed_ms: int = 0
    result: dict | None = None


class PlanStatsOut(BaseModel):
    documents: int = 0
    sessions: int = 0
    avg_score: float = 0.0
    wrong: int = 0


class PlanBrief(BaseModel):
    id: str
    goal: str = ""
    excerpt: str = ""
    created_at: str
    stats: PlanStatsOut = Field(default_factory=PlanStatsOut)
    steps: int = 0


class PlanDetail(PlanBrief):
    markdown: str = ""
    meta: dict = Field(default_factory=dict)


class PlanMetaOut(BaseModel):
    """前端渲染提示用：它到底能做什么、不能做什么。"""

    steps_max: int
    tools: list[dict]
    limits: list[str]
