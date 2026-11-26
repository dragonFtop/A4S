"""只读统计投影（read model）。

概览页需要跨多张表聚合，这属于"查询侧"职责，不适合塞进领域实体，
因此单独放在仓储层的只读实现里；接口层只消费返回值，不碰 ORM。

**所有聚合都限定在一个空间内**：概览显示的是"这个小组的学习情况"，
不是"整台机器上所有人的数据"。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.domain.models.entities import PracticeStatus
from shared.infrastructure.db.models import (
    DocumentORM,
    NoteORM,
    PracticeSessionORM,
    ReviewReportORM,
    TopicORM,
    WrongAnswerORM,
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


class StatsRepository:
    def __init__(self, db: Session, space_id: str) -> None:
        self.db = db
        self.space_id = space_id

    # ---- 内部工具 ----

    def _count(self, model, *clauses) -> int:
        stmt = select(func.count()).select_from(model).where(model.space_id == self.space_id)
        for clause in clauses:
            stmt = stmt.where(clause)
        return int(self.db.execute(stmt).scalar() or 0)

    def _scalar(self, stmt, default: float = 0.0) -> float:
        value = self.db.execute(stmt).scalar()
        return default if value is None else float(value)

    # ---- 对外 ----

    def summary(self) -> dict:
        now = _utcnow_naive()
        week_ago = now - timedelta(days=7)
        space = self.space_id

        chunk_total = int(
            self._scalar(
                select(func.coalesce(func.sum(DocumentORM.chunk_count), 0)).where(
                    DocumentORM.space_id == space
                )
            )
        )
        # 状态值以 PracticeStatus 为准（"submitted"）；早期这里误写成 "finished"，
        # 导致概览页的完成套数与平均正确率恒为 0。
        finished = PracticeSessionORM.status == PracticeStatus.SUBMITTED.value
        avg_score = self._scalar(
            select(func.avg(PracticeSessionORM.score)).where(
                PracticeSessionORM.space_id == space, finished
            ),
            default=0.0,
        )

        return {
            "documents": {
                "total": self._count(DocumentORM),
                "ready": self._count(DocumentORM, DocumentORM.status == "ready"),
                "failed": self._count(DocumentORM, DocumentORM.status == "failed"),
                "processing": self._count(
                    DocumentORM, DocumentORM.status.in_(("pending", "processing"))
                ),
                "chunks": chunk_total,
                "added_last_7d": self._count(DocumentORM, DocumentORM.created_at >= week_ago),
            },
            "practice": {
                "sessions": self._count(PracticeSessionORM),
                "finished": self._count(PracticeSessionORM, finished),
                "avg_score": round(avg_score, 4),
                "finished_last_7d": self._count(
                    PracticeSessionORM, finished, PracticeSessionORM.finished_at >= week_ago
                ),
            },
            "wrong": {
                "total": self._count(WrongAnswerORM),
                "added_last_7d": self._count(
                    WrongAnswerORM, WrongAnswerORM.last_wrong_at >= week_ago
                ),
                "top_repeated": int(
                    self._scalar(
                        select(func.count())
                        .select_from(WrongAnswerORM)
                        .where(
                            WrongAnswerORM.space_id == space,
                            WrongAnswerORM.wrong_count >= 2,
                        )
                    )
                ),
            },
            "review": {
                "reports": self._count(ReviewReportORM),
            },
            "notes": {
                "total": self._count(NoteORM),
                "draft": self._count(NoteORM, NoteORM.status == "draft"),
                "confirmed": self._count(NoteORM, NoteORM.status == "confirmed"),
            },
            "daily": self._daily(7),
            "recent_documents": self._recent_documents(5),
            "recent_wrong": self._recent_wrong(5),
            "recent_reports": self._recent_reports(3),
            "activity": self._activity(6),
            "generated_at": now,
        }

    def _daily(self, days: int) -> list[dict]:
        """近 N 天每日活跃度：导入资料数 + 完成练习数。

        刻意在 Python 侧聚合而非用 SQL 的日期函数——SQLite 与 PostgreSQL
        的日期截断方言不同，用 Python 可保证未来切库时行为一致。
        """
        start = (_utcnow_naive() - timedelta(days=days - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        buckets: dict[str, dict[str, int]] = {}
        for offset in range(days):
            key = (start + timedelta(days=offset)).strftime("%Y-%m-%d")
            buckets[key] = {"documents": 0, "practices": 0}

        for (created_at,) in self.db.execute(
            select(DocumentORM.created_at).where(
                DocumentORM.space_id == self.space_id,
                DocumentORM.created_at >= start,
            )
        ):
            key = _local_date_key(created_at)
            if key in buckets:
                buckets[key]["documents"] += 1

        for (finished_at,) in self.db.execute(
            select(PracticeSessionORM.finished_at).where(
                PracticeSessionORM.space_id == self.space_id,
                PracticeSessionORM.finished_at.is_not(None),
                PracticeSessionORM.finished_at >= start,
            )
        ):
            key = _local_date_key(finished_at)
            if key in buckets:
                buckets[key]["practices"] += 1

        return [{"date": key, **value} for key, value in buckets.items()]

    def _recent_documents(self, limit: int) -> list[dict]:
        rows = self.db.execute(
            select(DocumentORM, TopicORM)
            .outerjoin(TopicORM, TopicORM.id == DocumentORM.topic_id)
            .where(DocumentORM.space_id == self.space_id)
            .order_by(DocumentORM.created_at.desc())
            .limit(limit)
        ).all()
        return [
            {
                "id": doc.id,
                "name": doc.name,
                "ext": doc.ext,
                "status": doc.status,
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

    def _recent_wrong(self, limit: int) -> list[dict]:
        rows = self.db.execute(
            select(WrongAnswerORM)
            .where(WrongAnswerORM.space_id == self.space_id)
            .order_by(WrongAnswerORM.last_wrong_at.desc())
            .limit(limit)
        ).scalars()
        items = []
        for row in rows:
            question = row.question or {}
            items.append(
                {
                    "id": row.id,
                    "question_id": row.question_id,
                    "document_id": row.document_id,
                    "stem": question.get("stem", ""),
                    "qtype": question.get("qtype", ""),
                    "wrong_count": row.wrong_count,
                    "last_wrong_at": _iso(row.last_wrong_at),
                }
            )
        return items

    def _recent_reports(self, limit: int) -> list[dict]:
        rows = self.db.execute(
            select(ReviewReportORM)
            .where(ReviewReportORM.space_id == self.space_id)
            .order_by(ReviewReportORM.created_at.desc())
            .limit(limit)
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

    def _activity(self, limit: int) -> list[dict]:
        """把三类事件合成一条时间线，按时间倒序。"""
        events: list[dict] = []
        space = self.space_id

        for row in self.db.execute(
            select(DocumentORM)
            .where(DocumentORM.space_id == space)
            .order_by(DocumentORM.created_at.desc())
            .limit(limit)
        ).scalars():
            events.append(
                {
                    "kind": "document",
                    "at": _iso(row.created_at),
                    "title": f"导入资料「{row.name}」",
                    "detail": f"{row.chunk_count} 个知识片段",
                }
            )

        for row in self.db.execute(
            select(PracticeSessionORM)
            .where(
                PracticeSessionORM.space_id == space,
                PracticeSessionORM.finished_at.is_not(None),
            )
            .order_by(PracticeSessionORM.finished_at.desc())
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

        for row in self.db.execute(
            select(ReviewReportORM)
            .where(ReviewReportORM.space_id == space)
            .order_by(ReviewReportORM.created_at.desc())
            .limit(limit)
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
