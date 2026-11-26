"""M2 冒烟测试：主题标签 + 导入链路。

用法（在 backend/ 下）：
    .venv/Scripts/python.exe scripts/smoke_m2.py            # 只测不依赖 embedding 的部分
    .venv/Scripts/python.exe scripts/smoke_m2.py --ingest   # 追加真实入库（首次会下载 BGE 模型）

使用独立数据库 data/test_m2.db，不会污染开发库。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TEST_DB = BACKEND / "data" / "test_m2.db"
os.environ["DATABASE_URL"] = "sqlite:///./data/test_m2.db"

# 必须在 import app 之前清库，否则 alembic 会跳过建表
if TEST_DB.exists():
    TEST_DB.unlink()

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app
from scripts._auth import login  # noqa: E402

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
    with TestClient(app) as client:
        login(client)
        print("\n== 1. 内置主题与预置包 ==")
        r = client.get("/api/topics")
        body = r.json()
        check("初始只有「未分类」", len(body["topics"]) == 1, str(body["topics"]))
        check("「未分类」标记为内置", body["topics"][0]["is_builtin"] is True)

        r = client.get("/api/topics/seeded")
        check("零主题时 seeded=false", r.json()["seeded"] is False)

        r = client.get("/api/topics/packs")
        check("预置包 9 个", len(r.json()) == 9, str(len(r.json())))

        r = client.post("/api/topics/packs", json={"pack_ids": ["cs-core", "ai-llm"]})
        check("首次套用包返回 200", r.status_code == 200, str(r.status_code))
        result = r.json()
        check("建了 8 个计算机主题", result["created_topics"] == 18, str(result))
        check("建了 5 个默认标签", result["created_tags"] == 5, str(result))

        r = client.post("/api/topics/packs", json={"pack_ids": ["cs-core"]})
        check("重复套用幂等（不新增）", r.json()["created_topics"] == 0, str(r.json()))

        r = client.get("/api/topics/seeded")
        check("有主题后 seeded=true", r.json()["seeded"] is True)

        print("\n== 2. 主题 CRUD 与约束 ==")
        topics = client.get("/api/topics").json()["topics"]
        os_topic = next(t for t in topics if t["name"] == "操作系统")
        crypto = client.post("/api/topics", json={"name": "密码学"}).json()
        check("新建主题成功", crypto.get("count") == 0, str(crypto))

        r = client.post("/api/topics", json={"name": "密码学"})
        check("重名主题返回 409", r.status_code == 409, str(r.status_code))

        r = client.post("/api/topics", json={"name": "密码学", "kind": "tag"})
        check("同名不同 kind 允许（主题与标签各自唯一）", r.status_code == 201, str(r.status_code))

        r = client.patch(f"/api/topics/{crypto['id']}", json={"name": "现代密码学"})
        check("重命名成功", r.json()["name"] == "现代密码学", str(r.json()))

        r = client.delete("/api/topics/uncategorized")
        check("删除「未分类」被拒 400", r.status_code == 400, str(r.status_code))

        print("\n== 3. 导入参数校验 ==")
        r = client.post(
            "/api/documents/paste",
            json={"content": "测试正文" * 10, "title": "t", "topic_id": "not-exist"},
        )
        check("主题不存在 → 400", r.status_code == 400, str(r.status_code))

        big = "字" * 100_001
        r = client.post("/api/documents/paste", json={"content": big, "topic_id": "uncategorized"})
        check("正文超 100k → 400", r.status_code == 400, str(r.status_code))

        r = client.post(
            "/api/documents/upload",
            files={"files": ("bad.exe", b"x" * 10, "application/octet-stream")},
            data={"topic_id": "uncategorized"},
        )
        check("不支持的扩展名进 errors", len(r.json()["errors"]) == 1, str(r.json()))

        print("\n== 4. 粘贴导入（不等待索引）==")
        text = "数据结构中，栈是后进先出的线性表。队列是先进先出。\n\n" * 40
        r = client.post(
            "/api/documents/paste",
            json={"content": text, "title": "数据结构速记", "topic_id": os_topic["id"]},
        )
        check("粘贴创建成功 201", r.status_code == 201, str(r.status_code))
        pasted = r.json()
        check("归属主题正确", pasted["topic"]["id"] == os_topic["id"], str(pasted.get("topic")))
        check("初始状态为 pending", pasted["status"] in ("pending", "processing"), pasted["status"])

        r = client.get("/api/documents", params={"topic_ids": os_topic["id"]})
        check("按主题筛选命中", len(r.json()) == 1, str(len(r.json())))

        r = client.get("/api/documents", params={"topic_ids": crypto["id"]})
        check("其他主题筛选为空", len(r.json()) == 0, str(len(r.json())))

        if "--ingest" in sys.argv:
            print("\n== 5. 真实入库（含 BGE 向量化）==")
            doc_id = pasted["id"]
            deadline = time.time() + 600
            status = "pending"
            while time.time() < deadline:
                docs = client.get("/api/documents").json()
                target = next((d for d in docs if d["id"] == doc_id), None)
                if target:
                    status = target["status"]
                    if status in ("ready", "failed"):
                        break
                time.sleep(2)

            check("最终状态 ready", status == "ready", f"实际 {status}")
            detail = client.get(f"/api/documents/{doc_id}/chunks").json()
            check("产生了片段", len(detail) > 0, str(len(detail)))
            if detail:
                check("首片有正文", len(detail[0]["text"]) > 0)

            stats = client.get("/api/documents/stats").json()
            check("统计里 chunk 数 > 0", stats["chunks"] > 0, str(stats))

            # 改主题：验证分区迁移
            r = client.post(
                "/api/documents/batch",
                json={"document_ids": [doc_id], "topic_id": crypto["id"]},
            )
            check("批量改主题成功", r.status_code == 200, str(r.status_code))
            r = client.get("/api/documents", params={"topic_ids": crypto["id"]})
            check("改后在新主题下可见", len(r.json()) == 1, str(len(r.json())))

            # 删主题：资料应迁到「未分类」
            r = client.delete(f"/api/topics/{crypto['id']}")
            check("删主题返回迁移数 1", r.json().get("moved_documents") == 1, str(r.json()))

            # 删资料：向量应清零
            before = client.get("/api/documents/stats").json()["chunks"]
            r = client.delete(f"/api/documents/{doc_id}")
            check("删资料成功", r.status_code == 200, str(r.status_code))
            after = client.get("/api/documents/stats").json()["chunks"]
            check("删后 chunk 数归零", after == 0, f"{before} -> {after}")

    print(f"\n结果：{len(PASSED)} 通过 / {len(FAILED)} 失败")
    if FAILED:
        for name in FAILED:
            print("  失败：", name)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
