"""P3 联邦执行 —— 本机三进程集成验收。

拓扑（全部 127.0.0.1）：
- server:8100   中继：节点登记 / 领取 / 回传 / 查询持住 / 配额审计
- client:8000   请求方：借 server 中继向目标节点发问，用自身重排 + 生成
- client2:8001  目标节点：持有知识库，出站轮询领取任务，只回传 citations

覆盖：
- Task 12 / AC-13: 节点注册 + 出站轮询（server 有在线节点记录）
- Task 13 / AC-14: 目标不在线时 ~15s 返回"来源离线"，不挂起
- Task 14 / AC-15: 目标只回 citations（无答案/向量），请求方重排后生成带引用答案
- Task 15 / AC-16: 联邦请求写入审计；超配额被拒

用法：.venv/Scripts/python.exe scripts/integration_p3_federation.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
PORT_SERVER = 8100
PORT_REQ = 8000
PORT_TGT = 8001
BASE_SERVER = f"http://127.0.0.1:{PORT_SERVER}"
BASE_REQ = f"http://127.0.0.1:{PORT_REQ}"
BASE_TGT = f"http://127.0.0.1:{PORT_TGT}"

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


def req(method: str, url: str, body: dict | None = None, token: str | None = None, timeout: float = 30.0):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8") or "{}"
            return resp.status, json.loads(raw) if raw.strip().startswith(("{", "[")) else raw
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "ignore")


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def wait_health(base: str, seconds: float = 45.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            status, _ = req("GET", f"{base}/api/health", timeout=2.0)
            if status == 200:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    return False


def spawn(module: str, port: int, env_overrides: dict) -> subprocess.Popen:
    env = os.environ.copy()
    env.update(env_overrides)
    log = open(Path(_TMP) / f"{module.replace('.', '_')}_{port}.log", "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", module, "--port", str(port), "--log-level", "warning"],
        cwd=str(BACKEND),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    proc._log = log  # type: ignore[attr-defined]
    return proc


def stop(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
    log = getattr(proc, "_log", None)
    if log is not None:
        try:
            log.close()
        except Exception:  # noqa: BLE001
            pass


_TMP = ""
PROCS: list[subprocess.Popen] = []


def main() -> int:
    global _TMP
    _TMP = tempfile.mkdtemp(prefix="a4s_p3_")
    db_server = f"sqlite:///{Path(_TMP) / 'server.db'}"
    db_req = f"sqlite:///{Path(_TMP) / 'client.db'}"
    db_tgt = f"sqlite:///{Path(_TMP) / 'client2.db'}"

    for p in (PORT_SERVER, PORT_REQ, PORT_TGT):
        if not port_free(p):
            print(f"端口 {p} 被占用，无法启动集成测试")
            return 1

    server_env = {
        "APP_ROLE": "server",
        "DATABASE_URL": db_server,
        "FED_DAILY_QUOTA": "2",
        "FED_HOLD_SECONDS": "15",
    }
    target_env = {
        "APP_ROLE": "client",
        "DATABASE_URL": db_tgt,
        "SERVER_URL": BASE_SERVER,
        "FED_NODE_ID": "node-target",
        "FED_POLL_INTERVAL": "0.5",
        "FED_HEARTBEAT_INTERVAL": "1.0",
    }
    requester_env = {
        "APP_ROLE": "client",
        "DATABASE_URL": db_req,
        "SERVER_URL": BASE_SERVER,
        "FED_NODE_ID": "node-requester",
        "FED_POLL_INTERVAL": "0.5",
        "FED_HEARTBEAT_INTERVAL": "1.0",
        "FED_DAILY_QUOTA": "2",
    }

    try:
        section("启动三进程")
        PROCS.append(spawn("server.main:app", PORT_SERVER, server_env))
        PROCS.append(spawn("client.main:app", PORT_TGT, target_env))
        PROCS.append(spawn("client.main:app", PORT_REQ, requester_env))
        check("server 健康", wait_health(BASE_SERVER))
        check("client2(目标) 健康", wait_health(BASE_TGT))
        check("client(请求方) 健康", wait_health(BASE_REQ))

        # ---- 目标节点准备资料 ----
        section("准备目标库并等待节点上线")
        st, tgt = req("POST", f"{BASE_TGT}/api/auth/register", {"username": "targetusr", "password": "secret123"})
        check("目标节点注册账号", st == 201, str(tgt)[:120])
        token_tgt = tgt["token"]
        topic = req("POST", f"{BASE_TGT}/api/topics", {"name": "联邦主题", "kind": "topic"}, token_tgt)[1]["id"]
        doc = req(
            "POST",
            f"{BASE_TGT}/api/documents/paste",
            {
                "content": "# 联邦资料\n\n联邦检索要求目标节点只返回引用片段，不返回答案与向量。",
                "title": "联邦资料",
                "topic_id": topic,
            },
            token_tgt,
        )[1]
        ready = False
        for _ in range(120):
            docs = req("GET", f"{BASE_TGT}/api/documents", token=token_tgt)[1]
            row = next((d for d in docs if d["id"] == doc["id"]), None)
            if row and row["status"] in ("ready", "failed"):
                ready = row["status"] == "ready"
                break
            time.sleep(0.5)
        check("目标资料就绪", ready)

        # 等待 server 出现目标节点在线且 serving_space 已登记
        node_ok = False
        for _ in range(60):
            nodes = req("GET", f"{BASE_SERVER}/api/fed/nodes")[1]["items"]
            target = next((n for n in nodes if n["node_id"] == "node-target"), None)
            if target and target["online"] and target.get("serving_space_id"):
                node_ok = True
                break
            time.sleep(0.5)
        check("Task12/AC-13: server 有目标节点在线记录且已登记 serving 空间", node_ok, str(nodes)[:200])

        # ---- Task 13 / AC-14: 离线超时 ----
        section("Task 13 / AC-14: 目标离线 ~15s 返回")
        t0 = time.time()
        st, q_off = req(
            "POST",
            f"{BASE_SERVER}/api/fed/query",
            {"target_node_id": "node-ghost", "question": "联邦检索返回什么？", "requester_node_id": "node-requester"},
            timeout=30.0,
        )
        elapsed = time.time() - t0
        check("离线返回 200", st == 200, str(q_off)[:120])
        check("离线状态 offline", q_off.get("status") == "offline", str(q_off)[:120])
        check("离线耗时 12–20s（不挂起）", 12.0 <= elapsed <= 20.5, f"{elapsed:.1f}s")

        # ---- Task 14 / AC-15: 只返 citations，无答案/向量 ----
        section("Task 14 / AC-15: 目标只返 citations")
        st, q_ok = req(
            "POST",
            f"{BASE_SERVER}/api/fed/query",
            {
                "target_node_id": "node-target",
                "question": "联邦检索时目标节点返回什么？",
                "requester_node_id": "node-requester",
            },
            timeout=30.0,
        )
        check("在线查询 200", st == 200, str(q_ok)[:120])
        check("在线状态 ok", q_ok.get("status") == "ok", str(q_ok)[:160])
        cites = q_ok.get("citations") or []
        check("在线命中 ≥1 条 citations", len(cites) >= 1, str(len(cites)))
        if cites:
            keys = set(cites[0].keys())
            check("citation 不含向量字段", "vector_sim" not in keys and "vector" not in keys, str(keys))
            check("citation 不含生成答案字段", "answer" not in keys and "text" not in keys, str(keys))
            check("citation 含片段与出处", bool(cites[0].get("snippet")), str(cites[0])[:160])

        # ---- 请求方重排 + 生成 ----
        section("Task 14 / AC-15: 请求方重排生成带引用答案")
        st, req_user = req("POST", f"{BASE_REQ}/api/auth/register", {"username": "requester", "password": "secret123"})
        check("请求方注册账号", st == 201, str(req_user)[:120])
        token_req = req_user["token"]
        t0 = time.time()
        st, ask = req(
            "POST",
            f"{BASE_REQ}/api/fed/ask",
            {"target_node_id": "node-target", "question": "联邦检索时目标节点返回什么？"},
            token_req,
            timeout=30.0,
        )
        ask_elapsed = time.time() - t0
        check("请求方联邦问答 200", st == 200, str(ask)[:160])
        check("请求方返回 ok", ask.get("status") == "ok", str(ask)[:160])
        check("请求方答案含 [1] 引用", "[1]" in (ask.get("answer") or ""), str(ask.get("answer"))[:120])
        check("请求方引用带序号", bool((ask.get("citations") or [{}])[0].get("index") == 1), str(ask.get("citations"))[:160])
        check("在线问答耗时远小于超时", ask_elapsed < 12.0, f"{ask_elapsed:.1f}s")

        # ---- Task 15 / AC-16: 配额 + 审计 ----
        section("Task 15 / AC-16: 配额与审计")
        st, audit = req("GET", f"{BASE_SERVER}/api/fed/audit?limit=20")
        items = audit.get("items") or []
        check("审计有记录", len(items) >= 3, str(len(items)))
        statuses = {i["status"] for i in items}
        check("审计含 ok 与 offline", {"ok", "offline"} <= statuses, str(statuses))
        check("审计含命中数/延迟", all("hits" in i and "latency_ms" in i for i in items))

        st, over = req(
            "POST",
            f"{BASE_SERVER}/api/fed/query",
            {"target_node_id": "node-target", "question": "再问一次", "requester_node_id": "node-requester"},
            timeout=10.0,
        )
        check("超配额被拒（quota_exceeded）", over.get("status") == "quota_exceeded", str(over)[:120])

        print(f"\n==== 通过 {PASSED} 项，失败 {FAILED} 项 ====")
        return 0 if FAILED == 0 else 1
    finally:
        for p in PROCS:
            stop(p)


if __name__ == "__main__":
    raise SystemExit(main())
