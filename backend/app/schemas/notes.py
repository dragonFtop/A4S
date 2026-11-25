"""笔记的请求 / 响应模型（FR-13）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import TopicBrief, UtcDateTime

TemplateLiteral = Literal["outline", "concept", "code", "formula"]
StatusLiteral = Literal["draft", "confirmed"]


class NoteSourceOut(BaseModel):
    chunk_id: str
    document_id: str | None = None
    document_name: str | None = None
    snippet: str
    # 角标序号，从 1 开始，与正文 [1] 对应
    index: int
    # 来源资料已被删除时前端显示"来源已删除"，而不是报错
    missing: bool = False


class NoteBriefOut(BaseModel):
    id: str
    title: str
    excerpt: str
    status: StatusLiteral
    source_type: str
    template: TemplateLiteral | None = None
    ai_written_count: int = 0
    source_count: int = 0
    topic: TopicBrief
    tags: list[TopicBrief] = Field(default_factory=list)
    created_at: UtcDateTime
    updated_at: UtcDateTime
    confirmed_at: UtcDateTime | None = None


class NoteOut(BaseModel):
    id: str
    title: str
    markdown: str
    status: StatusLiteral
    source_type: str
    template: TemplateLiteral | None = None
    ai_model: str | None = None
    ai_written_count: int = 0
    topic: TopicBrief
    tags: list[TopicBrief] = Field(default_factory=list)
    sources: list[NoteSourceOut] = Field(default_factory=list)
    created_at: UtcDateTime
    updated_at: UtcDateTime
    confirmed_at: UtcDateTime | None = None


class NoteListOut(BaseModel):
    items: list[NoteBriefOut]
    total: int


class NoteCreate(BaseModel):
    title: str = Field(default="", max_length=200)
    topic_id: str = "uncategorized"
    tag_ids: list[str] = Field(default_factory=list)
    markdown: str = ""
    template: TemplateLiteral | None = None
    # 问答页「存为笔记」会标记 chat，用于列表上区分来源
    source_type: Literal["manual", "chat"] = "manual"


class NoteUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    markdown: str | None = None
    topic_id: str | None = None
    tag_ids: list[str] | None = None
    template: TemplateLiteral | None = None


class DraftIn(BaseModel):
    """起草请求。范围二选一：按资料 ID 列表，或按整个主题。"""

    template: TemplateLiteral
    document_ids: list[str] = Field(default_factory=list)
    topic_id: str | None = None
    note_topic_id: str | None = None
    tag_ids: list[str] = Field(default_factory=list)
    title: str = ""


class DraftJobOut(BaseModel):
    id: str
    state: Literal["running", "done", "failed"]
    note_id: str | None = None
    message: str = ""
    started_at: str | None = None
    finished_at: str | None = None


class TemplateOut(BaseModel):
    key: TemplateLiteral
    label: str
    hint: str


class TemplateListOut(BaseModel):
    items: list[TemplateOut]
