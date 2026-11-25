"""ORM 模型（持久化层）。

注意：这里只负责"怎么存"，不承载业务规则。
业务语义在 app/domain/models/entities.py，两者通过仓储实现互转。

向量不在本文件：sqlite-vec 的虚拟表由 infrastructure/vectorstore 管理，
通过 chunk_id 与这里的 chunks 表关联。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TopicORM(Base):
    """主题与标签共用一张表，用 kind 区分。

    kind='topic'：资料/笔记的唯一归属（单选）
    kind='tag'  ：横向切面（多选）
    """

    __tablename__ = "topics"
    __table_args__ = (UniqueConstraint("kind", "name", name="uq_topics_kind_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False, default="topic")
    color: Mapped[str] = mapped_column(String(16), nullable=False, default="#888780")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="local")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class DocumentORM(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    ext: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 归属主题：必填，缺省指向内置的「未分类」
    topic_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("topics.id"), nullable=False, default="uncategorized", index=True
    )
    # 原始文件相对 data/uploads 的路径（粘贴文本为空）
    storage_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="local")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DocumentTagORM(Base):
    """资料 ↔ 标签（多对多）。主题不走这张表，走 documents.topic_id。"""

    __tablename__ = "document_tags"

    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
    )


class ChunkORM(Base):
    """片段正文与定位信息。向量存在 vec_chunks，用 id 关联。"""

    __tablename__ = "chunks"
    __table_args__ = (
        Index("ix_chunks_document", "document_id"),
        Index("ix_chunks_topic", "topic_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    topic_id: Mapped[str] = mapped_column(String(36), nullable=False, default="uncategorized")


class NoteORM(Base):
    """电子笔记。draft → confirmed 两阶段，见 PRD FR-13。"""

    __tablename__ = "notes"
    __table_args__ = (Index("ix_notes_topic", "topic_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    topic_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("topics.id"), nullable=False, default="uncategorized"
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    source_type: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    template: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ai_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="local")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class NoteSourceORM(Base):
    """笔记段落的来源溯源：正文里的 [[chunk:xxx]] 与这张表双向一致。"""

    __tablename__ = "note_sources"

    note_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("notes.id", ondelete="CASCADE"), primary_key=True
    )
    chunk_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    snippet: Mapped[str] = mapped_column(Text, nullable=False, default="")


class NoteTagORM(Base):
    __tablename__ = "note_tags"

    note_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("notes.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
    )


class PracticeSessionORM(Base):
    __tablename__ = "practice_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    questions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    answers: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # 组卷参数快照（范围、配比、难度、告警）：题目本身在 questions 里
    meta: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="local")
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WrongAnswerORM(Base):
    __tablename__ = "wrong_answers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    question_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    topic_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    question: Mapped[dict] = mapped_column(JSON, nullable=False)
    wrong_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 最近一次答错时写下的答案：错题本要能并排对比「你的答案 / 正确答案」
    last_response: Mapped[str] = mapped_column(Text, nullable=False, default="")
    first_wrong_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    last_wrong_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class ReviewReportORM(Base):
    __tablename__ = "review_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    period_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
