"""片段仓储：写入、按资料/主题读取、删除。向量不在本表，见 vectorstore。"""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.domain.models.entities import Chunk
from app.infrastructure.db.models import ChunkORM


def _to_chunk(row: ChunkORM) -> Chunk:
    return Chunk(
        id=row.id,
        document_id=row.document_id,
        index=row.chunk_index,
        text=row.text,
        source_name="",  # 由调用方按需补全文档名，避免每次 join
        page=row.page,
        topic_id=row.topic_id,
        space_id=row.space_id,
    )


class SqlAlchemyChunkRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add_many(self, chunks: list[Chunk]) -> None:
        for chunk in chunks:
            self.db.add(
                ChunkORM(
                    id=chunk.id,
                    document_id=chunk.document_id,
                    chunk_index=chunk.index,
                    text=chunk.text,
                    page=chunk.page,
                    topic_id=chunk.topic_id,
                    space_id=chunk.space_id,
                )
            )
        self.db.commit()

    def get(self, chunk_id: str, space_id: str) -> Chunk | None:
        row = self.db.get(ChunkORM, chunk_id)
        if row is None or row.space_id != space_id:
            return None
        return _to_chunk(row)

    def list_by_document(self, document_id: str) -> list[Chunk]:
        """按资料取片段。document_id 本身已限定归属，调用方需先校验资料属于当前空间。"""
        rows = self.db.execute(
            select(ChunkORM)
            .where(ChunkORM.document_id == document_id)
            .order_by(ChunkORM.chunk_index)
        ).scalars()
        return [_to_chunk(row) for row in rows]

    def list_by_topic(self, topic_id: str, space_id: str, limit: int = 200) -> list[Chunk]:
        rows = self.db.execute(
            select(ChunkORM)
            .where(ChunkORM.topic_id == topic_id, ChunkORM.space_id == space_id)
            .order_by(ChunkORM.document_id, ChunkORM.chunk_index)
            .limit(limit)
        ).scalars()
        return [_to_chunk(row) for row in rows]

    def list_all(self, space_id: str, limit: int = 200, offset: int = 0) -> list[Chunk]:
        """全量遍历（重建索引时用）。按 id 稳定排序，保证翻页不重不漏。"""
        rows = self.db.execute(
            select(ChunkORM)
            .where(ChunkORM.space_id == space_id)
            .order_by(ChunkORM.id)
            .limit(limit)
            .offset(offset)
        ).scalars()
        return [_to_chunk(row) for row in rows]

    def delete_by_document(self, document_id: str) -> int:
        result = self.db.execute(delete(ChunkORM).where(ChunkORM.document_id == document_id))
        self.db.commit()
        return int(result.rowcount or 0)

    # ---- 以下两个是**运维**视角（重建索引、健康检查），故意跨空间 ----
    # 向量表是全局一张，一致性检查必须拿全局片段数去比，按空间比没有意义。

    def count_all(self) -> int:
        return int(
            self.db.execute(select(func.count()).select_from(ChunkORM)).scalar() or 0
        )

    def iter_all_chunks(self, limit: int = 200, offset: int = 0) -> list[Chunk]:
        rows = self.db.execute(
            select(ChunkORM).order_by(ChunkORM.id).limit(limit).offset(offset)
        ).scalars()
        return [_to_chunk(row) for row in rows]

    def count(self, space_id: str, topic_id: str | None = None) -> int:
        stmt = (
            select(func.count())
            .select_from(ChunkORM)
            .where(ChunkORM.space_id == space_id)
        )
        if topic_id:
            stmt = stmt.where(ChunkORM.topic_id == topic_id)
        return int(self.db.execute(stmt).scalar() or 0)
