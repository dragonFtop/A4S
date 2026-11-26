"""公共目录（catalog）的派生逻辑（P2）。

设计决策（对应 Task 8 的「表 或 视图」选项）
-------------------------------------------
不新增 `catalog` 表，改为**读取时实时派生**（等价于一个逻辑视图）：
- 行是否存在 = `spaces.share_enabled = true` 且存在 `space_shares.status = 'active'`；
- 共享被批准/撤销时无需任何钩子重建行，天然不会与授权表漂移（FR-2.1「不手工维护」）。
- 因此也**不需要**为 server 拆分 Alembic 链（没有 server 专属表要加）。

可见性（FR-2.4 / AC-11）
------------------------
一行对用户 U 可见，当且仅当：
- U 是该空间的成员（`space_members`），或
- U 是该空间某条 active 共享的被授权人（grantee）。
组外用户既非成员也无授权，查到的目录必然为空。
（"加入小组即视为同意目录对组内可见"——成员由 owner 拉入，加入动作本身即为同意；
 本轮不额外增加同意记录列，见 tasks.md 决策记录。）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.domain.models.entities import DocumentStatus
from shared.infrastructure.db.models import (
    DocumentORM,
    SpaceMemberORM,
    SpaceORM,
    SpaceShareORM,
    TopicORM,
    UserORM,
)

# 配额余量占位：P3 接入节点注册与审计后，按节点每日配额递减。
DEFAULT_QUOTA = 100


@dataclass(slots=True)
class CatalogEntry:
    space_id: str
    space_name: str
    owner_id: str
    owner_name: str
    topic: str
    document_count: int
    updated_at: datetime
    vector_version: str
    online: str
    quota_remaining: int


def _active_shared_space_ids(db: Session) -> set[str]:
    rows = db.execute(
        select(SpaceShareORM.space_id).where(
            SpaceShareORM.status == "active"
        ).distinct()
    ).scalars()
    return set(rows)


def _visible_space_ids(db: Session, viewer_id: str, candidates: set[str]) -> set[str]:
    """候选空间里，viewer 作为成员或被授权人能看到的那些。"""
    if not candidates:
        return set()
    member_ids = set(
        db.execute(
            select(SpaceMemberORM.space_id).where(SpaceMemberORM.user_id == viewer_id)
        ).scalars()
    )
    grantee_ids = set(
        db.execute(
            select(SpaceShareORM.space_id).where(
                SpaceShareORM.grantee_id == viewer_id,
                SpaceShareORM.status == "active",
            )
        ).scalars()
    )
    return candidates & (member_ids | grantee_ids)


def _representative_topic(db: Session, space_id: str) -> str:
    """代表主题：ready 资料最多的主题；无资料时退到该空间第一个 topic。"""
    row = db.execute(
        select(DocumentORM.topic_id, func.count(DocumentORM.id).label("n"))
        .where(
            DocumentORM.space_id == space_id,
            DocumentORM.status == DocumentStatus.READY.value,
        )
        .group_by(DocumentORM.topic_id)
        .order_by(func.count(DocumentORM.id).desc(), DocumentORM.topic_id)
        .limit(1)
    ).first()
    topic_id = row[0] if row else None
    if topic_id is None:
        topic_id = db.execute(
            select(TopicORM.id)
            .where(TopicORM.space_id == space_id, TopicORM.kind == "topic")
            .order_by(TopicORM.sort_order, TopicORM.name)
            .limit(1)
        ).scalar()
    if topic_id is None:
        return ""
    name = db.execute(
        select(TopicORM.name).where(
            TopicORM.space_id == space_id, TopicORM.id == topic_id
        )
    ).scalar()
    return name or ""


def build_catalog(db: Session, *, viewer_id: str) -> list[CatalogEntry]:
    """派生 viewer 可见的公共目录行。共享未生效时返回空列表。"""
    active_ids = _active_shared_space_ids(db)
    if not active_ids:
        return []

    spaces = list(
        db.execute(
            select(SpaceORM).where(
                SpaceORM.share_enabled.is_(True),
                SpaceORM.id.in_(active_ids),
            )
        ).scalars()
    )
    visible = _visible_space_ids(db, viewer_id, {s.id for s in spaces})
    spaces = [s for s in spaces if s.id in visible]
    if not spaces:
        return []

    owners = {
        u.id: u
        for u in db.execute(
            select(UserORM).where(UserORM.id.in_({s.owner_id for s in spaces}))
        ).scalars()
    }

    entries: list[CatalogEntry] = []
    for space in spaces:
        count, latest = db.execute(
            select(
                func.count(DocumentORM.id), func.max(DocumentORM.ready_at)
            ).where(
                DocumentORM.space_id == space.id,
                DocumentORM.status == DocumentStatus.READY.value,
            )
        ).one()
        doc_count = int(count or 0)
        updated = latest or space.updated_at
        owner = owners.get(space.owner_id)
        owner_name = ""
        if owner is not None:
            owner_name = owner.display_name or owner.username
        entries.append(
            CatalogEntry(
                space_id=space.id,
                space_name=space.name,
                owner_id=space.owner_id,
                owner_name=owner_name,
                topic=_representative_topic(db, space.id),
                document_count=doc_count,
                updated_at=updated,
                vector_version=f"{doc_count}:{updated.isoformat()}" if updated else "",
                online="unknown",
                quota_remaining=DEFAULT_QUOTA,
            )
        )

    # 资料多的、更新近的排前面：Agent 选源时优先问"更有货"的库。
    entries.sort(
        key=lambda e: (
            -e.document_count,
            -(e.updated_at.timestamp() if e.updated_at else 0.0),
            e.space_id,
        )
    )
    return entries
