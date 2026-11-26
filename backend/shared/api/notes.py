"""笔记接口（FR-13）。

起草是异步的：POST /notes/draft 立刻返回 job，前端轮询 /notes/drafts/{id}。
这样 LLM 慢的时候界面不会卡在一个转圈请求上，中途切页也不影响生成。
"""

from __future__ import annotations

import re
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from shared.domain.models.entities import (
    MemberRole,
    Note,
    NoteSourceType,
    NoteStatus,
    NoteTemplate,
    Space,
    SpaceKind,
    User,
)
from shared.api.deps import get_current_space, get_current_user
from shared.infrastructure.db.session import get_db
from shared.repositories.sqlalchemy_repo import SqlAlchemyTopicRepository
from shared.schemas.common import TopicBrief
from shared.schemas.notes import (
    DraftIn,
    DraftJobOut,
    NoteBriefOut,
    NoteCreate,
    NoteListOut,
    NoteOut,
    NoteSourceOut,
    NoteUpdate,
    TemplateListOut,
    TemplateOut,
)
import shared.services.note_prompts as note_prompts
import shared.services.note_service as note_service
from shared.services.note_service import NoteError, NoteNotFound

router = APIRouter(prefix="/notes", tags=["notes"])

# 有捕获组：导出时需要取出 chunk_id；_excerpt 的整匹配删除不受影响
_MARK = re.compile(r"\[\[chunk:([A-Za-z0-9\-]+)\]\]")


def _topics(db: Session, space_id: str) -> dict[str, TopicBrief]:
    return {
        t.id: TopicBrief(id=t.id, name=t.name, color=t.color)
        for t in SqlAlchemyTopicRepository(db).list(space_id)
    }


def _excerpt(markdown: str, limit: int = 140, keyword: str | None = None) -> str:
    """列表摘要：只留能读的文字。

    标题、代码块、表格分隔线、公式、链接地址在列表里既占地方又不通顺，
    一律先剔掉再纯文本化。标题整行删掉——列表左侧已经显示笔记标题了，
    再复述一遍只会浪费一辈子的第一行。

    搜索命中时把窗口挪到命中处：从头截 140 字，命中在第三段的话
    用户只看到一屏无关内容，等于没搜。
    """
    text = _MARK.sub("", markdown)
    text = text.replace(note_prompts.AI_MARK, "")
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"~~~[\s\S]*?~~~", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s.*$", " ", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\|?[\s:|-]*\|[\s:|-]*$", " ", text, flags=re.MULTILINE)
    # 表格整行跳过：几列词串成一句反而更难读，摘要取叙述性文字就够了
    text = re.sub(r"^\s*\|.*\|?\s*$", " ", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*_]{3,}\s*$", " ", text, flags=re.MULTILINE)
    # 公式留内容去符号：「时间 O(n)、空间 O(h)」比「时间 、空间 」像句话
    text = re.sub(r"\$\$([\s\S]*?)\$\$", r" \1 ", text)
    text = re.sub(r"\$([^$\n]+)\$", r"\1", text)
    # 列表项之间补个间隔点，否则「甲 乙 丙」会粘成一句
    text = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+(.+)$", r"\1 · ", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)
    text = text.replace("|", " ")
    text = re.sub(r"[*_`]", "", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"(?:\s*·\s*)+", " · ", text).strip(" ·")

    start = 0
    if keyword:
        hit = text.lower().find(keyword.strip().lower())
        if hit > limit // 3:
            start = max(0, hit - 36)
    window = text[start : start + limit]
    return ("…" if start > 0 else "") + window + ("…" if start + limit < len(text) else "")


def _brief(note: Note, topics: dict[str, TopicBrief], keyword: str | None = None) -> NoteBriefOut:
    return NoteBriefOut(
        id=note.id,
        title=note.title,
        excerpt=_excerpt(note.markdown, keyword=keyword),
        status=note.status.value,
        source_type=note.source_type.value,
        template=note.template.value if note.template else None,
        ai_written_count=note.ai_written_count,
        source_count=len(note.sources),
        topic=_topic(note, topics),
        tags=[topics[t] for t in note.tag_ids if t in topics],
        created_at=note.created_at,
        updated_at=note.updated_at,
        confirmed_at=note.confirmed_at,
        author_id=note.user_id,
    )


def _topic(note: Note, topics: dict[str, TopicBrief]) -> TopicBrief:
    return topics.get(note.topic_id) or TopicBrief(
        id=note.topic_id, name="未分类", color="#888780"
    )


def _full(note: Note, topics: dict[str, TopicBrief]) -> NoteOut:
    sources = [
        NoteSourceOut(
            chunk_id=source.chunk_id,
            document_id=source.document_id,
            document_name=source.document_name,
            snippet=source.snippet,
            index=index,
            missing=source.document_name is None,
        )
        for index, source in enumerate(note.sources, start=1)
    ]
    return NoteOut(
        id=note.id,
        title=note.title,
        markdown=note.markdown,
        status=note.status.value,
        source_type=note.source_type.value,
        template=note.template.value if note.template else None,
        ai_model=note.ai_model,
        ai_written_count=note.ai_written_count,
        topic=_topic(note, topics),
        tags=[topics[t] for t in note.tag_ids if t in topics],
        sources=sources,
        created_at=note.created_at,
        updated_at=note.updated_at,
        confirmed_at=note.confirmed_at,
        author_id=note.user_id,
    )


def _split(value: str | None) -> list[str] | None:
    if not value:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


def _needs_review(space: Space) -> bool:
    """member 向 GROUP 空间投稿需要 owner 审核；owner 自传与个人空间直入。"""
    return space.kind is SpaceKind.GROUP and space.my_role is not MemberRole.OWNER


def _export_markdown(note: Note) -> tuple[str, str]:
    """导出用正文：角标转为人类可读的 [序号]，文末附来源清单。

    [[chunk:uuid]] 只在本系统内有意义，导出到外面的 Markdown 里
    就是无法点击的噪音。按来源表顺序转成 [1]、[2]…，并在文末补一节
    「来源」，让笔记离开 A4S 依然能指回原文档。
    """
    order: dict[str, int] = {}

    def _replace(match: re.Match[str]) -> str:
        chunk_id = match.group(1)
        if chunk_id not in order:
            index = next(
                (i for i, s in enumerate(note.sources, start=1) if s.chunk_id == chunk_id),
                None,
            )
            if index is None:  # 校验本应保证来源与正文一致，兜底剔除
                return ""
            order[chunk_id] = index
        return f"[{order[chunk_id]}]"

    body = _MARK.sub(_replace, note.markdown).strip()

    # 正文没有一级标题就补上（手工笔记常直接写内容）
    first_line = next((line for line in body.splitlines() if line.strip()), "")
    if not first_line.startswith("# "):
        body = f"# {note.title}\n\n{body}" if note.title else body

    if note.sources:
        items = [
            f"{i}. {source.document_name or '（来源已删除）'}"
            for i, source in enumerate(note.sources, start=1)
        ]
        body = body.rstrip() + "\n\n---\n\n## 来源\n\n" + "\n".join(items) + "\n"

    safe_title = re.sub(r'[\\/:*?"<>|]', "_", note.title or "笔记").strip()[:80]
    return body, (safe_title or "笔记") + ".md"


@router.get("/templates", response_model=TemplateListOut)
def list_templates() -> TemplateListOut:
    return TemplateListOut(
        items=[
            TemplateOut(
                key=template.value,
                label=note_prompts.TEMPLATE_LABELS[template],
                hint=note_prompts.TEMPLATE_HINTS[template],
            )
            for template in NoteTemplate
        ]
    )


@router.get("", response_model=NoteListOut)
def list_notes(
    topic_ids: str | None = Query(default=None, description="逗号分隔"),
    tag_ids: str | None = Query(default=None, description="逗号分隔，多选取交集"),
    status: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    space: Space = Depends(get_current_space),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> NoteListOut:
    notes = note_service.list_notes(
        db,
        space_id=space.id,
        topic_ids=_split(topic_ids),
        tag_ids=_split(tag_ids),
        status=status if status in ("draft", "confirmed") else None,
        keyword=keyword,
        limit=limit,
        offset=offset,
    )
    topics = _topics(db, space.id)
    probe = (keyword or "").strip() or None
    return NoteListOut(items=[_brief(note, topics, probe) for note in notes], total=len(notes))


@router.post("", response_model=NoteOut, status_code=201)
def create_note(
    payload: NoteCreate,
    space: Space = Depends(get_current_space),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NoteOut:
    # member 向小组投稿 → 待审；owner 自传 / 个人空间 → 直接草稿。
    # 作者归属写 user_id，批准后仍保留。
    initial_status = NoteStatus.PENDING_REVIEW if _needs_review(space) else NoteStatus.DRAFT
    try:
        note = note_service.create(
            db,
            space_id=space.id,
            title=payload.title,
            topic_id=payload.topic_id,
            tag_ids=payload.tag_ids,
            markdown=payload.markdown,
            template=NoteTemplate(payload.template) if payload.template else None,
            source_type=NoteSourceType(payload.source_type),
            initial_status=initial_status,
            user_id=user.id,
        )
    except NoteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _full(note, _topics(db, space.id))


@router.get("/{note_id}", response_model=NoteOut)
def get_note(
    note_id: str,
    space: Space = Depends(get_current_space),
    db: Session = Depends(get_db),
) -> NoteOut:
    try:
        note = note_service.get(db, note_id, space.id)
    except NoteNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _full(note, _topics(db, space.id))


@router.patch("/{note_id}", response_model=NoteOut)
def update_note(
    note_id: str,
    payload: NoteUpdate,
    space: Space = Depends(get_current_space),
    db: Session = Depends(get_db),
) -> NoteOut:
    try:
        note = note_service.update(
            db,
            note_id,
            space_id=space.id,
            title=payload.title,
            markdown=payload.markdown,
            topic_id=payload.topic_id,
            tag_ids=payload.tag_ids,
            template=NoteTemplate(payload.template) if payload.template else None,
        )
    except NoteNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NoteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _full(note, _topics(db, space.id))


@router.post("/{note_id}/confirm", response_model=NoteOut)
def confirm_note(
    note_id: str,
    space: Space = Depends(get_current_space),
    db: Session = Depends(get_db),
) -> NoteOut:
    try:
        note = note_service.confirm(db, note_id, space.id)
    except NoteNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NoteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _full(note, _topics(db, space.id))


@router.delete("/{note_id}")
def delete_note(
    note_id: str,
    space: Space = Depends(get_current_space),
    db: Session = Depends(get_db),
) -> dict:
    note_service.delete(db, note_id, space.id)
    return {"deleted": note_id}


# ---------- AI 起草 ----------


@router.post("/draft", response_model=DraftJobOut, status_code=202)
def draft(
    payload: DraftIn,
    background: BackgroundTasks,
    space: Space = Depends(get_current_space),
    db: Session = Depends(get_db),
) -> DraftJobOut:
    if not payload.document_ids and not payload.topic_id:
        raise HTTPException(status_code=400, detail="请选择资料或主题作为整理范围")
    try:
        job = note_service.start_draft(
            db,
            space_id=space.id,
            template=NoteTemplate(payload.template),
            document_ids=payload.document_ids,
            topic_id=payload.topic_id,
            title=payload.title,
            note_topic_id=payload.note_topic_id,
            tag_ids=payload.tag_ids,
        )
    except NoteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    background.add_task(note_service.run_draft, job["id"])
    return DraftJobOut(**job)


@router.post("/{note_id}/regenerate", response_model=DraftJobOut, status_code=202)
def regenerate(
    note_id: str,
    payload: DraftIn,
    background: BackgroundTasks,
    confirm: bool = Query(default=False, description="必须显式确认覆盖"),
    db: Session = Depends(get_db),
) -> DraftJobOut:
    if not confirm:
        raise HTTPException(
            status_code=400, detail="重新整理会覆盖当前正文，请先确认（confirm=true）"
        )
    try:
        job = note_service.start_draft(
            db,
            space_id=space.id,
            template=NoteTemplate(payload.template),
            document_ids=payload.document_ids,
            topic_id=payload.topic_id,
            title=payload.title,
            note_topic_id=payload.note_topic_id,
            tag_ids=payload.tag_ids,
            note_id=note_id,
        )
    except NoteNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NoteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    background.add_task(note_service.run_draft, job["id"])
    return DraftJobOut(**job)


@router.get("/drafts/{job_id}", response_model=DraftJobOut)
def draft_status(job_id: str) -> DraftJobOut:
    try:
        return DraftJobOut(**note_service.job_snapshot(job_id))
    except NoteNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{note_id}/export")
def export_note(
    note_id: str,
    space: Space = Depends(get_current_space),
    db: Session = Depends(get_db),
) -> Response:
    """下载 Markdown 文件。来源角标转 [序号]，文末附来源清单。"""
    try:
        note = note_service.get(db, note_id, space.id)
    except NoteNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    markdown, filename = _export_markdown(note)
    return Response(
        content=markdown.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={
            # filename 是 ASCII 兜底，filename* 才是真实中文名
            "Content-Disposition": (
                f"attachment; filename=\"note-{note_id[:8]}.md\"; "
                f"filename*=UTF-8''{quote(filename)}"
            )
        },
    )


@router.get("/{note_id}/sources/{chunk_id}", response_model=NoteSourceOut)
def source_detail(
    note_id: str,
    chunk_id: str,
    space: Space = Depends(get_current_space),
    db: Session = Depends(get_db),
) -> NoteSourceOut:
    """角标二次点击时取完整原文（列表里的 snippet 只有 240 字）。"""
    from shared.repositories.sqlalchemy_repo import (
        SqlAlchemyChunkRepository,
        SqlAlchemyDocumentRepository,
    )

    chunk = SqlAlchemyChunkRepository(db).get(chunk_id, space.id)
    if chunk is None:
        raise HTTPException(status_code=404, detail="来源片段已随资料删除")
    doc = SqlAlchemyDocumentRepository(db).get(chunk.document_id, space.id)
    note = note_service.get(db, note_id, space.id)
    index = next(
        (i for i, s in enumerate(note.sources, start=1) if s.chunk_id == chunk_id), 1
    )
    return NoteSourceOut(
        chunk_id=chunk_id,
        document_id=chunk.document_id,
        document_name=doc.name if doc else None,
        snippet=chunk.text,
        index=index,
        missing=doc is None,
    )


__all__ = ["router", "NoteStatus"]
