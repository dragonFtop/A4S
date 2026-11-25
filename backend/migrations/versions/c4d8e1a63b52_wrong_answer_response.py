"""wrong_answers 增加 last_response 列（M4 判分与错题本）。

Revision ID: c4d8e1a63b52
Revises: 5c1f7a9d2b04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c4d8e1a63b52"
down_revision = "5c1f7a9d2b04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("wrong_answers") as batch:
        batch.add_column(
            sa.Column("last_response", sa.Text(), nullable=False, server_default=sa.text("''"))
        )


def downgrade() -> None:
    with op.batch_alter_table("wrong_answers") as batch:
        batch.drop_column("last_response")
