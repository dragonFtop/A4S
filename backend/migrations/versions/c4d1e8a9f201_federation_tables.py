"""federation tables (P3)

新增两张力联邦执行所需的表：
- fed_nodes：节点登记与心跳（server 据此判定在线）
- fed_audit：每次联邦查询的审计（来源/目标/命中数/时间/状态），用于配额与防抽取追溯

沿用**单条 Alembic 链**：两端启动都 `upgrade head`，因此 client.db 也会建出这两张
（空表、不影响客户端功能）。这样不必为 server 拆分迁移链，空库与已有库都能一致推进。

Revision ID: c4d1e8a9f201
Revises: 7a3f9c1e2b8d
Create Date: 2026-11-26 20:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d1e8a9f201"
down_revision: str | None = "7a3f9c1e2b8d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fed_nodes",
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("serving_space_id", sa.String(length=36), nullable=True),
        sa.Column("last_seen", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("node_id"),
    )
    op.create_table(
        "fed_audit",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("requester_node_id", sa.String(length=64), nullable=False),
        sa.Column("target_node_id", sa.String(length=64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("fed_audit")
    op.drop_table("fed_nodes")
