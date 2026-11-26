"""联邦中继（P3）。

流程
----
1. 请求方 client 调 `POST /api/fed/query`，server 建一个"待领取"任务并**持住请求**；
2. 目标节点（另一个 client）在后台出站轮询 `POST /api/fed/claim`，领到属于它的任务；
3. 目标节点检索后把 citations 回传 `POST /api/fed/result`，server 唤醒挂起的请求；
4. 超时仍未回传 → 返回"来源离线"（C6：不排队、不补发）。

实现要点
--------
- 用**线程安全**的 `concurrent.futures.Future` + 锁，而不是 asyncio 原语：
  `/api/fed/query` 是普通 def（跑在 FastAPI 线程池），claim/result 也是 def，
  跨线程用 Event 会踩坑；Future 本身就支持跨线程 set_result / result(timeout)。
- 任务只对**指定目标节点**可领取（按 target_node_id 过滤），避免别的节点抢走。
- 任务表是**内存**的：server 重启即丢，符合"不做持久任务队列"的约束。
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import Future

_lock = threading.Lock()
# 待领取：task_id -> Task
_pending: dict[str, "Task"] = {}
# 已领取待回传：task_id -> Task
_claimed: dict[str, "Task"] = {}


class Task:
    __slots__ = ("id", "question", "target_node_id", "created_at", "future")

    def __init__(self, question: str, target_node_id: str) -> None:
        self.id = uuid.uuid4().hex
        self.question = question
        self.target_node_id = target_node_id
        self.created_at = time.time()
        self.future: Future[list] = Future()


def enqueue(question: str, target_node_id: str) -> Task:
    task = Task(question, target_node_id)
    with _lock:
        _pending[task.id] = task
    return task


def claim(node_id: str) -> Task | None:
    """目标节点领取一条属于它的任务；没有则返回 None。"""
    with _lock:
        for task_id, task in list(_pending.items()):
            if task.target_node_id == node_id:
                del _pending[task_id]
                _claimed[task_id] = task
                return task
    return None


def submit_result(task_id: str, citations: list) -> bool:
    """目标节点回传结果；任务存在且尚未完成时返回 True。"""
    with _lock:
        task = _claimed.pop(task_id, None) or _pending.pop(task_id, None)
    if task is None:
        return False
    if not task.future.done():
        task.future.set_result(citations)
    return True


def wait(task: Task, timeout: float) -> list | None:
    """挂起等待结果；超时返回 None（调用方据此返回 offline）。"""
    try:
        return task.future.result(timeout=max(0.0, timeout))
    except Exception:  # noqa: BLE001 - 超时/取消都等价于"没等到"
        return None
    finally:
        with _lock:
            _pending.pop(task.id, None)
            _claimed.pop(task.id, None)


def stats() -> dict[str, int]:
    with _lock:
        return {"pending": len(_pending), "claimed": len(_claimed)}
