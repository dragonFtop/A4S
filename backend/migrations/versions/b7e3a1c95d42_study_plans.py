"""study_plans：学习计划（M7 规划 Agent）。

Revision ID: b7e3a1c95d42
Revises: c4d8e1a63b52

一行 = 一次规划会话的产物。markdown 是给人看的计划正文，meta 存过程快照
（每步工具调用与返回摘要），用于「计划里的数字可溯源」这条验收标准。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7e3a1c95d42"
down_revision = "c4d8e1a63b52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "study_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("goal", sa.String(200), nullable=False, server_default=sa.text("''")),
        sa.Column("markdown", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("meta", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("user_id", sa.String(64), nullable=False, server_default=sa.text("'local'")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_study_plans_created", "study_plans", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_study_plans_created", table_name="study_plans")
    op.drop_table("study_plans")
