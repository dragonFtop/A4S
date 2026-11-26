"""空间接口：我的空间列表、建小组、成员管理。

读操作只要求"是成员"，写操作（改名/加人/移除/改角色/删除）只要求 owner——
权限判断在 `deps.require_space_owner`，这里不重复写。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.auth import space_out
from app.api.deps import get_current_user, get_path_space, require_path_owner
from app.domain.models.entities import MemberRole, Space, User
from app.infrastructure.db.session import get_db
from app.schemas.auth import (
    MemberAddIn,
    MemberOut,
    MemberPatchIn,
    SpaceCreateIn,
    SpaceOut,
    SpacePatchIn,
)
from app.services import auth_service, space_service

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
def rename_space(
    space_id: str,
    payload: SpacePatchIn,
    space: Space = Depends(require_path_owner),
    db: Session = Depends(get_db),
) -> SpaceOut:
    try:
        updated = space_service.rename_group(db, space, payload.name)
    except space_service.SpaceError as exc:
        raise _handle(exc) from exc
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
