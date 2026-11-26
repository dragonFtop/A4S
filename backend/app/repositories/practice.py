"""练习卷仓储：题目与作答以 JSON 快照存取，不依赖题库表。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models.entities import AnswerRecord, PracticeSession, PracticeStatus
from app.infrastructure.db.models import PracticeSessionORM
from app.repositories._converters import question_from_dict, question_to_dict


def _to_practice_session(row: PracticeSessionORM) -> PracticeSession:
    return PracticeSession(
        id=row.id,
        questions=[question_from_dict(item) for item in (row.questions or [])],
        answers=[
            AnswerRecord(
                question_id=item.get("question_id", ""),
                response=item.get("response", ""),
                is_correct=bool(item.get("is_correct")),
                duration_seconds=float(item.get("duration_seconds") or 0.0),
            )
            for item in (row.answers or [])
        ],
        status=PracticeStatus(row.status),
        score=row.score,
        started_at=row.started_at,
        finished_at=row.finished_at,
        user_id=row.user_id,
        meta=dict(row.meta or {}),
        space_id=row.space_id,
    )


class SqlAlchemyPracticeRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def save(self, session: PracticeSession) -> None:
        row = self.db.get(PracticeSessionORM, session.id)
        questions = [question_to_dict(question) for question in session.questions]
        answers = [
            {
                "question_id": answer.question_id,
                "response": answer.response,
                "is_correct": answer.is_correct,
                "duration_seconds": answer.duration_seconds,
            }
            for answer in session.answers
        ]
        if row is None:
            row = PracticeSessionORM(
                id=session.id,
                status=session.status.value,
                score=session.score,
                questions=questions,
                answers=answers,
                meta=session.meta,
                user_id=session.user_id,
                space_id=session.space_id,
                started_at=session.started_at,
                finished_at=session.finished_at,
            )
            self.db.add(row)
        else:
            if row.space_id != session.space_id:
                return
            row.status = session.status.value
            row.score = session.score
            row.questions = questions
            row.answers = answers
            row.meta = session.meta
            row.finished_at = session.finished_at
        self.db.commit()

    def get(self, session_id: str, space_id: str) -> PracticeSession | None:
        row = self.db.get(PracticeSessionORM, session_id)
        if row is None or row.space_id != space_id:
            return None
        return _to_practice_session(row)

    def list_recent(self, space_id: str, limit: int = 50) -> list[PracticeSession]:
        rows = self.db.execute(
            select(PracticeSessionORM)
            .where(PracticeSessionORM.space_id == space_id)
            .order_by(PracticeSessionORM.started_at.desc())
            .limit(limit)
        ).scalars()
        return [_to_practice_session(row) for row in rows]

    def delete(self, session_id: str, space_id: str) -> None:
        row = self.db.get(PracticeSessionORM, session_id)
        if row is not None and row.space_id == space_id:
            self.db.delete(row)
            self.db.commit()
