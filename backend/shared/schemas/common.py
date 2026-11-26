"""API 请求 / 响应模型（DTO）。

只服务接口层，不承担业务规则，也不直接等同领域模型。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field


def _ensure_utc(value: object) -> object:
    """数据库里存的是 naive UTC（SQLite 无时区），
    直接序列化成 "2026-11-24T11:51:00" 会被前端当成**本地时间**解析，
    导致时间整体偏移时区差。这里统一补上 UTC 时区再输出。"""
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


UtcDateTime = Annotated[datetime, BeforeValidator(_ensure_utc)]


class HealthResponse(BaseModel):
    status: str
    version: str
    env: str
    db: bool
    llm_configured: bool
    embedding_provider: str


class TopicBrief(BaseModel):
    """嵌入在其他对象里的轻量主题信息（列表行、来源角标用）。"""

    id: str
    name: str
    color: str


class DocumentOut(BaseModel):
    id: str
    name: str
    ext: str
    size: int
    status: str
    chunk_count: int
    error: str | None = None
    created_at: UtcDateTime
    ready_at: UtcDateTime | None = None
    topic: TopicBrief
    tags: list[TopicBrief] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    detail: str
