"""主题与标签仓储：同一张表，用 kind 区分。

主题是**空间内私有**的：两个学习小组各自有「密码学」主题，互不可见。
所以 `list` / `get_by_name` / `next_sort_order` 都要带 space_id。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.domain.models.entities import Topic, TopicKind
from shared.infrastructure.db.models import DocumentORM, DocumentTagORM, NoteORM, TopicORM


def _to_topic(row: TopicORM, count: int = 0) -> Topic:
    return Topic(
        id=row.id,
        name=row.name,
        kind=TopicKind(row.kind),
        color=row.color,
        sort_order=row.sort_order,
        created_at=row.created_at,
        user_id=row.user_id,
        count=count,
        space_id=row.space_id,
    )


class SqlAlchemyTopicRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _counts(self, space_id: str, kind: TopicKind) -> dict[str, int]:
        if kind is TopicKind.TOPIC:
            stmt = (
                select(DocumentORM.topic_id, func.count())
                .where(DocumentORM.space_id == space_id)
                .group_by(DocumentORM.topic_id)
            )
        else:
            # 标签的引用数：只算本空间资料上的标签关联
            stmt = (
                select(DocumentTagORM.tag_id, func.count())
                .join(DocumentORM, DocumentORM.id == DocumentTagORM.document_id)
                .where(DocumentORM.space_id == space_id)
                .group_by(DocumentTagORM.tag_id)
            )
        return {row[0]: int(row[1]) for row in self.db.execute(stmt)}

    def list(self, space_id: str, kind: TopicKind | None = None) -> list[Topic]:
        stmt = select(TopicORM).where(TopicORM.space_id == space_id)
        if kind is not None:
            stmt = stmt.where(TopicORM.kind == kind.value)
            counts = self._counts(space_id, kind)
        else:
            counts = {}
        stmt = stmt.order_by(TopicORM.sort_order, TopicORM.created_at)
        rows = list(self.db.execute(stmt).scalars())
        if kind is None:
            counts = {
                **self._counts(space_id, TopicKind.TOPIC),
                **self._counts(space_id, TopicKind.TAG),
            }
        return [_to_topic(row, counts.get(row.id, 0)) for row in rows]

    def get(self, topic_id: str, space_id: str) -> Topic | None:
        row = self.db.get(TopicORM, (space_id, topic_id))
        if row is None or row.space_id != space_id:
            return None
        return _to_topic(row)

    def get_by_name(self, name: str, kind: TopicKind, space_id: str) -> Topic | None:
        row = self.db.execute(
            select(TopicORM).where(
                TopicORM.name == name,
                TopicORM.kind == kind.value,
                TopicORM.space_id == space_id,
            )
        ).scalar_one_or_none()
        return _to_topic(row) if row else None

    def add(self, topic: Topic) -> None:
        self.db.add(
            TopicORM(
                id=topic.id,
                name=topic.name,
                kind=topic.kind.value,
                color=topic.color,
                sort_order=topic.sort_order,
                created_at=topic.created_at,
                space_id=topic.space_id,
            )
        )
        self.db.commit()

    def update(self, topic: Topic) -> None:
        row = self.db.get(TopicORM, (topic.space_id, topic.id))
        if row is None or row.space_id != topic.space_id:
            return
        row.name = topic.name
        row.color = topic.color
        row.sort_order = topic.sort_order
        self.db.commit()

    def delete(self, topic_id: str, space_id: str) -> None:
        row = self.db.get(TopicORM, (space_id, topic_id))
        if row is None or row.space_id != space_id:
            return
        self.db.delete(row)
        self.db.commit()

    def reassign_notes(self, from_id: str, to_id: str, space_id: str) -> int:
        """删主题时把笔记迁走。资料的迁移由服务层逐个调用（要同步搬向量分区）。"""
        rows = self.db.execute(
            select(NoteORM).where(NoteORM.topic_id == from_id, NoteORM.space_id == space_id)
        ).scalars()
        count = 0
        for row in rows:
            row.topic_id = to_id
            count += 1
        self.db.commit()
        return count

    def next_sort_order(self, space_id: str, kind: TopicKind) -> int:
        value = self.db.execute(
            select(func.max(TopicORM.sort_order)).where(
                TopicORM.kind == kind.value, TopicORM.space_id == space_id
            )
        ).scalar()
        return int(value or 0) + 1
