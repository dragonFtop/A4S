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


class AgentStepOut(BaseModel):
    """一次运行里的一步：谁（role）做了什么（action）。"""

    seq: int
    role: str
    action: str
    tool: str | None = None
    args: dict = Field(default_factory=dict)
    ok: bool = True
    brief: str = ""
    result: dict | None = None
    elapsed_ms: int = 0


class AgentRunOut(BaseModel):
    """运行回放：预算、用量、审核结论与全部步骤。"""

    id: str
    kind: str
    goal: str = ""
    status: str
    model: str = ""
    budget_steps: int = 0
    steps_count: int = 0
    llm_calls: int = 0
    handoffs: int = 0
    summary: dict = Field(default_factory=dict)
    verdict: dict = Field(default_factory=dict)
    error: str = ""
    elapsed_ms: int = 0
    created_at: str
    finished_at: str | None = None
    steps: list[AgentStepOut] = Field(default_factory=list)
