"""应用工厂：客户端与服务端共用同一套启动流程，只是挂载的路由不同。

为什么做成工厂而不是两个各自写一遍：
- 启动顺序（建表 → 补「未分类」→ 装定时任务）改一次要生效两边；
- CORS、前缀、根路由这些"两个 App 必须一致"的东西，写在两处迟早会漂。

两端的差别只有三处：标题、额外路由、是否装定时任务（只有客户端跑回顾任务）。
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from shared.api.router import api_router as shared_router
from shared.config import APP_VERSION, DATA_DIR, settings
from shared.infrastructure.db.session import init_db

CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


def _prepare_data() -> None:
    """建表 + 给每个空间补齐「未分类」主题。两端都要做，各自对自己的库做。"""
    (DATA_DIR / "uploads").mkdir(parents=True, exist_ok=True)
    init_db()

    from shared.infrastructure.db.models import SpaceORM
    from shared.infrastructure.db.seed import ensure_builtin
    from shared.infrastructure.db.session import SessionLocal

    with SessionLocal() as db:
        for (space_id,) in db.query(SpaceORM.id).all():
            ensure_builtin(db, space_id)
        db.commit()


def build_app(
    *,
    title: str,
    description: str,
    extra_routers: list[APIRouter] | None = None,
    # 由入口注入的调度器模块。放在这里而不是在 shared 里 import client，
    # 是为了让 shared 对两端都零依赖——否则服务端会莫名其妙加载客户端的模块。
    scheduler_module: object | None = None,
    on_startup: Callable[[], None] | None = None,
) -> FastAPI:
    extra_routers = extra_routers or []

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        _prepare_data()
        if on_startup is not None:
            on_startup()

        scheduler = scheduler_module
        if scheduler is not None:
            scheduler.start()
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.shutdown()

    app = FastAPI(
        title=title,
        description=description,
        version=APP_VERSION,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(shared_router, prefix="/api")
    for router in extra_routers:
        app.include_router(router, prefix="/api")

    @app.get("/")
    def root() -> dict:
        return {
            "name": title,
            "version": APP_VERSION,
            "docs": "/docs",
            "health": "/api/health",
            "env": settings.app_env,
        }

    return app
