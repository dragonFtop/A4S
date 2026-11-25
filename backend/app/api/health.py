"""系统健康检查：回答「服务活着吗、依赖配好了吗」。"""

from __future__ import annotations

from fastapi import APIRouter

from app.config import settings
from app.infrastructure.db.session import ping_db
from app.schemas.common import HealthResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version="0.1.0",
        env=settings.app_env,
        db=ping_db(),
        llm_configured=settings.llm_configured,
        embedding_provider=settings.embedding_provider,
    )
