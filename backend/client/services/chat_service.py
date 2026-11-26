"""RAG 问答业务（FR-03）。

链路：检索 → 先下发引用 → 流式生成 → 结束。一次 POST 完成，
事件用 SSE 分发，前端不需要"先检索再提问"两个请求。

检索片段逻辑已抽到 `shared/services/retrieval.py`，client 与 server 共用。
本文件只保留问答编排（空库/无命中的兜底提示词、流式生成）。

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

from shared.config import settings
from shared.infrastructure.db.session import SessionLocal
from shared.infrastructure.llm.openai_compat import build_llm
from shared.repositories.sqlalchemy_repo import SqlAlchemyChunkRepository
import shared.services.chat_prompts as chat_prompts
# retrieve 已抽到 shared，client 与 server 共用；此处重导出以保持向后兼容
# （脚本/评估仍通过 chat_service.retrieve 访问）。
from shared.services.retrieval import MIN_SIMILARITY, _embed_query, retrieve

logger = logging.getLogger(__name__)

# 低于此值算"擦边命中"，提示词里要额外提醒模型先判断片段是否真的能回答
LOW_CONFIDENCE = 0.55
# 检索不到时是否改用模型的通用知识作答。
# 打开后必须显式声明"没找到资料"与"以下由 AI 生成"——这个声明由界面渲染，
# 不依赖模型自觉，也不允许模型自己编引用编号。
ANSWER_WITHOUT_CONTEXT = True
MAX_HISTORY_ROUNDS = 5
# Agent 选源（P2）：本地 + 组内 + 联邦，来源数合计上限
MAX_SOURCES = 3
_KIND_ORDER = {"local": 0, "group": 1, "fed": 2}


def _event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _trim_history(history: list[dict]) -> list[dict]:
    """只留最近 N 轮，且第一条必须是 user（OpenAI 兼容端点不要求，但更稳）。"""
    kept = history[-(MAX_HISTORY_ROUNDS * 2) :]
    while kept and kept[0]["role"] != "user":
        kept = kept[1:]
    return kept


def _text_score(question: str, text: str) -> float:
    """跨源重排用的纯文本相关度（字符二元组命中率）。

    只做文本层比较：不同空间的向量库相似度量纲不可比，按 C8 不得跨源融合，
    所以这里刻意不读 `vector_sim`，只比字面。
    """
    q = (question or "").strip()
    if not q or not text:
        return 0.0
    grams = {q[i : i + 2] for i in range(len(q) - 1)} or set(q)
    hit = sum(1 for g in grams if g in text)
    return hit / len(grams)


def _federation_retrieve(
    question: str, *, space_id: str
) -> tuple[list[dict], list[dict], float]:
    """联邦检索占位（P3 实装）。

    P3 会在这里向 server 发起联邦请求，等目标节点返回 citations（不返向量、
    不生成答案），再由本模块做文本层重排。P2 阶段没有联邦来源，返回空。
    """
    return [], [], 0.0


def _multi_source_retrieve(
    db, question: str, *, space_id: str, user_id: str
) -> tuple[list[dict], list[dict], float, list[dict]]:
    """Agent 选源：本地 → 组内(catalog) → 联邦，来源数上限 3。

    返回 (citations, blocks, best, plan)。plan 记录实际查询过的来源，
    供前端与排查看清"这次到底问了谁、各命中多少"。
    """
    candidates: list[dict] = []
    plan: list[dict] = []

    def _collect(kind: str, sid: str, name: str, cites: list[dict], blocks: list[str]) -> None:
        for rank, (cite, block) in enumerate(zip(cites, blocks)):
            # retrieve 的 block 形如「[n] 《来源》第 x 页\n正文」，正文在换行之后
            body = block.split("\n", 1)[1] if "\n" in block else block
            candidates.append(
                {
                    "kind": kind,
                    "space_id": sid,
                    "rank": rank,
                    "text": _text_score(question, cite.get("snippet") or body),
                    "cite": {
                        **cite,
                        "source_kind": kind,
                        "source_space_id": sid,
                        "source_space_name": name,
                    },
                    "body": body,
                }
            )

    # 1) 本地：当前空间
    local_cites, local_blocks, _ = retrieve(db, question, space_id=space_id)
    plan.append(
        {"kind": "local", "space_id": space_id, "space_name": "本地", "hits": len(local_cites)}
    )
    _collect("local", space_id, "本地", local_cites, local_blocks)
    used = 1

    # 2) 组内：catalog 里除当前空间外的来源（资料多、更新近的优先）
    if user_id:
        from shared.services.catalog_service import build_catalog

        for entry in build_catalog(db, viewer_id=user_id):
            if used >= MAX_SOURCES:
                break
            if entry.space_id == space_id:
                continue
            group_cites, group_blocks, _ = retrieve(db, question, space_id=entry.space_id)
            plan.append(
                {
                    "kind": "group",
                    "space_id": entry.space_id,
                    "space_name": entry.space_name,
                    "hits": len(group_cites),
                }
            )
            _collect("group", entry.space_id, entry.space_name, group_cites, group_blocks)
            used += 1

    # 3) 联邦：P3 实装；P2 无来源
    if used < MAX_SOURCES:
        fed_cites, fed_blocks, _ = _federation_retrieve(question, space_id=space_id)
        if fed_cites or fed_blocks:
            plan.append({"kind": "fed", "space_id": "", "space_name": "联邦", "hits": len(fed_cites)})
            _collect("fed", "", "联邦", fed_cites, fed_blocks)

    if not candidates:
        return [], [], 0.0, plan

    # 跨源只在文本层重排；同分时按来源优先级（本地在前）与原排名。
    candidates.sort(key=lambda c: (-c["text"], _KIND_ORDER[c["kind"]], c["rank"]))

    citations: list[dict] = []
    blocks: list[str] = []
    best = 0.0
    for nidx, item in enumerate(candidates, start=1):
        cite = item["cite"]
        cite["index"] = nidx
        citations.append(cite)
        source = cite.get("document_name") or "资料"
        page = f" 第 {cite['page']} 页" if cite.get("page") else ""
        blocks.append(f"[{nidx}] 《{source}》{page}\n{item['body']}")
        sim = cite.get("vector_sim")
        if isinstance(sim, (int, float)):
            best = max(best, float(sim))
    return citations, blocks, best, plan


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

        # Agent 选源（P2）：本地 → 组内(catalog) → 联邦，来源数上限 3。
        # 已显式圈定本地范围时不做跨源扩散（那些 id 只在本空间成立）。
        scoped_local = bool(
            payload.get("topic_id") or payload.get("tag_ids") or payload.get("document_ids")
        )
        if payload.get("multi_source") and not scoped_local:
            citations, blocks, best, plan = _multi_source_retrieve(
                db,
                payload["question"],
                space_id=payload["space_id"],
                user_id=payload.get("user_id") or "",
            )
            if not citations:
                # 允许"不知道"：不伪造引用，明确声明所有来源都没命中。
                yield _event(
                    "no_context",
                    {
                        "reason": "no_match",
                        "fallback": False,
                        "scoped": False,
                        "detail": "本地、组内与联邦来源中都没有找到相关资料，无法回答这个问题。",
                    },
                )
                answer_id = uuid.uuid4().hex[:12]
                yield _event("delta", {"text": "不知道：没有找到相关资料。", "id": answer_id})
                yield _event("done", {"id": answer_id})
                return
            yield _event("sources", {"items": citations, "plan": plan})
            materials = "\n\n---\n\n".join(blocks)
            messages = chat_prompts.build_messages(
                payload["question"],
                materials,
                history=history,
                unsure=best < LOW_CONFIDENCE,
            )
            async for chunk in _stream_answer(messages, temperature=0.3):
                yield chunk
            return

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
