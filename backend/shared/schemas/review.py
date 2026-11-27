"""回顾报告的出入参（FR-07 / FR-08）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ReportGenerateIn(BaseModel):
    window_days: Literal[7, 14, 30] = Field(
        default=7, description="统计窗口，只允许 7 / 14 / 30 天"
    )
    notify: bool = Field(default=False, description="生成后立即通过邮件推送")


class ReportMetricsOut(BaseModel):
    """列表页需要的概览数字，不返回完整 metrics（详情里才有）。"""

    documents: int = 0
    chunks: int = 0
    sessions: int = 0
    answered: int = 0
    correct: int = 0
    accuracy: float = 0.0
    wrong: int = 0
    has_activity: bool = False
    analysis_source: str = "rule"
    health_score: int | None = None
    health_issues: int = 0


class ReportBriefOut(BaseModel):
    id: str
    window_days: int
    period_start: datetime
    period_end: datetime
    created_at: datetime
    trigger: str = "manual"
    push_status: str = "none"
    push_detail: str = ""
    summary: ReportMetricsOut


class ReportOut(ReportBriefOut):
    markdown: str
    metrics: dict[str, Any]


class PushStateOut(BaseModel):
    status: str = "none"
    detail: str = ""
    at: str | None = None
    report_id: str | None = None


class PushResultOut(BaseModel):
    ok: bool
    detail: str = ""
    report: ReportOut | None = None
