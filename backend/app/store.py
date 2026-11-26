"""数据读写：按实体分组，一组一个类。

一张表（或一组紧邻的表）一个类，方法就是那条 SQL。类名直接叫实体名——
这里只有一个实现，不需要在名字里声明 `SqlAlchemy`。

统计与回顾不在这里：它们要跨多张表聚合，各自成模块（`stats.py` / `reviews.py`）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app import fts
from app.models import (
    Chunk,
    Document,
    DocumentStatus,
    DocumentTag,
    Note,
    NoteSource,
    NoteTag,
    PracticeSession,
    StudyPlan,
    Topic,
    TopicKind,
    WrongAnswer,
)


class Topics:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _counts(self, kind: TopicKind) -> dict[str, int]:
        if kind is TopicKind.TOPIC:
            stmt = select(Document.topic_id, func.count()).group_by(Document.topic_id)
        else:
            stmt = select(DocumentTag.tag_id, func.count()).group_by(DocumentTag.tag_id)
        return {row[0]: int(row[1]) for row in self.db.execute(stmt)}

    def list(self, kind: TopicKind | None = None) -> list[Topic]:
        stmt = select(Topic)
        if kind is not None:
            stmt = stmt.where(Topic.kind == kind)
            counts = self._counts(kind)
        else:
            counts = {}
        stmt = stmt.order_by(Topic.sort_order, Topic.created_at)
        rows = list(self.db.execute(stmt).scalars())
        if kind is None:
            counts = {**self._counts(TopicKind.TOPIC), **self._counts(TopicKind.TAG)}
        for row in rows:
            row.count = counts.get(row.id, 0)
        return rows

    def get(self, topic_id: str) -> Topic | None:
        return self.db.get(Topic, topic_id)

    def get_by_name(self, name: str, kind: TopicKind) -> Topic | None:
        return self.db.execute(
            select(Topic).where(Topic.name == name, Topic.kind == kind)
        ).scalar_one_or_none()

    def add(self, topic: Topic) -> None:
        self.db.add(topic)
        self.db.commit()

    def update(self, topic: Topic) -> None:
        row = self.db.get(Topic, topic.id)
        if row is None:
            return
        row.name = topic.name
        row.color = topic.color
        row.sort_order = topic.sort_order
        self.db.commit()

    def delete(self, topic_id: str) -> None:
        row = self.db.get(Topic, topic_id)
        if row is not None:
            self.db.delete(row)
            self.db.commit()

    def reassign_notes(self, from_id: str, to_id: str) -> int:
        """删主题时把笔记迁走。资料的迁移由服务层逐个调用（要同步搬向量分区）。"""
        rows = self.db.execute(select(Note).where(Note.topic_id == from_id)).scalars()
        count = 0
        for row in rows:
            row.topic_id = to_id
            count += 1
        self.db.commit()
        return count

    def next_sort_order(self, kind: TopicKind) -> int:
        value = self.db.execute(
            select(func.max(Topic.sort_order)).where(Topic.kind == kind)
        ).scalar()
        return int(value or 0) + 1


class Documents:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _tags_map(self, doc_ids: list[str]) -> dict[str, list[str]]:
        if not doc_ids:
            return {}
        rows = self.db.execute(
            select(DocumentTag.document_id, DocumentTag.tag_id).where(
                DocumentTag.document_id.in_(doc_ids)
            )
        )
        result: dict[str, list[str]] = {}
        for doc_id, tag_id in rows:
            result.setdefault(doc_id, []).append(tag_id)
        return result

    def _filtered(self, topic_ids, tag_ids, status, keyword):
        stmt = select(Document)
        if topic_ids:
            stmt = stmt.where(Document.topic_id.in_(topic_ids))
        if status is not None:
            stmt = stmt.where(Document.status == status)
        if keyword:
            stmt = stmt.where(or_(Document.name.contains(keyword)))
        if tag_ids:
            # 多标签取交集：每个标签都要有
            for tag_id in tag_ids:
                sub = select(DocumentTag.document_id).where(DocumentTag.tag_id == tag_id)
                stmt = stmt.where(Document.id.in_(sub))
        return stmt

    def add(self, document: Document) -> None:
        self.db.add(document)
        self.db.flush()
        self.set_tags(document.id, document.tag_ids, commit=False)
        self.db.commit()

    def get(self, document_id: str) -> Document | None:
        row = self.db.get(Document, document_id)
        if row is not None:
            row.tag_ids = self._tags_map([document_id]).get(document_id, [])
        return row

    def list(
        self,
        limit: int = 100,
        offset: int = 0,
        topic_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        status: DocumentStatus | None = None,
        keyword: str | None = None,
    ) -> list[Document]:
        stmt = (
            self._filtered(topic_ids, tag_ids, status, keyword)
            .order_by(Document.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = list(self.db.execute(stmt).scalars())
        tags = self._tags_map([row.id for row in rows])
        for row in rows:
            row.tag_ids = tags.get(row.id, [])
        return rows

    def count(
        self,
        topic_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        status: DocumentStatus | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(Document)
        if topic_ids:
            stmt = stmt.where(Document.topic_id.in_(topic_ids))
        if status is not None:
            stmt = stmt.where(Document.status == status)
        if tag_ids:
            for tag_id in tag_ids:
                sub = select(DocumentTag.document_id).where(DocumentTag.tag_id == tag_id)
                stmt = stmt.where(Document.id.in_(sub))
        return int(self.db.execute(stmt).scalar() or 0)

    def update(self, document: Document) -> None:
        row = self.db.get(Document, document.id)
        if row is None:
            return
        row.name = document.name
        row.status = document.status
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
    ) -> None:
        row = self.db.get(Document, document_id)
        if row is None:
            return
        row.status = status
        if chunk_count is not None:
            row.chunk_count = chunk_count
        row.error = error
        if status is DocumentStatus.READY:
            row.ready_at = datetime.now(timezone.utc)
        self.db.commit()

    def set_topic(self, document_id: str, topic_id: str) -> None:
        row = self.db.get(Document, document_id)
        if row is None:
            return
        row.topic_id = topic_id
        # chunk 的分区键必须跟着改，否则按主题检索会漏
        for chunk in self.db.execute(
            select(Chunk).where(Chunk.document_id == document_id)
        ).scalars():
            chunk.topic_id = topic_id
        self.db.commit()

    def set_tags(self, document_id: str, tag_ids: list[str], commit: bool = True) -> None:
        self.db.execute(delete(DocumentTag).where(DocumentTag.document_id == document_id))
        for tag_id in dict.fromkeys(tag_ids):  # 去重保序
            self.db.add(DocumentTag(document_id=document_id, tag_id=tag_id))
        if commit:
            self.db.commit()

    def list_ids_by_topic(self, topic_id: str) -> list[str]:
        rows = self.db.execute(
            select(Document.id).where(Document.topic_id == topic_id)
        ).scalars()
        return list(rows)

    def delete(self, document_id: str) -> None:
        row = self.db.get(Document, document_id)
        if row is not None:
            self.db.execute(delete(DocumentTag).where(DocumentTag.document_id == document_id))
            self.db.execute(delete(Chunk).where(Chunk.document_id == document_id))
            self.db.delete(row)
            self.db.commit()


class Chunks:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add_many(self, chunks: list[Chunk]) -> None:
        self.db.add_all(chunks)
        self.db.commit()

    def get(self, chunk_id: str) -> Chunk | None:
        return self.db.get(Chunk, chunk_id)

    def list_by_document(self, document_id: str) -> list[Chunk]:
        return list(
            self.db.execute(
                select(Chunk)
                .where(Chunk.document_id == document_id)
                .order_by(Chunk.index)
            ).scalars()
        )

    def list_by_topic(self, topic_id: str, limit: int = 200) -> list[Chunk]:
        return list(
            self.db.execute(
                select(Chunk)
                .where(Chunk.topic_id == topic_id)
                .order_by(Chunk.document_id, Chunk.index)
                .limit(limit)
            ).scalars()
        )

    def list_all(self, limit: int = 200, offset: int = 0) -> list[Chunk]:
        """全量遍历（重建索引时用）。按 id 稳定排序，保证翻页不重不漏。"""
        return list(
            self.db.execute(select(Chunk).order_by(Chunk.id).limit(limit).offset(offset)).scalars()
        )

    def delete_by_document(self, document_id: str) -> int:
        result = self.db.execute(delete(Chunk).where(Chunk.document_id == document_id))
        self.db.commit()
        return int(result.rowcount or 0)

    def count(self, topic_id: str | None = None) -> int:
        stmt = select(func.count()).select_from(Chunk)
        if topic_id:
            stmt = stmt.where(Chunk.topic_id == topic_id)
        return int(self.db.execute(stmt).scalar() or 0)


class Notes:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _tags_map(self, note_ids: list[str]) -> dict[str, list[str]]:
        if not note_ids:
            return {}
        rows = self.db.execute(
            select(NoteTag.note_id, NoteTag.tag_id).where(NoteTag.note_id.in_(note_ids))
        )
        result: dict[str, list[str]] = {}
        for note_id, tag_id in rows:
            result.setdefault(note_id, []).append(tag_id)
        return result

    def _sources_map(self, note_ids: list[str]) -> dict[str, list[NoteSource]]:
        if not note_ids:
            return {}
        rows = self.db.execute(
            select(NoteSource, Document.name)
            .outerjoin(Document, Document.id == NoteSource.document_id)
            .where(NoteSource.note_id.in_(note_ids))
        )
        result: dict[str, list[NoteSource]] = {}
        for source, doc_name in rows:
            source.document_name = doc_name
            result.setdefault(source.note_id, []).append(source)
        return result

    def _fill_relations(self, notes: list[Note]) -> list[Note]:
        """把标签 id 与来源（含资料名）回填到对象上，供接口层直接取。"""
        ids = [note.id for note in notes]
        tags = self._tags_map(ids)
        sources = self._sources_map(ids)
        for note in notes:
            note.tag_ids = tags.get(note.id, [])
            note.sources = sources.get(note.id, [])
        return notes

    def add(self, note: Note) -> None:
        self.db.add(note)
        self.db.flush()
        self._replace_relations(note)
        self._sync_fts(note)
        self.db.commit()

    def _sync_fts(self, note: Note) -> None:
        """同一事务里维护 FTS 行，杜绝"笔记改了索引还是旧的"。"""
        from app.db import FTS_ENABLED

        if not FTS_ENABLED:
            return
        fts.upsert(self.db.connection(), note.id, note.title, note.markdown)

    def get(self, note_id: str) -> Note | None:
        row = self.db.get(Note, note_id)
        if row is None:
            return None
        return self._fill_relations([row])[0]

    def list(
        self,
        limit: int = 100,
        offset: int = 0,
        topic_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        status: str | None = None,
        keyword: str | None = None,
    ) -> list[Note]:
        stmt = select(Note)
        if topic_ids:
            stmt = stmt.where(Note.topic_id.in_(topic_ids))
        if status:
            stmt = stmt.where(Note.status == status)
        ranked: list[str] = []
        if keyword:
            # FTS5 走相关度排序；不可用时退回 LIKE（仍然能搜，只是没有单字中文能力）
            from app.db import FTS_ENABLED

            if FTS_ENABLED:
                ranked = fts.search(self.db.connection(), keyword, limit=500)
            if ranked:
                stmt = stmt.where(Note.id.in_(ranked))
            else:
                stmt = stmt.where(
                    or_(Note.title.contains(keyword), Note.markdown.contains(keyword))
                )
        if tag_ids:
            for tag_id in tag_ids:
                sub = select(NoteTag.note_id).where(NoteTag.tag_id == tag_id)
                stmt = stmt.where(Note.id.in_(sub))
        stmt = stmt.order_by(Note.updated_at.desc()).limit(limit).offset(offset)

        rows = list(self.db.execute(stmt).scalars())
        if ranked:
            order = {note_id: index for index, note_id in enumerate(ranked)}
            rows.sort(key=lambda row: order.get(row.id, len(order)))
        return self._fill_relations(rows)

    def update(self, note: Note) -> None:
        # merge 只复制映射列，tag_ids / sources 是投影属性，要显式带过去
        row = self.db.merge(note)
        row.tag_ids = note.tag_ids
        row.sources = note.sources
        self._replace_relations(row)
        self._sync_fts(row)
        self.db.commit()

    def _replace_relations(self, note: Note) -> None:
        self.db.execute(delete(NoteTag).where(NoteTag.note_id == note.id))
        for tag_id in dict.fromkeys(note.tag_ids):
            self.db.add(NoteTag(note_id=note.id, tag_id=tag_id))
        self.db.execute(delete(NoteSource).where(NoteSource.note_id == note.id))
        for source in note.sources:
            self.db.add(
                NoteSource(
                    note_id=note.id,
                    chunk_id=source.chunk_id,
                    document_id=source.document_id,
                    snippet=source.snippet[:500],
                )
            )

    def delete(self, note_id: str) -> None:
        row = self.db.get(Note, note_id)
        if row is not None:
            self.db.execute(delete(NoteTag).where(NoteTag.note_id == note_id))
            self.db.execute(delete(NoteSource).where(NoteSource.note_id == note_id))
            self.db.delete(row)
            from app.db import FTS_ENABLED

            if FTS_ENABLED:
                fts.remove(self.db.connection(), note_id)
            self.db.commit()


class PracticeSessions:
    def __init__(self, db: Session) -> None:
        self.db = db

    def save(self, session: PracticeSession) -> None:
        # merge：已存在就更新，不存在就插入。questions/answers 的 setter 已经
        # 把 Question 快照序列化成 JSON，这里不用再管。
        self.db.merge(session)
        self.db.commit()

    def get(self, session_id: str) -> PracticeSession | None:
        return self.db.get(PracticeSession, session_id)

    def list_recent(self, limit: int = 50) -> list[PracticeSession]:
        return list(
            self.db.execute(
                select(PracticeSession)
                .order_by(PracticeSession.started_at.desc())
                .limit(limit)
            ).scalars()
        )

    def delete(self, session_id: str) -> None:
        row = self.db.get(PracticeSession, session_id)
        if row is not None:
            self.db.delete(row)
            self.db.commit()


class WrongAnswers:
    def __init__(self, db: Session) -> None:
        self.db = db

    def upsert(self, wrong: WrongAnswer) -> WrongAnswer:
        row = self.db.execute(
            select(WrongAnswer).where(WrongAnswer.question_id == wrong.question_id)
        ).scalar_one_or_none()

        if row is None:
            self.db.add(wrong)
            row = wrong
        else:
            # 同一题再错：次数 +1，条目不动；快照以最新为准，题目被修正过时不该继续展示旧版
            row.wrong_count += 1
            row.last_wrong_at = wrong.last_wrong_at
            row.last_response = wrong.last_response
            row.question_json = wrong.question_json
            row.document_id = wrong.document_id
            row.topic_id = wrong.topic_id
        self.db.commit()
        self.db.refresh(row)
        return row

    def list(
        self,
        *,
        topic_id: str | None = None,
        document_id: str | None = None,
        question_type: str | None = None,
        days: int | None = None,
        min_wrong: int | None = None,
        sort: str = "recent",
        limit: int = 200,
    ) -> list[WrongAnswer]:
        stmt = select(WrongAnswer)
        if topic_id:
            stmt = stmt.where(WrongAnswer.topic_id == topic_id)
        if document_id:
            stmt = stmt.where(WrongAnswer.document_id == document_id)
        if days:
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
            stmt = stmt.where(WrongAnswer.last_wrong_at >= cutoff)
        if min_wrong and min_wrong > 1:
            stmt = stmt.where(WrongAnswer.wrong_count >= min_wrong)
        order = (
            WrongAnswer.wrong_count.desc()
            if sort == "count"
            else WrongAnswer.last_wrong_at.desc()
        )
        stmt = stmt.order_by(order, WrongAnswer.first_wrong_at.desc()).limit(limit)
        items = list(self.db.execute(stmt).scalars())
        if question_type:
            # 题型在 JSON 快照里，SQLite 的 JSON 查询不值得为这点数据量引入
            items = [item for item in items if item.question.type.value == question_type]
        return items

    def list_by_ids(self, ids: list[str]) -> list[WrongAnswer]:
        if not ids:
            return []
        rows = self.db.execute(select(WrongAnswer).where(WrongAnswer.id.in_(ids))).scalars()
        return list(rows)

    def get(self, wrong_id: str) -> WrongAnswer | None:
        return self.db.get(WrongAnswer, wrong_id)

    def delete(self, wrong_id: str) -> bool:
        row = self.db.get(WrongAnswer, wrong_id)
        if row is None:
            return False
        self.db.delete(row)
        self.db.commit()
        return True

    def count(self) -> int:
        return int(self.db.execute(select(func.count()).select_from(WrongAnswer)).scalar_one())


class Plans:
    def __init__(self, db: Session) -> None:
        self.db = db

    def save(self, plan: StudyPlan) -> StudyPlan:
        self.db.add(plan)
        self.db.commit()
        self.db.refresh(plan)
        return plan

    def list(self, limit: int = 20) -> list[StudyPlan]:
        return list(
            self.db.execute(
                select(StudyPlan)
                .order_by(StudyPlan.created_at.desc())
                .limit(max(1, min(limit, 100)))
            ).scalars()
        )

    def get(self, plan_id: str) -> StudyPlan | None:
        return self.db.get(StudyPlan, plan_id)

    def update_meta(self, plan_id: str, extra: dict) -> None:
        """补充过程字段（耗时、步数）。

        计划正文落库发生在循环内部，那一刻还不知道总耗时；
        事后补写比在保存时编一个数更诚实。
        """
        row = self.db.get(StudyPlan, plan_id)
        if row is None:
            return
        row.meta = {**(row.meta or {}), **extra}
        self.db.commit()

    def delete(self, plan_id: str) -> bool:
        row = self.db.get(StudyPlan, plan_id)
        if row is None:
            return False
        self.db.delete(row)
        self.db.commit()
        return True
