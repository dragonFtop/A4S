"""向量索引维护：状态检测与重建。

职责边界
--------
只回答两个问题：**当前索引还能不能用**、**要重建该怎么重建**。
不碰解析、不碰切分、不碰主题归属——那些属于 ingest_service。

为什么重建不重新解析原文件
--------------------------
片段正文已经在 `chunks` 表里，重新解析会引入新的切分差异（切分器改过一次就会
对不上），还会把用户改过的主题归属冲掉。所以重建只做「拿现有正文重新算向量」，
既快又不会改变既有数据语义。

维度以实测为准
--------------
配置里的 `embedding_dimension` 是手填的，容易和模型真实输出不一致。重建时先算一条
探针向量，用真实维度回写配置，再按真实维度建表——配置应当描述事实，而不是反过来。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.config_store import save_settings
from app.db import VEC_ENABLED, SessionLocal, get_vector_store
from app.llm import build_embedding
from app.store import Chunks

logger = logging.getLogger(__name__)

EMBED_BATCH = 32
PAGE_SIZE = 200
PROBE_TEXT = "A4S 索引重建探针"

_LOCK = threading.Lock()
_JOB: dict[str, Any] = {
    "state": "idle",  # idle | running | done | failed
    "total": 0,
    "done": 0,
    "message": "",
    "started_at": None,
    "finished_at": None,
}


class RebuildError(RuntimeError):
    """重建前提不满足（向量扩展不可用、正在重建等）。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_job(**changes: Any) -> None:
    with _LOCK:
        _JOB.update(changes)


def _job_snapshot() -> dict[str, Any]:
    with _LOCK:
        return dict(_JOB)


# ---------- 状态 ----------


def index_status(db) -> dict[str, Any]:
    """索引与配置的对照视图。`needs_rebuild` 为真时检索结果不可信。"""
    store = get_vector_store()
    index_dimension = store.index_dimension() if VEC_ENABLED else None
    vector_count = store.count() if VEC_ENABLED else 0
    chunk_count = Chunks(db).count()

    configured = settings.embedding_dimension
    # 还没有索引表（空库首启）不算不一致：下一次入库会按配置建表
    dimension_ok = index_dimension is None or index_dimension == configured
    count_ok = vector_count == chunk_count
    job = _job_snapshot()
    running = job["state"] == "running"

    return {
        "enabled": VEC_ENABLED,
        "provider": settings.embedding_provider,
        "model": settings.embedding_model,
        "index_dimension": index_dimension,
        "configured_dimension": configured,
        "compatible": dimension_ok,
        "chunk_count": chunk_count,
        "vector_count": vector_count,
        "needs_rebuild": VEC_ENABLED and (not dimension_ok or not count_ok),
        "reason": _reason(VEC_ENABLED, dimension_ok, count_ok, index_dimension),
        "job": job,
        "can_rebuild": VEC_ENABLED and not running,
    }


def _reason(enabled: bool, dimension_ok: bool, count_ok: bool, index_dimension: int | None) -> str:
    if not enabled:
        return "当前未启用 sqlite-vec，向量检索不可用"
    if not dimension_ok:
        return (
            f"索引维度 {index_dimension} 与当前配置 {settings.embedding_dimension} 不一致，"
            "检索结果不可用"
        )
    if not count_ok:
        return "向量条数与片段数不一致，部分内容检索不到"
    return "索引与配置一致"


# ---------- 重建 ----------


def start_rebuild() -> dict[str, Any]:
    """标记任务开始并返回快照。真正的活在 `run_rebuild` 里，由调用方后台执行。"""
    if not VEC_ENABLED:
        raise RebuildError("当前环境未启用 sqlite-vec，无法重建索引")

    with _LOCK:
        if _JOB["state"] == "running":
            raise RebuildError("已有重建任务在进行中")
        _JOB.update(
            state="running",
            total=0,
            done=0,
            message="正在准备…",
            started_at=_now_iso(),
            finished_at=None,
        )
    return _job_snapshot()


def run_rebuild() -> None:
    """后台任务入口。自己开 Session，不跨线程复用请求的会话。"""
    db = SessionLocal()
    try:
        embedder = build_embedding()

        probe = embedder.embed_one(PROBE_TEXT)
        real_dimension = len(probe)
        if real_dimension != settings.embedding_dimension:
            save_settings({"embedding_dimension": real_dimension})
            logger.info(
                "Embedding 实际维度 %s 与配置不一致，已回写配置", real_dimension
            )

        # 维度可能刚被改动，重新取实例（会按新维度重建单例）
        store = get_vector_store()
        store.reset()

        repo = Chunks(db)
        total = repo.count()
        _set_job(total=total, message=f"正在重新向量化 {total} 个片段…")

        offset = 0
        done = 0
        while True:
            page = repo.list_all(limit=PAGE_SIZE, offset=offset)
            if not page:
                break
            for start in range(0, len(page), EMBED_BATCH):
                part = page[start : start + EMBED_BATCH]
                vectors = embedder.embed([chunk.text for chunk in part])
                store.add(
                    [(chunk.id, vector, chunk.topic_id) for chunk, vector in zip(part, vectors)]
                )
                done += len(part)
                _set_job(done=done)
            offset += len(page)

        _set_job(
            state="done",
            done=done,
            total=max(total, done),
            message=f"共 {done} 个片段，维度 {real_dimension}",
            finished_at=_now_iso(),
        )
        logger.info("向量索引重建完成：%s 个片段，维度 %s", done, real_dimension)
    except Exception as exc:  # noqa: BLE001 - 失败原因要原样给到界面
        message = str(exc) or exc.__class__.__name__
        logger.exception("向量索引重建失败")
        _set_job(state="failed", message=message[:500], finished_at=_now_iso())
    finally:
        db.close()
