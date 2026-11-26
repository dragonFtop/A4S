"""联邦执行的请求 / 响应模型（P3 / FR-3.1 ~ FR-3.5）。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from shared.schemas.common import UtcDateTime


class NodeRegisterIn(BaseModel):
    node_id: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=64)
    serving_space_id: str | None = None


class NodeOut(BaseModel):
    node_id: str
    name: str = ""
    serving_space_id: str | None = None
    online: bool = False
    last_seen: UtcDateTime


class NodeListOut(BaseModel):
    items: list[NodeOut] = Field(default_factory=list)
    total: int = 0


class FedCitation(BaseModel):
    """联邦回传的引用片段。

    只有文本与出处（以及一个用于排序的 score），**不含向量、不含生成答案**（C7）。
    """

    chunk_id: str
    document_id: str | None = None
    document_name: str | None = None
    snippet: str
    page: int | None = None
    score: float = 0.0


class ClaimIn(BaseModel):
    node_id: str = Field(min_length=1, max_length=64)


class TaskOut(BaseModel):
    id: str
    question: str


class ClaimOut(BaseModel):
    task: TaskOut | None = None


class ResultIn(BaseModel):
    task_id: str = Field(min_length=1, max_length=64)
    node_id: str = Field(min_length=1, max_length=64)
    citations: list[FedCitation] = Field(default_factory=list)


class QueryIn(BaseModel):
    target_node_id: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=2000)
    requester_node_id: str = Field(default="", max_length=64)
    # 便于测试缩短等待；默认用服务端配置的 fed_hold_seconds。
    timeout_seconds: float | None = None


class QueryOut(BaseModel):
    # ok / offline / quota_exceeded
    status: str
    target_node_id: str
    citations: list[FedCitation] = Field(default_factory=list)
    latency_ms: int = 0


class AuditOut(BaseModel):
    id: str
    requester_node_id: str
    target_node_id: str
    hits: int
    status: str
    latency_ms: int
    created_at: UtcDateTime


class AuditListOut(BaseModel):
    items: list[AuditOut] = Field(default_factory=list)
    total: int = 0
