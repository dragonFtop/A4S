"""检索片段（RAG 的召回阶段）。

从 `client/services/chat_service.py` 抽出的纯函数，供 client 与 server 共用：
client 问答、server 小组问答都走这里。不依赖 FastAPI，只依赖传入的 Session。

范围过滤的两条路径
------------------
- `topic_id`：走 sqlite-vec 的 partition key，在分区内取 Top-K。
  分区内取 K 才保证小主题不被大主题淹没。
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
"""

from __future__ import annotations

import logging

from shared.domain.models.entities import Document, DocumentStatus
from shared.infrastructure.db.session import get_vector_store
from shared.repositories.sqlalchemy_repo import (
    SqlAlchemyChunkRepository,
    SqlAlchemyDocumentRepository,
)

logger = logging.getLogger(__name__)

TOP_K = 5
# 每路各取多少候选再融合。取 3 倍是为了让"只在另一路排得靠前"的片段也有机会进来。
CANDIDATES_PER_ROUTE = TOP_K * 3
# 按主题检索时先取该主题的片段 id 集合（后置过滤用）。单主题片段数远超此值时
# 说明该主题已经不适合作为检索范围，应当拆分。
TOPIC_CHUNK_LIMIT = 5000
# RRF 的平滑常数。60 是原论文的取值：让排名 1 和排名 2 的分差不过分悬殊，
# 避免某一路的头名直接吃掉全部权重。
RRF_K = 60
# 低于此值直接丢弃：实测无关提问最高 0.46，相关提问最低 0.55
MIN_SIMILARITY = 0.42
MAX_CONTEXT_CHARS = 12_000
SNIPPET_CHARS = 240


def _rrf(*rankings: list[str]) -> dict[str, float]:
    """Reciprocal Rank Fusion：只看名次，不看原始分数。

    两路分数的量纲天然不同（向量是余弦距离，全文是 bm25），直接加权会让
    分数量级大的那一路说了算。RRF 把它们都压成「第几名」，再按 1/(k+rank)
    求和，跨尺度可比，也不需要调权重。
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)
    return scores


def _matched_label(similarity: float | None, keyword: bool) -> str:
    """这条片段是被哪一路找出来的，给界面与排查用。"""
    if similarity is not None and keyword:
        return "both"
    return "keyword" if keyword else "vector"


def _embed_query(question: str) -> list[float]:
    from shared.infrastructure.llm.bge_local import build_embedding

    return build_embedding().embed_one(question)


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

    # 主题筛选也走 allowed，而不是 vec0 的分区键：分区键已经给了 space_id
    # （每次查询必带，剪枝收益最大），vec0 也不允许在 KNN 里按普通列过滤。
    if topic_id:
        in_topic = {
            c.id
            for c in chunk_repo.list_by_topic(topic_id, space_id, limit=TOPIC_CHUNK_LIMIT)
        }
        allowed = in_topic if allowed is None else (allowed & in_topic)
        if not allowed:
            return [], [], 0.0

    vector = _embed_query(question)
    hits = store.search(
        vector,
        top_k=CANDIDATES_PER_ROUTE,
        space_id=space_id,
        allowed_chunk_ids=allowed,
    )
    vec_sim = {chunk_id: 1.0 - distance for chunk_id, distance in hits}

    # 第二路：全文。中文按二元组 OR 召回，补的是"向量没命中但字面对得上"的情况
    # —— 专有名词、编号、公式符号这类，向量化之后往往就糊了。
    keyword_ids: list[str] = []
    from shared.infrastructure.db import fts
    from shared.infrastructure.db.session import FTS_ENABLED

    if FTS_ENABLED:
        try:
            raw = fts.search_chunks(db.connection(), question, limit=CANDIDATES_PER_ROUTE)
            keyword_ids = [chunk_id for chunk_id, _ in raw]
            if allowed is not None:
                # 全文路也要服从"按资料/标签圈定"的范围，否则会捞到范围外的东西
                keyword_ids = [cid for cid in keyword_ids if cid in allowed]
        except Exception as exc:  # noqa: BLE001 - 全文路挂了不该让问答整体失败
            logger.warning("全文召回失败，本次只用向量结果：%s", exc)
            keyword_ids = []

    # 融合只在**名次**上做：向量给的是距离、全文给的是 bm25，两者量纲不同，
    # 直接加权会被其中一路吞掉（RAGFlow 能加权是因为两路都在 ES 里算）。
    fused = _rrf([cid for cid, _ in hits], keyword_ids)
    ordered = sorted(fused.items(), key=lambda item: -item[1])
    keyword_set = set(keyword_ids)

    # 注意：best 必须在**空间过滤之后**才算。原始 hits 里混着别的空间的高分片段，
    # 拿它判断"这条问题有没有依据"会误判（bob 会拿到 alice 资料的分数）。
    best = 0.0

    # 仅 READY 资料参与检索：pending_review / rejected 即使曾有片段也不返回。
    # 正常流程下它们没有片段（审核前不入库），这里是显式防御，
    # 防止"状态与片段不一致"时把未生效内容泄漏进检索结果。
    doc_cache: dict[str, Document | None] = {}

    def _get_doc(document_id: str | None) -> Document | None:
        if document_id is None:
            return None
        if document_id not in doc_cache:
            doc_cache[document_id] = doc_repo.get(document_id, space_id)
        return doc_cache[document_id]

    citations: list[dict] = []
    blocks: list[str] = []
    used = 0
    for chunk_id, fused_score in ordered:
        if len(citations) >= TOP_K:
            break
        similarity = vec_sim.get(chunk_id)
        matched_keyword = chunk_id in keyword_set
        # 只被向量召回、且分数低于阈值 → 丢弃（原来的行为）。
        # 但**字面命中**的即使向量分低也保留：专有名词被向量化糊掉是常态，
        # 字面对上往往比"看起来像"更可信。
        if similarity is not None and not matched_keyword and similarity < MIN_SIMILARITY:
            continue
        if similarity is None and not matched_keyword:
            continue
        chunk = chunk_repo.get(chunk_id, space_id)
        if chunk is None:
            continue
        doc = _get_doc(chunk.document_id)
        if doc is None or doc.status is not DocumentStatus.READY:
            continue
        if similarity is not None:
            best = max(best, similarity)
        block = chunk.text.strip()
        if used + len(block) > MAX_CONTEXT_CHARS:
            break
        used += len(block)
        name = doc.name if doc else None
        index = len(citations) + 1
        citations.append(
            {
                "index": index,
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "document_name": name,
                "snippet": block[:SNIPPET_CHARS],
                "page": chunk.page,
                # 可观测：这个片段是被哪一路找出来的、各自多少分。
                # 排查"为什么这条被引用"时不用再猜。
                "score": round(fused_score, 5),
                "vector_sim": None if similarity is None else round(similarity, 3),
                "matched": _matched_label(similarity, matched_keyword),
            }
        )
        source = name or "资料"
        page = f" 第 {chunk.page} 页" if chunk.page else ""
        blocks.append(f"[{index}] 《{source}》{page}\n{block}")

    return citations, blocks, best
