"""账号 / 空间隔离 / 小组权限 的冒烟测试。

重点不是"功能能用"，而是**不该看见的东西确实看不见**：
跨空间取资料、非成员访问空间、非 owner 管理成员、移除唯一 owner，
这四条任一漏掉，整个多用户模型就是假的。

运行：
    cd backend
    .venv/Scripts/python.exe scripts/smoke_auth.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 独立临时库，不碰开发库（必须在 import app.* 之前设置）
_tmp_dir = tempfile.mkdtemp(prefix="a4s_auth_")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp_dir) / 'smoke.db'}"

from fastapi.testclient import TestClient  # noqa: E402
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


def register(client: TestClient, username: str, password: str = "secret123") -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "display_name": username},
    )
    assert r.status_code == 201, r.text
    return r.json()


def headers(auth: dict, space_id: str | None = None) -> dict:
    h = {"Authorization": f"Bearer {auth['token']}"}
    if space_id:
        h["X-Space-Id"] = space_id
    return h


def main() -> int:
    with TestClient(app) as client:
        print("\n== 1. 未登录一律拒绝 ==")
        for path in ("/api/documents", "/api/topics", "/api/overview", "/api/notes"):
            r = client.get(path)
            check(f"未登录访问 {path} → 401", r.status_code == 401, str(r.status_code))

        print("\n== 2. 注册即建个人空间 ==")
        alice = register(client, "alice")
        check("拿到 token", bool(alice["token"]))
        check("自动建了个人空间", alice["space"]["kind"] == "personal")
        check("自己是 owner", alice["space"]["my_role"] == "owner")
        ha = headers(alice)

        topics = client.get("/api/topics", headers=ha).json()
        check("空间内有「未分类」主题", any(t["is_builtin"] for t in topics["topics"]))

        bob = register(client, "bob")
        hb = headers(bob)
        check("bob 有独立的个人空间", bob["space"]["id"] != alice["space"]["id"])

        print("\n== 3. 跨空间隔离：资料互不可见 ==")
        tid = client.post("/api/topics", json={"name": "密码学", "kind": "topic"}, headers=ha).json()["id"]
        r = client.post(
            "/api/documents/paste",
            json={"content": "CBC 模式需要随机初始化向量，否则相同明文加密结果相同。", "title": "CBC 笔记", "topic_id": tid},
            headers=ha,
        )
        check("alice 粘贴资料成功", r.status_code == 201, r.text[:120])
        doc_id = r.json()["id"]

        a_docs = client.get("/api/documents", headers=ha).json()
        b_docs = client.get("/api/documents", headers=hb).json()
        check("alice 能看到自己的资料", len(a_docs) == 1, str(len(a_docs)))
        check("bob 看不到 alice 的资料", len(b_docs) == 0, str(len(b_docs)))

        r = client.get(f"/api/documents/{doc_id}/chunks", headers=hb)
        check("bob 按 id 直取 alice 的片段 → 404", r.status_code == 404, str(r.status_code))

        check("alice 的概览有资料", client.get("/api/overview", headers=ha).json()["documents"]["total"] == 1)
        check("bob 的概览为 0", client.get("/api/overview", headers=hb).json()["documents"]["total"] == 0)

        print("\n== 4. 学习小组：邀请后共享，之前看不到 ==")
        group = client.post("/api/spaces", json={"name": "密码学小组"}, headers=ha).json()
        check("建组成功", group["kind"] == "group")
        gid = group["id"]
        hg = headers(alice, gid)

        check("组内也有自己的未分类", any(t["is_builtin"] for t in client.get("/api/topics", headers=hg).json()["topics"]))
        check("组内看不到个人空间的资料", client.get("/api/documents", headers=hg).json() == [])

        r = client.post(f"/api/spaces/{gid}/members", json={"username": "bob"}, headers=ha)
        check("owner 能把 bob 加进来", r.status_code == 201, r.text[:120])

        r = client.get("/api/spaces", headers=hb)
        names = [s["name"] for s in r.json()]
        check("bob 的空间列表里出现该小组", "密码学小组" in names, str(names))
        check("bob 在组内是 member", any(s["my_role"] == "member" and s["id"] == gid for s in r.json()))

        print("\n== 5. 越权一律拒绝 ==")
        r = client.post(f"/api/spaces/{gid}/members", json={"username": "alice"}, headers=headers(bob, gid))
        check("普通成员不能加人 → 403", r.status_code == 403, str(r.status_code))

        carol = register(client, "carol")
        r = client.get("/api/documents", headers=headers(carol, gid))
        check("非成员指定该空间 → 403", r.status_code == 403, str(r.status_code))

        alice_id = alice["user"]["id"]
        r = client.delete(f"/api/spaces/{gid}/members/{alice_id}", headers=ha)
        check("不能移除唯一 owner", r.status_code == 400, r.text[:120])
        r = client.patch(f"/api/spaces/{gid}/members/{alice_id}", json={"role": "member"}, headers=ha)
        check("不能降级唯一 owner", r.status_code == 400, r.text[:120])

        r = client.delete(f"/api/spaces/{gid}", headers=headers(bob, gid))
        check("非 owner 不能删小组 → 403", r.status_code == 403, str(r.status_code))

        print("\n== 6. 认证细节 ==")
        r = client.post("/api/auth/login", json={"username": "alice", "password": "wrong"})
        check("密码错误 → 401", r.status_code == 401, str(r.status_code))
        r = client.post("/api/auth/register", json={"username": "alice", "password": "secret123"})
        check("用户名重复 → 400", r.status_code == 400, str(r.status_code))
        r = client.post("/api/auth/register", json={"username": "ab", "password": "secret123"})
        check("用户名过短 → 4xx", 400 <= r.status_code < 500, str(r.status_code))

        r = client.post("/api/auth/logout", headers=ha)
        check("登出成功", r.status_code == 204, str(r.status_code))
        r = client.get("/api/auth/me", headers=ha)
        check("登出后 token 失效 → 401", r.status_code == 401, str(r.status_code))

    print(f"\n{'=' * 46}")
    print(f"通过 {PASSED} 项，失败 {FAILED} 项")
    print(f"{'=' * 46}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
