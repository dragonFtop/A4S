"""知识库体检的出参模型（M8）。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from shared.schemas.plan import AgentStepOut


class HealthFindingOut(BaseModel):
    id: str
    check: str = ""
    severity: str = "low"
    claim: str = ""
    evidence: dict = Field(default_factory=dict)
    producer: str = "inspector"


class HealthFixOut(BaseModel):
    id: str
    action: str
    label: str = ""
    reason: str = ""
    target: list[str] = Field(default_factory=list)
    topic_id: str = ""
    finding_id: str = ""


class HealthReportOut(BaseModel):
    run_id: str
    status: str
    trigger: str = "manual"
    score: int = 0
    documents: int = 0
    counts: dict[str, int] = Field(default_factory=dict)
    findings: list[HealthFindingOut] = Field(default_factory=list)
    proposals: list[HealthFixOut] = Field(default_factory=list)
    steps: list[AgentStepOut] = Field(default_factory=list)
    elapsed_ms: int = 0
    created_at: str = ""