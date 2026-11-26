"""M2.5 冒烟测试：电子笔记（FR-13）。

用法（在 backend/ 下）：
    .venv/Scripts/python.exe scripts/smoke_m25.py           # CRUD / 状态机 / 搜索 / 清洗
    .venv/Scripts/python.exe scripts/smoke_m25.py --draft   # 追加真实 LLM 起草（需配置 API Key）

使用独立数据库 data/test_m25.db，不会污染开发库。
LLM 起草部分用假模型跑通链路，所以不配 Key 也能验证来源角标清洗与落库。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TEST_DB = BACKEND / "data" / "test_m25.db"
os.environ["DATABASE_URL"] = "sqlite:///./data/test_m25.db"
os.environ["LLM_API_KEY"] = os.environ.get("LLM_API_KEY", "")

if TEST_DB.exists():
    TEST_DB.unlink()

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  [ok] {name}")
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name} {detail}")


def main() -> int:
    want_draft = "--draft" in sys.argv
    with TestClient(app) as client:
        print("\n== 1. 模板与空列表 ==")
        r = client.get("/api/notes/templates")
        templates = r.json()["items"]
        check("四个模板", len(templates) == 4, str(len(templates)))
        check("模板按键稳定", {t["key"] for t in templates} == {"outline", "concept", "code", "formula"})

        r = client.get("/api/notes")
        check("初始列表为空", r.json()["items"] == [])

        print("\n== 2. 主题准备 ==")
        r = client.post("/api/topics", json={"name": "算法", "kind": "topic"})
        topic_id = r.json()["id"]
        r = client.post("/api/topics", json={"name": "重点", "kind": "tag"})
        tag_id = r.json()["id"]
        check("建主题与标签", bool(topic_id and tag_id))

        print("\n== 3. 手动新建与状态机 ==")
        r = client.post(
            "/api/notes",
            json={"title": "二分查找", "topic_id": topic_id, "tag_ids": [tag_id],
                  "markdown": "## 要点\n\n- 有序数组才能二分\n"},
        )
        check("新建笔记 201", r.status_code == 201, str(r.status_code))
        note = r.json()
        note_id = note["id"]
        check("初始为草稿", note["status"] == "draft")
        check("带主题与标签", note["topic"]["id"] == topic_id and len(note["tags"]) == 1)
        check("手动来源类型", note["source_type"] == "manual")

        r = client.post(f"/api/notes/{note_id}/confirm")
        check("确认定稿", r.json()["status"] == "confirmed")
        check("定稿时间有值", bool(r.json()["confirmed_at"]))

        r = client.patch(f"/api/notes/{note_id}", json={"markdown": "## 要点\n\n- 改一句\n"})
        check("改动后退回草稿", r.json()["status"] == "draft")
        check("退回后清空定稿时间", r.json()["confirmed_at"] is None)

        r = client.post(f"/api/notes/{note_id}/confirm")
        check("可再次确认", r.json()["status"] == "confirmed")

        print("\n== 4. 校验与错误 ==")
        r = client.post("/api/notes", json={"title": "x", "topic_id": "no-such-topic"})
        check("主题不存在 → 400", r.status_code == 400, str(r.status_code))
        r = client.post("/api/notes", json={"title": "x", "topic_id": topic_id, "tag_ids": ["bad"]})
        check("非法标签被忽略", r.json()["tags"] == [], str(r.json()["tags"]))
        r = client.get("/api/notes/not-exist")
        check("详情 404", r.status_code == 404, str(r.status_code))
        r = client.patch("/api/notes/not-exist", json={"title": "y"})
        check("更新 404", r.status_code == 404, str(r.status_code))

        print("\n== 5. 列表筛选 ==")
        client.post("/api/notes", json={"title": "快速排序", "topic_id": topic_id,
                                        "markdown": "## 划分\n\n- 选主元\n"})
        r = client.get(f"/api/notes?topic_ids={topic_id}")
        check("按主题筛选命中 3 条", len(r.json()["items"]) == 3, str(len(r.json()["items"])))
        r = client.get("/api/notes?status=draft")
        check("按状态筛选草稿 2 条", len(r.json()["items"]) == 2, str(len(r.json()["items"])))
        r = client.get(f"/api/notes?tag_ids={tag_id}")
        check("按标签筛选 1 条", len(r.json()["items"]) == 1, str(len(r.json()["items"])))

        print("\n== 6. 全文搜索（中文单字）==")
        r = client.get("/api/notes?keyword=主元")
        check("多字命中", len(r.json()["items"]) == 1, str(r.json()["items"]))
        excerpt = r.json()["items"][0]["excerpt"]
        check("摘要含命中词（不从头截）", "主元" in excerpt, excerpt)
        check("摘要无 Markdown 残留", not any(ch in excerpt for ch in "#|`*"), excerpt)
        r = client.get("/api/notes?keyword=分")
        hits = [item["title"] for item in r.json()["items"]]
        check("单字命中二分查找", "二分查找" in hits, str(hits))
        r = client.get("/api/notes?keyword=不存在的词")
        check("无命中返回空", r.json()["items"] == [])
        r = client.get("/api/notes?keyword=二分")
        check("标题命中排在前面", r.json()["items"][0]["title"] == "二分查找")

        print("\n== 7. 输出清洗（不需要真模型）==")
        from app.services import note_service

        allowed = {"c1": {"document_id": "d1", "snippet": "片段一"}, "c2": {"document_id": "d1", "snippet": "片段二"}}
        raw = (
            "# 第 1 章 测试\n\n"
            "## 小节\n\n"
            "- 有出处的要点 [[chunk:c1]]\n"
            "- 编造出处的要点 [[chunk:fake]]\n\n"
            "这是一段没有出处的推断文字。\n\n"
            "```python\nprint(1)\n\nprint(2)\n```\n"
        )
        cleaned = note_service._assemble(raw, allowed)
        check("保留合法角标", "[[chunk:c1]]" in cleaned)
        check("剔除编造角标", "fake" not in cleaned)
        check("无出处段落标记 AI 补写", "没有出处的推断文字。 <!--ai-->" in cleaned, cleaned[-200:])
        check("代码块内不插标记", "print(1)\n\nprint(2)" in cleaned)
        sources = note_service._sources_from(cleaned, allowed)
        check("来源表只含合法片段", [s.chunk_id for s in sources] == ["c1"])
        check("AI 补写计数", cleaned.count("<!--ai-->") == 1, str(cleaned.count("<!--ai-->")))

        print("\n== 7.5 手工角标对齐（create / update 也认 [[chunk:id]]）==")
        from app.models import Chunk, Document
        from app.db import SessionLocal

        with SessionLocal() as session:
            session.merge(
                Document(
                    id="doc-smoke", name="冒烟资料", ext="txt", size=12, status="ready",
                    chunk_count=1, topic_id=topic_id,
                )
            )
            session.merge(
                Chunk(
                    id="chunk-smoke", document_id="doc-smoke", index=0,
                    text="这是真实片段的正文，用来做来源摘要。", topic_id=topic_id,
                )
            )
            session.commit()

        r = client.post(
            "/api/notes",
            json={
                "title": "带角标的手写笔记", "topic_id": topic_id,
                "markdown": "## 要点\n\n- 有真实出处 [[chunk:chunk-smoke]]\n- 编造的出处 [[chunk:no-such]]\n",
            },
        )
        cited = r.json()
        cite_id = cited["id"]
        check("创建时解析角标", len(cited["sources"]) == 1, str(len(cited["sources"])))
        check("来源指向真实片段", cited["sources"] and cited["sources"][0]["chunk_id"] == "chunk-smoke")
        check("来源摘要取片段正文", "真实片段" in (cited["sources"][0]["snippet"] if cited["sources"] else ""))
        check(
            "来源带文档名（不算已删除）",
            bool(cited["sources"]) and cited["sources"][0]["document_name"] == "冒烟资料"
            and cited["sources"][0]["missing"] is False,
            str(cited["sources"][0] if cited["sources"] else None),
        )
        check("死角标被剔除", "no-such" not in cited["markdown"], cited["markdown"][:120])

        r = client.patch(f"/api/notes/{cite_id}", json={"markdown": "## 改过\n\n- 仍引用 [[chunk:chunk-smoke]]\n"})
        check("编辑后来源保持", len(r.json()["sources"]) == 1, str(len(r.json()["sources"])))
        r = client.patch(f"/api/notes/{cite_id}", json={"markdown": "## 改过\n\n- 角标删掉了\n"})
        check("正文删角标 → 来源同步清空", r.json()["sources"] == [], str(r.json()["sources"]))
        r = client.patch(f"/api/notes/{cite_id}", json={"title": "只改标题"})
        check("只改标题不动来源", r.json()["sources"] == [])
        client.delete(f"/api/notes/{cite_id}")

        print("\n== 8. 删除语义 ==")
        r = client.delete(f"/api/notes/{note_id}")
        check("删除笔记", r.status_code == 200, str(r.status_code))
        r = client.get("/api/notes")
        check("删除后列表少一条", len(r.json()["items"]) == 2, str(len(r.json()["items"])))
        r = client.get(f"/api/notes?keyword=二分")
        check("删除后搜索不再命中", r.json()["items"] == [])

        print("\n== 9. 起草前置校验 ==")
        r = client.post("/api/notes/draft", json={"template": "outline", "topic_id": topic_id})
        check("无资料时拒绝起草", r.status_code == 400, str(r.status_code))
        r = client.post("/api/notes/draft", json={"template": "outline"})
        check("未选范围 → 400", r.status_code == 400, str(r.status_code))
        r = client.get("/api/notes/drafts/not-exist")
        check("任务不存在 → 404", r.status_code == 404, str(r.status_code))

        if want_draft:
            print("\n== 10. 真实起草（需要 LLM Key 与已索引资料）==")
            docs = client.get("/api/documents").json()
            ready = [d["id"] for d in docs if d["status"] == "ready"]
            if not ready:
                print("  [skip] 没有 ready 资料，先跑 smoke_m2.py --ingest 或自己导入")
            else:
                r = client.post(
                    "/api/notes/draft",
                    json={"template": "outline", "document_ids": ready[:1], "topic_id": topic_id,
                          "note_topic_id": topic_id},
                )
                check("起草任务已受理", r.status_code == 202, str(r.status_code))
                job_id = r.json()["id"]
                for _ in range(120):
                    info = client.get(f"/api/notes/drafts/{job_id}").json()
                    if info["state"] != "running":
                        break
                    time.sleep(1)
                check("起草成功", info["state"] == "done", info["message"])
                if info["note_id"]:
                    detail = client.get(f"/api/notes/{info['note_id']}").json()
                    check("草稿状态", detail["status"] == "draft")
                    check("有来源角标", len(detail["sources"]) >= 1, str(len(detail["sources"])))
                    check("正文非空", len(detail["markdown"]) > 100)

    print(f"\n结果：{len(PASSED)} 通过 / {len(FAILED)} 失败")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
