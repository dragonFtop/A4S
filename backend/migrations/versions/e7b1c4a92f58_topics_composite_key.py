"""topics 改复合主键 (space_id, id)

主题是空间私有的：两个学习小组各自都要有一个「未分类」，共用 id="uncategorized"，
全局唯一主键在这里不成立。SQLite 改不了既有表的主键，只能重建。

Revision ID: e7b1c4a92f58
Revises: d5a2f83c1e07
Create Date: 2026-11-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7b1c4a92f58"
down_revision: str | None = "d5a2f83c1e07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = ("space_id", "id", "name", "kind", "color", "sort_order", "user_id", "created_at")


def upgrade() -> None:
    op.create_table(
        "topics_new",
        sa.Column("space_id", sa.String(36), primary_key=True),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False, server_default="topic"),
        sa.Column("color", sa.String(16), nullable=False, server_default="#888780"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("user_id", sa.String(64), nullable=False, server_default="local"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("space_id", "kind", "name", name="uq_topics_space_kind_name"),
    )
    joined = ", ".join(_COLUMNS)
    op.execute(f"INSERT INTO topics_new ({joined}) SELECT {joined} FROM topics")
    op.drop_table("topics")
    op.rename_table("topics_new", "topics")


def downgrade() -> None:
    op.create_table(
        "topics_old",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("space_id", sa.String(36), nullable=False, server_default="default"),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False, server_default="topic"),
        sa.Column("color", sa.String(16), nullable=False, server_default="#888780"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("user_id", sa.String(64), nullable=False, server_default="local"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("kind", "name", name="uq_topics_kind_name"),
    )
    joined = ", ".join(_COLUMNS)
    op.execute(f"INSERT INTO topics_old ({joined}) SELECT {joined} FROM topics")
    op.drop_table("topics")
    op.rename_table("topics_old", "topics")
