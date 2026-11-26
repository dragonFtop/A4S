"""系统健康检查：回答「服务活着吗、依赖配好了吗」。"""

from __future__ import annotations

from fastapi import APIRouter

from app.config import APP_VERSION, settings
from app.db import ping_db
from app.schemas.common import HealthResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=APP_VERSION,
        env=settings.app_env,
        db=ping_db(),
        llm_configured=settings.llm_configured,
        embedding_provider=settings.embedding_provider,
    )
