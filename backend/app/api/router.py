"""路由聚合。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api import (
    auth,
    chat,
    documents,
    health,
    notes,
    overview,
    plan,
    practice,
    review,
    schedule,
    settings,
    spaces,
    topics,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(spaces.router)
api_router.include_router(overview.router)
api_router.include_router(topics.router)
api_router.include_router(documents.router)
api_router.include_router(notes.router)
api_router.include_router(chat.router)
api_router.include_router(plan.router)
api_router.include_router(practice.router)
api_router.include_router(review.router)
api_router.include_router(settings.router)
api_router.include_router(schedule.router)
