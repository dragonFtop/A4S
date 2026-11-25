"""主题与标签仓储：同一张表，用 kind 区分。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.models.entities import Topic, TopicKind
from app.infrastructure.db.models import DocumentORM, DocumentTagORM, NoteORM, TopicORM


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
    )


class SqlAlchemyTopicRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _counts(self, kind: TopicKind) -> dict[str, int]:
        if kind is TopicKind.TOPIC:
            stmt = select(DocumentORM.topic_id, func.count()).group_by(DocumentORM.topic_id)
        else:
            stmt = select(DocumentTagORM.tag_id, func.count()).group_by(DocumentTagORM.tag_id)
        return {row[0]: int(row[1]) for row in self.db.execute(stmt)}

    def list(self, kind: TopicKind | None = None) -> list[Topic]:
        stmt = select(TopicORM)
        if kind is not None:
            stmt = stmt.where(TopicORM.kind == kind.value)
            counts = self._counts(kind)
        else:
            counts = {}
        stmt = stmt.order_by(TopicORM.sort_order, TopicORM.created_at)
        rows = self.db.execute(stmt).scalars()
        if kind is None:
            topic_counts = self._counts(TopicKind.TOPIC)
            tag_counts = self._counts(TopicKind.TAG)
            counts = {**topic_counts, **tag_counts}
        return [_to_topic(row, counts.get(row.id, 0)) for row in rows]

    def get(self, topic_id: str) -> Topic | None:
        row = self.db.get(TopicORM, topic_id)
        return _to_topic(row) if row else None

    def get_by_name(self, name: str, kind: TopicKind) -> Topic | None:
        row = self.db.execute(
            select(TopicORM).where(TopicORM.name == name, TopicORM.kind == kind.value)
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
            )
        )
        self.db.commit()

    def update(self, topic: Topic) -> None:
        row = self.db.get(TopicORM, topic.id)
        if row is None:
            return
        row.name = topic.name
        row.color = topic.color
        row.sort_order = topic.sort_order
        self.db.commit()

    def delete(self, topic_id: str) -> None:
        row = self.db.get(TopicORM, topic_id)
        if row is not None:
            self.db.delete(row)
            self.db.commit()

    def reassign_notes(self, from_id: str, to_id: str) -> int:
        """删主题时把笔记迁走。资料的迁移由服务层逐个调用（要同步搬向量分区）。"""
        rows = self.db.execute(select(NoteORM).where(NoteORM.topic_id == from_id)).scalars()
        count = 0
        for row in rows:
            row.topic_id = to_id
            count += 1
        self.db.commit()
        return count

    def next_sort_order(self, kind: TopicKind) -> int:
        value = self.db.execute(
            select(func.max(TopicORM.sort_order)).where(TopicORM.kind == kind.value)
        ).scalar()
        return int(value or 0) + 1
