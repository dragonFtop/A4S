"""学习计划接口（M7 规划 Agent）。

- `POST /api/plan`：SSE。事件序 start → thinking/step… → plan → done，
  异常出口是 no_data / no_plan / error。和问答一样用 POST + 流式，
  因为一次规划要调 3~5 次模型，够等 1~2 分钟，不适合普通请求。
- `GET /api/plan`：历史计划（不含正文，带摘要）。
- `GET /api/plan/{id}`：详情（正文 + 每步过程）。
- `DELETE /api/plan/{id}`：删除一次规划。
- `GET /api/plan/meta`：页面提示用的工具清单与边界说明。
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from shared.domain.models.entities import StudyPlan
from shared.api.deps import get_current_space
from shared.domain.models.entities import Space
from shared.infrastructure.db.session import get_db
from shared.repositories.sqlalchemy_repo import SqlAlchemyStudyPlanRepository
from shared.schemas.plan import PlanBrief, PlanDetail, PlanIn, PlanMetaOut
import client.services.plan_agent as plan_agent
import client.services.plan_tools as plan_tools

router = APIRouter(prefix="/plan", tags=["plan"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}

_HEADING = re.compile(r"^#{1,6}\s*", re.MULTILINE)
_MARKS = re.compile(r"[*_`>|-]")


def _excerpt(markdown: str, limit: int = 110) -> str:
    """列表页摘要：去掉标题符号与列表符，压成一行。"""
    text = _HEADING.sub("", markdown or "")
    text = _MARKS.sub("", text)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _brief(plan: StudyPlan) -> PlanBrief:
    meta = plan.meta or {}
    stats = meta.get("stats") or {}
    return PlanBrief(
        id=plan.id,
        goal=plan.goal,
        excerpt=_excerpt(plan.markdown),
        created_at=plan.created_at.isoformat() if plan.created_at else "",
        stats={
            "documents": int(stats.get("documents") or 0),
            "sessions": int(stats.get("sessions") or 0),
            "avg_score": float(stats.get("avg_score") or 0.0),
            "wrong": int(stats.get("wrong") or 0),
        },
        steps=len(meta.get("steps") or []),
    )


@router.post("")
async def create_plan(
    payload: PlanIn, space: Space = Depends(get_current_space)
) -> StreamingResponse:
    body = payload.model_dump()
    body["space_id"] = space.id
    return StreamingResponse(
        plan_agent.sse_plan(body),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.get("/meta", response_model=PlanMetaOut)
def plan_meta() -> PlanMetaOut:
    return PlanMetaOut(
        steps_max=plan_agent.MAX_STEPS,
        tools=[
            {"name": name, "label": label}
            for name, label in plan_tools.TOOL_LABELS.items()
        ],
        limits=[
            "只读数据、只写计划：不改笔记、错题本与题库",
            "计划里的数字全部来自上面这些查询，不自己推算",
            "一次生成一份 7 天计划，不做长期规划",
            "资料与练习都是 0 时不会生成",
        ],
    )


@router.get("", response_model=list[PlanBrief])
def list_plans(
    limit: int = 20,
    space: Space = Depends(get_current_space),
    db: Session = Depends(get_db),
) -> list[PlanBrief]:
    return [
        _brief(plan)
        for plan in SqlAlchemyStudyPlanRepository(db).list(space.id, limit=limit)
    ]


@router.get("/{plan_id}", response_model=PlanDetail)
def get_plan(
    plan_id: str,
    space: Space = Depends(get_current_space),
    db: Session = Depends(get_db),
) -> PlanDetail:
    plan = SqlAlchemyStudyPlanRepository(db).get(plan_id, space.id)
    if plan is None:
        raise HTTPException(status_code=404, detail="计划不存在或已删除")
    brief = _brief(plan)
    return PlanDetail(
        **brief.model_dump(),
        markdown=plan.markdown,
        meta=plan.meta or {},
    )


@router.delete("/{plan_id}")
def delete_plan(
    plan_id: str,
    space: Space = Depends(get_current_space),
    db: Session = Depends(get_db),
) -> dict:
    if not SqlAlchemyStudyPlanRepository(db).delete(plan_id, space.id):
        raise HTTPException(status_code=404, detail="计划不存在或已删除")
    return {"deleted": plan_id}
