"""笔记仓储：正文 + 标签 + 来源角标，写入时同步维护 FTS 索引。"""

from __future__ import annotations

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.domain.models.entities import Note, NoteSource, NoteSourceType, NoteStatus, NoteTemplate
from app.infrastructure.db import fts
from app.infrastructure.db.models import DocumentORM, NoteORM, NoteSourceORM, NoteTagORM


def _to_note(row: NoteORM, tag_ids: list[str], sources: list[NoteSource]) -> Note:
    return Note(
        id=row.id,
        title=row.title,
        markdown=row.markdown,
        topic_id=row.topic_id,
        status=NoteStatus(row.status),
        source_type=NoteSourceType(row.source_type),
        template=NoteTemplate(row.template) if row.template else None,
        ai_model=row.ai_model,
        tag_ids=tag_ids,
        sources=sources,
        created_at=row.created_at,
        updated_at=row.updated_at,
        confirmed_at=row.confirmed_at,
        user_id=row.user_id,
        space_id=row.space_id,
    )


class SqlAlchemyNoteRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _tags_map(self, note_ids: list[str]) -> dict[str, list[str]]:
        if not note_ids:
            return {}
        rows = self.db.execute(
            select(NoteTagORM.note_id, NoteTagORM.tag_id).where(NoteTagORM.note_id.in_(note_ids))
        )
        result: dict[str, list[str]] = {}
        for note_id, tag_id in rows:
            result.setdefault(note_id, []).append(tag_id)
        return result

    def _sources_map(self, note_ids: list[str]) -> dict[str, list[NoteSource]]:
        if not note_ids:
            return {}
        rows = self.db.execute(
            select(NoteSourceORM, DocumentORM.name)
            .outerjoin(DocumentORM, DocumentORM.id == NoteSourceORM.document_id)
            .where(NoteSourceORM.note_id.in_(note_ids))
        )
        result: dict[str, list[NoteSource]] = {}
        for source, doc_name in rows:
            result.setdefault(source.note_id, []).append(
                NoteSource(
                    chunk_id=source.chunk_id,
                    snippet=source.snippet,
                    document_id=source.document_id,
                    document_name=doc_name,
                )
            )
        return result

    def add(self, note: Note) -> None:
        self.db.add(
            NoteORM(
                id=note.id,
                title=note.title,
                markdown=note.markdown,
                topic_id=note.topic_id,
                status=note.status.value,
                source_type=note.source_type.value,
                template=note.template.value if note.template else None,
                ai_model=note.ai_model,
                created_at=note.created_at,
                updated_at=note.updated_at,
                confirmed_at=note.confirmed_at,
                user_id=note.user_id,
                space_id=note.space_id,
            )
        )
        self.db.flush()
        self._replace_relations(note)
        self._sync_fts(note)
        self.db.commit()

    def _sync_fts(self, note: Note) -> None:
        """同一事务里维护 FTS 行，杜绝"笔记改了索引还是旧的"。"""
        from app.infrastructure.db.session import FTS_ENABLED

        if not FTS_ENABLED:
            return
        fts.upsert(self.db.connection(), note.id, note.title, note.markdown)

    def get(self, note_id: str, space_id: str) -> Note | None:
        row = self.db.get(NoteORM, note_id)
        if row is None or row.space_id != space_id:
            return None
        tags = self._tags_map([note_id]).get(note_id, [])
        sources = self._sources_map([note_id]).get(note_id, [])
        return _to_note(row, tags, sources)

    def list(
        self,
        space_id: str,
        limit: int = 100,
        offset: int = 0,
        topic_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        status: str | None = None,
        keyword: str | None = None,
    ) -> list[Note]:
        stmt = select(NoteORM).where(NoteORM.space_id == space_id)
        if topic_ids:
            stmt = stmt.where(NoteORM.topic_id.in_(topic_ids))
        if status:
            stmt = stmt.where(NoteORM.status == status)
        ranked: list[str] = []
        if keyword:
            # FTS5 走相关度排序；不可用时退回 LIKE（仍然能搜，只是没有单字中文能力）
            from app.infrastructure.db.session import FTS_ENABLED

            if FTS_ENABLED:
                ranked = fts.search(self.db.connection(), keyword, limit=500)
            if ranked:
                stmt = stmt.where(NoteORM.id.in_(ranked))
            else:
                stmt = stmt.where(
                    or_(NoteORM.title.contains(keyword), NoteORM.markdown.contains(keyword))
                )
        if tag_ids:
            for tag_id in tag_ids:
                sub = select(NoteTagORM.note_id).where(NoteTagORM.tag_id == tag_id)
                stmt = stmt.where(NoteORM.id.in_(sub))
        stmt = stmt.order_by(NoteORM.updated_at.desc()).limit(limit).offset(offset)

        rows = list(self.db.execute(stmt).scalars())
        if ranked:
            order = {note_id: index for index, note_id in enumerate(ranked)}
            rows.sort(key=lambda row: order.get(row.id, len(order)))
        ids = [row.id for row in rows]
        tags = self._tags_map(ids)
        sources = self._sources_map(ids)
        return [_to_note(row, tags.get(row.id, []), sources.get(row.id, [])) for row in rows]

    def update(self, note: Note) -> None:
        row = self.db.get(NoteORM, note.id)
        if row is None or row.space_id != note.space_id:
            return
        row.title = note.title
        row.markdown = note.markdown
        row.topic_id = note.topic_id
        row.status = note.status.value
        row.template = note.template.value if note.template else None
        row.ai_model = note.ai_model
        row.updated_at = note.updated_at
        row.confirmed_at = note.confirmed_at
        self._replace_relations(note)
        self._sync_fts(note)
        self.db.commit()

    def _replace_relations(self, note: Note) -> None:
        self.db.execute(delete(NoteTagORM).where(NoteTagORM.note_id == note.id))
        for tag_id in dict.fromkeys(note.tag_ids):
            self.db.add(NoteTagORM(note_id=note.id, tag_id=tag_id))
        self.db.execute(delete(NoteSourceORM).where(NoteSourceORM.note_id == note.id))
        for source in note.sources:
            self.db.add(
                NoteSourceORM(
                    note_id=note.id,
                    chunk_id=source.chunk_id,
                    document_id=source.document_id,
                    snippet=source.snippet[:500],
                )
            )

    def delete(self, note_id: str, space_id: str) -> None:
        row = self.db.get(NoteORM, note_id)
        if row is not None and row.space_id == space_id:
            self.db.execute(delete(NoteTagORM).where(NoteTagORM.note_id == note_id))
            self.db.execute(delete(NoteSourceORM).where(NoteSourceORM.note_id == note_id))
            self.db.delete(row)
            from app.infrastructure.db.session import FTS_ENABLED

            if FTS_ENABLED:
                fts.remove(self.db.connection(), note_id)
            self.db.commit()
