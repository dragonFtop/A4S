"""空间接口：我的空间列表、建小组、成员管理。

读操作只要求"是成员"，写操作（改名/加人/移除/改角色/删除）只要求 owner——
权限判断在 `deps.require_space_owner`，这里不重复写。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from shared.api.auth import space_out
from shared.api.deps import get_current_user, get_path_space, require_path_owner
from shared.domain.models.entities import MemberRole, Space, User
from shared.infrastructure.db.session import get_db
from shared.schemas.auth import (
    MemberAddIn,
    MemberOut,
    MemberPatchIn,
    ShareDecideIn,
    ShareGrantIn,
    ShareOut,
    SpaceCreateIn,
    SpaceOut,
    SpacePatchIn,
)
import shared.identity as auth_service
import shared.services.share_service as share_service
import shared.services.space_service as space_service

router = APIRouter(prefix="/spaces", tags=["spaces"])


def _handle(exc: space_service.SpaceError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


@router.get("", response_model=list[SpaceOut])
def list_spaces(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SpaceOut]:
    """我加入的全部空间（个人空间排第一）。"""
    return [space_out(item) for item in auth_service.list_spaces(db, user.id)]


@router.post("", response_model=SpaceOut, status_code=status.HTTP_201_CREATED)
def create_space(
    payload: SpaceCreateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SpaceOut:
    try:
        space = space_service.create_group(db, user, payload.name)
    except space_service.SpaceError as exc:
        raise _handle(exc) from exc
    return space_out(space)


@router.patch("/{space_id}", response_model=SpaceOut)
def patch_space(
    space_id: str,
    payload: SpacePatchIn,
    space: Space = Depends(require_path_owner),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SpaceOut:
    """改名与「对外可查」开关合在一个接口：两者都是空间属性，前端一次 PATCH 即可。"""
    if payload.name is None and payload.share_enabled is None:
        raise HTTPException(status_code=400, detail="没有要修改的字段")

    updated = space
    try:
        if payload.name is not None:
            updated = space_service.rename_group(db, updated, payload.name)
        if payload.share_enabled is not None:
            updated = share_service.set_share_enabled(db, updated, user, payload.share_enabled)
    except space_service.SpaceError as exc:
        raise _handle(exc) from exc
    except share_service.ShareError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return space_out(updated)


@router.delete("/{space_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_space(
    space_id: str,
    space: Space = Depends(require_path_owner),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    try:
        space_service.delete_group(db, space, user)
    except space_service.SpaceError as exc:
        raise _handle(exc) from exc


@router.get("/{space_id}/members", response_model=list[MemberOut])
def list_members(
    space_id: str,
    space: Space = Depends(get_path_space),
    db: Session = Depends(get_db),
) -> list[MemberOut]:
    return [
        MemberOut(
            user_id=member.user_id,
            username=member.username,
            display_name=member.display_name,
            role=member.role.value,
            joined_at=member.joined_at,
        )
        for member in auth_service.list_members(db, space.id)
    ]


@router.post(
    "/{space_id}/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED
)
def add_member(
    space_id: str,
    payload: MemberAddIn,
    space: Space = Depends(require_path_owner),
    db: Session = Depends(get_db),
) -> MemberOut:
    try:
        member = space_service.add_member(db, space, payload.username)
    except space_service.SpaceError as exc:
        raise _handle(exc) from exc
    return MemberOut(
        user_id=member.user_id,
        username=member.username,
        display_name=member.display_name,
        role=member.role.value,
        joined_at=member.joined_at,
    )


@router.delete("/{space_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def remove_member(
    space_id: str,
    user_id: str,
    space: Space = Depends(require_path_owner),
    db: Session = Depends(get_db),
) -> None:
    try:
        space_service.remove_member(db, space, user_id)
    except space_service.SpaceError as exc:
        raise _handle(exc) from exc


@router.patch("/{space_id}/members/{user_id}", response_model=MemberOut)
def patch_member(
    space_id: str,
    user_id: str,
    payload: MemberPatchIn,
    space: Space = Depends(require_path_owner),
    db: Session = Depends(get_db),
) -> MemberOut:
    try:
        space_service.set_role(db, space, user_id, MemberRole(payload.role))
    except space_service.SpaceError as exc:
        raise _handle(exc) from exc

    for member in auth_service.list_members(db, space.id):
        if member.user_id == user_id:
            return MemberOut(
                user_id=member.user_id,
                username=member.username,
                display_name=member.display_name,
                role=member.role.value,
                joined_at=member.joined_at,
            )
    raise HTTPException(status_code=404, detail="TA 不是这个小组的成员")


# ---------------------------------------------------------------- 共享（主人视角）


@router.get("/{space_id}/shares", response_model=list[ShareOut])
def list_shares(
    space_id: str,
    space: Space = Depends(get_path_space),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ShareOut]:
    """这个空间的共享记录。只有主人看得到全部；成员看不到（那是别人的授权关系）。"""
    if space.owner_id != user.id:
        raise HTTPException(status_code=403, detail="只有空间创建者能看共享名单")
    return [
        ShareOut.model_validate(item)
        for item in share_service.list_granted(db, user, space_id=space.id)
    ]


@router.post("/{space_id}/shares", response_model=ShareOut, status_code=status.HTTP_201_CREATED)
def grant_share(
    space_id: str,
    payload: ShareGrantIn,
    space: Space = Depends(require_path_owner),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ShareOut:
    try:
        return ShareOut.model_validate(
            share_service.grant(db, space, user, payload.username, payload.message)
        )
    except share_service.ShareError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


@router.patch("/{space_id}/shares/{share_id}", response_model=ShareOut)
def decide_share(
    space_id: str,
    share_id: str,
    payload: ShareDecideIn,
    space: Space = Depends(require_path_owner),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ShareOut:
    try:
        share = share_service.decide(
            db, space, user, share_id, approve=payload.action == "approve"
        )
    except share_service.ShareError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return ShareOut.model_validate(share)


@router.delete(
    "/{space_id}/shares/{share_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
def revoke_share(
    space_id: str,
    share_id: str,
    space: Space = Depends(require_path_owner),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    try:
        share_service.revoke(db, space, user, share_id)
    except share_service.ShareError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
