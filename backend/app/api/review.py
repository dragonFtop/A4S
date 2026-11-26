"""回顾报告接口（FR-07 / FR-08）。

生成是**同步**的：报告要立刻展示给用户，走 job 轮询反而多一次跳转。
模型慢的时候一整次请求大约 10~30 秒，前端用显式 loading 承接，
比"点完没反应、过一会列表里冒出一份"更符合预期。

定时任务不在这里触发，见 `app/infrastructure/scheduler.py`。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.models import ReviewReport
from app.db import get_db
from app.schemas.review import (
    PushResultOut,
    PushStateOut,
    ReportBriefOut,
    ReportGenerateIn,
    ReportMetricsOut,
    ReportOut,
)
from app.services import review_service
from app.services.review_service import ReviewError, ReviewNotFound

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reviews", tags=["reviews"])


def _summary(report: ReviewReport) -> ReportMetricsOut:
    metrics = report.metrics or {}
    documents = metrics.get("documents", {})
    practice = metrics.get("practice", {})
    wrong = metrics.get("wrong", {})
    return ReportMetricsOut(
        documents=documents.get("total", 0),
        chunks=documents.get("chunks", 0),
        sessions=practice.get("sessions", 0),
        answered=practice.get("answered", 0),
        correct=practice.get("correct", 0),
        accuracy=practice.get("accuracy", 0.0),
        wrong=wrong.get("total", 0),
        has_activity=bool(metrics.get("has_activity")),
        analysis_source=metrics.get("analysis_source", "rule"),
    )


def _brief(report: ReviewReport) -> ReportBriefOut:
    metrics = report.metrics or {}
    push = metrics.get("push", {})
    return ReportBriefOut(
        id=report.id,
        window_days=report.window_days,
        period_start=report.period_start,
        period_end=report.period_end,
        created_at=report.created_at,
        trigger=metrics.get("trigger", "manual"),
        push_status=push.get("status", "none"),
        push_detail=push.get("detail", ""),
        summary=_summary(report),
    )


def _detail(report: ReviewReport) -> ReportOut:
    base = _brief(report)
    return ReportOut(
        **base.model_dump(),
        markdown=report.markdown,
        metrics=report.metrics or {},
    )


def _push(db: Session, report: ReviewReport) -> tuple[bool, str]:
    """发信并记录结果。发信失败只写状态，不影响报告本身。"""
    from app.config import settings

    if not settings.smtp_configured:
        review_service.mark_push(
            db, report, False, "尚未配置 SMTP（服务器 / 账号 / 授权码 / 收件人）"
        )
        return False, "尚未配置 SMTP"

    from app.mailer import build_notifier

    result = build_notifier().send_report(report)
    review_service.mark_push(db, report, result.ok, result.detail)
    if not result.ok:
        logger.warning("报告推送失败：%s", result.detail)
    return result.ok, result.detail


@router.get("/push-state", response_model=PushStateOut)
def read_push_state(db: Session = Depends(get_db)) -> PushStateOut:
    """必须注册在 /{report_id} 之前，否则 "push-state" 会被当成报告 id。"""
    return PushStateOut(**review_service.push_state(db))


@router.get("", response_model=list[ReportBriefOut])
def list_reports(limit: int = 50, db: Session = Depends(get_db)) -> list[ReportBriefOut]:
    return [_brief(report) for report in review_service.list_reports(db, limit=min(limit, 200))]


@router.post("", response_model=ReportOut, status_code=201)
async def create_report(payload: ReportGenerateIn, db: Session = Depends(get_db)) -> ReportOut:
    try:
        report = await review_service.generate_report(db, payload.window_days, trigger="manual")
    except ReviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if payload.notify:
        _push(db, report)
        report = review_service.get_report(db, report.id)
    return _detail(report)


@router.get("/{report_id}", response_model=ReportOut)
def read_report(report_id: str, db: Session = Depends(get_db)) -> ReportOut:
    try:
        return _detail(review_service.get_report(db, report_id))
    except ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{report_id}")
def delete_report(report_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        review_service.remove_report(db, report_id)
    except ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": report_id}


@router.post("/{report_id}/push", response_model=PushResultOut)
def push_report(report_id: str, db: Session = Depends(get_db)) -> PushResultOut:
    try:
        report = review_service.get_report(db, report_id)
    except ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    ok, detail = _push(db, report)
    refreshed = review_service.get_report(db, report_id)
    return PushResultOut(ok=ok, detail=detail, report=_detail(refreshed))
