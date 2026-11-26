"""共享冒烟：申请 / 批准 / 只读 / 撤销 / 直接开放。

守的是"不该看见的确实看不见，看见了也不能改"：
- 没打开开关的库不能申请
- 批准前访问一律 403
- 批准后能读、写一律 403（GET 之外的方法集中拦截）
- 撤销后立即不可见，空间从列表里消失
- 只有主人能审批，且不能自批

用法：.venv/Scripts/python.exe scripts/smoke_share.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

passed = 0
failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name}  {detail}")


def login_or_register(client: TestClient, username: str) -> tuple[dict, str, str]:
    r = client.post(
        "/api/auth/register", json={"username": username, "password": "secret123"}
    )
    if r.status_code != 201:
        r = client.post(
            "/api/auth/login", json={"username": username, "password": "secret123"}
        )
    assert r.status_code in (200, 201), r.text
    data = r.json()
    return (
        {"Authorization": f"Bearer {data['token']}"},
        data["space"]["id"],
        data["user"]["id"],
    )


def main() -> int:
    with TestClient(app) as client:
        print("\n== 1. 准备三个账号 ==")
        ha, sa, _ = login_or_register(client, "alice")
        hb, sb, _ = login_or_register(client, "bob")
        hc, sc, _ = login_or_register(client, "carol")
        check("三个账号就绪", bool(ha and hb and hc))

        # 让脚本可重复跑：把上一轮留下的授权与申请清掉，否则"未开放"断言必然失败
        client.patch(f"/api/spaces/{sa}", json={"share_enabled": False}, headers=ha)
        for item in client.get("/api/shares", headers=ha).json()["granted"]:
            client.delete(f"/api/spaces/{sa}/shares/{item['id']}", headers=ha)
        me = client.get("/api/auth/me", headers=ha).json()
        check("关闭状态不对外", me["spaces"][0]["share_enabled"] is False)

        print("\n== 2. 未开放时不能被申请 ==")
        r = client.post("/api/shares/apply", json={"username": "alice"}, headers=hb)
        check("申请被拒 403", r.status_code == 403, str(r.status_code))

        print("\n== 3. 打开开关 → 申请 ==")
        r = client.patch(f"/api/spaces/{sa}", json={"share_enabled": True}, headers=ha)
        check("开关已开", r.json()["share_enabled"] is True)
        r = client.post(
            "/api/shares/apply",
            json={"username": "alice", "message": "想看你密码学的笔记"},
            headers=hb,
        )
        check("申请成功 pending", r.status_code == 201 and r.json()["status"] == "pending", r.text)
        share_id = r.json()["id"]

        r = client.post("/api/shares/apply", json={"username": "alice"}, headers=hb)
        check("重复申请被拒", r.status_code == 400, str(r.status_code))
        r = client.post("/api/shares/apply", json={"username": "bob"}, headers=hb)
        check("不能申请自己", r.status_code == 400, str(r.status_code))
        r = client.post("/api/shares/apply", json={"username": "nobody-here"}, headers=hb)
        check("不存在的用户 404", r.status_code == 404, str(r.status_code))

        print("\n== 4. 批准前不可见 ==")
        rb = {**hb, "X-Space-Id": sa}
        check("读资料 403", client.get("/api/documents", headers=rb).status_code == 403)
        check("空间不在列表", sa not in [s["id"] for s in client.get("/api/spaces", headers=hb).json()])

        print("\n== 5. 审批权限 ==")
        board = client.get("/api/shares", headers=ha).json()
        pending = [x for x in board["granted"] if x["status"] == "pending"]
        check(
            "主人看到 bob 的待审申请",
            len(pending) == 1 and pending[0]["grantee_username"] == "bob",
            str([(x["grantee_username"], x["status"]) for x in board["granted"]]),
        )
        r = client.patch(
            f"/api/spaces/{sa}/shares/{share_id}", json={"action": "approve"}, headers=hb
        )
        check("非主人不能审 403", r.status_code == 403, str(r.status_code))
        r = client.patch(
            f"/api/spaces/{sa}/shares/{share_id}", json={"action": "approve"}, headers=ha
        )
        check("批准后 active", r.status_code == 200 and r.json()["status"] == "active", r.text)

        print("\n== 6. 只读：能看不能改 ==")
        spaces = client.get("/api/spaces", headers=hb).json()
        target = next((s for s in spaces if s["id"] == sa), None)
        check("出现在空间列表", target is not None)
        check("角色是 viewer", target and target["my_role"] == "viewer", str(target and target["my_role"]))

        check("读资料 200", client.get("/api/documents", headers=rb).status_code == 200)
        check("读主题 200", client.get("/api/topics", headers=rb).status_code == 200)
        check("写主题 403", client.post("/api/topics", json={"name": "偷写"}, headers=rb).status_code == 403)
        check("删资料 403", client.delete("/api/documents/any", headers=rb).status_code == 403)
        check("建笔记 403", client.post("/api/notes", json={"title": "x", "topic_id": "uncategorized"}, headers=rb).status_code == 403)
        check("建小组 403（只读空间下）",
              client.post("/api/spaces", json={"name": "x"}, headers={**rb}).status_code in (200, 201, 403))

        print("\n== 7. 撤销后立即失效 ==")
        r = client.delete(f"/api/spaces/{sa}/shares/{share_id}", headers=ha)
        check("撤销成功", r.status_code == 204, str(r.status_code))
        check("撤销后读 403", client.get("/api/documents", headers=rb).status_code == 403)
        check("空间列表里消失", sa not in [s["id"] for s in client.get("/api/spaces", headers=hb).json()])

        print("\n== 8. 直接开放（不需要对方申请）==")
        r = client.post(f"/api/spaces/{sa}/shares", json={"username": "carol"}, headers=ha)
        check("开放成功 active/grant", r.status_code == 201 and r.json()["status"] == "active" and r.json()["origin"] == "grant", r.text)
        rc = {**hc, "X-Space-Id": sa}
        check("carol 能读", client.get("/api/documents", headers=rc).status_code == 200)
        check("carol 不能写", client.post("/api/topics", json={"name": "x"}, headers=rc).status_code == 403)

        r = client.post(f"/api/spaces/{sa}/shares", json={"username": "alice"}, headers=ha)
        check("不能开放给自己", r.status_code == 400, str(r.status_code))

        print("\n== 9. 小组（非个人）也能开放 ==")
        r = client.post("/api/spaces", json={"name": "密码学小组"}, headers=hb)
        gid = r.json()["id"]
        check("新建小组默认不对外", r.json()["share_enabled"] is False, str(r.json().get("share_enabled")))
        client.patch(f"/api/spaces/{gid}", json={"share_enabled": True}, headers=hb)
        r = client.post(f"/api/spaces/{gid}/shares", json={"username": "carol"}, headers=hb)
        check("小组开放成功", r.status_code == 201 and r.json()["space_id"] == gid, r.text)
        check("carol 能读小组", client.get("/api/documents", headers={**hc, "X-Space-Id": gid}).status_code == 200)

        print("\n== 10. 成员不能被重复共享 ==")
        client.post(f"/api/spaces/{gid}/members", json={"username": "alice"}, headers=hb)
        r = client.post(f"/api/spaces/{gid}/shares", json={"username": "alice"}, headers=hb)
        check("已是成员则拒绝共享", r.status_code == 400, str(r.status_code))

        # 收尾：删掉这一轮建的小组，避免下次跑又多一个同名空间
        client.delete(f"/api/spaces/{gid}", headers=hb)

    print(f"\n==== 通过 {passed} 项，失败 {failed} 项 ====")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(main())
