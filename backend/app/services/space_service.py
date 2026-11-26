"""学习小组。

从 ragflow-plus 抄来的是**模型**，不是实现：团队不另建实体，就是「把别人加进
同一个归属容器」。所以这里没有 Team 表，只有 Space 和 SpaceMember。

几条规则集中在这里，路由不用重复判断：

- 个人空间不能改名、不能加人、不能删——它不是"小组"，是账号的家；
- 唯一 owner 不能被移除、不能降级，否则小组会变成没人能管的孤儿；
- 按用户名加人，不加邀请链接（单机/小团队，省一整条链路）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.models.entities import (
    MemberRole,
    Space,
    SpaceKind,
    SpaceMember,
    User,
)
from app.infrastructure.db.models import SpaceMemberORM, SpaceORM, UserORM


class SpaceError(Exception):
    def __init__(self, detail: str, *, status: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_space(row: SpaceORM, *, role: MemberRole, member_count: int) -> Space:
    return Space(
        id=row.id,
        name=row.name,
        kind=SpaceKind(row.kind),
        owner_id=row.owner_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        member_count=member_count,
        my_role=role,
    )


def _member_count(db: Session, space_id: str) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(SpaceMemberORM)
            .where(SpaceMemberORM.space_id == space_id)
        ).scalar()
        or 0
    )


def create_group(db: Session, actor: User, name: str) -> Space:
    label = (name or "").strip()
    if not label:
        raise SpaceError("小组需要一个名字")
    if len(label) > 64:
        raise SpaceError("名字最长 64 个字符")

    now = _now()
    row = SpaceORM(
        id=str(uuid.uuid4()),
        name=label,
        kind=SpaceKind.GROUP.value,
        owner_id=actor.id,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    db.add(
        SpaceMemberORM(
            space_id=row.id,
            user_id=actor.id,
            role=MemberRole.OWNER.value,
            joined_at=now,
        )
    )
    from app.infrastructure.db.seed import ensure_builtin

    ensure_builtin(db, row.id)
    db.commit()
    db.refresh(row)
    return _to_space(row, role=MemberRole.OWNER, member_count=1)


def rename_group(db: Session, space: Space, name: str) -> Space:
    if space.kind is SpaceKind.PERSONAL:
        raise SpaceError("个人空间不能改名")
    label = (name or "").strip()
    if not label:
        raise SpaceError("小组需要一个名字")
    row = db.get(SpaceORM, space.id)
    if row is None:
        raise SpaceError("小组不存在", status=404)
    row.name = label[:64]
    row.updated_at = _now()
    db.commit()
    return space


def delete_group(db: Session, space: Space, actor: User) -> None:
    if space.kind is SpaceKind.PERSONAL:
        raise SpaceError("个人空间不能删除")
    if space.owner_id != actor.id:
        raise SpaceError("只有创建者能删除小组", status=403)
    row = db.get(SpaceORM, space.id)
    if row is None:
        return
    db.delete(row)
    db.commit()


def add_member(db: Session, space: Space, username: str) -> SpaceMember:
    if space.kind is SpaceKind.PERSONAL:
        raise SpaceError("个人空间不能加成员，先建一个学习小组")
    target = db.execute(
        select(UserORM).where(UserORM.username == (username or "").strip())
    ).scalar_one_or_none()
    if target is None:
        raise SpaceError("没有这个用户", status=404)
    if db.get(SpaceMemberORM, (space.id, target.id)) is not None:
        raise SpaceError("TA 已经在这个小组里了")

    now = _now()
    db.add(
        SpaceMemberORM(
            space_id=space.id,
            user_id=target.id,
            role=MemberRole.MEMBER.value,
            joined_at=now,
        )
    )
    db.commit()
    return SpaceMember(
        space_id=space.id,
        user_id=target.id,
        role=MemberRole.MEMBER,
        joined_at=now,
        username=target.username,
        display_name=target.display_name,
    )


def _owner_count(db: Session, space_id: str) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(SpaceMemberORM)
            .where(
                SpaceMemberORM.space_id == space_id,
                SpaceMemberORM.role == MemberRole.OWNER.value,
            )
        ).scalar()
        or 0
    )


def remove_member(db: Session, space: Space, user_id: str) -> None:
    if space.kind is SpaceKind.PERSONAL:
        raise SpaceError("个人空间不能移除成员")
    row = db.get(SpaceMemberORM, (space.id, user_id))
    if row is None:
        raise SpaceError("TA 不是这个小组的成员", status=404)
    if row.role == MemberRole.OWNER.value and _owner_count(db, space.id) <= 1:
        # 唯一的创建者走了，这个小组就没人能管了
        raise SpaceError("至少要留一个创建者，不能移除最后一个 owner")
    db.delete(row)
    db.commit()


def set_role(db: Session, space: Space, user_id: str, role: MemberRole) -> None:
    if space.kind is SpaceKind.PERSONAL:
        raise SpaceError("个人空间没有角色可改")
    row = db.get(SpaceMemberORM, (space.id, user_id))
    if row is None:
        raise SpaceError("TA 不是这个小组的成员", status=404)
    if (
        row.role == MemberRole.OWNER.value
        and role is MemberRole.MEMBER
        and _owner_count(db, space.id) <= 1
    ):
        raise SpaceError("至少要留一个创建者，不能降级最后一个 owner")
    row.role = role.value
    db.commit()
