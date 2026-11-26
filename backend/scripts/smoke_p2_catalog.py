"""P2 公共目录 + Agent 选源 冒烟测试。

覆盖：
- Task 8 / AC-10: catalog 由 active 共享派生，出现/撤销随之增减；字段正确（资料数=ready 数）
- Task 9 / AC-11: catalog 仅组内（成员/被授权人）可见，组外为空
- Task 10 / AC-12: Agent 选源 本地→组内→联邦，来源数 ≤ 3；无命中回答"不知道"且不伪造引用

用法：.venv/Scripts/python.exe scripts/smoke_p2_catalog.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_tmp = tempfile.mkdtemp(prefix="a4s_p2_")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp) / 'smoke_p2.db'}"
os.environ.setdefault("APP_ROLE", "client")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from shared.config import settings  # noqa: E402
import client.services.chat_service as chat_service  # noqa: E402

DOC = (
    "# 分组密码 CBC\n\n"
    "分组密码 CBC 模式每次加密都必须重新生成随机初始化向量 IV，"
    "否则相同明文会得到相同密文，破坏语义安全。"
)

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


class FakeLLM:
    name = "fake"

    def __init__(self, **_kwargs) -> None:
        pass

    async def chat(self, messages, **options):  # noqa: ANN001, ARG002
        return "[1] 假回答"

    async def stream(self, messages, **options):  # noqa: ANN001, ARG002
        for piece in ("根据组内来源，", "CBC 需要随机 IV。", "[1]"):
            yield piece


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in text.split("\n\n"):
        name = None
        data: dict = {}
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name:
            events.append((name, data))
    return events


def reg(client, username):
    r = client.post("/api/auth/register", json={"username": username, "password": "secret123"})
    assert r.status_code == 201, r.text
    d = r.json()
    return {"Authorization": f"Bearer {d['token']}"}, d["space"]["id"], d["user"]["id"]


def make_group(client, headers, name):
    r = client.post("/api/spaces", json={"name": name}, headers=headers)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def enable_share(client, headers, gid):
    r = client.patch(f"/api/spaces/{gid}", json={"share_enabled": True}, headers=headers)
    assert r.status_code == 200, r.text


def add_member(client, headers, gid, username):
    r = client.post(f"/api/spaces/{gid}/members", json={"username": username}, headers=headers)
    assert r.status_code in (200, 201), r.text


def grant(client, headers, gid, username):
    r = client.post(f"/api/spaces/{gid}/shares", json={"username": username}, headers=headers)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def make_topic(client, headers, name):
    r = client.post("/api/topics", json={"name": name, "kind": "topic"}, headers=headers)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def paste(client, headers, topic_id, title, body=DOC):
    r = client.post(
        "/api/documents/paste",
        json={"content": body, "title": title, "topic_id": topic_id},
        headers=headers,
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def wait_ready(client, headers, doc_id, seconds=120.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        for d in client.get("/api/documents", headers=headers).json():
            if d["id"] == doc_id and d["status"] in ("ready", "failed"):
                return d["status"]
        time.sleep(0.5)
    return "timeout"


def catalog(client, headers):
    r = client.get("/api/catalog", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    return {e["space_id"]: e for e in body["items"]}, body["total"]


def main() -> int:
    with TestClient(app) as client:
        section("准备：owner(bob) / member(alice) / 被授权人(carol) / 组外(dave)")
        hb, sb, _ = reg(client, "bobowner")
        ha, sa, _ = reg(client, "aliceusr")
        hc, sc, _ = reg(client, "carolgrant")
        hd, sd, _ = reg(client, "daveout")
        ha_g = ha  # alice 查目录不需要空间头
        check("四个账号就绪", all([sb and sa and sc and sd]))

        g1 = make_group(client, hb, "密码学一组")
        enable_share(client, hb, g1)
        add_member(client, hb, g1, "aliceusr")
        topic_g1 = make_topic(client, {**hb, "X-Space-Id": g1}, "对称加密")
        doc1 = paste(client, {**hb, "X-Space-Id": g1}, topic_g1, "CBC 模式说明")
        check("组内资料就绪", wait_ready(client, {**hb, "X-Space-Id": g1}, doc1["id"]) == "ready")

        # ---- Task 8 / AC-10: 派生与生命周期 ----
        section("Task 8 / AC-10: catalog 由 active 共享派生")
        ids_before, total_before = catalog(client, ha_g)
        check("未产生 active 共享时目录无该行（仅开 share_enabled 不算）", g1 not in ids_before, str(ids_before))

        share_id = grant(client, hb, g1, "carolgrant")
        ids_after, _ = catalog(client, ha_g)
        check("产生 active 共享后成员可见该行", g1 in ids_after, str(ids_after))
        entry = ids_after.get(g1, {})
        check("字段非空：空间名/主人/主题/版本", all([
            entry.get("space_name"), entry.get("owner_name"), entry.get("topic"), entry.get("vector_version"),
        ]), str(entry))
        check("资料数=该空间 ready 文档数(=1)", entry.get("document_count") == 1, str(entry.get("document_count")))
        check("在线状态为占位 unknown", entry.get("online") == "unknown", str(entry.get("online")))

        # ---- Task 9 / AC-11: 仅组内可见 ----
        section("Task 9 / AC-11: catalog 仅组内可见")
        ids_outsider, total_out = catalog(client, hd)
        check("组外用户目录为空", ids_outsider == {} and total_out == 0, str(ids_outsider))
        ids_carol, _ = catalog(client, hc)
        check("被授权人（组外但被共享）可见该行", g1 in ids_carol, str(ids_carol))

        # 撤销后消失
        r = client.delete(f"/api/spaces/{g1}/shares/{share_id}", headers=hb)
        check("撤销共享 204", r.status_code == 204, str(r.status_code))
        ids_revoked, _ = catalog(client, ha_g)
        check("撤销后该行消失（成员也看不到）", g1 not in ids_revoked, str(ids_revoked))

        # ---- Task 10 / AC-12: Agent 选源 ----
        section("Task 10 / AC-12: Agent 选源 本地→组内→联邦，上限 3")
        # 重建 4 个共享生效的小组，alice 均为成员；只有 G1 有资料（决定 catalog 排序）
        groups = [g1]
        for idx in range(2, 5):
            gid = make_group(client, hb, f"密码学{idx}组")
            enable_share(client, hb, gid)
            add_member(client, hb, gid, "aliceusr")
            grant(client, hb, gid, "carolgrant")
            groups.append(gid)
        # 重新给 G1 开一条有效共享（前面撤销了）
        grant(client, hb, g1, "carolgrant")
        ids_multi, _ = catalog(client, ha_g)
        check("四个小组均在 alice 目录", all(g in ids_multi for g in groups), str(set(groups) - set(ids_multi)))

        settings.llm_api_key = "test-key"
        original_build = chat_service.build_llm
        chat_service.build_llm = lambda: FakeLLM()
        try:
            r = client.post(
                "/api/chat",
                json={"question": "CBC 模式每次加密是否需要重新生成初始化向量？", "multi_source": True},
                headers={"Authorization": ha["Authorization"], "X-Space-Id": sa},
            )
            events = parse_sse(r.text)
            names = [n for n, _ in events]
            check("有命中时先下发 sources", "sources" in names, str(names))
            sources = next(d for n, d in events if n == "sources")
            items = sources.get("items") or []
            plan = sources.get("plan") or []
            check("引用非空", len(items) >= 1, str(len(items)))
            check("来源数(含本地) ≤ 3", len(plan) <= 3, str(plan))
            check("首个来源是本地", plan and plan[0]["kind"] == "local", str(plan))
            kinds = [p["kind"] for p in plan]
            check("来源顺序符合 本地→组内→联邦", kinds == sorted(kinds, key=lambda k: {"local": 0, "group": 1, "fed": 2}[k]), str(kinds))
            check("实际用到组内来源", any(p["kind"] == "group" and p["hits"] >= 1 for p in plan), str(plan))
            group_cites = [c for c in items if c.get("source_kind") == "group"]
            check("引用带组内来源标记", len(group_cites) >= 1, str([c.get("source_kind") for c in items]))
            check("引用可定位到原始文档名", any(c.get("document_name") == "CBC 模式说明" for c in items),
                  str([c.get("document_name") for c in items]))

            # 无命中 → 不知道，不伪造引用
            r = client.post(
                "/api/chat",
                json={"question": "请证明黎曼猜想与量子色动力学的渐近自由关系", "multi_source": True},
                headers={"Authorization": ha["Authorization"], "X-Space-Id": sa},
            )
            events = parse_sse(r.text)
            names = [n for n, _ in events]
            check("无命中时不返回 sources", "sources" not in names, str(names))
            check("无命中时下发 no_context", "no_context" in names, str(names))
            detail = next((d.get("detail", "") for n, d in events if n == "no_context"), "")
            check("声明未找到", "没有找到" in detail, detail)
            answer = "".join(d.get("text", "") for n, d in events if n == "delta")
            check("回答为『不知道』且不含伪造引用编号", "不知道" in answer and "[1]" not in answer, answer)
        finally:
            chat_service.build_llm = original_build
            settings.llm_api_key = ""

    print(f"\n==== 通过 {PASSED} 项，失败 {FAILED} 项 ====")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
