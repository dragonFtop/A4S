"""概览接口（FR-11）：给首页提供一次性聚合数据。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app import stats

router = APIRouter(tags=["overview"])


@router.get("/overview")
def overview(db: Session = Depends(get_db)) -> dict:
    return stats.summary(db)
