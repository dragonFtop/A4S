"""资料入库流水线：解析 → 切分 → 向量化 → 写入关系库与向量库。

设计取舍：
- 粘贴文本也落成 .txt 文件。这样解析、重跑、删除、溯源只有一条路径，
  不用为"无文件"的资料写分支。
- 解析与向量化都在后台任务里同步跑（单机场景够用），状态机保证前端可见进度。
- 每次处理前先清空旧片段与旧向量，因此"重新处理"是幂等的。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.config import DATA_DIR
from app.domain.models.entities import (
    UNCATEGORIZED_ID,
    Chunk,
    Document,
    DocumentStatus,
)
from app.infrastructure.db.session import SessionLocal, get_vector_store
from app.infrastructure.llm.bge_local import build_embedding
from app.infrastructure.parsers.base import DocumentParser
from app.infrastructure.parsers.builtin import PARSERS
from app.infrastructure.splitters.text_splitter import SplitConfig, split_document
from app.repositories.sqlalchemy_repo import (
    SqlAlchemyChunkRepository,
    SqlAlchemyDocumentRepository,
    SqlAlchemyTopicRepository,
)

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 100_000
ALLOWED_EXTS = {"pdf", "docx", "md", "markdown", "txt"}
EMBED_BATCH = 32


class IngestError(ValueError):
    """可展示给用户的入库错误（文案直接进 UI）。"""


def resolve_parser(ext: str) -> DocumentParser:
    """按扩展名挑解析器。"""
    normalized = ext.lower().lstrip(".")
    for parser in PARSERS:
        if parser.supports(normalized):
            return parser
    raise IngestError(f"暂不支持 .{normalized} 格式（可导入 PDF / DOCX / MD / TXT）")


def _upload_rel_path(document_id: str, ext: str) -> str:
    now = datetime.now(timezone.utc)
    return f"uploads/{now:%Y/%m}/{document_id}.{ext}"


def _write_file(rel_path: str, content: bytes) -> Path:
    path = Path(DATA_DIR) / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _guess_title(text: str) -> str:
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:60]
    return "未命名资料"


# ---------- 创建 ----------


def create_from_upload(
    db,
    *,
    space_id: str,
    filename: str,
    content: bytes,
    topic_id: str = UNCATEGORIZED_ID,
    tag_ids: list[str] | None = None,
) -> Document:
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in ALLOWED_EXTS:
        raise IngestError(f"暂不支持 .{ext or '未知'} 格式（可导入 PDF / DOCX / MD / TXT）")
    if len(content) == 0:
        raise IngestError("文件为空")
    if len(content) > MAX_FILE_BYTES:
        raise IngestError(f"文件超过 20 MB 上限（当前 {len(content) / 1024 / 1024:.1f} MB）")

    document_id = str(uuid.uuid4())
    rel_path = _upload_rel_path(document_id, ext)
    _write_file(rel_path, content)

    doc = Document(
        id=document_id,
        name=filename,
        ext=ext,
        size=len(content),
        status=DocumentStatus.PENDING,
        topic_id=topic_id or UNCATEGORIZED_ID,
        tag_ids=tag_ids or [],
        storage_path=rel_path,
        space_id=space_id,
    )
    SqlAlchemyDocumentRepository(db).add(doc)
    return doc


def create_from_text(
    db,
    *,
    space_id: str,
    content: str,
    title: str = "",
    topic_id: str = UNCATEGORIZED_ID,
    tag_ids: list[str] | None = None,
) -> Document:
    body = content.strip()
    if not body:
        raise IngestError("正文为空")
    if len(body) > MAX_TEXT_CHARS:
        raise IngestError(f"正文超过 {MAX_TEXT_CHARS:,} 字符上限（当前 {len(body):,}）")

    document_id = str(uuid.uuid4())
    rel_path = _upload_rel_path(document_id, "txt")
    payload = body.encode("utf-8")
    _write_file(rel_path, payload)

    doc = Document(
        id=document_id,
        name=(title.strip() or _guess_title(body))[:255],
        ext="txt",
        size=len(payload),
        status=DocumentStatus.PENDING,
        topic_id=topic_id or UNCATEGORIZED_ID,
        tag_ids=tag_ids or [],
        storage_path=rel_path,
        space_id=space_id,
    )
    SqlAlchemyDocumentRepository(db).add(doc)
    return doc


# ---------- 处理 ----------


def run_ingest(document_id: str) -> None:
    """后台任务入口。自己开 Session，避免跨线程复用请求的会话。"""
    db = SessionLocal()
    try:
        # 这是后台任务：只拿得到内部 id，先把记录读出来确认它属于哪个空间，
        # 之后所有查询都在那个空间里进行（而不是"不限空间"）。
        from app.infrastructure.db.models import DocumentORM

        row = db.get(DocumentORM, document_id)
        if row is None:
            logger.warning("待处理资料不存在：%s", document_id)
            return
        space_id = row.space_id

        docs = SqlAlchemyDocumentRepository(db)
        doc = docs.get(document_id, space_id)
        if doc is None:
            logger.warning("待处理资料不属于任何空间：%s", document_id)
            return

        docs.update_status(document_id, DocumentStatus.PROCESSING, space_id=space_id)
        try:
            count = _process(db, doc)
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            logger.exception("资料处理失败：%s（%s）", doc.name, document_id)
            docs.update_status(
                document_id, DocumentStatus.FAILED, chunk_count=0, error=message[:500],
                space_id=space_id,
            )
            return
        docs.update_status(document_id, DocumentStatus.READY, chunk_count=count, space_id=space_id)
        logger.info("资料处理完成：%s，%s 个片段", doc.name, count)
    finally:
        db.close()


def _process(db, doc: Document) -> int:
    if not doc.storage_path:
        raise IngestError("缺少原始文件路径，无法处理")
    path = Path(DATA_DIR) / doc.storage_path
    if not path.exists():
        raise IngestError("原始文件已丢失，请重新上传")

    parser = resolve_parser(doc.ext)
    parsed = parser.parse(path)

    slices = split_document(parsed.pages, parsed.text, SplitConfig())
    if not slices:
        raise IngestError("未能从资料中切分出有效内容")

    texts = [item.text for item in slices]
    vectors = _embed_batched(texts)

    store = get_vector_store()
    if not store.is_compatible():
        raise IngestError(
            f"向量索引维度为 {store.index_dimension()}，当前配置为 {store.dimension}，"
            "请先在设置中重建索引"
        )

    chunks = [
        Chunk(
            id=str(uuid.uuid4()),
            document_id=doc.id,
            index=item.order,
            text=item.text,
            source_name=doc.name,
            page=item.page,
            topic_id=doc.topic_id,
            space_id=doc.space_id,
        )
        for item in slices
    ]

    # 幂等：先清掉上一次可能残留的片段与向量
    chunk_repo = SqlAlchemyChunkRepository(db)
    old_ids = [c.id for c in chunk_repo.list_by_document(doc.id)]
    if old_ids:
        store.delete_chunks(old_ids)
        chunk_repo.delete_by_document(doc.id)

    chunk_repo.add_many(chunks)
    store.add([(chunk.id, vector, chunk.topic_id) for chunk, vector in zip(chunks, vectors)])

    # 全文索引跟着片段一起写：这是问答第二路召回的数据来源。
    # 与向量索引不同，它不依赖模型，失败也不会让入库失败（下次重建可补）。
    from app.infrastructure.db import fts
    from app.infrastructure.db.session import FTS_ENABLED

    if FTS_ENABLED:
        try:
            fts.upsert_chunks(db.connection(), [(c.id, c.text) for c in chunks])
            db.commit()
        except Exception as exc:  # noqa: BLE001 - 索引失败不该拖垮入库
            logger.warning("片段全文索引写入失败（可从设置页重建）：%s", exc)

    return len(chunks)


def _embed_batched(texts: list[str]) -> list[list[float]]:
    embedder = build_embedding()
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        batch = texts[start : start + EMBED_BATCH]
        vectors.extend(embedder.embed(batch))
    if len(vectors) != len(texts):
        raise IngestError("向量化结果数量与片段数不一致，请重试")
    return vectors


# ---------- 删除 ----------


def delete_document(db, document_id: str, space_id: str) -> None:
    """删资料：向量 → 片段 → 关联 → 文档 → 原始文件。顺序不能反。"""
    docs = SqlAlchemyDocumentRepository(db)
    doc = docs.get(document_id, space_id)
    if doc is None:
        return

    chunk_repo = SqlAlchemyChunkRepository(db)
    chunk_ids = [c.id for c in chunk_repo.list_by_document(document_id)]
    if chunk_ids:
        get_vector_store().delete_chunks(chunk_ids)
        from app.infrastructure.db import fts
        from app.infrastructure.db.session import FTS_ENABLED

        if FTS_ENABLED:
            try:
                fts.remove_chunks(db.connection(), chunk_ids)
                db.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("片段全文索引删除失败：%s", exc)

    docs.delete(document_id, space_id)  # 仓储内部级联删 chunks 与 document_tags

    if doc.storage_path:
        path = Path(DATA_DIR) / doc.storage_path
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("删除原始文件失败（忽略）：%s", exc)


# ---------- 主题变更 ----------


def move_document_topic(db, document_id: str, topic_id: str, space_id: str) -> None:
    """改归属主题。向量分区键必须同步，否则按主题检索会漏掉这份资料。"""
    SqlAlchemyDocumentRepository(db).set_topic(document_id, topic_id, space_id)
    chunk_ids = [c.id for c in SqlAlchemyChunkRepository(db).list_by_document(document_id)]
    # vec0 不能原地改分区键，但可原样搬移向量，无需重新 embedding
    get_vector_store().repartition(chunk_ids, topic_id)


def topic_exists(db, topic_id: str, space_id: str) -> bool:
    return SqlAlchemyTopicRepository(db).get(topic_id, space_id) is not None
