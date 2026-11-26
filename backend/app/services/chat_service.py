"""RAG 问答业务（FR-03）。

链路：检索 → 先下发引用 → 流式生成 → 结束。一次 POST 完成，
事件用 SSE 分发，前端不需要"先检索再提问"两个请求。

范围过滤的两条路径
------------------
- `topic_id`：走 sqlite-vec 的 partition key，在分区内取 Top-K。
  分区内取 K 才保证小主题不被大主题淹没（FR-02 规则 7 的同一结论）。
- `tag_ids` / `document_ids`：无法下推到分区，先查出片段白名单，
  检索时按 `allowed_chunk_ids` 过滤（store 会 4 倍超采再截断，
  避免过滤后不足 Top-K）。
- 两者同时给定时取交集：先按主题分区检索，再按白名单过滤。

相似度阈值
----------
sqlite-vec 返回 cosine distance，相似度 = 1 - distance。
实测（bge-small-zh，单份资料）相关提问落在 0.55~0.76，无关提问 0.13~0.46，
原先的 0.3 会把"如何用 pandas 读 excel"这类问题也放进来，所以抬到 0.42。

0.42~0.55 之间属于"擦边命中"：片段确实是中文相关文本，但未必回答了问题。
这种区间不直接拒答，而是在提示词里加一段让人自己判断，
模型判断片段没用时会明确说没找到，比硬设阈值更不容易误伤。

三种"没找到"，三种处理
------------------------
- 库里一条片段都没有（empty）：仍然作答，声明"知识库中还没有资料，下面用通用知识回答"；
- 有资料但本次召回为空（no_match）：声明"当前范围内没有相关片段"，再作答；
- 召回命中但相似度低（擦边）：不声明未命中，改为在提示词里要求模型先判断能否回答。

三种都走 `build_ungrounded_messages` 或带 `UNSURE_NOTE` 的提示词，
共同点是**不允许模型伪造引用编号**——没有资料就没有 `[n]`，界面的 AI 标注据此分档。
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator

from app.config import settings
from app.infrastructure.db.session import SessionLocal, get_vector_store
from app.infrastructure.llm.openai_compat import build_llm
from app.repositories.sqlalchemy_repo import (
    SqlAlchemyChunkRepository,
    SqlAlchemyDocumentRepository,
)
from app.services import chat_prompts

logger = logging.getLogger(__name__)

TOP_K = 5
# 低于此值直接丢弃：实测无关提问最高 0.46，相关提问最低 0.55
MIN_SIMILARITY = 0.42
# 低于此值算"擦边命中"，提示词里要额外提醒模型先判断片段是否真的能回答
LOW_CONFIDENCE = 0.55
# 检索不到时是否改用模型的通用知识作答。
# 打开后必须显式声明"没找到资料"与"以下由 AI 生成"——这个声明由界面渲染，
# 不依赖模型自觉，也不允许模型自己编引用编号。
ANSWER_WITHOUT_CONTEXT = True
MAX_CONTEXT_CHARS = 12_000
MAX_HISTORY_ROUNDS = 5
SNIPPET_CHARS = 240


def _event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def retrieve(
    db,
    question: str,
    *,
    space_id: str,
    topic_id: str | None = None,
    tag_ids: list[str] | None = None,
    document_ids: list[str] | None = None,
) -> tuple[list[dict], list[dict], float]:
    """检索片段。

    返回 (citations, chunks_text, best_similarity)：
    citations 是对外引用列表（带 index），chunks_text 是拼给模型的原文，
    best 是本次召回里最像的一条的相似度，用来判断该不该提醒模型"别硬答"。
    """
    store = get_vector_store()
    chunk_repo = SqlAlchemyChunkRepository(db)
    doc_repo = SqlAlchemyDocumentRepository(db)

    allowed: set[str] | None = None
    if document_ids or tag_ids:
        doc_ids: list[str] = list(dict.fromkeys(document_ids or []))
        if tag_ids:
            # 标签 → 资料 → 片段：多标签取并集（任一标签命中即算相关）
            tagged = doc_repo.list(space_id, tag_ids=tag_ids, limit=500)
            doc_ids.extend(doc.id for doc in tagged)
        allowed = set()
        for doc_id in dict.fromkeys(doc_ids):
            allowed.update(c.id for c in chunk_repo.list_by_document(doc_id))
        if not allowed:
            return [], [], 0.0

    vector = _embed_query(question)
    hits = store.search(vector, top_k=TOP_K, topic_id=topic_id, allowed_chunk_ids=allowed)
    # 注意：best 必须在**空间过滤之后**才算。原始 hits 里混着别的空间的高分片段，
    # 拿它判断"这条问题有没有依据"会误判（bob 会拿到 alice 资料的分数）。
    best = 0.0

    names: dict[str, str | None] = {}

    def _doc_name(document_id: str | None) -> str | None:
        if document_id is None:
            return None
        if document_id not in names:
            doc = doc_repo.get(document_id, space_id)
            names[document_id] = doc.name if doc else None
        return names[document_id]

    citations: list[dict] = []
    blocks: list[str] = []
    used = 0
    for chunk_id, distance in hits:
        similarity = 1.0 - distance
        if similarity < MIN_SIMILARITY:
            continue
        chunk = chunk_repo.get(chunk_id, space_id)
        if chunk is None:
            continue
        best = max(best, similarity)
        block = chunk.text.strip()
        if used + len(block) > MAX_CONTEXT_CHARS:
            break
        used += len(block)
        name = _doc_name(chunk.document_id)
        index = len(citations) + 1
        citations.append(
            {
                "index": index,
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "document_name": name,
                "snippet": block[:SNIPPET_CHARS],
                "page": chunk.page,
            }
        )
        source = name or "资料"
        page = f" 第 {chunk.page} 页" if chunk.page else ""
        blocks.append(f"[{index}] 《{source}》{page}\n{block}")

    return citations, blocks, best


def _embed_query(question: str) -> list[float]:
    from app.infrastructure.llm.bge_local import build_embedding

    return build_embedding().embed_one(question)


def _trim_history(history: list[dict]) -> list[dict]:
    """只留最近 N 轮，且第一条必须是 user（OpenAI 兼容端点不要求，但更稳）。"""
    kept = history[-(MAX_HISTORY_ROUNDS * 2) :]
    while kept and kept[0]["role"] != "user":
        kept = kept[1:]
    return kept


async def _stream_answer(messages: list[dict], temperature: float) -> AsyncIterator[str]:
    """把一次生成摊成 SSE 事件：delta... → done（或 error）。有依据与无依据共用。"""
    llm = build_llm()
    answer_id = uuid.uuid4().hex[:12]
    try:
        async for delta in llm.stream(messages, temperature=temperature):
            if delta:
                yield _event("delta", {"text": delta, "id": answer_id})
    except Exception as exc:  # noqa: BLE001 - 供应商报错要原样转给人
        logger.exception("问答生成失败")
        yield _event("error", {"detail": _explain(str(exc) or exc.__class__.__name__)})
        return
    yield _event("done", {"id": answer_id})


async def sse_chat(payload: dict) -> AsyncIterator[str]:
    """问答 SSE 主循环。自己开 Session，响应期间保持可用。"""
    db = SessionLocal()
    try:
        # 空库也要能问：先声明"知识库没有资料"，再用通用知识作答。
        # 所以 Key 的检查必须放在空库分支之前——没有 Key 时连兜底答案都给不了。
        if not settings.llm_configured:
            yield _event("error", {"detail": "尚未配置大模型 API Key，请先在设置页填写"})
            return

        history = _trim_history(payload.get("history") or [])

        chunk_repo = SqlAlchemyChunkRepository(db)
        if chunk_repo.count(payload["space_id"]) == 0:
            # 三种"没找到"里最干净的一种：库里确实一条片段都没有。
            # 仍然作答，但声明必须写清楚"一句资料都没用上"，否则用户会以为回答有出处。
            yield _event(
                "no_context",
                {
                    "reason": "empty",
                    "fallback": ANSWER_WITHOUT_CONTEXT,
                    "scoped": False,
                    "detail": (
                        "当前知识库中还没有资料，下面直接用 AI 的通用知识回答，内容未经你的资料核对。"
                        if ANSWER_WITHOUT_CONTEXT
                        else "先在知识库导入文档或网页，等索引完成后再来提问。"
                    ),
                },
            )
            if not ANSWER_WITHOUT_CONTEXT:
                return
            async for chunk in _stream_answer(
                chat_prompts.build_ungrounded_messages(payload["question"], history),
                temperature=0.4,
            ):
                yield chunk
            return

        citations, blocks, best = retrieve(
            db,
            payload["question"],
            space_id=payload["space_id"],
            topic_id=payload.get("topic_id"),
            tag_ids=payload.get("tag_ids") or [],
            document_ids=payload.get("document_ids") or [],
        )

        if not citations:
            # 明确区分两种"没找到"：库是空的，还是库里有东西但都不相关。
            # 前者引导去导入；后者如果开了无依据作答，就直接用通用知识回答，
            # 但先把"没找到资料"这件事说在前面，界面据此渲染声明块。
            scoped = bool(payload.get("topic_id") or payload.get("tag_ids") or payload.get("document_ids"))
            yield _event(
                "no_context",
                {
                    "reason": "no_match",
                    "fallback": ANSWER_WITHOUT_CONTEXT,
                    "scoped": scoped,
                    "detail": (
                        "当前范围内没有相关片段，下面改用通用知识回答。"
                        if ANSWER_WITHOUT_CONTEXT
                        else "换个更具体的说法，或确认相关资料是否已经索引完成。"
                    ),
                },
            )
            if not ANSWER_WITHOUT_CONTEXT:
                return
            async for chunk in _stream_answer(
                chat_prompts.build_ungrounded_messages(payload["question"], history),
                temperature=0.4,
            ):
                yield chunk
            return

        yield _event("sources", {"items": citations})

        materials = "\n\n---\n\n".join(blocks)
        messages = chat_prompts.build_messages(
            payload["question"],
            materials,
            history=history,
            unsure=best < LOW_CONFIDENCE,
        )
        async for chunk in _stream_answer(messages, temperature=0.3):
            yield chunk
    finally:
        db.close()


def _explain(message: str) -> str:
    lowered = message.lower()
    if "401" in message or "unauthorized" in lowered or "api key" in lowered:
        return "模型鉴权失败（401）：请检查设置页的 API Key"
    if "timeout" in lowered or "timed out" in lowered:
        return "模型响应超时：请稍后重试"
    if "429" in message:
        return "模型额度不足或请求过快（429）：请稍后重试"
    return message[:400]
