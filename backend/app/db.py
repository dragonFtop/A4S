"""数据库：引擎、会话、迁移、向量库单例。

建表结构在 `models.py`；这里只管"怎么连、怎么初始化"。

- `engine` 由 `DATABASE_URL` 决定；
- `get_db` 供 FastAPI 依赖注入，请求结束自动关；
- `init_db` 启动时执行 Alembic 迁移到 head（不用 create_all，避免迁移链与真实结构脱节）。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import BACKEND_DIR, DATA_DIR, settings
from app.models import Base

logger = logging.getLogger(__name__)

url = settings.resolved_database_url
_connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}

if url.startswith("sqlite"):
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)

engine = create_engine(url, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """执行迁移到最新版本。失败时回退到 create_all（仅用于首次启动的兜底）。"""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    try:
        command.upgrade(cfg, "head")
    except Exception as exc:  # 迁移失败不应让应用完全起不来
        logger.warning("Alembic 迁移失败，回退到 create_all：%s", exc)
        Base.metadata.create_all(bind=engine)


def ping_db() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - 探活失败就是失败，不区分原因
        return False


# ---- 向量库（sqlite-vec）----
# 在 engine 建好后立刻给连接加载扩展，之后所有连接都能用 vec0 语法。
from app.vectorstore import SqliteVecStore, attach_sqlite_vec  # noqa: E402

VEC_ENABLED = attach_sqlite_vec(engine)

# ---- 笔记全文检索（FTS5）----
# 虚表只能用 DDL 建，迁移脚本无法还原，所以启动时确保存在。
from app import fts  # noqa: E402

FTS_ENABLED = fts.ensure_schema(engine)

_vector_store: SqliteVecStore | None = None
_vector_store_dim: int | None = None


def get_vector_store() -> SqliteVecStore:
    """进程内单例；维度来自配置。

    配置里改了 `embedding_dimension` 之后必须重建实例，否则它会一直拿着旧维度
    去和真实索引比、并据此写出错误结论（"维度一致"／"维度不一致"都可能误判）。
    """
    global _vector_store, _vector_store_dim
    dimension = settings.embedding_dimension
    if _vector_store is None or _vector_store_dim != dimension:
        _vector_store = SqliteVecStore(engine, dimension)
        _vector_store_dim = dimension
        if VEC_ENABLED:
            _vector_store.ensure_schema()
    return _vector_store
