"""混合检索（向量 + 全文，RRF 融合）的冒烟测试。

要证明的不是"能检索到"，而是**第二路确实补上了向量漏掉的东西**：
用带有精确字面特征（编号、符号、英文缩写）的资料提问，这类内容向量化之后
往往就糊了，靠字面才能命中。

运行：
    cd backend
    .venv/Scripts/python.exe scripts/smoke_rag.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_tmp_dir = tempfile.mkdtemp(prefix="a4s_rag_")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp_dir) / 'smoke.db'}"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from app.main import app  # noqa: E402

PASSED = 0
FAILED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  [ok] {label}")
    else:
        FAILED += 1
        print(f"  [FAIL] {label} {detail}")


# 资料刻意混入：精确编号（CBC-128 / IV-0x1F）、英文缩写、纯语义描述。
# 前两类是向量检索的弱项，正好用来验证第二路的价值。
DOC_CRYPTO = """# 分组密码的三种工作模式

CBC-128 模式需要随机初始化向量。工程约定里，初始化向量的样本值记作 IV-0x1F，
每次加密都必须重新生成；如果复用同一个 IV-0x1F，相同明文会得到相同密文。

ECB 模式不使用初始化向量。它把明文切成固定长度的块分别加密，相同明文块
总是产出相同的密文块，因此会泄露明文的重复结构——一张图片加密后仍能看出轮廓。

GCM 是带认证的加密模式，它在加密的同时计算认证标签，可以检出密文被篡改。
"""

DOC_NET = """# TCP 拥塞控制

慢启动阶段，拥塞窗口每收到一个确认就加一，呈指数增长。
到达慢启动阈值 ssthresh 后转入拥塞避免，改为每个往返加一，呈线性增长。
发生超时重传时，ssthresh 减半，拥塞窗口回到一个报文段，重新进入慢启动。
"""


def wait_ready(client: TestClient, headers: dict, doc_id: str, timeout: float = 90.0) -> str:
    deadline = time.time() + timeout
    status = "pending"
    while time.time() < deadline:
        docs = client.get("/api/documents", headers=headers).json()
        for doc in docs:
            if doc["id"] == doc_id:
                status = doc["status"]
                if status in ("ready", "failed"):
                    return status
        time.sleep(0.5)
    return status


def main() -> int:
    with TestClient(app) as client:
        reg = client.post(
            "/api/auth/register",
            json={"username": "rag", "password": "secret123", "display_name": "RAG"},
        )
        assert reg.status_code == 201, reg.text
        h = {"Authorization": f"Bearer {reg.json()['token']}"}
        space_id = reg.json()["space"]["id"]

        print("\n== 1. 片段全文索引已建立 ==")
        from app.infrastructure.db.session import FTS_ENABLED, SessionLocal

        check("FTS5 可用", FTS_ENABLED)
        with SessionLocal() as db:
            exists = db.execute(
                text("select name from sqlite_master where type='table' and name='chunks_fts'")
            ).fetchone()
        check("chunks_fts 表存在", exists is not None)

        print("\n== 2. 导入两份资料 ==")
        topic = client.post("/api/topics", json={"name": "计算机", "kind": "topic"}, headers=h).json()
        ids = []
        for title, body in (("分组密码", DOC_CRYPTO), ("拥塞控制", DOC_NET)):
            r = client.post(
                "/api/documents/paste",
                json={"content": body, "title": title, "topic_id": topic["id"]},
                headers=h,
            )
            check(f"导入《{title}》", r.status_code == 201, r.text[:120])
            ids.append(r.json()["id"])

        for doc_id in ids:
            check(f"索引完成 {doc_id[:8]}", wait_ready(client, h, doc_id) == "ready")

        with SessionLocal() as db:
            indexed = db.execute(text("select count(*) from chunks_fts")).scalar()
            chunks = db.execute(text("select count(*) from chunks")).scalar()
        check("全文索引条数与片段数一致", indexed == chunks and chunks > 0, f"{indexed} vs {chunks}")

        print("\n== 3. 两路召回都工作 ==")
        from app.services import chat_service

        with SessionLocal() as db:
            citations, blocks, best = chat_service.retrieve(
                db, "ECB 模式为什么不用初始化向量", space_id=space_id
            )
        check("语义提问有引用", len(citations) >= 1, str(len(citations)))
        check("引用带 matched 标记", all("matched" in c for c in citations))
        check("引用带融合分", all("score" in c for c in citations))
        labels = {c["matched"] for c in citations}
        check("至少有一条是语义命中", labels & {"vector", "both"}, str(labels))

        print("\n== 4. 精确字面特征能被召回（向量的弱项）==")
        with SessionLocal() as db:
            raw = __import__(
                "app.infrastructure.db.fts", fromlist=["search_chunks"]
            ).search_chunks(db.connection(), "IV-0x1F", limit=10)
        check("全文路能命中 IV-0x1F", len(raw) >= 1, str(len(raw)))

        with SessionLocal() as db:
            citations, _, _ = chat_service.retrieve(db, "IV-0x1F 这个样本值是什么", space_id=space_id)
        check("融合后引用非空", len(citations) >= 1, str(len(citations)))
        hit_docs = {c["document_name"] for c in citations}
        check("命中正确的资料", "分组密码" in hit_docs, str(hit_docs))
        check(
            "该条被标为字面命中或双路命中",
            any(c["matched"] in ("keyword", "both") for c in citations),
            str([(c["matched"], c["vector_sim"]) for c in citations]),
        )

        print("\n== 5. 范围过滤对两路都生效 ==")
        net_only = ids[1]
        with SessionLocal() as db:
            cited, _, _ = chat_service.retrieve(
                db, "分组密码的初始化向量", space_id=space_id, document_ids=[net_only]
            )
        names = {c["document_name"] for c in cited}
        check("限定资料范围后不会捞到别的资料", names <= {"拥塞控制"}, str(names))

        print("\n== 6. 查不到时干净返回 ==")
        with SessionLocal() as db:
            empty, _, best2 = chat_service.retrieve(
                db, "量子色动力学的渐近自由", space_id=space_id
            )
        print(f"     （无关提问命中 {len(empty)} 条，best={best2:.3f}）")
        check("无关问题不会硬凑引用", len(empty) <= 2, str(len(empty)))

        print("\n== 7. 删除资料后索引同步清理 ==")
        client.delete(f"/api/documents/{ids[0]}", headers=h)
        with SessionLocal() as db:
            remain = db.execute(text("select count(*) from chunks_fts")).scalar()
            chunks_now = db.execute(text("select count(*) from chunks")).scalar()
        check("全文索引随片段一起删除", remain == chunks_now, f"{remain} vs {chunks_now}")

    print(f"\n{'=' * 46}")
    print(f"通过 {PASSED} 项，失败 {FAILED} 项")
    print(f"{'=' * 46}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
