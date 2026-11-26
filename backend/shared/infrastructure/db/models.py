"""ORM 模型（持久化层）。

注意：这里只负责"怎么存"，不承载业务规则。
业务语义在 shared.domain/models/entities.py，两者通过仓储实现互转。

向量不在本文件：sqlite-vec 的虚拟表由 infrastructure/vectorstore 管理，
通过 chunk_id 与这里的 chunks 表关联。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
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

from shared.infrastructure.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


# 与 domain/models/entities.py 的 LEGACY_SPACE_ID 保持一致：
# 迁移时已有数据统一挂这里，首个用户注册时整批认领。
LEGACY_SPACE_ID = "default"


class TopicORM(Base):
    """主题与标签共用一张表，用 kind 区分。

    kind='topic'：资料/笔记的唯一归属（单选）
    kind='tag'  ：横向切面（多选）

    主键是 (space_id, id)：主题是**空间私有**的，两个学习小组可以各自有一个
    叫「未分类」的主题、各用同一个 id。全局唯一 id 在这里不成立。
    """

    __tablename__ = "topics"
    __table_args__ = (
        UniqueConstraint("space_id", "kind", "name", name="uq_topics_space_kind_name"),
    )

    space_id: Mapped[str] = mapped_column(String(36), primary_key=True)
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

    # 归属空间：所有查询都必须带上它，否则会跨空间串数据
    space_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("spaces.id"), nullable=False, default=LEGACY_SPACE_ID, index=True
    )
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

    # 归属空间：所有查询都必须带上它，否则会跨空间串数据
    space_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("spaces.id"), nullable=False, default=LEGACY_SPACE_ID, index=True
    )
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

    # 归属空间：所有查询都必须带上它，否则会跨空间串数据
    space_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("spaces.id"), nullable=False, default=LEGACY_SPACE_ID, index=True
    )
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

    # 归属空间：所有查询都必须带上它，否则会跨空间串数据
    space_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("spaces.id"), nullable=False, default=LEGACY_SPACE_ID, index=True
    )
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

    # 归属空间：所有查询都必须带上它，否则会跨空间串数据
    space_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("spaces.id"), nullable=False, default=LEGACY_SPACE_ID, index=True
    )
    question_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    topic_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    question: Mapped[dict] = mapped_column(JSON, nullable=False)
    wrong_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 最近一次答错时写下的答案：错题本要能并排对比「你的答案 / 正确答案」
    last_response: Mapped[str] = mapped_column(Text, nullable=False, default="")
    first_wrong_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    last_wrong_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class StudyPlanORM(Base):
    """学习计划。一行一次规划会话：markdown 是产物，meta 存过程（每步工具调用与返回摘要）。"""

    __tablename__ = "study_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    # 归属空间：所有查询都必须带上它，否则会跨空间串数据
    space_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("spaces.id"), nullable=False, default=LEGACY_SPACE_ID, index=True
    )
    # 用户这次补充的学习目标，可为空
    goal: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 过程快照：steps / stats / practice_session_id / model / elapsed_seconds
    meta: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="local")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class ReviewReportORM(Base):
    __tablename__ = "review_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    # 归属空间：所有查询都必须带上它，否则会跨空间串数据
    space_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("spaces.id"), nullable=False, default=LEGACY_SPACE_ID, index=True
    )
    window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    period_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


# ---------------------------------------------------------------- 用户与空间


class UserORM(Base):
    """登录账号。password_hash 是 scrypt 派生串，明文不落库。"""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    display_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AuthTokenORM(Base):
    """会话 token。

    没有用 JWT：token 存库才能"退出即失效"、才能看到谁在线。
    个人项目的代价只是每次请求多一次 SQLite 主键查询。
    """

    __tablename__ = "auth_tokens"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class SpaceORM(Base):
    """空间：个人空间（注册时自动建）或学习小组（手动建）。

    团队不另建模型，直接复用这个容器——把别人加进个人空间就变成了小组。
    """

    __tablename__ = "spaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="personal")
    owner_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    # 对外可查开关：默认 0。打开后才进公共目录、才接受别人的申请。
    share_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class SpaceMemberORM(Base):
    """空间成员。role 只有 owner / member，唯一 owner 不可移除。"""

    __tablename__ = "space_members"

    space_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("spaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="member")
    joined_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class SpaceShareORM(Base):
    """知识库共享（外部只读授权）。

    唯一约束放在 (space_id, grantee_id)：同一个人对同一个空间只保留一条记录，
    重新申请时改状态而不是插新行——否则列表里会堆出一串历史申请。
    """

    __tablename__ = "space_shares"
    __table_args__ = (
        UniqueConstraint("space_id", "grantee_id", name="uq_share_space_grantee"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    space_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    grantee_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    # request = 对方申请，grant = 主人主动开放
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="request")
    message: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class FedNodeORM(Base):
    """联邦节点登记（P3）。

    节点 = 一个客户端进程。它启动后周期性向 server 心跳注册，server 据此判定
    在线（last_seen 在阈值内）。`serving_space_id` 是它愿意对外提供检索的空间。
    """

    __tablename__ = "fed_nodes"

    node_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    serving_space_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class FedAuditORM(Base):
    """联邦请求审计（P3）：每次联邦查询一行，用于配额与防抽取攻击追溯。"""

    __tablename__ = "fed_audit"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    requester_node_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    target_node_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    question: Mapped[str] = mapped_column(Text, nullable=False, default="")
    hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # ok / offline / quota_exceeded
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)

