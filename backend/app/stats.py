"""概览统计（只读投影）。

概览页要跨多张表聚合，属于"查询侧"职责，不塞进实体，也不单独占一层：
这里的每个函数都只做"查库 → 算数"，调用方 `stats.summary(db)` 一句话。

数字一律在这里算完，接口层和 Agent 工具只消费返回值，不自己加减乘除。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Document,
    Note,
    PracticeSession,
    PracticeStatus,
    ReviewReport,
    Topic,
    WrongAnswer,
)


def _utcnow_naive() -> datetime:
    """SQLite 的 DateTime 列不带时区，统一用"朴素 UTC"比较，避免 aware/naive 混用报错。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: datetime | None) -> str | None:
    """输出带时区的 ISO 字符串。

    裸 naive ISO（"2026-11-24T11:51:00"）会被浏览器当成**本地时间**解析，
    导致"刚导入的资料显示 8 小时前"。这里补上 UTC 偏移量。
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _local_date_key(value: datetime | None) -> str:
    """把库里的朴素 UTC 时间转成本地日期键（YYYY-MM-DD），用于按天分桶。"""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().strftime("%Y-%m-%d")


def _count(db: Session, model, *clauses) -> int:
    stmt = select(func.count()).select_from(model)
    for clause in clauses:
        stmt = stmt.where(clause)
    return int(db.execute(stmt).scalar() or 0)


def _scalar(db: Session, stmt, default: float = 0.0) -> float:
    value = db.execute(stmt).scalar()
    return default if value is None else float(value)


def summary(db: Session) -> dict:
    now = _utcnow_naive()
    week_ago = now - timedelta(days=7)

    chunk_total = int(_scalar(db, select(func.coalesce(func.sum(Document.chunk_count), 0))))
    # 状态值以 PracticeStatus 为准（"submitted"）；早期这里误写成 "finished"，
    # 导致概览页的完成套数与平均正确率恒为 0。
    finished = PracticeSession.status == PracticeStatus.SUBMITTED
    avg_score = _scalar(db, select(func.avg(PracticeSession.score)).where(finished), default=0.0)

    return {
        "documents": {
            "total": _count(db, Document),
            "ready": _count(db, Document, Document.status == "ready"),
            "failed": _count(db, Document, Document.status == "failed"),
            "processing": _count(db, Document, Document.status.in_(("pending", "processing"))),
            "chunks": chunk_total,
            "added_last_7d": _count(db, Document, Document.created_at >= week_ago),
        },
        "practice": {
            "sessions": _count(db, PracticeSession),
            "finished": _count(db, PracticeSession, finished),
            "avg_score": round(avg_score, 4),
            "finished_last_7d": _count(
                db, PracticeSession, finished, PracticeSession.finished_at >= week_ago
            ),
        },
        "wrong": {
            "total": _count(db, WrongAnswer),
            "added_last_7d": _count(db, WrongAnswer, WrongAnswer.last_wrong_at >= week_ago),
            "top_repeated": int(
                _scalar(
                    db,
                    select(func.count())
                    .select_from(WrongAnswer)
                    .where(WrongAnswer.wrong_count >= 2),
                )
            ),
        },
        "review": {
            "reports": _count(db, ReviewReport),
        },
        "notes": {
            "total": _count(db, Note),
            "draft": _count(db, Note, Note.status == "draft"),
            "confirmed": _count(db, Note, Note.status == "confirmed"),
        },
        "daily": _daily(db, 7),
        "recent_documents": _recent_documents(db, 5),
        "recent_wrong": _recent_wrong(db, 5),
        "recent_reports": _recent_reports(db, 3),
        "activity": _activity(db, 6),
        "generated_at": now,
    }


def _daily(db: Session, days: int) -> list[dict]:
    """近 N 天每日活跃度：导入资料数 + 完成练习数。

    在 Python 侧分桶而不是用 SQL 日期函数：时区换算在两边不一样，
    放到 Python 里能保证"今天"就是用户看到的今天。
    """
    start = (_utcnow_naive() - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    buckets: dict[str, dict[str, int]] = {}
    for offset in range(days):
        key = (start + timedelta(days=offset)).strftime("%Y-%m-%d")
        buckets[key] = {"documents": 0, "practices": 0}

    for (created_at,) in db.execute(
        select(Document.created_at).where(Document.created_at >= start)
    ):
        key = _local_date_key(created_at)
        if key in buckets:
            buckets[key]["documents"] += 1

    for (finished_at,) in db.execute(
        select(PracticeSession.finished_at).where(
            PracticeSession.finished_at.is_not(None),
            PracticeSession.finished_at >= start,
        )
    ):
        key = _local_date_key(finished_at)
        if key in buckets:
            buckets[key]["practices"] += 1

    return [{"date": key, **value} for key, value in buckets.items()]


def _recent_documents(db: Session, limit: int) -> list[dict]:
    rows = db.execute(
        select(Document, Topic)
        .outerjoin(Topic, Topic.id == Document.topic_id)
        .order_by(Document.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": doc.id,
            "name": doc.name,
            "ext": doc.ext,
            "status": doc.status.value,
            "chunk_count": doc.chunk_count,
            "created_at": _iso(doc.created_at),
            "topic": (
                {"id": topic.id, "name": topic.name, "color": topic.color}
                if topic is not None
                else {"id": doc.topic_id, "name": "未分类", "color": "#888780"}
            ),
        }
        for doc, topic in rows
    ]


def _recent_wrong(db: Session, limit: int) -> list[dict]:
    rows = db.execute(
        select(WrongAnswer).order_by(WrongAnswer.last_wrong_at.desc()).limit(limit)
    ).scalars()
    items = []
    for row in rows:
        question = row.question_json or {}
        items.append(
            {
                "id": row.id,
                "question_id": row.question_id,
                "document_id": row.document_id,
                "stem": question.get("stem", ""),
                "qtype": question.get("type", ""),
                "wrong_count": row.wrong_count,
                "last_wrong_at": _iso(row.last_wrong_at),
            }
        )
    return items


def _recent_reports(db: Session, limit: int) -> list[dict]:
    rows = db.execute(
        select(ReviewReport).order_by(ReviewReport.created_at.desc()).limit(limit)
    ).scalars()
    return [
        {
            "id": row.id,
            "window_days": row.window_days,
            "period_start": _iso(row.period_start),
            "period_end": _iso(row.period_end),
            "created_at": _iso(row.created_at),
        }
        for row in rows
    ]


def _activity(db: Session, limit: int) -> list[dict]:
    """把三类事件合成一条时间线，按时间倒序。"""
    events: list[dict] = []

    for row in db.execute(
        select(Document).order_by(Document.created_at.desc()).limit(limit)
    ).scalars():
        events.append(
            {
                "kind": "document",
                "at": _iso(row.created_at),
                "title": f"导入资料「{row.name}」",
                "detail": f"{row.chunk_count} 个知识片段",
            }
        )

    for row in db.execute(
        select(PracticeSession)
        .where(PracticeSession.finished_at.is_not(None))
        .order_by(PracticeSession.finished_at.desc())
        .limit(limit)
    ).scalars():
        events.append(
            {
                "kind": "practice",
                "at": _iso(row.finished_at),
                "title": "完成一次练习",
                # score 本身就是 0~100 的分值，之前又乘了 100，33.3 分会显示成 3330%
                "detail": f"得分 {round(row.score or 0)}%",
            }
        )

    for row in db.execute(
        select(ReviewReport).order_by(ReviewReport.created_at.desc()).limit(limit)
    ).scalars():
        events.append(
            {
                "kind": "review",
                "at": _iso(row.created_at),
                "title": "生成回顾报告",
                "detail": f"覆盖最近 {row.window_days} 天",
            }
        )

    events.sort(key=lambda e: e["at"] or "", reverse=True)
    return events[:limit]
