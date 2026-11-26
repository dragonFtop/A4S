"""知识库共享：把**整个空间**以只读方式开给一个不在空间里的用户。

与「学习小组」的区别（这个区分决定了全部实现）：

|              | 小组（space_members） | 共享（space_shares） |
| ------------ | -------------------- | ------------------- |
| 关系          | 进同一个空间，一起生产 | 在外面看，不加入      |
| 权限          | 可读写                | 只读 + 可问答        |
| 撤销          | 移出成员，数据留在组里 | 撤销授权，立即看不到  |

两条入口都要支持：
- 申请制：B 按用户名申请 → A 批准 / 拒绝
- 开放制：A 主动按用户名开给 B，不用对方申请

只有 `spaces.share_enabled` 打开的空间才会被申请——注册、建组都不会自动
把库暴露出去。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from shared.domain.models.entities import (
    ShareOrigin,
    ShareStatus,
    Space,
    SpaceShare,
    User,
)
from shared.infrastructure.db.models import (
    SpaceMemberORM,
    SpaceORM,
    SpaceShareORM,
    UserORM,
)

logger = logging.getLogger(__name__)


class ShareError(Exception):
    def __init__(self, detail: str, *, status: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_share(row: SpaceShareORM, space: SpaceORM, owner: UserORM, grantee: UserORM) -> SpaceShare:
    return SpaceShare(
        id=row.id,
        space_id=row.space_id,
        grantee_id=row.grantee_id,
        status=ShareStatus(row.status),
        origin=ShareOrigin(row.origin),
        message=row.message or "",
        created_at=row.created_at,
        decided_at=row.decided_at,
        space_name=space.name,
        owner_name=owner.display_name or owner.username,
        grantee_name=grantee.display_name or grantee.username,
        grantee_username=grantee.username,
    )


def _base_query(db: Session):
    owner = aliased(UserORM)
    grantee = aliased(UserORM)
    return (
        select(SpaceShareORM, SpaceORM, owner, grantee)
        .join(SpaceORM, SpaceORM.id == SpaceShareORM.space_id)
        .join(owner, owner.id == SpaceORM.owner_id)
        .join(grantee, grantee.id == SpaceShareORM.grantee_id)
    )


def _user_by_name(db: Session, username: str) -> UserORM:
    row = db.execute(
        select(UserORM).where(UserORM.username == (username or "").strip())
    ).scalar_one_or_none()
    if row is None:
        raise ShareError("没有这个用户", status=404)
    return row


def _personal_space(db: Session, user_id: str) -> SpaceORM:
    row = db.execute(
        select(SpaceORM).where(SpaceORM.owner_id == user_id, SpaceORM.kind == "personal")
    ).scalars().first()
    if row is None:
        raise ShareError("对方还没有个人空间", status=404)
    return row


def _is_member(db: Session, space_id: str, user_id: str) -> bool:
    return db.get(SpaceMemberORM, (space_id, user_id)) is not None


def _existing(db: Session, space_id: str, grantee_id: str) -> SpaceShareORM | None:
    return db.execute(
        select(SpaceShareORM).where(
            SpaceShareORM.space_id == space_id,
            SpaceShareORM.grantee_id == grantee_id,
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------- 申请（B → A）


def apply(db: Session, actor: User, username: str, message: str = "") -> SpaceShare:
    """按用户名申请查看对方的个人知识库。"""
    target = _user_by_name(db, username)
    if target.id == actor.id:
        raise ShareError("不能申请自己的知识库")

    space = _personal_space(db, target.id)
    if not space.share_enabled:
        # 不暴露"这个库存在但没开"，只说没开放——避免被用来探测谁有什么
        raise ShareError("对方还没有开放知识库", status=403)
    if _is_member(db, space.id, actor.id):
        raise ShareError("你已经在对方的空间里，不需要申请")

    row = _existing(db, space.id, actor.id)
    now = _now()
    if row is not None:
        if row.status == ShareStatus.ACTIVE.value:
            raise ShareError("你已经可以查看这个知识库了")
        if row.status == ShareStatus.PENDING.value:
            raise ShareError("申请已提交，等待对方处理")
        # 被拒或被撤销过：允许重新申请，复用同一行（唯一约束只允许一条）
        row.status = ShareStatus.PENDING.value
        row.origin = ShareOrigin.REQUEST.value
        row.message = (message or "").strip()[:200]
        row.created_at = now
        row.decided_at = None
        db.commit()
    else:
        row = SpaceShareORM(
            id=str(uuid.uuid4()),
            space_id=space.id,
            grantee_id=actor.id,
            status=ShareStatus.PENDING.value,
            origin=ShareOrigin.REQUEST.value,
            message=(message or "").strip()[:200],
            created_at=now,
        )
        db.add(row)
        db.commit()

    return _detail(db, row.id)


# ---------------------------------------------------------------- 开放（A → B）


def grant(db: Session, space: Space, actor: User, username: str, message: str = "") -> SpaceShare:
    """主人主动把自己的库开给某人，不需要对方申请。"""
    if space.owner_id != actor.id:
        raise ShareError("只有空间创建者能开放知识库", status=403)

    target = _user_by_name(db, username)
    if target.id == actor.id:
        raise ShareError("不能开放给自己")
    if _is_member(db, space.id, target.id):
        raise ShareError("TA 已经是这个空间的成员，不需要共享")

    row = db.get(SpaceORM, space.id)
    if row is None:
        raise ShareError("空间不存在", status=404)
    if not row.share_enabled:
        # 开放动作本身就意味着同意对外，顺手把开关打开，少一步操作
        row.share_enabled = True

    now = _now()
    share = _existing(db, space.id, target.id)
    if share is not None:
        if share.status == ShareStatus.ACTIVE.value:
            raise ShareError("TA 已经在共享名单里了")
        share.status = ShareStatus.ACTIVE.value
        share.origin = ShareOrigin.GRANT.value
        share.message = (message or "").strip()[:200]
        share.created_at = now
        share.decided_at = now
    else:
        share = SpaceShareORM(
            id=str(uuid.uuid4()),
            space_id=space.id,
            grantee_id=target.id,
            status=ShareStatus.ACTIVE.value,
            origin=ShareOrigin.GRANT.value,
            message=(message or "").strip()[:200],
            created_at=now,
            decided_at=now,
        )
        db.add(share)
    db.commit()
    return _detail(db, share.id)


# ---------------------------------------------------------------- 审批 / 撤销


def decide(db: Session, space: Space, actor: User, share_id: str, approve: bool) -> SpaceShare:
    if space.owner_id != actor.id:
        raise ShareError("只有空间创建者能处理申请", status=403)
    row = db.get(SpaceShareORM, share_id)
    if row is None or row.space_id != space.id:
        raise ShareError("申请不存在", status=404)
    if row.status != ShareStatus.PENDING.value:
        raise ShareError("这个申请已经处理过了")
    if row.grantee_id == actor.id:
        # 自己申请自己不可能发生，兜一层：审核人不能是受益人
        raise ShareError("不能批准自己的申请", status=403)

    row.status = (ShareStatus.ACTIVE if approve else ShareStatus.REJECTED).value
    row.decided_at = _now()
    db.commit()
    return _detail(db, row.id)


def revoke(db: Session, space: Space, actor: User, share_id: str) -> None:
    if space.owner_id != actor.id:
        raise ShareError("只有空间创建者能撤销共享", status=403)
    row = db.get(SpaceShareORM, share_id)
    if row is None or row.space_id != space.id:
        raise ShareError("共享记录不存在", status=404)
    row.status = ShareStatus.REVOKED.value
    row.decided_at = _now()
    db.commit()


def cancel(db: Session, actor: User, share_id: str) -> None:
    """申请人自己撤回申请（只对还在 pending 的有效）。"""
    row = db.get(SpaceShareORM, share_id)
    if row is None or row.grantee_id != actor.id:
        raise ShareError("申请不存在", status=404)
    if row.status != ShareStatus.PENDING.value:
        raise ShareError("这个申请已经处理过了")
    row.status = ShareStatus.REVOKED.value
    row.decided_at = _now()
    db.commit()


# ---------------------------------------------------------------- 查询


def _detail(db: Session, share_id: str) -> SpaceShare:
    owner = aliased(UserORM)
    grantee = aliased(UserORM)
    row = db.execute(
        select(SpaceShareORM, SpaceORM, owner, grantee)
        .join(SpaceORM, SpaceORM.id == SpaceShareORM.space_id)
        .join(owner, owner.id == SpaceORM.owner_id)
        .join(grantee, grantee.id == SpaceShareORM.grantee_id)
        .where(SpaceShareORM.id == share_id)
    ).first()
    if row is None:
        raise ShareError("共享记录不存在", status=404)
    return _to_share(*row)


def list_granted(
    db: Session,
    actor: User,
    space_id: str | None = None,
    *,
    include_history: bool = False,
) -> list[SpaceShare]:
    """我开放的（待审批 + 生效中）。

    默认不返回 rejected / revoked：那是历史，堆在名单里只会让"现在谁能看"
    变得看不清。需要审计时用 include_history=True 取全量。
    """
    stmt = _base_query(db).where(SpaceORM.owner_id == actor.id)
    if space_id:
        stmt = stmt.where(SpaceShareORM.space_id == space_id)
    if not include_history:
        stmt = stmt.where(
            SpaceShareORM.status.in_(
                [ShareStatus.PENDING.value, ShareStatus.ACTIVE.value]
            )
        )
    rows = db.execute(stmt.order_by(SpaceShareORM.created_at.desc())).all()
    return [_to_share(*row) for row in rows]


def list_incoming(db: Session, actor: User) -> list[SpaceShare]:
    """别人共享给我的（已生效）。"""
    rows = db.execute(
        _base_query(db)
        .where(
            SpaceShareORM.grantee_id == actor.id,
            SpaceShareORM.status == ShareStatus.ACTIVE.value,
        )
        .order_by(SpaceShareORM.decided_at.desc())
    ).all()
    return [_to_share(*row) for row in rows]


def list_outgoing(db: Session, actor: User) -> list[SpaceShare]:
    """我申请出去的（含状态与理由）。"""
    rows = db.execute(
        _base_query(db)
        .where(SpaceShareORM.grantee_id == actor.id)
        .order_by(SpaceShareORM.created_at.desc())
    ).all()
    return [_to_share(*row) for row in rows]


def set_share_enabled(db: Session, space: Space, actor: User, enabled: bool) -> Space:
    if space.owner_id != actor.id:
        raise ShareError("只有空间创建者能改这个设置", status=403)
    row = db.get(SpaceORM, space.id)
    if row is None:
        raise ShareError("空间不存在", status=404)
    row.share_enabled = bool(enabled)
    db.commit()
    space.share_enabled = bool(enabled)
    return space
