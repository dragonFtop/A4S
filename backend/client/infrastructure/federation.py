"""客户端联邦节点（P3 / FR-3.1 / FR-3.2）。

作为**目标节点**：
- 周期性向服务端心跳注册（node_id / serving_space），server 据此判定在线；
- 周期性**出站轮询**领取属于本节点的联邦检索任务（不要求公网 IP）；
- 领到任务后在本机 serving 空间做检索，只回传 citations（文本 + 出处 + 分数），
  **不生成答案、不返回向量**（C7）。

设计取舍
--------
- 用独立守护线程 + 短超时 urllib，而不是 asyncio：与 `scheduler.py` 的取舍一致，
  检索本身是同步的（BGE + sqlite-vec），放事件循环里会卡住问答接口。
- 服务端未配置（`SERVER_URL` 为空）时不启动，纯本地模式零副作用。
- 失败只记日志，不让线程崩掉。
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.request

from shared.config import settings

logger = logging.getLogger(__name__)

_stop = threading.Event()
_thread: threading.Thread | None = None
_node_name = "a4s-client"


def node_id() -> str:
    if settings.fed_node_id:
        return settings.fed_node_id
    return f"{socket.gethostname()}-{os.getpid()}"


def _base() -> str:
    return (settings.server_url or "").strip().rstrip("/")


def _post(path: str, body: dict, timeout: float = 5.0) -> dict | None:
    base = _base()
    if not base:
        return None
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
        logger.debug("联邦请求 %s 失败：%s", path, exc)
        return None


def _serving_space_id() -> str | None:
    """对外提供检索的空间：优先配置，否则选"有 ready 资料的空间"。"""
    if settings.fed_serving_space_id:
        return settings.fed_serving_space_id
    from sqlalchemy import select

    from shared.infrastructure.db.models import DocumentORM, SpaceORM
    from shared.infrastructure.db.session import SessionLocal

    with SessionLocal() as db:
        row = db.execute(
            select(DocumentORM.space_id)
            .where(DocumentORM.status == "ready")
            .order_by(DocumentORM.ready_at.desc())
            .limit(1)
        ).scalar()
        if row:
            return row
        return db.execute(select(SpaceORM.id).limit(1)).scalar()


def _heartbeat() -> None:
    _post(
        "/api/fed/register",
        {"node_id": node_id(), "name": _node_name, "serving_space_id": _serving_space_id()},
    )


def _handle_task(task: dict) -> None:
    """领取到的任务：本机检索 → 只回传 citations。"""
    from shared.infrastructure.db.session import SessionLocal
    from shared.services.retrieval import retrieve

    space_id = _serving_space_id()
    if not space_id:
        _post(
            "/api/fed/result",
            {"task_id": task["id"], "node_id": node_id(), "citations": []},
        )
        return

    with SessionLocal() as db:
        citations, _blocks, _best = retrieve(db, task["question"], space_id=space_id)

    # 只保留文本与出处 + 一个排序分，剔除 vector_sim 等内部字段（C7：不返向量）
    payload = [
        {
            "chunk_id": c["chunk_id"],
            "document_id": c.get("document_id"),
            "document_name": c.get("document_name"),
            "snippet": c["snippet"],
            "page": c.get("page"),
            "score": float(c.get("score") or 0.0),
        }
        for c in citations
    ]
    _post(
        "/api/fed/result",
        {"task_id": task["id"], "node_id": node_id(), "citations": payload},
    )


def _loop() -> None:
    last_beat = 0.0
    while not _stop.is_set():
        now = time.time()
        if now - last_beat >= settings.fed_heartbeat_interval:
            _heartbeat()
            last_beat = now
        try:
            claimed = _post("/api/fed/claim", {"node_id": node_id()}, timeout=3.0)
            task = (claimed or {}).get("task")
            if task:
                _handle_task(task)
        except Exception as exc:  # noqa: BLE001 - 守护线程不能因单次异常退出
            logger.debug("联邦轮询异常：%s", exc)
        _stop.wait(settings.fed_poll_interval)


def start() -> None:
    """启动后台联邦节点线程（未配置 server_url 时静默跳过）。"""
    global _thread
    if not _base():
        return
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="a4s-fed-node", daemon=True)
    _thread.start()
    logger.info("联邦节点已启动：node_id=%s server=%s", node_id(), _base())


def stop() -> None:
    _stop.set()
