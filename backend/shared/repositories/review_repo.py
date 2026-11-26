"""回顾报告的数据来源（FR-07）。

两个职责合在一个模块里，因为它们共享同一套「时间窗」口径：

1. `collect_window`：把一段时间内的新增资料、练习作答、错题聚合成结构化指标；
2. `SqlAlchemyReviewRepository`：报告本体的持久化。

为什么单独开文件而不塞进 `sqlalchemy_repo.py`：那份文件已经 800 行，
而这里查的是「跨表的时间窗投影」，读多写少，性质更接近 `stats_repo.py`。

口径约定（与练习模块保持一致，避免两处算出不同的正确率）
----------------------------------------------------------
- 正确率分母是**真实作答数**（answers 条数），不是题量——跳过的题不该拉低正确率；
- 只有 `submitted` 的练习计入窗口，草稿与进行中不算；
- 未分类资料/题目统一落到 `UNCATEGORIZED_ID`，报告里显示「未分类」而不是丢掉。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.domain.models.entities import (
    UNCATEGORIZED_ID,
    UNCATEGORIZED_NAME,
    PracticeStatus,
    ReviewReport,
)
from shared.infrastructure.db.models import (
    DocumentORM,
    PracticeSessionORM,
    ReviewReportORM,
    TopicORM,
)


def utcnow_naive() -> datetime:
    """SQLite 的 DateTime 列不带时区，统一用「朴素 UTC」比较。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_iso(value: datetime | None) -> str | None:
    """输出带时区的 ISO 字符串，避免浏览器把它当本地时间。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _accuracy(correct: int, answered: int) -> float:
    return round(correct / answered * 100, 1) if answered else 0.0


# ---------- 时间窗聚合 ----------


def _documents(db: Session, start: datetime, end: datetime, space_id: str) -> dict:
    """窗口内新增资料，按主题分组。"""
    rows = db.execute(
        select(DocumentORM, TopicORM)
        .outerjoin(TopicORM, TopicORM.id == DocumentORM.topic_id)
        .where(
            DocumentORM.space_id == space_id,
            DocumentORM.created_at >= start,
            DocumentORM.created_at <= end,
        )
        .order_by(DocumentORM.created_at.desc())
    ).all()

    groups: dict[str, dict] = {}
    for doc, topic in rows:
        key = doc.topic_id or UNCATEGORIZED_ID
        bucket = groups.setdefault(
            key,
            {
                "topic_id": key,
                "name": topic.name if topic else UNCATEGORIZED_NAME,
                "color": topic.color if topic else "#888780",
                "count": 0,
                "chunks": 0,
                "documents": [],
            },
        )
        bucket["count"] += 1
        bucket["chunks"] += doc.chunk_count or 0
        bucket["documents"].append(
            {
                "id": doc.id,
                "name": doc.name,
                "ext": doc.ext,
                "chunk_count": doc.chunk_count or 0,
                "status": doc.status,
            }
        )

    by_topic = sorted(groups.values(), key=lambda item: (-item["count"], item["name"]))
    return {
        "total": len(rows),
        "chunks": sum(doc.chunk_count or 0 for doc, _ in rows),
        "by_topic": by_topic,
    }


def _practice(db: Session, start: datetime, end: datetime, space_id: str) -> dict:
    """窗口内已提交练习，按主题与题型拆分。"""
    rows = list(
        db.execute(
            select(PracticeSessionORM)
            .where(
                PracticeSessionORM.space_id == space_id,
                PracticeSessionORM.status == PracticeStatus.SUBMITTED.value,
                PracticeSessionORM.finished_at.is_not(None),
                PracticeSessionORM.finished_at >= start,
                PracticeSessionORM.finished_at <= end,
            )
            .order_by(PracticeSessionORM.finished_at.desc())
        ).scalars()
    )

    topic_bucket: dict[str, dict] = {}
    type_bucket: dict[str, dict] = {}
    answered = correct = questions = 0
    duration = 0.0

    for row in rows:
        question_map = {item.get("id"): item for item in (row.questions or [])}
        duration += float((row.meta or {}).get("result", {}).get("duration_seconds") or 0)
        questions += len(row.questions or [])

        seen_topics: set[str] = set()
        for answer in row.answers or []:
            question = question_map.get(answer.get("question_id"))
            if question is None:
                # 题目被手动删除后作答记录会失去对应题，跳过而不是崩掉
                continue
            topic_id = question.get("topic_id") or UNCATEGORIZED_ID
            qtype = question.get("type") or "unknown"
            is_correct = bool(answer.get("is_correct"))

            answered += 1
            correct += 1 if is_correct else 0

            topic = topic_bucket.setdefault(
                topic_id,
                {
                    "topic_id": topic_id,
                    "name": UNCATEGORIZED_NAME,
                    "color": "#888780",
                    "answered": 0,
                    "correct": 0,
                    "sessions": 0,
                },
            )
            topic["answered"] += 1
            topic["correct"] += 1 if is_correct else 0
            if topic_id not in seen_topics:
                topic["sessions"] += 1
                seen_topics.add(topic_id)

            pattern = type_bucket.setdefault(
                qtype, {"type": qtype, "answered": 0, "correct": 0}
            )
            pattern["answered"] += 1
            pattern["correct"] += 1 if is_correct else 0

    # 主题名与颜色回填：练习快照里只有 topic_id，主题被删也要能显示
    topic_ids = [key for key in topic_bucket if key != UNCATEGORIZED_ID]
    if topic_ids:
        for topic in db.execute(
            select(TopicORM).where(
                TopicORM.id.in_(topic_ids), TopicORM.space_id == space_id
            )
        ).scalars():
            bucket = topic_bucket.get(topic.id)
            if bucket:
                bucket["name"] = topic.name
                bucket["color"] = topic.color

    for bucket in topic_bucket.values():
        bucket["accuracy"] = _accuracy(bucket["correct"], bucket["answered"])
    for bucket in type_bucket.values():
        bucket["accuracy"] = _accuracy(bucket["correct"], bucket["answered"])

    # 主题名可能属于已删除主题：练习快照里没存名字，只能标成「已删除主题」
    for key, bucket in topic_bucket.items():
        if key != UNCATEGORIZED_ID and bucket["name"] == UNCATEGORIZED_NAME:
            bucket["name"] = "已删除主题"

    return {
        "sessions": len(rows),
        "questions": questions,
        "answered": answered,
        "correct": correct,
        "accuracy": _accuracy(correct, answered),
        "duration_seconds": round(duration),
        "by_topic": sorted(
            topic_bucket.values(), key=lambda item: (-item["answered"], item["name"])
        ),
        "by_type": sorted(type_bucket.values(), key=lambda item: -item["answered"]),
    }


def collect_window(
    db: Session, *, window_days: int, space_id: str
) -> tuple[datetime, datetime, dict]:
    """聚合一整段时间窗，返回 (start, end, metrics)。

    metrics 是**快照**：报告存下来之后就与库解耦，日后删资料也不会让历史报告变空。
    """
    end = utcnow_naive()
    start = end - timedelta(days=window_days)

    documents = _documents(db, start, end, space_id)
    practice = _practice(db, start, end, space_id)

    return start, end, {
        "window_days": window_days,
        "space_id": space_id,
        "documents": documents,
        "practice": practice,
        "period": {"start": to_iso(start), "end": to_iso(end)},
    }


# ---------- 报告持久化 ----------


def _to_report(row: ReviewReportORM) -> ReviewReport:
    return ReviewReport(
        id=row.id,
        window_days=row.window_days,
        period_start=row.period_start,
        period_end=row.period_end,
        markdown=row.markdown,
        metrics=row.metrics or {},
        created_at=row.created_at,
        space_id=row.space_id,
    )


class SqlAlchemyReviewRepository:
    def __init__(self, db: Session, space_id: str) -> None:
        self.db = db
        self.space_id = space_id

    def save(self, report: ReviewReport) -> None:
        row = self.db.get(ReviewReportORM, report.id)
        if row is None:
            row = ReviewReportORM(
                id=report.id,
                window_days=report.window_days,
                period_start=report.period_start,
                period_end=report.period_end,
                markdown=report.markdown,
                metrics=report.metrics,
                space_id=report.space_id,
                created_at=report.created_at,
            )
            self.db.add(row)
        else:
            row.markdown = report.markdown
            row.metrics = report.metrics
        self.db.commit()

    def get(self, report_id: str) -> ReviewReport | None:
        row = self.db.get(ReviewReportORM, report_id)
        if row is None or row.space_id != self.space_id:
            return None
        return _to_report(row)

    def list(self, limit: int = 50) -> list[ReviewReport]:
        rows = self.db.execute(
            select(ReviewReportORM)
            .where(ReviewReportORM.space_id == self.space_id)
            .order_by(ReviewReportORM.created_at.desc())
            .limit(limit)
        ).scalars()
        return [_to_report(row) for row in rows]

    def latest(self) -> ReviewReport | None:
        row = (
            self.db.execute(
                select(ReviewReportORM)
                .where(ReviewReportORM.space_id == self.space_id)
                .order_by(ReviewReportORM.created_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        return _to_report(row) if row else None

    def delete(self, report_id: str) -> None:
        row = self.db.get(ReviewReportORM, report_id)
        if row is not None and row.space_id == self.space_id:
            self.db.delete(row)
            self.db.commit()

    def count(self) -> int:
        return int(
            self.db.execute(
                select(func.count())
                .select_from(ReviewReportORM)
                .where(ReviewReportORM.space_id == self.space_id)
            ).scalar()
            or 0
        )
