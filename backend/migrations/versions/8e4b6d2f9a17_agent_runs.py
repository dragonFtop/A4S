"""agent_runs / agent_steps：多智能体运行账本（M8）。

Revision ID: 8e4b6d2f9a17
Revises: c4d1e8a9f201

一次运行 = agent_runs 一行；每一步（工具、Finding、审核、写入）落 agent_steps。
不存模型对话原文：回放只需要「谁、做了什么、结论是什么」。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "8e4b6d2f9a17"
down_revision = "c4d1e8a9f201"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "space_id",
            sa.String(36),
            sa.ForeignKey("spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(24), nullable=False, server_default=sa.text("'coach'")),
        sa.Column("goal", sa.String(200), nullable=False, server_default=sa.text("''")),
        sa.Column("trigger", sa.String(16), nullable=False, server_default=sa.text("'user'")),
        sa.Column("model", sa.String(80), nullable=False, server_default=sa.text("''")),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'running'")),
        sa.Column("budget_steps", sa.Integer(), nullable=False, server_default=sa.text("6")),
        sa.Column("steps_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("llm_calls", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("handoffs", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("summary", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("verdict", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("error", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("elapsed_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_agent_runs_space", "agent_runs", ["space_id"])
    op.create_index("ix_agent_runs_created", "agent_runs", ["created_at"])

    op.create_table(
        "agent_steps",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("tool", sa.String(40), nullable=True),
        sa.Column("args", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("brief", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("elapsed_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_agent_steps_run", "agent_steps", ["run_id"])
    op.create_index("ix_agent_steps_run_seq", "agent_steps", ["run_id", "seq"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_agent_steps_run_seq", table_name="agent_steps")
    op.drop_index("ix_agent_steps_run", table_name="agent_steps")
    op.drop_table("agent_steps")
    op.drop_index("ix_agent_runs_created", table_name="agent_runs")
    op.drop_index("ix_agent_runs_space", table_name="agent_runs")
    op.drop_table("agent_runs")