"""A4S 后端入口。

启动流程：建表（开发环境）→ 准备数据目录 → 装载定时任务 → 挂载路由。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.config import DATA_DIR, settings
from app.infrastructure.db.session import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    (DATA_DIR / "uploads").mkdir(parents=True, exist_ok=True)
    init_db()
    # 内置「未分类」主题必须存在，documents.topic_id 的默认值指向它
    from app.infrastructure.db.seed import ensure_builtin
    from app.infrastructure.db.session import SessionLocal

    with SessionLocal() as db:
        ensure_builtin(db)

    # 定时回顾：按 scheduler_enabled 决定是否装载任务
    from app.infrastructure import scheduler

    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()


app = FastAPI(
    title="A4S API",
    description="Agent For Students — 大学生个人学习助手",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")


@app.get("/")
def root() -> dict:
    return {
        "name": "A4S",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/api/health",
        "env": settings.app_env,
    }
