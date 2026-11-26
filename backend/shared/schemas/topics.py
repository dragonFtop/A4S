"""主题与标签的请求 / 响应模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

TopicKindLiteral = Literal["topic", "tag"]


class TopicOut(BaseModel):
    id: str
    name: str
    kind: TopicKindLiteral
    color: str
    sort_order: int
    count: int = 0
    is_builtin: bool = False


class TopicCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    kind: TopicKindLiteral = "topic"
    color: str | None = Field(default=None, max_length=16)


class TopicUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    color: str | None = Field(default=None, max_length=16)
    sort_order: int | None = None


class TopicListOut(BaseModel):
    topics: list[TopicOut]
    tags: list[TopicOut]


class PackOut(BaseModel):
    id: str
    name: str
    summary: str
    topics: list[str]


class ApplyPacksIn(BaseModel):
    pack_ids: list[str] = Field(min_length=1)


class ApplyPacksOut(BaseModel):
    created_topics: int
    created_tags: int
    skipped_packs: list[str] = Field(default_factory=list)


class SeededOut(BaseModel):
    """是否需要展示"选学习方向"引导：已有除「未分类」外的主题就不用。"""

    seeded: bool
