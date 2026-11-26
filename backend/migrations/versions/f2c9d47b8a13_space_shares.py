"""space_shares：外部只读共享授权

团队（space_members）是"进来一起干"，共享（space_shares）是"在外面看一眼"。
合成一张表会让「只读成员」这种状态污染所有权限判断，所以独立建。

Revision ID: f2c9d47b8a13
Revises: e7b1c4a92f58
Create Date: 2026-11-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2c9d47b8a13"
down_revision: str | None = "e7b1c4a92f58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "space_shares",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "space_id",
            sa.String(36),
            sa.ForeignKey("spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "grantee_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("origin", sa.String(16), nullable=False, server_default="request"),
        sa.Column("message", sa.String(200), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("space_id", "grantee_id", name="uq_share_space_grantee"),
    )
    op.create_index("ix_space_shares_space_id", "space_shares", ["space_id"])
    op.create_index("ix_space_shares_grantee_id", "space_shares", ["grantee_id"])


def downgrade() -> None:
    op.drop_index("ix_space_shares_grantee_id", table_name="space_shares")
    op.drop_index("ix_space_shares_space_id", table_name="space_shares")
    op.drop_table("space_shares")
