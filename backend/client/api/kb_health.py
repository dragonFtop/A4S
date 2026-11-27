"""知识库体检接口（M8）。

- `POST /api/kb-health/run`：立刻跑一次体检，返回健康分、问题清单与修复建议。
- `GET /api/kb-health/latest`：上一次体检的结果；没跑过返回 404。

体检只读业务表，只写 `agent_runs` / `agent_steps`，不改资料本身。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from shared.api.deps import get_current_space
from shared.domain.models.agents import AgentRun
from shared.domain.models.entities import Space
from shared.infrastructure.db.session import get_db
from shared.schemas.health import HealthFindingOut, HealthFixOut, HealthReportOut
from shared.schemas.plan import AgentStepOut
from client.services.agents import health

router = APIRouter(prefix="/kb-health", tags=["kb-health"])


def _to_report(run: AgentRun) -> HealthReportOut:
    summary = run.summary or {}
    findings = [
        HealthFindingOut(
            id=str(item.get("id") or ""),
            check=str((item.get("evidence") or {}).get("check") or ""),
            severity=str((item.get("evidence") or {}).get("severity") or "low"),
            claim=str(item.get("claim") or ""),
            evidence=item.get("evidence") or {},
            producer=str(item.get("producer") or "inspector"),
        )
        for item in summary.get("findings") or []
        if isinstance(item, dict)
    ]
    proposals = [
        HealthFixOut(
            id=str(item.get("id") or ""),
            action=str(item.get("action") or ""),
            label=str(item.get("label") or ""),
            reason=str(item.get("reason") or ""),
            target=[str(value) for value in item.get("target") or []],
            topic_id=str(item.get("topic_id") or ""),
            finding_id=str(item.get("finding_id") or ""),
        )
        for item in summary.get("proposals") or []
        if isinstance(item, dict)
    ]
    return HealthReportOut(
        run_id=run.id,
        status=run.status,
        trigger=run.trigger,
        score=int(summary.get("score") or 0),
        documents=int(summary.get("documents") or 0),
        counts={key: int(value) for key, value in (summary.get("counts") or {}).items()},
        findings=findings,
        proposals=proposals,
        steps=[
            AgentStepOut(
                seq=step.seq,
                role=step.role,
                action=step.action,
                tool=step.tool,
                args=step.args,
                ok=step.ok,
                brief=step.brief,
                result=step.result,
                elapsed_ms=step.elapsed_ms,
            )
            for step in run.steps
        ],
        elapsed_ms=run.elapsed_ms,
        created_at=run.created_at.isoformat() if run.created_at else "",
    )


@router.post("/run", response_model=HealthReportOut)
def run_health(
    space: Space = Depends(get_current_space),
    db: Session = Depends(get_db),
) -> HealthReportOut:
    run = health.run_inspection(db, space_id=space.id, trigger="manual")
    return _to_report(run)


@router.get("/latest", response_model=HealthReportOut)
def latest_health(
    space: Space = Depends(get_current_space),
    db: Session = Depends(get_db),
) -> HealthReportOut:
    run = health.latest(db, space.id)
    if run is None:
        raise HTTPException(status_code=404, detail="还没有体检记录")
    return _to_report(run)