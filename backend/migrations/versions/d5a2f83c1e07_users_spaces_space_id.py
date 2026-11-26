"""users / spaces / space_members / auth_tokens，业务表补 space_id

从 ragflow-plus 借来的模型：团队不另建一层，直接复用「归属容器」。
个人空间与学习小组是同一张表，kind 区分；成员关系用 role 分 owner / member。

已有数据统一挂到 LEGACY_SPACE_ID = "default"，首个用户注册时整批认领
（见 app/services/auth_service.py 的 claim_legacy_data）。

Revision ID: d5a2f83c1e07
Revises: b7e3a1c95d42
Create Date: 2026-11-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5a2f83c1e07"
down_revision: str | None = "b7e3a1c95d42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 加 space_id 的业务表
SPACE_TABLES = (
    "topics",
    "documents",
    "chunks",
    "notes",
    "practice_sessions",
    "wrong_answers",
    "study_plans",
    "review_reports",
)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False, server_default=""),
        sa.Column("display_name", sa.String(64), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "spaces",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="personal"),
        sa.Column(
            "owner_id",
            sa.String(36),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_spaces_owner_id", "spaces", ["owner_id"])

    op.create_table(
        "space_members",
        sa.Column(
            "space_id",
            sa.String(36),
            sa.ForeignKey("spaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(16), nullable=False, server_default="member"),
        sa.Column("joined_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "auth_tokens",
        sa.Column("token", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_auth_tokens_user_id", "auth_tokens", ["user_id"])

    # 已有行全部落到占位空间。SQLite 的 ADD COLUMN 不支持先加 NOT NULL 再回填，
    # 所以用 server_default 一步到位；这里不声明外键——SQLite 的 ALTER ADD COLUMN
    # 加外键要重建整表，风险远大于收益，归属校验由应用层保证。
    for table in SPACE_TABLES:
        op.add_column(
            table,
            sa.Column("space_id", sa.String(36), nullable=False, server_default="default"),
        )
        op.create_index(f"ix_{table}_space_id", table, ["space_id"])


def downgrade() -> None:
    for table in SPACE_TABLES:
        op.drop_index(f"ix_{table}_space_id", table_name=table)
        op.drop_column(table, "space_id")

    op.drop_index("ix_auth_tokens_user_id", table_name="auth_tokens")
    op.drop_table("auth_tokens")
    op.drop_table("space_members")
    op.drop_index("ix_spaces_owner_id", table_name="spaces")
    op.drop_table("spaces")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
