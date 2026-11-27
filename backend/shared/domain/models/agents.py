"""多智能体协作的契约（P0）。

结构是「黑板 + 角色契约」，不是自由对话：

- **Finding**：观察员 / 巡检员产出的一条事实，必须带证据；没证据的直接丢弃。
- **Proposal**：规划员提出的改动（计划 / 修复），此时还没落库。
- **Verdict**：审核员的结论；只有 `passed=True` 才允许 Writer 写入。
- **AgentRun / AgentStep**：一次运行的预算、状态与逐步回放，
  对应 `agent_runs` / `agent_steps` 两张表。

契约放在 shared 层，是因为仓储要按它落库；角色实现留在 client 侧。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex
    return f"{prefix}-{raw[:10]}" if prefix else raw[:12]


class AgentKind(str, Enum):
    """一次运行属于哪条泳道。"""

    COACH = "coach"
    KB_INSPECTION = "kb_inspection"


class AgentRole(str, Enum):
    COORDINATOR = "coordinator"
    OBSERVER = "observer"
    PLANNER = "planner"
    VERIFIER = "verifier"
    WRITER = "writer"
    INSPECTOR = "inspector"
    FIXER = "fixer"


ROLE_LABELS: dict[str, str] = {
    AgentRole.COORDINATOR.value: "协调员",
    AgentRole.OBSERVER.value: "观察员",
    AgentRole.PLANNER.value: "规划员",
    AgentRole.VERIFIER.value: "审核员",
    AgentRole.WRITER.value: "记录员",
    AgentRole.INSPECTOR.value: "资料巡检员",
    AgentRole.FIXER.value: "修复员",
}


class RunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class FindingKind(str, Enum):
    PROGRESS = "progress"
    WEAK_POINT = "weak_point"
    WRONG_ANSWER = "wrong_answer"
    PRACTICE = "practice"
    DOCUMENT = "document"


@dataclass
class Finding:
    """黑板上的最小事实单位。"""

    kind: FindingKind
    claim: str
    evidence: dict
    producer: str = AgentRole.OBSERVER.value
    confidence: float = 1.0
    id: str = field(default_factory=lambda: new_id("f"))
    created_at: datetime = field(default_factory=now_utc)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "claim": self.claim,
            "evidence": self.evidence,
            "producer": self.producer,
            "confidence": self.confidence,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class Proposal:
    """等待审核的改动。P0 只有计划一种。"""

    kind: str
    payload: dict
    producer: str = AgentRole.PLANNER.value
    id: str = field(default_factory=lambda: new_id("p"))
    created_at: datetime = field(default_factory=now_utc)

    @property
    def markdown(self) -> str:
        return str(self.payload.get("markdown") or "")

    @property
    def goal(self) -> str:
        return str(self.payload.get("goal") or "")

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "producer": self.producer,
            "chars": len(self.markdown),
            "goal": self.goal,
        }


@dataclass
class Verdict:
    """审核结论。reasons 会原样交给规划员，所以要写成人能读懂的一句话。"""

    passed: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)
    producer: str = AgentRole.VERIFIER.value

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "reasons": self.reasons,
            "warnings": self.warnings,
            "checked": self.checked,
        }


@dataclass
class AgentStep:
    """一行可回放的步骤。role + action 说明「谁做了什么」。"""

    run_id: str
    seq: int
    role: str
    action: str
    tool: str | None = None
    args: dict = field(default_factory=dict)
    ok: bool = True
    brief: str = ""
    result: dict | None = None
    elapsed_ms: int = 0
    id: int | None = None
    created_at: datetime = field(default_factory=now_utc)


@dataclass
class AgentRun:
    """一次协作运行的账本：预算、用量、结论。"""

    id: str
    space_id: str
    kind: str
    goal: str = ""
    trigger: str = "user"
    model: str = ""
    status: str = RunStatus.RUNNING.value
    budget_steps: int = 6
    steps_count: int = 0
    llm_calls: int = 0
    handoffs: int = 0
    summary: dict = field(default_factory=dict)
    verdict: dict = field(default_factory=dict)
    error: str = ""
    elapsed_ms: int = 0
    created_at: datetime = field(default_factory=now_utc)
    finished_at: datetime | None = None
    steps: list[AgentStep] = field(default_factory=list)