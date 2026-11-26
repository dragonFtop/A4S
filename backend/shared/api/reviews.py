"""小组投稿审核接口（P1.5）。

全部 owner 专用：
- GET  /spaces/{space_id}/reviews/pending        列出待审资料
- GET  /documents/{document_id}/preview           预览原文（按需解析，审核前未存解析文本）
- POST /documents/{document_id}/approve            批准 → 触发向量化（run_ingest）
- POST /documents/{document_id}/reject             驳回 → 置 rejected，理由存入 error 字段（可查）
- GET  /spaces/{space_id}/reviews/pending-notes   列出待审笔记（含作者归属）
- POST /notes/{note_id}/approve                    批准 → 置 confirmed，进入小组库
- POST /notes/{note_id}/reject                     驳回 → 置 rejected，不进入小组库

非 owner 由依赖 require_path_owner / require_space_owner 拦截为 403。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from shared.api.deps import require_path_owner, require_space_owner
from shared.api.documents import _out, _topic_map
from shared.api.notes import _brief, _full, _topics
from shared.domain.models.entities import Document, DocumentStatus, Note, NoteStatus, Space
from shared.infrastructure.db.session import get_db
from shared.repositories.notes import SqlAlchemyNoteRepository
from shared.repositories.sqlalchemy_repo import SqlAlchemyDocumentRepository
from shared.schemas.common import DocumentOut
from shared.schemas.notes import NoteBriefOut, NoteOut
from shared.services.ingest_service import DATA_DIR, IngestError, resolve_parser, run_ingest

router = APIRouter(tags=["reviews"])


class RejectIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class PreviewOut(BaseModel):
    text: str
    chars: int


def _get_pending_doc(db: Session, document_id: str, space_id: str) -> Document:
    doc = SqlAlchemyDocumentRepository(db).get(document_id, space_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="资料不存在")
    if doc.status is not DocumentStatus.PENDING_REVIEW:
        raise HTTPException(status_code=400, detail="该资料不在待审状态")
    return doc


@router.get("/spaces/{space_id}/reviews/pending", response_model=list[DocumentOut])
def list_pending_reviews(
    space_id: str,
    space: Space = Depends(require_path_owner),
    db: Session = Depends(get_db),
) -> list[DocumentOut]:
    """列出本空间所有待审资料（owner 专用）。"""
    docs = SqlAlchemyDocumentRepository(db).list(
        space.id, status=DocumentStatus.PENDING_REVIEW, limit=500
    )
    topics = _topic_map(db, space.id)
    return [_out(doc, topics) for doc in docs]


@router.get("/documents/{document_id}/preview", response_model=PreviewOut)
def preview_document(
    document_id: str,
    space: Space = Depends(require_space_owner),
    db: Session = Depends(get_db),
) -> PreviewOut:
    """预览待审资料的解析文本（审核前不预解析，这里按需解析原始文件）。"""
    doc = _get_pending_doc(db, document_id, space.id)
    if not doc.storage_path:
        raise HTTPException(status_code=400, detail="缺少原始文件，无法预览")
    path = Path(DATA_DIR) / doc.storage_path
    if not path.exists():
        raise HTTPException(status_code=400, detail="原始文件已丢失")
    try:
        parsed = resolve_parser(doc.ext).parse(path)
    except IngestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    text = parsed.text
    return PreviewOut(text=text, chars=len(text))


@router.post("/documents/{document_id}/approve", response_model=DocumentOut)
def approve_document(
    document_id: str,
    background: BackgroundTasks,
    space: Space = Depends(require_space_owner),
    db: Session = Depends(get_db),
) -> DocumentOut:
    """批准：置 processing 并调度 run_ingest（切分/向量化/FTS），完成后变 ready。"""
    doc = _get_pending_doc(db, document_id, space.id)
    repo = SqlAlchemyDocumentRepository(db)
    repo.update_status(document_id, DocumentStatus.PROCESSING, space_id=space.id)
    db.commit()
    background.add_task(run_ingest, document_id)
    topics = _topic_map(db, space.id)
    return _out(repo.get(document_id, space.id) or doc, topics)


@router.post("/documents/{document_id}/reject", response_model=DocumentOut)
def reject_document(
    document_id: str,
    payload: RejectIn,
    space: Space = Depends(require_space_owner),
    db: Session = Depends(get_db),
) -> DocumentOut:
    """驳回：置 rejected，理由写入 error 字段（上传者可在资料列表查看到状态与理由）。"""
    doc = _get_pending_doc(db, document_id, space.id)
    repo = SqlAlchemyDocumentRepository(db)
    repo.update_status(
        document_id, DocumentStatus.REJECTED, space_id=space.id, error=payload.reason
    )
    db.commit()
    topics = _topic_map(db, space.id)
    return _out(repo.get(document_id, space.id) or doc, topics)


# ---------- 笔记审核 ----------


def _get_pending_note(db: Session, note_id: str, space_id: str) -> Note:
    note = SqlAlchemyNoteRepository(db).get(note_id, space_id)
    if note is None:
        raise HTTPException(status_code=404, detail="笔记不存在")
    if note.status is not NoteStatus.PENDING_REVIEW:
        raise HTTPException(status_code=400, detail="该笔记不在待审状态")
    return note


def _transition_note(db: Session, note_id: str, space_id: str, status: NoteStatus) -> Note:
    note = _get_pending_note(db, note_id, space_id)
    note.status = status
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    note.updated_at = now
    if status is NoteStatus.CONFIRMED:
        note.confirmed_at = now
    SqlAlchemyNoteRepository(db).update(note)
    return SqlAlchemyNoteRepository(db).get(note_id, space_id) or note


@router.get("/spaces/{space_id}/reviews/pending-notes", response_model=list[NoteBriefOut])
def list_pending_notes(
    space_id: str,
    space: Space = Depends(require_path_owner),
    db: Session = Depends(get_db),
) -> list[NoteBriefOut]:
    """列出本空间所有待审笔记（owner 专用），投影含作者归属。"""
    notes = SqlAlchemyNoteRepository(db).list(
        space.id, status=NoteStatus.PENDING_REVIEW.value, limit=500
    )
    topics = _topics(db, space.id)
    return [_brief(note, topics) for note in notes]


@router.post("/notes/{note_id}/approve", response_model=NoteOut)
def approve_note(
    note_id: str,
    space: Space = Depends(require_space_owner),
    db: Session = Depends(get_db),
) -> NoteOut:
    """批准笔记：置 confirmed，进入小组库；作者归属（user_id）保留。"""
    note = _transition_note(db, note_id, space.id, NoteStatus.CONFIRMED)
    return _full(note, _topics(db, space.id))


@router.post("/notes/{note_id}/reject", response_model=NoteOut)
def reject_note(
    note_id: str,
    space: Space = Depends(require_space_owner),
    db: Session = Depends(get_db),
) -> NoteOut:
    """驳回笔记：置 rejected，不进入小组库（默认列表与搜索均排除）。"""
    note = _transition_note(db, note_id, space.id, NoteStatus.REJECTED)
    return _full(note, _topics(db, space.id))
