"""联邦执行中继接口（P3 / FR-3.1 ~ FR-3.5）。

只挂在服务端：客户端不 import 本模块（C1 红线），通过 HTTP 与之交互。
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.federation import relay
from shared.config import settings
from shared.infrastructure.db.models import FedAuditORM, FedNodeORM
from shared.infrastructure.db.session import get_db
from shared.schemas.fed import (
    AuditListOut,
    AuditOut,
    ClaimIn,
    ClaimOut,
    FedCitation,
    NodeListOut,
    NodeOut,
    NodeRegisterIn,
    QueryIn,
    QueryOut,
    ResultIn,
    TaskOut,
)

router = APIRouter(prefix="/fed", tags=["federation"])


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _online(last_seen: datetime | None) -> bool:
    if last_seen is None:
        return False
    return (_now() - last_seen) <= timedelta(seconds=settings.fed_node_online_seconds)


def _audit(
    db: Session,
    *,
    requester: str,
    target: str,
    question: str,
    hits: int,
    status: str,
    latency_ms: int,
) -> None:
    db.add(
        FedAuditORM(
            id=uuid.uuid4().hex,
            requester_node_id=requester,
            target_node_id=target,
            question=question[:2000],
            hits=hits,
            status=status,
            latency_ms=latency_ms,
            created_at=_now(),
        )
    )
    db.commit()


def _used_today(db: Session, requester: str) -> int:
    """今天该请求方已成功使用的联邦次数（用于配额）。"""
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    return int(
        db.execute(
            select(func.count(FedAuditORM.id)).where(
                FedAuditORM.requester_node_id == requester,
                FedAuditORM.status == "ok",
                FedAuditORM.created_at >= start,
            )
        ).scalar()
        or 0
    )


# ---------- 节点登记 / 在线状态 ----------


@router.post("/register", response_model=NodeOut)
def register(payload: NodeRegisterIn, db: Session = Depends(get_db)) -> NodeOut:
    """节点心跳注册（客户端周期性调用；同 node_id 走 upsert）。"""
    row = db.get(FedNodeORM, payload.node_id)
    if row is None:
        row = FedNodeORM(
            node_id=payload.node_id,
            name=payload.name,
            serving_space_id=payload.serving_space_id,
            last_seen=_now(),
            created_at=_now(),
        )
        db.add(row)
    else:
        row.name = payload.name or row.name
        if payload.serving_space_id is not None:
            row.serving_space_id = payload.serving_space_id
        row.last_seen = _now()
    db.commit()
    return NodeOut(
        node_id=row.node_id,
        name=row.name,
        serving_space_id=row.serving_space_id,
        online=True,
        last_seen=row.last_seen,
    )


@router.get("/nodes", response_model=NodeListOut)
def list_nodes(db: Session = Depends(get_db)) -> NodeListOut:
    rows = list(db.execute(select(FedNodeORM).order_by(FedNodeORM.last_seen.desc())).scalars())
    items = [
        NodeOut(
            node_id=r.node_id,
            name=r.name,
            serving_space_id=r.serving_space_id,
            online=_online(r.last_seen),
            last_seen=r.last_seen,
        )
        for r in rows
    ]
    return NodeListOut(items=items, total=len(items))


# ---------- 目标节点：出站领取 + 回传结果 ----------


@router.post("/claim", response_model=ClaimOut)
def claim(payload: ClaimIn, db: Session = Depends(get_db)) -> ClaimOut:
    """目标节点轮询领取属于它的任务。顺带刷新心跳，免得被误判离线。"""
    row = db.get(FedNodeORM, payload.node_id)
    if row is not None:
        row.last_seen = _now()
        db.commit()
    task = relay.claim(payload.node_id)
    if task is None:
        return ClaimOut(task=None)
    return ClaimOut(task=TaskOut(id=task.id, question=task.question))


@router.post("/result")
def submit_result(payload: ResultIn) -> dict:
    """目标节点回传检索结果（只含 citations）。server 唤醒挂起的请求方。"""
    citations = [c.model_dump() for c in payload.citations]
    ok = relay.submit_result(payload.task_id, citations)
    return {"ok": ok}


# ---------- 请求方：发起联邦查询（服务端持住等待） ----------


@router.post("/query", response_model=QueryOut)
def query(payload: QueryIn, db: Session = Depends(get_db)) -> QueryOut:
    """请求方发起联邦检索。

    - 配额超限 → 直接 quota_exceeded；
    - 否则建任务并**持住**最多 fed_hold_seconds 等目标节点回传；
    - 超时未回传 → offline（不排队、不补发，C6）；
    - 回传成功 → ok + citations（只含文本/出处，C7）。
    """
    started = time.perf_counter()
    requester = payload.requester_node_id or "unknown"

    if _used_today(db, requester) >= settings.fed_daily_quota:
        latency = int((time.perf_counter() - started) * 1000)
        _audit(
            db,
            requester=requester,
            target=payload.target_node_id,
            question=payload.question,
            hits=0,
            status="quota_exceeded",
            latency_ms=latency,
        )
        return QueryOut(
            status="quota_exceeded",
            target_node_id=payload.target_node_id,
            citations=[],
            latency_ms=latency,
        )

    timeout = settings.fed_hold_seconds
    if payload.timeout_seconds is not None:
        timeout = max(0.0, min(float(payload.timeout_seconds), 60.0))

    task = relay.enqueue(payload.question, payload.target_node_id)
    result = relay.wait(task, timeout)
    latency = int((time.perf_counter() - started) * 1000)

    if result is None:
        _audit(
            db,
            requester=requester,
            target=payload.target_node_id,
            question=payload.question,
            hits=0,
            status="offline",
            latency_ms=latency,
        )
        return QueryOut(
            status="offline",
            target_node_id=payload.target_node_id,
            citations=[],
            latency_ms=latency,
        )

    citations = [FedCitation(**c) for c in result]
    _audit(
        db,
        requester=requester,
        target=payload.target_node_id,
        question=payload.question,
        hits=len(citations),
        status="ok",
        latency_ms=latency,
    )
    return QueryOut(
        status="ok",
        target_node_id=payload.target_node_id,
        citations=citations,
        latency_ms=latency,
    )


@router.get("/audit", response_model=AuditListOut)
def list_audit(limit: int = Query(default=50, ge=1, le=500), db: Session = Depends(get_db)) -> AuditListOut:
    rows = list(
        db.execute(
            select(FedAuditORM).order_by(FedAuditORM.created_at.desc()).limit(limit)
        ).scalars()
    )
    items = [
        AuditOut(
            id=r.id,
            requester_node_id=r.requester_node_id,
            target_node_id=r.target_node_id,
            hits=r.hits,
            status=r.status,
            latency_ms=r.latency_ms,
            created_at=r.created_at,
        )
        for r in rows
    ]
    return AuditListOut(items=items, total=len(items))
