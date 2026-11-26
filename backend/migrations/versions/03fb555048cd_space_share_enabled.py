"""space share enabled

给 spaces 加「对外可查」开关。

默认 0（关）：注册、建组都不会自动把库暴露出去，想被申请必须显式打开。
server_default 是必须的——已有空间在新增的非空列上不能为 NULL。

Revision ID: 03fb555048cd
Revises: f2c9d47b8a13
Create Date: 2026-11-26 17:26:44.839180
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "03fb555048cd"
down_revision: str | None = "f2c9d47b8a13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "spaces",
        sa.Column(
            "share_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("spaces", "share_enabled")
