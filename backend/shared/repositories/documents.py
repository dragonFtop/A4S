"""资料仓储：元信息、标签关联、主题迁移与删除级联。

所有读写都带 `space_id`：不是可选参数，是必填。漏传会直接 TypeError，
而不是悄悄返回全量数据——**串数据的代价比一次报错高得多**。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from shared.domain.models.entities import Document, DocumentStatus
from shared.infrastructure.db.models import ChunkORM, DocumentORM, DocumentTagORM


def _to_document(row: DocumentORM, tag_ids: list[str] | None = None) -> Document:
    return Document(
        id=row.id,
        name=row.name,
        ext=row.ext,
        size=row.size,
        status=DocumentStatus(row.status),
        chunk_count=row.chunk_count,
        error=row.error,
        created_at=row.created_at,
        ready_at=row.ready_at,
        topic_id=row.topic_id,
        tag_ids=tag_ids or [],
        storage_path=row.storage_path,
        user_id=row.user_id,
        space_id=row.space_id,
    )


class SqlAlchemyDocumentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _tags_map(self, doc_ids: list[str]) -> dict[str, list[str]]:
        if not doc_ids:
            return {}
        rows = self.db.execute(
            select(DocumentTagORM.document_id, DocumentTagORM.tag_id).where(
                DocumentTagORM.document_id.in_(doc_ids)
            )
        )
        result: dict[str, list[str]] = {}
        for doc_id, tag_id in rows:
            result.setdefault(doc_id, []).append(tag_id)
        return result

    def _filtered(self, space_id: str, topic_ids, tag_ids, status, keyword):
        stmt = select(DocumentORM).where(DocumentORM.space_id == space_id)
        if topic_ids:
            stmt = stmt.where(DocumentORM.topic_id.in_(topic_ids))
        if status is not None:
            stmt = stmt.where(DocumentORM.status == status.value)
        if keyword:
            stmt = stmt.where(or_(DocumentORM.name.contains(keyword)))
        if tag_ids:
            # 多标签取交集：每个标签都要有
            for tag_id in tag_ids:
                sub = select(DocumentTagORM.document_id).where(DocumentTagORM.tag_id == tag_id)
                stmt = stmt.where(DocumentORM.id.in_(sub))
        return stmt

    def add(self, document: Document) -> None:
        self.db.add(
            DocumentORM(
                id=document.id,
                name=document.name,
                ext=document.ext,
                size=document.size,
                status=document.status.value,
                chunk_count=document.chunk_count,
                error=document.error,
                topic_id=document.topic_id,
                storage_path=document.storage_path,
                user_id=document.user_id,
                space_id=document.space_id,
                created_at=document.created_at,
                ready_at=document.ready_at,
            )
        )
        self.db.flush()
        self.set_tags(document.id, document.tag_ids, commit=False)
        self.db.commit()

    def get(self, document_id: str, space_id: str) -> Document | None:
        row = self.db.get(DocumentORM, document_id)
        if row is None or row.space_id != space_id:
            return None
        return _to_document(row, self._tags_map([document_id]).get(document_id, []))

    def list(
        self,
        space_id: str,
        limit: int = 100,
        offset: int = 0,
        topic_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        status: DocumentStatus | None = None,
        keyword: str | None = None,
    ) -> list[Document]:
        stmt = (
            self._filtered(space_id, topic_ids, tag_ids, status, keyword)
            .order_by(DocumentORM.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = list(self.db.execute(stmt).scalars())
        tags = self._tags_map([row.id for row in rows])
        return [_to_document(row, tags.get(row.id, [])) for row in rows]

    def count(
        self,
        space_id: str,
        topic_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        status: DocumentStatus | None = None,
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(DocumentORM)
            .where(DocumentORM.space_id == space_id)
        )
        if topic_ids:
            stmt = stmt.where(DocumentORM.topic_id.in_(topic_ids))
        if status is not None:
            stmt = stmt.where(DocumentORM.status == status.value)
        if tag_ids:
            for tag_id in tag_ids:
                sub = select(DocumentTagORM.document_id).where(DocumentTagORM.tag_id == tag_id)
                stmt = stmt.where(DocumentORM.id.in_(sub))
        return int(self.db.execute(stmt).scalar() or 0)

    def update(self, document: Document) -> None:
        row = self.db.get(DocumentORM, document.id)
        if row is None or row.space_id != document.space_id:
            return
        row.name = document.name
        row.status = document.status.value
        row.chunk_count = document.chunk_count
        row.error = document.error
        row.ready_at = document.ready_at
        row.topic_id = document.topic_id
        self.db.commit()

    def update_status(
        self,
        document_id: str,
        status: DocumentStatus,
        chunk_count: int | None = None,
        error: str | None = None,
        *,
        space_id: str | None = None,
    ) -> None:
        """后台任务按 id 更新状态，空间校验交给调用方（它刚创建这条记录）。"""
        row = self.db.get(DocumentORM, document_id)
        if row is None:
            return
        if space_id is not None and row.space_id != space_id:
            return
        row.status = status.value
        if chunk_count is not None:
            row.chunk_count = chunk_count
        row.error = error
        if status is DocumentStatus.READY:
            row.ready_at = datetime.now(timezone.utc)
        self.db.commit()

    def set_topic(self, document_id: str, topic_id: str, space_id: str) -> None:
        row = self.db.get(DocumentORM, document_id)
        if row is None or row.space_id != space_id:
            return
        row.topic_id = topic_id
        # chunk 的分区键必须跟着改，否则按主题检索会漏
        for chunk in self.db.execute(
            select(ChunkORM).where(ChunkORM.document_id == document_id)
        ).scalars():
            chunk.topic_id = topic_id
        self.db.commit()

    def set_tags(self, document_id: str, tag_ids: list[str], commit: bool = True) -> None:
        # 关联表不带 space_id：document_id 已经限定了归属
        self.db.execute(delete(DocumentTagORM).where(DocumentTagORM.document_id == document_id))
        for tag_id in dict.fromkeys(tag_ids):  # 去重保序
            self.db.add(DocumentTagORM(document_id=document_id, tag_id=tag_id))
        if commit:
            self.db.commit()

    def list_ids_by_topic(self, topic_id: str, space_id: str) -> list[str]:
        rows = self.db.execute(
            select(DocumentORM.id).where(
                DocumentORM.topic_id == topic_id, DocumentORM.space_id == space_id
            )
        ).scalars()
        return list(rows)

    def delete(self, document_id: str, space_id: str) -> None:
        row = self.db.get(DocumentORM, document_id)
        if row is None or row.space_id != space_id:
            return
        self.db.execute(delete(DocumentTagORM).where(DocumentTagORM.document_id == document_id))
        self.db.execute(delete(ChunkORM).where(ChunkORM.document_id == document_id))
        self.db.delete(row)
        self.db.commit()
