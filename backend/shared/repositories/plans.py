"""学习计划仓储（M7）。

与其它仓储一样，只负责「怎么存」；计划的生成过程在 services/plan_agent.py。
列表页不读 markdown 全文也能工作，但本地 SQLite 取 20 份几 KB 的正文没有成本，
所以不做额外的投影——少一层映射就少一处不一致。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.domain.models.entities import StudyPlan
from shared.infrastructure.db.models import StudyPlanORM


def _to_plan(row: StudyPlanORM) -> StudyPlan:
    return StudyPlan(
        id=row.id,
        markdown=row.markdown or "",
        goal=row.goal or "",
        meta=row.meta or {},
        created_at=row.created_at,
        user_id=row.user_id,
        space_id=row.space_id,
    )


class SqlAlchemyStudyPlanRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def save(self, plan: StudyPlan) -> StudyPlan:
        row = StudyPlanORM(
            id=plan.id,
            goal=plan.goal,
            markdown=plan.markdown,
            meta=plan.meta,
            user_id=plan.user_id,
            space_id=plan.space_id,
            created_at=plan.created_at,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return _to_plan(row)

    def list(self, space_id: str, limit: int = 20) -> list[StudyPlan]:
        rows = (
            self.db.execute(
                select(StudyPlanORM)
                .where(StudyPlanORM.space_id == space_id)
                .order_by(StudyPlanORM.created_at.desc())
                .limit(max(1, min(limit, 100)))
            )
            .scalars()
            .all()
        )
        return [_to_plan(row) for row in rows]

    def get(self, plan_id: str, space_id: str) -> StudyPlan | None:
        row = self.db.get(StudyPlanORM, plan_id)
        if row is None or row.space_id != space_id:
            return None
        return _to_plan(row)

    def update_meta(self, plan_id: str, extra: dict, space_id: str) -> None:
        """补充过程字段（耗时、步数）。

        计划正文落库发生在循环内部，那一刻还不知道总耗时；
        事后补写比在保存时编一个数更诚实。
        """
        row = self.db.get(StudyPlanORM, plan_id)
        if row is None or row.space_id != space_id:
            return
        row.meta = {**(row.meta or {}), **extra}
        self.db.commit()

    def delete(self, plan_id: str, space_id: str) -> bool:
        row = self.db.get(StudyPlanORM, plan_id)
        if row is None or row.space_id != space_id:
            return False
        self.db.delete(row)
        self.db.commit()
        return True
