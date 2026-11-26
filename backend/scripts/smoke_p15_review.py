"""P1.5 小组投稿与审核 冒烟测试。

覆盖：
- Task 3 / AC-2,3,4: member 投稿待审不向量化；owner 自传直入；个人空间不受影响
- Task 4 / AC-5,6:  owner 批准触发向量化；驳回带理由不入库；非 owner 403
- Task 5 / AC-7:  待审/驳回资料不参与检索与统计
- Task 6 / AC-8:  笔记投稿审核流程 + 作者归属

用法：.venv/Scripts/python.exe scripts/smoke_p15_review.py
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

_tmp = tempfile.mkdtemp(prefix="a4s_p15_")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp) / 'smoke_p15.db'}"
os.environ.setdefault("APP_ROLE", "client")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.main import app  # noqa: E402
from shared.infrastructure.db.session import SessionLocal  # noqa: E402
from shared.services.retrieval import retrieve  # noqa: E402

DOC = "分组密码 CBC-128 模式使用随机初始化向量 IV-0x1F，每次加密必须重新生成。"

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


def section(title: str) -> None:
    print(f"\n== {title} ==")


def reg(client, username):
    r = client.post("/api/auth/register", json={"username": username, "password": "secret123"})
    assert r.status_code == 201, r.text
    d = r.json()
    return {"Authorization": f"Bearer {d['token']}"}, d["space"]["id"], d["user"]["id"]


def make_topic(client, headers, name):
    return client.post("/api/topics", json={"name": name, "kind": "topic"}, headers=headers).json()["id"]


def paste(client, headers, topic_id, body=DOC, title="doc"):
    return client.post(
        "/api/documents/paste",
        json={"content": body, "title": title, "topic_id": topic_id},
        headers=headers,
    ).json()


def poll_status(client, headers, doc_id, seconds=90.0):
    deadline = time.time() + seconds
    last = None
    while time.time() < deadline:
        for d in client.get("/api/documents", headers=headers).json():
            if d["id"] == doc_id:
                last = d["status"]
                if last in ("ready", "failed"):
                    return last
        time.sleep(0.4)
    return last


def poll_until(client, headers, doc_id, target, seconds=90.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        for d in client.get("/api/documents", headers=headers).json():
            if d["id"] == doc_id and d["status"] == target:
                return True
        time.sleep(0.4)
    return False


def chunk_count(doc_id):
    with SessionLocal() as db:
        return db.execute(
            text("select count(*) from chunks where document_id=:d"), {"d": doc_id}
        ).scalar()


def fts_count():
    with SessionLocal() as db:
        try:
            return db.execute(text("select count(*) from chunks_fts")).scalar()
        except Exception:  # noqa: BLE001
            return None


def main() -> int:
    with TestClient(app) as client:
        section("准备：owner(bob) + member(alice) + GROUP 空间")
        hb, _, bob_id = reg(client, "bobowner")
        ha, sa, alice_id = reg(client, "aliceusr")
        g = client.post("/api/spaces", json={"name": "密码学小组"}, headers=hb).json()
        gid = g["id"]
        check("建组 kind=group", g.get("kind") == "group", str(g.get("kind")))
        r = client.post(f"/api/spaces/{gid}/members", json={"username": "aliceusr"}, headers=hb)
        check("alice 加入小组", r.status_code in (200, 201), r.text[:80])
        hb_g = {**hb, "X-Space-Id": gid}
        ha_g = {**ha, "X-Space-Id": gid}

        # ---- Task 3: 审核前不向量化 ----
        section("Task 3 / AC-2: member 投稿待审，不向量化")
        topic_m = make_topic(client, ha_g, "成员主题")
        doc = paste(client, ha_g, topic_m, title="成员投稿")
        check("member 投稿 status=pending_review", doc["status"] == "pending_review", doc["status"])
        check("4s 后仍 pending_review（未被索引）", poll_status(client, ha_g, doc["id"], 4.0) == "pending_review")
        check("chunks 表该文档行数=0", chunk_count(doc["id"]) == 0, str(chunk_count(doc["id"])))
        with SessionLocal() as db:
            cites, _, _ = retrieve(db, "CBC 模式初始化向量 IV-0x1F", space_id=gid)
        check("member 待审资料检索不到", len(cites) == 0, str(len(cites)))

        section("Task 3 / AC-3: owner 自传直入")
        topic_o = make_topic(client, hb_g, "主人主题")
        doc_o = paste(client, hb_g, topic_o, title="主人投稿")
        check("owner 投稿初始非 pending_review", doc_o["status"] != "pending_review", doc_o["status"])
        check("owner 投稿最终 ready", poll_until(client, hb_g, doc_o["id"], "ready"))
        check("owner 投稿 chunks>0", chunk_count(doc_o["id"]) > 0, str(chunk_count(doc_o["id"])))
        with SessionLocal() as db:
            cites, _, _ = retrieve(db, "CBC 模式初始化向量 IV-0x1F", space_id=gid)
        check("owner 资料可被检索", len(cites) >= 1, str(len(cites)))
        check("检索命中不含 member 待审资料", doc["id"] not in {c["document_id"] for c in cites})

        section("Task 3 / AC-4: 个人空间不受影响（回归）")
        topic_p = make_topic(client, ha, "个人主题")
        doc_p = paste(client, ha, topic_p, title="个人投稿")
        check("个人投稿最终 ready", poll_until(client, ha, doc_p["id"], "ready"))
        check("个人投稿 chunks>0", chunk_count(doc_p["id"]) > 0, str(chunk_count(doc_p["id"])))

        # ---- Task 4: 审核接口 ----
        section("Task 4 / AC-5: owner 批准触发向量化")
        pending = client.get(f"/api/spaces/{gid}/reviews/pending", headers=hb_g).json()
        check(
            "待审列表含 member 投稿",
            any(d["id"] == doc["id"] for d in pending),
            str([d["id"] for d in pending]),
        )
        pv = client.get(f"/api/documents/{doc['id']}/preview", headers=hb_g)
        check(
            "预览返回 200 且含原文",
            pv.status_code == 200 and "IV-0x1F" in pv.json().get("text", ""),
            pv.text[:80],
        )
        ap = client.post(f"/api/documents/{doc['id']}/approve", headers=hb_g)
        check(
            "批准返回 200 且 status=processing",
            ap.status_code == 200 and ap.json()["status"] == "processing",
            ap.text[:80],
        )
        check("批准后最终 ready", poll_until(client, hb_g, doc["id"], "ready"))
        check("批准后 chunks>0", chunk_count(doc["id"]) > 0, str(chunk_count(doc["id"])))
        with SessionLocal() as db:
            cites, _, _ = retrieve(db, "CBC 模式初始化向量 IV-0x1F", space_id=gid)
        check("批准后可被检索", any(c["document_id"] == doc["id"] for c in cites))

        section("Task 4 / AC-6: owner 驳回带理由不入库")
        doc_r = paste(client, ha_g, topic_m, title="待驳回")
        check("驳回用投稿待审", doc_r["status"] == "pending_review", doc_r["status"])
        rj = client.post(
            f"/api/documents/{doc_r['id']}/reject", json={"reason": "内容不相关"}, headers=hb_g
        )
        check(
            "驳回返回 200 且 status=rejected",
            rj.status_code == 200 and rj.json()["status"] == "rejected",
            rj.text[:80],
        )
        check("驳回后 chunks 仍为 0", chunk_count(doc_r["id"]) == 0, str(chunk_count(doc_r["id"])))
        rej_row = next(
            (d for d in client.get("/api/documents", headers=ha_g).json() if d["id"] == doc_r["id"]),
            None,
        )
        check(
            "驳回理由可查（error 字段）",
            rej_row is not None and rej_row.get("error") == "内容不相关",
            str(rej_row),
        )
        with SessionLocal() as db:
            cites, _, _ = retrieve(db, "CBC 模式初始化向量 IV-0x1F", space_id=gid)
        check("驳回资料检索不到", doc_r["id"] not in {c["document_id"] for c in cites})

        section("Task 4 / TR-4.3: 非 owner 调用审核接口 403")
        check(
            "member 查待审列表 403",
            client.get(f"/api/spaces/{gid}/reviews/pending", headers=ha_g).status_code == 403,
        )
        doc_x = paste(client, ha_g, topic_m, title="越权测试")
        check(
            "member 批准 403",
            client.post(f"/api/documents/{doc_x['id']}/approve", headers=ha_g).status_code == 403,
        )
        check(
            "member 驳回 403",
            client.post(
                f"/api/documents/{doc_x['id']}/reject", json={"reason": "x"}, headers=ha_g
            ).status_code
            == 403,
        )
        check(
            "member 预览 403",
            client.get(f"/api/documents/{doc_x['id']}/preview", headers=ha_g).status_code == 403,
        )

        # ---- Task 5: 待审/驳回不参与检索与统计 ----
        section("Task 5 / AC-7: 待审/驳回不参与检索与统计")
        # 小组现状：doc(approve→ready)、doc_o(owner→ready) 共 2 份 ready；
        # doc_r(rejected)、doc_x(pending_review) 不应计入 ready 统计。
        st = client.get("/api/documents/stats", headers=hb_g).json()
        check("stats.ready 不含待审/驳回（应为 2）", st.get("ready") == 2, str(st))
        # 显式 READY 过滤：把一份已有片段的 ready 资料强行置 rejected，
        # 验证检索不再返回它——证明不是靠"没有片段"侥幸排除，而是状态门控生效。
        doc_f = paste(client, hb_g, topic_o, title="强制驳回样本")
        check("强制驳回样本先 ready", poll_until(client, hb_g, doc_f["id"], "ready"))
        with SessionLocal() as db:
            cites_before, _, _ = retrieve(db, "CBC 模式初始化向量 IV-0x1F", space_id=gid)
        check("置 rejected 前可被检索", any(c["document_id"] == doc_f["id"] for c in cites_before))
        with SessionLocal() as db:
            db.execute(text("update documents set status='rejected' where id=:d"), {"d": doc_f["id"]})
            db.commit()
        with SessionLocal() as db:
            cites_after, _, _ = retrieve(db, "CBC 模式初始化向量 IV-0x1F", space_id=gid)
        check(
            "置 rejected 后检索不再返回（即使仍有片段）",
            doc_f["id"] not in {c["document_id"] for c in cites_after},
        )

        # ---- Task 6: 笔记投稿审核 + 作者归属 ----
        section("Task 6 / AC-8: 笔记投稿审核 + 作者归属")
        note_m = client.post(
            "/api/notes",
            json={"title": "成员笔记", "topic_id": topic_o, "markdown": "## 成员笔记\nCBC IV-0x1F"},
            headers=ha_g,
        ).json()
        check("member 小组笔记 status=pending_review", note_m["status"] == "pending_review", note_m["status"])
        check("member 小组笔记作者=alice", note_m["author_id"] == alice_id,
              f"{note_m.get('author_id')} != {alice_id}")
        listed = client.get("/api/notes", headers=hb_g).json()["items"]
        check("待审笔记不在 owner 小组库列表", note_m["id"] not in {n["id"] for n in listed})
        listed_m = client.get("/api/notes", headers=ha_g).json()["items"]
        check("待审笔记对 member 也不在列表", note_m["id"] not in {n["id"] for n in listed_m})
        # 关键词搜索也搜不到待审笔记
        kw = client.get("/api/notes?keyword=CBC", headers=ha_g).json()["items"]
        check("待审笔记关键词搜不到", note_m["id"] not in {n["id"] for n in kw})
        pending_notes = client.get(f"/api/spaces/{gid}/reviews/pending-notes", headers=hb_g).json()
        check("owner 待审笔记列表含成员投稿", note_m["id"] in {n["id"] for n in pending_notes},
              str([n["id"] for n in pending_notes]))
        pn = next(n for n in pending_notes if n["id"] == note_m["id"])
        check("待审笔记投影作者=alice", pn["author_id"] == alice_id, str(pn.get("author_id")))
        check("member 看待审笔记列表 403",
              client.get(f"/api/spaces/{gid}/reviews/pending-notes", headers=ha_g).status_code == 403)
        check("member 批准笔记 403",
              client.post(f"/api/notes/{note_m['id']}/approve", headers=ha_g).status_code == 403)
        check("member 驳回笔记 403",
              client.post(f"/api/notes/{note_m['id']}/reject", headers=ha_g).status_code == 403)
        check("member 用 /confirm 绕过审核被拒（400）",
              client.post(f"/api/notes/{note_m['id']}/confirm", headers=ha_g).status_code == 400)
        r = client.post(f"/api/notes/{note_m['id']}/approve", headers=hb_g)
        check("owner 批准笔记 200", r.status_code == 200, r.text[:80])
        check("批准后 status=confirmed", r.json()["status"] == "confirmed", r.json().get("status"))
        check("批准后作者仍=alice", r.json()["author_id"] == alice_id, r.json().get("author_id"))
        listed2 = client.get("/api/notes", headers=hb_g).json()["items"]
        check("批准后笔记进入小组库列表", note_m["id"] in {n["id"] for n in listed2})
        kw2 = client.get("/api/notes?keyword=CBC", headers=ha_g).json()["items"]
        check("批准后关键词可搜到", note_m["id"] in {n["id"] for n in kw2})
        # 驳回路径
        note_r = client.post(
            "/api/notes",
            json={"title": "驳回笔记", "topic_id": topic_o, "markdown": "## 驳回\n内容"},
            headers=ha_g,
        ).json()
        check("驳回用笔记初始 pending_review", note_r["status"] == "pending_review")
        rj = client.post(f"/api/notes/{note_r['id']}/reject", headers=hb_g)
        check("owner 驳回笔记 200", rj.status_code == 200, rj.text[:80])
        check("驳回后 status=rejected", rj.json()["status"] == "rejected", rj.json().get("status"))
        listed3 = client.get("/api/notes", headers=hb_g).json()["items"]
        check("驳回笔记不在小组库列表", note_r["id"] not in {n["id"] for n in listed3})
        pending_after = client.get(f"/api/spaces/{gid}/reviews/pending-notes", headers=hb_g).json()
        check("驳回后从待审列表消失", note_r["id"] not in {n["id"] for n in pending_after})
        # 个人空间笔记不受影响（回归）
        note_p = client.post(
            "/api/notes",
            json={"title": "个人笔记", "topic_id": topic_p, "markdown": "## 个人\n内容"},
            headers=ha,
        ).json()
        check("个人笔记 status=draft（非待审）", note_p["status"] == "draft", note_p["status"])
        listed_p = client.get("/api/notes", headers=ha).json()["items"]
        check("个人笔记在个人列表可见", note_p["id"] in {n["id"] for n in listed_p})

    print(f"\n==== 通过 {PASSED} 项，失败 {FAILED} 项 ====")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(main())
