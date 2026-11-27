"""M3 冒烟测试：RAG 问答（FR-03）。

用法（在 backend/ 下）：
    .venv/Scripts/python.exe scripts/smoke_m3.py           # 检索 / SSE 链路 / 兜底
    .venv/Scripts/python.exe scripts/smoke_m3.py --real    # 追加真实 LLM 流式（需配置 API Key）

使用独立数据库 data/test_m3.db，不会污染开发库。
LLM 用假模型跑通全链路（不配 Key 也能测）；检索与向量库走真实 BGE + sqlite-vec。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TEST_DB = BACKEND / "data" / "test_m3.db"
os.environ["DATABASE_URL"] = "sqlite:///./data/test_m3.db"
os.environ["LLM_API_KEY"] = os.environ.get("LLM_API_KEY", "")

if TEST_DB.exists():
    TEST_DB.unlink()

from fastapi.testclient import TestClient  # noqa: E402

from shared.config import settings  # noqa: E402
from app.main import app
from scripts._auth import login  # noqa: E402
import client.services.chat_service as chat_service  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []
CAPTURED: list[list[dict]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  [ok] {name}")
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name} {detail}")


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in text.split("\n\n"):
        name: str | None = None
        data: dict = {}
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name:
            events.append((name, data))
    return events


class FakeLLM:
    """只记录收到的 messages，并按固定增量流出引用答案。"""

    name = "fake"

    def __init__(self, **_kwargs) -> None:
        pass

    async def chat(self, messages: list[dict], **options) -> str:  # noqa: ARG002
        return "[1] 假回答"

    async def stream(self, messages: list[dict], **options):  # noqa: ARG002, ANN201
        CAPTURED.append(messages)
        for piece in ("根据片段，", "分区检索是正确语义。", "[1]"):
            yield piece


def wait_ready(client: TestClient, document_id: str, timeout: int = 120) -> str:
    for _ in range(timeout):
        time.sleep(1)
        docs = client.get("/api/documents").json()
        current = next((d for d in docs if d["id"] == document_id), None)
        if current and current["status"] in ("ready", "failed"):
            return current["status"]
    return "timeout"


def main() -> int:
    want_real = "--real" in sys.argv
    with TestClient(app) as client:
        login(client)
        print("\n== 1. 空库兜底 ==")
        # 空库现在也要作答（先声明知识库为空，再用通用知识回答），
        # 所以这一段需要"有 Key + 假模型"：假模型不会真的联网，只负责把流跑完。
        settings.llm_api_key = "test-key"
        original_build = chat_service.build_llm
        chat_service.build_llm = lambda: FakeLLM()
        try:
            r = client.post("/api/chat", json={"question": "什么是向量检索？"})
            events = parse_sse(r.text)
            names = [name for name, _ in events]
            check("空库返回 no_context", events and names[0] == "no_context", str(events[:1]))
            check("提示文案可读", "知识库" in events[0][1].get("detail", ""))
            check("空库提示带甄别", "请注意甄别" in events[0][1].get("detail", ""))
            check("空库声明 reason=empty", events[0][1].get("reason") == "empty", str(events[:1]))
            check("空库声明 fallback=True", events[0][1].get("fallback") is True)
            # 本轮改动：空库不再只声明，声明之后要继续用通用知识作答
            check("空库仍然作答", names.count("delta") >= 1, str(names))
            check("空库以 done 收尾", names[-1] == "done", str(names))
            check("空库不返回来源", "sources" not in names, str(names))
            # 无依据作答不能把资料片段塞进提示词，否则就成了"假装有出处"
            grounded = [m for m in CAPTURED[-1] if m["role"] == "user" and "《" in m["content"]]
            check("无依据提示词不含来源块", not grounded, str(grounded)[:80])
        finally:
            chat_service.build_llm = original_build
            settings.llm_api_key = ""

        print("\n== 2. 准备资料（真实切分与向量化）==")
        r = client.post("/api/topics", json={"name": "问答冒烟", "kind": "topic"})
        topic_id = r.json()["id"]
        r = client.post("/api/topics", json={"name": "高频", "kind": "tag"})
        tag_id = r.json()["id"]

        body_a = (
            "# 分区检索\n\n\nsqlite-vec 的 vec0 支持 partition key。"
            "把主题设为分区键后，KNN 检索在分区内取 Top-K，而不是全库取 K 再过滤。\n\n"
            "小主题不会被大主题淹没，这是按主题问答能命中小众资料的关键。\n\n"
            "改主题时向量要重新落分区：读回、删除、按新分区插入，不需要重新做 embedding。"
        )
        r = client.post("/api/documents/paste", json={
            "content": body_a, "title": "分区检索说明", "topic_id": topic_id, "tag_ids": [tag_id],
        })
        doc_a = r.json()
        status_a = wait_ready(client, doc_a["id"])
        check("资料 A 就绪", status_a == "ready", status_a)

        body_b = (
            "# 二分查找\n\n\n二分查找要求数组有序，时间复杂度 O(log n)。\n\n"
            "mid 的计算用 lo + (hi - lo) // 2 防止整数溢出，这是常见面试考点。"
        )
        r = client.post("/api/documents/paste", json={
            "content": body_b, "title": "二分查找说明", "topic_id": topic_id,
        })
        doc_b = r.json()
        status_b = wait_ready(client, doc_b["id"])
        check("资料 B 就绪", status_b == "ready", status_b)

        print("\n== 3. 未配置 Key（已有资料）==")
        r = client.post("/api/chat", json={"question": "什么是分区检索？"})
        events = parse_sse(r.text)
        check("未配 Key 返回 error 事件", events and events[0][0] == "error", str(events[:1]))
        check("错误文案指向设置页", "设置页" in events[0][1].get("detail", ""))

        return _run_scope_tests(client, topic_id, tag_id, doc_a, doc_b, want_real)


def _run_scope_tests(client, topic_id, tag_id, doc_a, doc_b, want_real):
    from shared.infrastructure.db.session import SessionLocal
    import client.services.chat_service as cs

    space_id = client.get("/api/auth/me").json()["current_space"]["id"]
    db = SessionLocal()
    try:
        print("\n== 4. 检索与范围 ==")
        citations, blocks, _ = cs.retrieve(
            db, "按主题问答为什么要在分区内取 Top-K？", space_id=space_id
        )
        check("全库检索有引用", len(citations) >= 1, str(len(citations)))
        check("引用带序号与文档名", citations[0]["index"] == 1 and citations[0]["document_name"], str(citations[0])[:120])
        check("引用带片段摘要", bool(citations[0]["snippet"]))
        check("资料块带 [n] 标记", blocks and blocks[0].startswith("[1] 《"), blocks[0][:40] if blocks else "")

        part_cites, _, _ = cs.retrieve(
            db,
            "按主题问答为什么要在分区内取 Top-K？",
            space_id=space_id,
            topic_id=topic_id,
        )
        check("主题分区检索命中", len(part_cites) >= 1)

        tag_cites, _, _ = cs.retrieve(
            db,
            "按主题问答为什么要在分区内取 Top-K？",
            space_id=space_id,
            tag_ids=[tag_id],
        )
        check("标签白名单检索命中带标签资料", len(tag_cites) >= 1)
        check(
            "标签过滤只留打标资料",
            all(c["document_name"] == "分区检索说明" for c in tag_cites),
            str([c["document_name"] for c in tag_cites]),
        )

        doc_cites, _, _ = cs.retrieve(
            db,
            "按主题问答为什么要在分区内取 Top-K？",
            space_id=space_id,
            document_ids=[doc_b["id"]],
        )
        check(
            "资料白名单命中另一篇",
            all(c["document_name"] == "二分查找说明" for c in doc_cites),
            str([c["document_name"] for c in doc_cites]),
        )

        empty_cites, _, _ = cs.retrieve(
            db, "任意问题", space_id=space_id, document_ids=["no-such-doc"]
        )
        check("白名单为空 → 无引用", empty_cites == [])

        print("\n== 5. 历史裁剪 ==")
        history = [
            {"role": "assistant", "content": "旧回答"},
            {"role": "user", "content": "第一问"},
            {"role": "assistant", "content": "第一答"},
            {"role": "user", "content": "第二问"},
            {"role": "assistant", "content": "第二答"},
            {"role": "user", "content": "第三问"},
            {"role": "assistant", "content": "第三答"},
        ]
        trimmed = cs._trim_history(history)
        check("只留最近 5 轮内", len(trimmed) <= 10 and trimmed[0]["role"] == "user", str(len(trimmed)))
        # 历史的结尾是上一轮回答，本次问题由请求体单独携带
        check("结尾是上一轮回答", trimmed[-1]["role"] == "assistant", str(trimmed[-1])[:60])

        print("\n== 6. SSE 全链路（假模型）==")
        settings.llm_api_key = "test-key"
        original_build = cs.build_llm
        cs.build_llm = lambda: FakeLLM()
        try:
            r = client.post("/api/chat", json={
                "question": "分区检索是什么？",
                "topic_id": topic_id,
                "history": [
                    {"role": "user", "content": "第一问"},
                    {"role": "assistant", "content": "第一答"},
                ],
            })
            events = parse_sse(r.text)
            names = [n for n, _ in events]
            check("事件序列 sources→delta→done", names[0] == "sources" and "delta" in names and names[-1] == "done", str(names))
            sources = events[0][1]["items"]
            check("引用先行下发", len(sources) >= 1 and sources[0]["index"] == 1)
            deltas = "".join(d["text"] for n, d in events if n == "delta")
            check("增量拼出完整回答", "分区检索" in deltas and "[1]" in deltas, deltas[:80])
            messages = CAPTURED[-1]
            check("提示词含资料片段", any("[1] 《" in m["content"] for m in messages if m["role"] == "user"))
            check("多轮历史已注入", sum(1 for m in messages if m["role"] == "user") == 2)

            r = client.post("/api/chat", json={"question": "任意问题", "document_ids": ["no-such-doc"]})
            events = parse_sse(r.text)
            check("范围内无内容 → no_context", events[0][0] == "no_context", str(events[:1]))

            r = client.post("/api/chat", json={"question": "", })
            check("空问题 → 422", r.status_code == 422, str(r.status_code))
        finally:
            cs.build_llm = original_build
            settings.llm_api_key = ""

        print("\n== 7. 存为笔记（source_type=chat）==")
        r = client.post("/api/notes", json={
            "title": "问答存档",
            "topic_id": topic_id,
            "source_type": "chat",
            "markdown": f"# 问答存档\n\n分区检索在分区内取 Top-K。",
        })
        check("chat 来源类型落库", r.status_code == 201 and r.json()["source_type"] == "chat", r.text[:120])

        if want_real:
            print("\n== 8. 真实流式（需要 LLM Key）==")
            if not settings.llm_configured:
                print("  [skip] 未配置 LLM_API_KEY")
            else:
                from shared.infrastructure.llm.openai_compat import build_llm

                cs.build_llm = build_llm
                settings.llm_api_key = os.environ["LLM_API_KEY"] or settings.llm_api_key
                r = client.post("/api/chat", json={"question": "一句话说明分区检索的作用", "topic_id": topic_id})
                events = parse_sse(r.text)
                names = [n for n, _ in events]
                deltas = "".join(d.get("text", "") for n, d in events if n == "delta")
                check("真实流式有增量", "delta" in names and len(deltas) > 5, deltas[:60])
                check("真实回答带引用", "[1]" in deltas, deltas[-60:])
                check("真实流式以 done 收尾", names[-1] == "done")
        return finish()
    finally:
        db.close()


def finish() -> int:
    print(f"\n==== 通过 {len(PASSED)} 项，失败 {len(FAILED)} 项 ====")
    if FAILED:
        for name in FAILED:
            print("  FAIL:", name)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
