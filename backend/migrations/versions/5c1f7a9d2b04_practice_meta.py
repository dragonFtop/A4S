"""practice_sessions 增加 meta 列（M4 组卷参数快照）。

Revision ID: 5c1f7a9d2b04
Revises: 9fdd17ddd198
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "5c1f7a9d2b04"
down_revision = "9fdd17ddd198"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("practice_sessions") as batch:
        batch.add_column(
            sa.Column("meta", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )


def downgrade() -> None:
    with op.batch_alter_table("practice_sessions") as batch:
        batch.drop_column("meta")
