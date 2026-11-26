"""资料接口（FR-01 / FR-02）。

上传后立即返回 pending 状态的文档，解析与向量化在后台任务执行；
前端轮询列表即可看到 processing → ready / failed。
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from shared.api.deps import get_current_space
from shared.domain.models.entities import (
    UNCATEGORIZED_ID,
    Document,
    DocumentStatus,
    MemberRole,
    Space,
    SpaceKind,
    TopicKind,
)
from shared.infrastructure.db.session import get_db
from shared.repositories.sqlalchemy_repo import (
    SqlAlchemyChunkRepository,
    SqlAlchemyDocumentRepository,
    SqlAlchemyTopicRepository,
)
from shared.schemas.common import DocumentOut, TopicBrief
from shared.schemas.documents import (
    BatchIn,
    ChunkOut,
    DocumentStatsOut,
    PasteIn,
    UploadError,
    UploadOut,
    WebFetchIn,
    WebFetchOut,
)
import shared.services.ingest_service as ingest_service
import shared.services.webfetch_service as webfetch_service
from shared.services.ingest_service import IngestError
from shared.services.webfetch_service import WebImportError

router = APIRouter(prefix="/documents", tags=["documents"])


def _get_doc_repo(db: Session = Depends(get_db)) -> SqlAlchemyDocumentRepository:
    return SqlAlchemyDocumentRepository(db)


def _topic_map(db: Session, space_id: str) -> dict[str, TopicBrief]:
    repo = SqlAlchemyTopicRepository(db)
    return {
        t.id: TopicBrief(id=t.id, name=t.name, color=t.color)
        for t in repo.list(space_id)
    }


def _out(doc: Document, topics: dict[str, TopicBrief]) -> DocumentOut:
    topic = topics.get(doc.topic_id) or TopicBrief(id=doc.topic_id, name="未分类", color="#888780")
    return DocumentOut(
        id=doc.id,
        name=doc.name,
        ext=doc.ext,
        size=doc.size,
        status=doc.status.value,
        chunk_count=doc.chunk_count,
        error=doc.error,
        created_at=doc.created_at,
        ready_at=doc.ready_at,
        topic=topic,
        tags=[topics[t] for t in doc.tag_ids if t in topics],
    )


def _parse_tag_ids(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _needs_review(space: Space) -> bool:
    """小组空间里非 owner 的投稿先进入待审（不切分、不向量化、不进 FTS）；
    个人空间与 owner 自传直接走正常入库。viewer 已在空间依赖里被写保护拦截。"""
    return space.kind is SpaceKind.GROUP and space.my_role is not MemberRole.OWNER


@router.get("", response_model=list[DocumentOut])
def list_documents(
    limit: int = 100,
    space: Space = Depends(get_current_space),
    offset: int = 0,
    topic_ids: str | None = None,
    tag_ids: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
    db: Session = Depends(get_db),
) -> list[DocumentOut]:
    docs = SqlAlchemyDocumentRepository(db).list(
        space.id,
        limit=limit,
        offset=offset,
        topic_ids=_parse_tag_ids(topic_ids) if topic_ids else None,
        tag_ids=_parse_tag_ids(tag_ids) if tag_ids else None,
        status=DocumentStatus(status) if status else None,
        keyword=keyword or None,
    )
    topics = _topic_map(db, space.id)
    return [_out(doc, topics) for doc in docs]


@router.get("/stats", response_model=DocumentStatsOut)
def document_stats(
    space: Space = Depends(get_current_space), db: Session = Depends(get_db)
) -> DocumentStatsOut:
    repo = SqlAlchemyDocumentRepository(db)
    total = repo.count(space.id)
    ready = repo.count(space.id, status=DocumentStatus.READY)
    failed = repo.count(space.id, status=DocumentStatus.FAILED)
    pending = repo.count(space.id, status=DocumentStatus.PENDING) + repo.count(
        space.id,
        status=DocumentStatus.PROCESSING
    )
    return DocumentStatsOut(
        total=total,
        ready=ready,
        processing=pending,
        failed=failed,
        chunks=SqlAlchemyChunkRepository(db).count(space.id),
    )


@router.post("/upload", response_model=UploadOut, status_code=201)
async def upload_documents(
    background: BackgroundTasks,
    space: Space = Depends(get_current_space),
    files: list[UploadFile] = File(...),
    topic_id: str = Form(UNCATEGORIZED_ID),
    tag_ids: str = Form(""),
    db: Session = Depends(get_db),
) -> UploadOut:
    if not ingest_service.topic_exists(db, topic_id, space.id):
        raise HTTPException(status_code=400, detail="主题不存在，请先创建或选择「未分类」")

    tags = _parse_tag_ids(tag_ids)
    topics = _topic_map(db, space.id)
    created: list[DocumentOut] = []
    errors: list[UploadError] = []
    needs_review = _needs_review(space)
    initial = DocumentStatus.PENDING_REVIEW if needs_review else DocumentStatus.PENDING

    for upload in files:
        name = upload.filename or "未命名文件"
        try:
            content = await upload.read()
            doc = ingest_service.create_from_upload(
                db,
                space_id=space.id, filename=name, content=content, topic_id=topic_id, tag_ids=tags,
                initial_status=initial,
            )
        except IngestError as exc:
            errors.append(UploadError(name=name, reason=str(exc)))
            continue
        except Exception as exc:  # 单个文件失败不应中断整批
            errors.append(UploadError(name=name, reason=f"保存失败：{exc}"))
            continue
        created.append(_out(doc, topics))
        # 待审资料不调度入库：审核前不切分、不向量化、不进 FTS
        if not needs_review:
            background.add_task(ingest_service.run_ingest, doc.id)

    return UploadOut(documents=created, errors=errors)


@router.post("/webfetch", response_model=WebFetchOut)
def webfetch_document(payload: WebFetchIn) -> WebFetchOut:
    """抓取网页转 Markdown，只做预览不入库；确认后走 /paste。"""
    try:
        preview = webfetch_service.fetch_for_preview(payload.url)
    except WebImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WebFetchOut(**preview)


@router.post("/paste", response_model=DocumentOut, status_code=201)
def paste_document(
    payload: PasteIn,
    background: BackgroundTasks,
    space: Space = Depends(get_current_space),
    db: Session = Depends(get_db),
) -> DocumentOut:
    if not ingest_service.topic_exists(db, payload.topic_id, space.id):
        raise HTTPException(status_code=400, detail="主题不存在，请先创建或选择「未分类」")
    needs_review = _needs_review(space)
    try:
        doc = ingest_service.create_from_text(
            db,
            space_id=space.id,
            content=payload.content,
            title=payload.title,
            topic_id=payload.topic_id,
            tag_ids=payload.tag_ids,
            initial_status=DocumentStatus.PENDING_REVIEW if needs_review else DocumentStatus.PENDING,
        )
    except IngestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 待审资料不调度入库：审核前不切分、不向量化、不进 FTS
    if not needs_review:
        background.add_task(ingest_service.run_ingest, doc.id)
    return _out(doc, _topic_map(db, space.id))


@router.post("/batch")
def batch_update(
    payload: BatchIn,
    space: Space = Depends(get_current_space),
    db: Session = Depends(get_db),
) -> dict:
    """批量设主题 / 加标签 / 去标签。"""
    repo = SqlAlchemyDocumentRepository(db)
    tag_repo = SqlAlchemyTopicRepository(db)

    valid_tags = {t.id for t in tag_repo.list(TopicKind.TAG)}
    touched = 0
    for document_id in payload.document_ids:
        doc = repo.get(document_id)
        if doc is None:
            continue
        if payload.topic_id and payload.topic_id != doc.topic_id:
            ingest_service.move_document_topic(db, document_id, payload.topic_id, space.id)
        if payload.add_tag_ids or payload.remove_tag_ids:
            doc = repo.get(document_id) or doc
            merged = [
                t for t in doc.tag_ids if t not in payload.remove_tag_ids
            ]
            merged.extend(t for t in payload.add_tag_ids if t in valid_tags and t not in merged)
            repo.set_tags(document_id, merged)
        touched += 1
    return {"updated": touched}


@router.get("/{document_id}/chunks", response_model=list[ChunkOut])
def list_chunks(
    document_id: str,
    space: Space = Depends(get_current_space),
    db: Session = Depends(get_db),
) -> list[ChunkOut]:
    doc = SqlAlchemyDocumentRepository(db).get(document_id, space.id)
    if doc is None:
        raise HTTPException(status_code=404, detail="资料不存在")
    chunks = SqlAlchemyChunkRepository(db).list_by_document(document_id)
    return [
        ChunkOut(id=c.id, index=c.index, text=c.text, page=c.page)
        for c in chunks
    ]


@router.post("/{document_id}/reingest", response_model=DocumentOut)
def reingest_document(
    document_id: str,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
) -> DocumentOut:
    doc = SqlAlchemyDocumentRepository(db).get(document_id, space.id)
    if doc is None:
        raise HTTPException(status_code=404, detail="资料不存在")
    SqlAlchemyDocumentRepository(db).update_status(
        document_id, DocumentStatus.PENDING, space_id=space.id
    )
    background.add_task(ingest_service.run_ingest, document_id)
    return _out(
        SqlAlchemyDocumentRepository(db).get(document_id, space.id) or doc,
        _topic_map(db, space.id),
    )


@router.delete("/{document_id}")
def delete_document(
    document_id: str,
    space: Space = Depends(get_current_space),
    db: Session = Depends(get_db),
) -> dict:
    doc = SqlAlchemyDocumentRepository(db).get(document_id, space.id)
    if doc is None:
        raise HTTPException(status_code=404, detail="资料不存在")
    ingest_service.delete_document(db, document_id, space.id)
    return {"deleted": document_id}
