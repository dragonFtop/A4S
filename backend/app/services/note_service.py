"""电子笔记业务（FR-13）。

三条不能省的规则
----------------
1. **两阶段**：AI 生成的是 `draft`，人工点「确认定稿」才变 `confirmed`。
   定稿后任何内容改动都会退回 `draft`——否则"定稿"就成了没有意义的标签。
2. **可溯源**：正文里的 `[[chunk:id]]` 必须能在 `note_sources` 找到对应片段。
   模型编出来的 id 一律剔除（宁可少一个角标，也不能给假出处）。
3. **AI 补写要留痕**：没有来源的段落打上 `<!--ai-->`，前端据此高亮并汇总条数，
   让人一眼看出哪几句是模型的推断而不是资料原文。
"""

from __future__ import annotations

import logging
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from app.domain.models.entities import (
    Note,
    NoteSource,
    NoteSourceType,
    NoteStatus,
    NoteTemplate,
)
from app.infrastructure.db.session import FTS_ENABLED, SessionLocal
from app.infrastructure.llm.openai_compat import build_llm
from app.repositories.sqlalchemy_repo import (
    SqlAlchemyChunkRepository,
    SqlAlchemyDocumentRepository,
    SqlAlchemyNoteRepository,
    SqlAlchemyTopicRepository,
)
from app.services import note_prompts

logger = logging.getLogger(__name__)

MAX_CONTEXT_CHARS = 14_000
MAX_MATERIAL_CHUNKS = 24
MAX_CHUNKS_PER_DOC = 6
SNIPPET_CHARS = 240
MARKDOWN_LIMIT = 200_000

_MARK = re.compile(r"\[\[chunk:([A-Za-z0-9\-]+)\]\]")


class NoteError(ValueError):
    """笔记操作的前置条件不满足（范围为空、资料不足等）。"""


class NoteNotFound(NoteError):
    pass


# ---------- CRUD ----------


def create(
    db,
    *,
    space_id: str,
    title: str,
    topic_id: str,
    tag_ids: list[str] | None = None,
    markdown: str = "",
    template: NoteTemplate | None = None,
    source_type: NoteSourceType = NoteSourceType.MANUAL,
    ai_model: str | None = None,
) -> Note:
    _assert_topic(db, topic_id, space_id)
    repo = SqlAlchemyNoteRepository(db)
    resolved, sources = _resolve_cites(db, markdown, space_id) if markdown else (markdown, [])
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    note = Note(
        id=str(uuid.uuid4()),
        title=(title or "未命名笔记").strip()[:200],
        markdown=resolved,
        topic_id=topic_id,
        status=NoteStatus.DRAFT,
        source_type=source_type,
        template=template,
        ai_model=ai_model,
        tag_ids=_valid_tags(db, tag_ids, space_id),
        sources=sources,
        created_at=now,
        updated_at=now,
        space_id=space_id,
    )
    repo.add(note)
    # 回读一次：来源的文档名是 join 出来的，内存对象上没有
    return repo.get(note.id, space_id) or note


def get(db, note_id: str, space_id: str) -> Note:
    note = SqlAlchemyNoteRepository(db).get(note_id, space_id)
    if note is None:
        raise NoteNotFound("笔记不存在或已删除")
    return note


def list_notes(
    db,
    *,
    space_id: str,
    topic_ids: list[str] | None = None,
    tag_ids: list[str] | None = None,
    status: str | None = None,
    keyword: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Note]:
    return SqlAlchemyNoteRepository(db).list(
        space_id,
        limit=limit,
        offset=offset,
        topic_ids=topic_ids,
        tag_ids=tag_ids,
        status=status,
        keyword=(keyword or "").strip() or None,
    )


def update(
    db,
    note_id: str,
    *,
    space_id: str,
    title: str | None = None,
    markdown: str | None = None,
    topic_id: str | None = None,
    tag_ids: list[str] | None = None,
    template: NoteTemplate | None = None,
    sources: list[NoteSource] | None = None,
) -> Note:
    repo = SqlAlchemyNoteRepository(db)
    note = repo.get(note_id, space_id)
    if note is None:
        raise NoteNotFound("笔记不存在或已删除")

    content_changed = False
    derived: list[NoteSource] | None = None
    if title is not None and title.strip() != note.title:
        note.title = title.strip()[:200] or note.title
        content_changed = True
    if markdown is not None and markdown != note.markdown:
        if len(markdown) > MARKDOWN_LIMIT:
            raise NoteError(f"正文超出 {MARKDOWN_LIMIT} 字符上限")
        # 正文一改，角标与来源表就可能对不上：以正文为准重算，双向校验才守得住
        note.markdown, derived = _resolve_cites(db, markdown, space_id)
        content_changed = True
    if topic_id is not None and topic_id != note.topic_id:
        _assert_topic(db, topic_id, space_id)
        note.topic_id = topic_id
    if tag_ids is not None:
        note.tag_ids = _valid_tags(db, tag_ids, space_id)
    if template is not None:
        note.template = template
    if sources is not None:
        note.sources = sources
    elif derived is not None:
        note.sources = derived

    if content_changed:
        note.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        # 定稿后一改就不再是"确认过的内容"，必须重新确认
        if note.status is NoteStatus.CONFIRMED:
            note.status = NoteStatus.DRAFT
            note.confirmed_at = None
    repo.update(note)
    # 同 create：来源的文档名靠 join 补全，得回读才拿得到
    return repo.get(note.id, space_id) or note


def confirm(db, note_id: str, space_id: str) -> Note:
    repo = SqlAlchemyNoteRepository(db)
    note = repo.get(note_id, space_id)
    if note is None:
        raise NoteNotFound("笔记不存在或已删除")
    if not note.markdown.strip():
        raise NoteError("空白笔记没有可确认的内容")
    if note.status is not NoteStatus.CONFIRMED:
        note.status = NoteStatus.CONFIRMED
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        note.confirmed_at = now
        note.updated_at = now
        repo.update(note)
    return note


def delete(db, note_id: str, space_id: str) -> None:
    """删笔记不动来源资料——来源是只读引用，不是子资源。"""
    SqlAlchemyNoteRepository(db).delete(note_id, space_id)


# ---------- 校验辅助 ----------


def _assert_topic(db, topic_id: str, space_id: str) -> None:
    from app.domain.models.entities import TopicKind

    repo = SqlAlchemyTopicRepository(db)
    topic = repo.get(topic_id, space_id)
    if topic is None or topic.kind is not TopicKind.TOPIC:
        raise NoteError("主题不存在，请先创建主题再保存笔记")


def _resolve_cites(db, markdown: str, space_id: str) -> tuple[str, list[NoteSource]]:
    """把正文里的 `[[chunk:id]]` 对齐到真实片段（手工创建/编辑走这条）。

    与起草路径的差别：起草时只信 `allowed` 里的 id，这里没有"给定范围"，
    就以片段是否真实存在为准——存在的补进来源表，不存在的从正文剔除，
    免得留下点了没反应的死角标。
    """
    ids: list[str] = []
    for match in _MARK.finditer(markdown):
        chunk_id = match.group(1)
        if chunk_id not in ids:
            ids.append(chunk_id)
    if not ids:
        return markdown, []

    repo = SqlAlchemyChunkRepository(db)
    found: dict[str, Any] = {}
    for chunk_id in ids:
        chunk = repo.get(chunk_id, space_id)
        if chunk is not None:
            found[chunk_id] = chunk

    dropped = {chunk_id for chunk_id in ids if chunk_id not in found}
    if dropped:
        markdown = _MARK.sub(
            lambda m: "" if m.group(1) in dropped else m.group(0), markdown
        )

    sources = [
        NoteSource(
            chunk_id=chunk_id,
            snippet=(found[chunk_id].text or "").strip()[:SNIPPET_CHARS],
            document_id=found[chunk_id].document_id,
        )
        for chunk_id in ids
        if chunk_id in found
    ]
    return markdown, sources


def _valid_tags(db, tag_ids: list[str] | None, space_id: str) -> list[str]:
    from app.domain.models.entities import TopicKind

    if not tag_ids:
        return []
    repo = SqlAlchemyTopicRepository(db)
    out: list[str] = []
    for tag_id in dict.fromkeys(tag_ids):
        tag = repo.get(tag_id, space_id)
        if tag is not None and tag.kind is TopicKind.TAG:
            out.append(tag_id)
    return out


# ---------- 起草任务 ----------

_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}
_JOB_KEEP = 20  # 只留最近的任务，避免进程内字典无限长


def start_draft(
    db,
    *,
    space_id: str,
    template: NoteTemplate,
    document_ids: list[str] | None = None,
    topic_id: str | None = None,
    title: str = "",
    note_topic_id: str | None = None,
    tag_ids: list[str] | None = None,
    note_id: str | None = None,
) -> dict[str, Any]:
    """校验范围与资料，登记任务并返回公开快照。真正的生成在 `run_draft`。"""
    from app.config import settings

    if not settings.llm_configured:
        raise NoteError("尚未配置大模型 API Key，请先在设置页填写")

    materials, allowed = _collect_materials(
        db, space_id=space_id, document_ids=document_ids, topic_id=topic_id
    )
    if not allowed:
        raise NoteError("选定范围内没有可用的资料片段，请先导入并等待索引完成")

    target_topic = note_topic_id or topic_id or "uncategorized"
    _assert_topic(db, target_topic, space_id)

    if note_id:
        existing = SqlAlchemyNoteRepository(db).get(note_id, space_id)
        if existing is None:
            raise NoteNotFound("要重新整理的笔记不存在")

    job_id = str(uuid.uuid4())
    job = {
        "id": job_id,
        "state": "running",
        "note_id": note_id,
        "message": "正在整理资料…",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        # 以下为内部载荷，不进接口快照
        "_template": template,
        "_materials": materials,
        "_allowed": allowed,
        "_title": title,
        "_topic_id": target_topic,
        "_tag_ids": tag_ids or [],
        "_space_id": space_id,
    }
    with _LOCK:
        _JOBS[job_id] = job
        _trim_jobs()
    return _public(job)


def job_snapshot(job_id: str) -> dict[str, Any]:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            raise NoteNotFound("起草任务不存在或已过期")
        return _public(job)


def _trim_jobs() -> None:
    if len(_JOBS) <= _JOB_KEEP:
        return
    for key in list(_JOBS)[: len(_JOBS) - _JOB_KEEP]:
        _JOBS.pop(key, None)


def _public(job: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in job.items() if not key.startswith("_")}


async def run_draft(job_id: str) -> None:
    """后台执行起草。自己开 Session；LLM 是异步的，所以整条链路 async。"""
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        payload = dict(job)

    db = SessionLocal()
    try:
        from app.config import settings

        space_id = payload["_space_id"]
        llm = build_llm()
        messages = note_prompts.build_messages(
            payload["_template"],
            payload["_materials"],
            topic_name=_topic_name(db, payload["_topic_id"], space_id),
        )
        raw = await llm.chat(messages, temperature=0.3)
        markdown = _assemble(raw, payload["_allowed"])

        template = payload["_template"]
        if payload["note_id"]:
            note = update(
                db,
                payload["note_id"],
                space_id=space_id,
                title=markdown_title(markdown) or payload["_title"] or None,
                markdown=markdown,
                template=template,
                sources=_sources_from(markdown, payload["_allowed"]),
            )
            note.source_type = NoteSourceType.DOCUMENT
            note.ai_model = settings.llm_model
            SqlAlchemyNoteRepository(db).update(note)
        else:
            note = create(
                db,
                space_id=space_id,
                title=markdown_title(markdown) or payload["_title"] or "未命名笔记",
                topic_id=payload["_topic_id"],
                tag_ids=payload["_tag_ids"],
                markdown=markdown,
                template=template,
                source_type=NoteSourceType.DOCUMENT,
                ai_model=settings.llm_model,
            )
            note.sources = _sources_from(markdown, payload["_allowed"])
            SqlAlchemyNoteRepository(db).update(note)

        with _LOCK:
            job.update(
                state="done",
                note_id=note.id,
                message="草稿已生成，请核对后确认定稿",
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
        logger.info("笔记起草完成：%s（模板 %s）", note.id, template.value)
    except Exception as exc:  # noqa: BLE001 - 失败原因要能显示给用户
        message = str(exc) or exc.__class__.__name__
        logger.exception("笔记起草失败")
        with _LOCK:
            job.update(
                state="failed",
                message=_explain(message),
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
    finally:
        db.close()


def _explain(message: str) -> str:
    lowered = message.lower()
    if "401" in message or "unauthorized" in lowered or "api key" in lowered:
        return "模型鉴权失败（401）：请检查设置页的 API Key"
    if "timeout" in lowered or "timed out" in lowered:
        return "模型响应超时：可将一份资料拆小后重试"
    if "429" in message:
        return "模型额度不足或请求过快（429）：请稍后重试"
    return message[:400]


def _topic_name(db, topic_id: str, space_id: str) -> str:
    topic = SqlAlchemyTopicRepository(db).get(topic_id, space_id)
    return topic.name if topic else ""


# ---------- 资料拼装 ----------


def _collect_materials(
    db, *, space_id: str, document_ids: list[str] | None, topic_id: str | None
) -> tuple[str, dict[str, dict]]:
    """挑出要喂给模型的片段，同时返回"允许出现的 chunk_id"白名单。"""
    chunk_repo = SqlAlchemyChunkRepository(db)
    doc_repo = SqlAlchemyDocumentRepository(db)

    picked: list = []
    if document_ids:
        for doc_id in dict.fromkeys(document_ids):
            doc = doc_repo.get(doc_id, space_id)
            if doc is None:
                continue
            picked.extend(chunk_repo.list_by_document(doc_id)[:MAX_CHUNKS_PER_DOC])
            if len(picked) >= MAX_MATERIAL_CHUNKS:
                break
    elif topic_id:
        picked = chunk_repo.list_by_topic(topic_id, space_id, limit=MAX_MATERIAL_CHUNKS)

    picked = picked[:MAX_MATERIAL_CHUNKS]

    blocks: list[str] = []
    allowed: dict[str, dict] = {}
    used = 0
    for chunk in picked:
        block = chunk.text.strip()
        if not block:
            continue
        if used + len(block) > MAX_CONTEXT_CHARS:
            break
        used += len(block)
        allowed[chunk.id] = {
            "document_id": chunk.document_id,
            "snippet": block[:SNIPPET_CHARS],
        }
        source = chunk.source_name or "资料"
        page = f" 第 {chunk.page} 页" if getattr(chunk, "page", None) else ""
        blocks.append(f"[chunk:{chunk.id}] 《{source}》{page}\n{block}")

    return "\n\n---\n\n".join(blocks), allowed


# ---------- 输出清洗 ----------


def _assemble(raw: str, allowed: dict[str, dict]) -> str:
    """剔除编造的来源角标，并把没有来源的内容块标记为 AI 补写。"""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:markdown|md)?\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)

    def _drop(match: re.Match[str]) -> str:
        return match.group(0) if match.group(1) in allowed else ""

    text = _MARK.sub(_drop, text)
    return _mark_ai_blocks(text)


def _mark_ai_blocks(markdown: str) -> str:
    """按块扫描：没有来源且没被模型标过的内容块，补上 AI 补写标记。"""
    out: list[str] = []
    block: list[str] = []
    in_fence = False

    def flush() -> None:
        if not block:
            return
        chunk = "\n".join(block)
        stripped = chunk.strip()
        # 纯代码块不做标记：代码的出处由它上面的要点承担，否则代码笔记会满篇"AI 补写"
        if (
            stripped
            and not stripped.startswith("#")
            and not stripped.startswith("```")
            and "[[" not in chunk
            and note_prompts.AI_MARK not in chunk
            and not re.fullmatch(r"[-|:\s]+", stripped)
        ):
            chunk = chunk.rstrip() + " " + note_prompts.AI_MARK
        out.append(chunk)
        block.clear()

    for line in markdown.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            block.append(line)
            continue
        if not in_fence and not line.strip():
            flush()
            out.append(line)
            continue
        block.append(line)
    flush()

    return "\n".join(out).strip() + "\n"


def markdown_title(markdown: str) -> str:
    for line in markdown.split("\n"):
        if line.startswith("# "):
            return line[2:].strip()[:200]
    return ""


def _sources_from(markdown: str, allowed: dict[str, dict]) -> list[NoteSource]:
    """按首次出现顺序建立来源表，前端角标序号即此顺序。"""
    seen: list[str] = []
    for match in _MARK.finditer(markdown):
        chunk_id = match.group(1)
        if chunk_id in allowed and chunk_id not in seen:
            seen.append(chunk_id)
    return [
        NoteSource(
            chunk_id=chunk_id,
            snippet=allowed[chunk_id]["snippet"],
            document_id=allowed[chunk_id]["document_id"],
        )
        for chunk_id in seen
    ]


def fts_enabled() -> bool:
    return FTS_ENABLED
