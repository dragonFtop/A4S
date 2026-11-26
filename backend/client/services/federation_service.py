"""请求方联邦问答（P3 / FR-3.3 / FR-3.4 / C7 / C8）。

流程：本机（请求方）→ 服务端中继 `/api/fed/query` 持住 → 目标节点回传 citations。
请求方**在自己的机器上**：
1. 用自身文本相关度对候选重排（C8：不融合向量分，只在文本层重排）；
2. 用自身模型生成答案并标注 [n] 引用（C7：目标节点只给片段，不给答案/向量）。

模型未配置时退化为可读的抽取式摘要（仍带 [1] 引用），保证离线/无 Key 也能给出可核对的答案。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.request

from shared.config import settings
import shared.services.chat_prompts as chat_prompts
from shared.infrastructure.llm.openai_compat import build_llm
from client.services.chat_service import _text_score

logger = logging.getLogger(__name__)


class FedUnavailable(RuntimeError):
    """服务端未配置或连不上。"""


def _post(path: str, body: dict, timeout: float) -> dict | None:
    base = (settings.server_url or "").strip().rstrip("/")
    if not base:
        raise FedUnavailable("未配置服务端地址，联邦问答不可用")
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{base}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        logger.warning("联邦查询失败：%s", exc)
        return None


def _fallback_answer(citations: list[dict]) -> str:
    top = citations[0]
    snippet = (top.get("snippet") or "").strip()
    return f"根据联邦来源 [1]：{snippet[:200]}"


def ask(target_node_id: str, question: str, *, requester_node_id: str = "") -> dict:
    """发起一次联邦问答，返回 {status, answer, citations, target_node_id, latency_ms}。"""
    started = time.perf_counter()
    hold = settings.fed_hold_seconds + 5.0
    data = _post(
        "/api/fed/query",
        {
            "target_node_id": target_node_id,
            "question": question,
            "requester_node_id": requester_node_id,
        },
        timeout=hold,
    )
    if data is None:
        raise FedUnavailable("连不上服务端或联邦查询超时")

    status = data.get("status") or "offline"
    citations = data.get("citations") or []
    latency_ms = int((time.perf_counter() - started) * 1000)

    if status != "ok":
        return {
            "status": status,
            "answer": "",
            "citations": [],
            "target_node_id": target_node_id,
            "latency_ms": latency_ms,
        }

    if not citations:
        return {
            "status": "ok",
            "answer": "不知道：联邦来源没有找到相关资料。",
            "citations": [],
            "target_node_id": target_node_id,
            "latency_ms": latency_ms,
        }

    # C8：请求方只在文本层重排，不融合目标节点的向量分
    for c in citations:
        c["_text"] = _text_score(question, c.get("snippet") or "")
    citations.sort(key=lambda c: -float(c.get("_text") or 0.0))
    for index, c in enumerate(citations, start=1):
        c["index"] = index

    materials = "\n\n---\n\n".join(
        f"[{c['index']}] 《{c.get('document_name') or '资料'}》\n{c.get('snippet') or ''}"
        for c in citations
    )

    answer = ""
    if settings.llm_configured:
        try:
            messages = chat_prompts.build_messages(question, materials, history=[], unsure=False)
            answer = asyncio.run(build_llm().chat(messages, temperature=0.3))
        except Exception as exc:  # noqa: BLE001 - 模型失败退化到抽取式，不阻断联邦链路
            logger.warning("联邦答案生成失败，改用抽取式摘要：%s", exc)
            answer = _fallback_answer(citations)
    else:
        answer = _fallback_answer(citations)

    for c in citations:
        c.pop("_text", None)

    return {
        "status": "ok",
        "answer": answer,
        "citations": citations,
        "target_node_id": target_node_id,
        "latency_ms": latency_ms,
    }
