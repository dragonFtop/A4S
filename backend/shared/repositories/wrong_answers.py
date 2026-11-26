"""错题本仓储：一题一条，重复答错只累加次数。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.domain.models.entities import WrongAnswer
from shared.infrastructure.db.models import WrongAnswerORM
from shared.repositories._converters import question_from_dict, question_to_dict


def _to_wrong_answer(row: WrongAnswerORM) -> WrongAnswer:
    return WrongAnswer(
        question=question_from_dict(row.question or {}),
        id=row.id,
        wrong_count=row.wrong_count,
        first_wrong_at=row.first_wrong_at,
        last_wrong_at=row.last_wrong_at,
        topic_id=row.topic_id,
        document_id=row.document_id,
        last_response=row.last_response or "",
        space_id=row.space_id,
    )


class SqlAlchemyWrongAnswerRepository:
    """错题本。

    去重键选 question_id 而不是题干：同一道题在重练卷里 id 不变，重练答错才能累加次数。
    跨卷子重新生成的近似题会各算一条——机器无法可靠判断「是不是同一道题」，
    与其用文本相似度猜，不如让这个边界清楚。

    注意去重范围是**空间内**：两个小组各自有一道同 id 的题，各记各的。
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def upsert(self, wrong: WrongAnswer) -> WrongAnswer:
        row = self.db.execute(
            select(WrongAnswerORM).where(
                WrongAnswerORM.question_id == wrong.question.id,
                WrongAnswerORM.space_id == wrong.space_id,
            )
        ).scalar_one_or_none()

        if row is None:
            row = WrongAnswerORM(
                id=wrong.id or wrong.question.id,
                question_id=wrong.question.id,
                document_id=wrong.document_id,
                topic_id=wrong.topic_id,
                space_id=wrong.space_id,
                question=question_to_dict(wrong.question),
                wrong_count=max(1, wrong.wrong_count),
                last_response=wrong.last_response,
                first_wrong_at=wrong.first_wrong_at,
                last_wrong_at=wrong.last_wrong_at,
            )
            self.db.add(row)
        else:
            # 同一题再错：次数 +1，条目不动；快照以最新为准，题目被修正过时不该继续展示旧版
            row.wrong_count += 1
            row.last_wrong_at = wrong.last_wrong_at
            row.last_response = wrong.last_response
            row.question = question_to_dict(wrong.question)
            row.document_id = wrong.document_id
            row.topic_id = wrong.topic_id
        self.db.commit()
        self.db.refresh(row)
        return _to_wrong_answer(row)

    def list(
        self,
        space_id: str,
        *,
        topic_id: str | None = None,
        document_id: str | None = None,
        question_type: str | None = None,
        days: int | None = None,
        min_wrong: int | None = None,
        sort: str = "recent",
        limit: int = 200,
    ) -> list[WrongAnswer]:
        stmt = select(WrongAnswerORM).where(WrongAnswerORM.space_id == space_id)
        if topic_id:
            stmt = stmt.where(WrongAnswerORM.topic_id == topic_id)
        if document_id:
            stmt = stmt.where(WrongAnswerORM.document_id == document_id)
        if days:
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
            stmt = stmt.where(WrongAnswerORM.last_wrong_at >= cutoff)
        if min_wrong and min_wrong > 1:
            stmt = stmt.where(WrongAnswerORM.wrong_count >= min_wrong)
        order = (
            WrongAnswerORM.wrong_count.desc()
            if sort == "count"
            else WrongAnswerORM.last_wrong_at.desc()
        )
        stmt = stmt.order_by(order, WrongAnswerORM.first_wrong_at.desc()).limit(limit)
        items = [_to_wrong_answer(row) for row in self.db.execute(stmt).scalars()]
        if question_type:
            # 题型在 JSON 快照里，SQLite 的 JSON 查询不值得为这点数据量引入
            items = [item for item in items if item.question.type.value == question_type]
        return items

    def list_by_ids(self, ids: list[str], space_id: str) -> list[WrongAnswer]:
        if not ids:
            return []
        rows = self.db.execute(
            select(WrongAnswerORM).where(
                WrongAnswerORM.id.in_(ids), WrongAnswerORM.space_id == space_id
            )
        ).scalars()
        return [_to_wrong_answer(row) for row in rows]

    def get(self, wrong_id: str, space_id: str) -> WrongAnswer | None:
        row = self.db.get(WrongAnswerORM, wrong_id)
        if row is None or row.space_id != space_id:
            return None
        return _to_wrong_answer(row)

    def delete(self, wrong_id: str, space_id: str) -> bool:
        row = self.db.get(WrongAnswerORM, wrong_id)
        if row is None or row.space_id != space_id:
            return False
        self.db.delete(row)
        self.db.commit()
        return True

    def count(self, space_id: str) -> int:
        return int(
            self.db.execute(
                select(func.count())
                .select_from(WrongAnswerORM)
                .where(WrongAnswerORM.space_id == space_id)
            ).scalar_one()
        )
