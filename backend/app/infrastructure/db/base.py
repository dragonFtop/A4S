"""SQLAlchemy 声明基类。

所有 ORM 模型都继承 Base；Alembic 通过 Base.metadata 自动比对差异。
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
