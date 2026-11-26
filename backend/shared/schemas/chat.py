"""问答的请求数据模型（FR-03）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# 历史记录
class HistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)

# 聊天输入
class ChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    # 范围三选一或组合：主题走向量分区，标签/资料走片段白名单
    topic_id: str | None = None
    tag_ids: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    history: list[HistoryItem] = Field(default_factory=list)
    # Agent 选源（P2）：开启后按 本地 → 组内(catalog) → 联邦 选源，来源数上限 3。
    # 默认关闭，保持单空间问答的既有行为不变。
    # 已显式圈定本地范围（topic/tag/document）时不做跨源扩散——那些 id 只在本空间成立。
    multi_source: bool = False

# 引用内容
class CitationOut(BaseModel):
    index: int
    chunk_id: str
    document_id: str | None = None
    document_name: str | None = None
    snippet: str
    page: int | None = None
