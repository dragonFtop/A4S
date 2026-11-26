"""主题与标签的业务规则。

规则出处：PRD FR-12。
- 名称同类内唯一（不区分大小写）
- 「未分类」不可删、不可改
- 删主题不删资料，资料迁到「未分类」
- 删标签只解关联
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.domain.models.entities import UNCATEGORIZED_ID, Topic, TopicKind
from app.infrastructure.db.seed import DEFAULT_TAG_COLOR, apply_topic_packs, topic_seeded
from app.infrastructure.db.models import DocumentTagORM, NoteTagORM
from app.repositories.sqlalchemy_repo import SqlAlchemyTopicRepository

PALETTE = (
    "#7F77DD",
    "#1D9E75",
    "#D85A30",
    "#D4537E",
    "#378ADD",
    "#639922",
    "#BA7517",
    "#888780",
)


class TopicConflict(ValueError):
    """名称重复。"""


class TopicProtected(ValueError):
    """试图改动内置主题。"""


def _pick_color(db: Session, kind: TopicKind, space_id: str) -> str:
    existing = SqlAlchemyTopicRepository(db).list(space_id, kind)
    return PALETTE[len(existing) % len(PALETTE)]


def create_topic(
    db: Session,
    *,
    space_id: str,
    name: str,
    kind: TopicKind = TopicKind.TOPIC,
    color: str | None = None,
) -> Topic:
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("名称不能为空")
    repo = SqlAlchemyTopicRepository(db)
    if repo.get_by_name(cleaned, kind, space_id) is not None:
        label = "主题" if kind is TopicKind.TOPIC else "标签"
        raise TopicConflict(f"{label}「{cleaned}」已存在")

    topic = Topic(
        id=str(uuid.uuid4()),
        space_id=space_id,
        name=cleaned,
        kind=kind,
        color=color or _pick_color(db, kind, space_id),
        sort_order=repo.next_sort_order(space_id, kind),
    )
    repo.add(topic)
    return topic


def update_topic(
    db: Session,
    topic_id: str,
    *,
    space_id: str,
    name: str | None = None,
    color: str | None = None,
    sort_order: int | None = None,
) -> Topic:
    repo = SqlAlchemyTopicRepository(db)
    topic = repo.get(topic_id, space_id)
    if topic is None:
        raise LookupError("主题不存在")
    if topic.id == UNCATEGORIZED_ID and name is not None and name.strip() != topic.name:
        raise TopicProtected("「未分类」是内置主题，不能改名")

    if name is not None:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("名称不能为空")
        duplicated = repo.get_by_name(cleaned, topic.kind, space_id)
        if duplicated is not None and duplicated.id != topic_id:
            label = "主题" if topic.kind is TopicKind.TOPIC else "标签"
            raise TopicConflict(f"{label}「{cleaned}」已存在")
        topic.name = cleaned
    if color is not None:
        topic.color = color
    if sort_order is not None:
        topic.sort_order = sort_order

    repo.update(topic)
    return topic


def delete_topic(db: Session, topic_id: str, space_id: str) -> dict:
    """返回迁移结果，供前端提示"已把 N 份资料移到未分类"。"""
    if topic_id == UNCATEGORIZED_ID:
        raise TopicProtected("「未分类」不能删除")

    repo = SqlAlchemyTopicRepository(db)
    topic = repo.get(topic_id, space_id)
    if topic is None:
        raise LookupError("主题不存在")

    moved = 0
    if topic.kind is TopicKind.TOPIC:
        # 逐个迁移：文档归属、chunk 分区键、向量分区一次改完（见 move_document_topic）
        from app.repositories.sqlalchemy_repo import SqlAlchemyDocumentRepository
        from app.services.ingest_service import move_document_topic

        document_ids = SqlAlchemyDocumentRepository(db).list_ids_by_topic(topic_id, space_id)
        for document_id in document_ids:
            move_document_topic(db, document_id, UNCATEGORIZED_ID, space_id)
        repo.reassign_notes(topic_id, UNCATEGORIZED_ID, space_id)
        moved = len(document_ids)
    else:
        db.execute(delete(DocumentTagORM).where(DocumentTagORM.tag_id == topic_id))
        db.execute(delete(NoteTagORM).where(NoteTagORM.tag_id == topic_id))
        db.commit()

    repo.delete(topic_id, space_id)
    return {"moved_documents": moved}


def seed_packs(db: Session, pack_ids: list[str], space_id: str) -> dict:
    return apply_topic_packs(db, pack_ids, space_id)


def has_topics(db: Session, space_id: str) -> bool:
    return topic_seeded(db, space_id)


__all__ = [
    "PALETTE",
    "DEFAULT_TAG_COLOR",
    "TopicConflict",
    "TopicProtected",
    "create_topic",
    "update_topic",
    "delete_topic",
    "seed_packs",
    "has_topics",
]
