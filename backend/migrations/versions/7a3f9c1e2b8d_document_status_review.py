"""document status review

为 DocumentStatus 增加 P1.5 小组投稿审核流程的两个状态：
- pending_review：member 投稿待审，审核前不切分/不向量化/不进 FTS
- rejected：owner 驳回，永不参与检索

DB 侧 documents.status 是无 CHECK 约束的 String(16)（参见 initial_schema），
"pending_review"(14) 与 "rejected"(8) 均在长度内，因此无需改列，
本迁移仅作为版本标记，保证空库与已有库 upgrade head 一致推进。

Revision ID: 7a3f9c1e2b8d
Revises: 03fb555048cd
Create Date: 2026-11-26 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "7a3f9c1e2b8d"
down_revision: str | None = "03fb555048cd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 无 DDL：状态列已是无约束的 String(16)，新枚举值由应用层写入。
    pass


def downgrade() -> None:
    pass
