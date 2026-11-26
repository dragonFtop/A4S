"""账号接口：注册 / 登录 / 登出 / 当前身份。

注册和登录都直接返回完整现场（token + 用户 + 空间列表），前端拿到就能渲染，
不用再追三个请求。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.api.deps import get_current_space, get_current_user
from app.domain.models.entities import Space, User
from app.infrastructure.db.session import get_db
from app.schemas.auth import (
    AuthOut,
    LoginIn,
    MeOut,
    RegisterIn,
    SpaceOut,
    UserOut,
)
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

_bearer = HTTPBearer(auto_error=False)


def space_out(space: Space) -> SpaceOut:
    return SpaceOut(
        id=space.id,
        name=space.name,
        kind=space.kind.value,
        owner_id=space.owner_id,
        member_count=space.member_count,
        my_role=space.my_role.value,
        created_at=space.created_at,
    )


def user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


@router.post("/register", response_model=AuthOut, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterIn, db: Session = Depends(get_db)) -> AuthOut:
    """注册。第一个注册的账号会认领升级前遗留的数据。"""
    try:
        user, space, token = auth_service.register(
            db,
            username=payload.username,
            password=payload.password,
            display_name=payload.display_name,
        )
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc

    spaces = auth_service.list_spaces(db, user.id)
    return AuthOut(
        token=token,
        user=user_out(user),
        space=space_out(space),
        spaces=[space_out(item) for item in spaces],
    )


@router.post("/login", response_model=AuthOut)
def login(payload: LoginIn, db: Session = Depends(get_db)) -> AuthOut:
    try:
        user, token = auth_service.login(
            db, username=payload.username, password=payload.password
        )
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=401, detail=exc.detail) from exc

    spaces = auth_service.list_spaces(db, user.id)
    if not spaces:
        raise HTTPException(status_code=403, detail="账号下没有任何空间")
    return AuthOut(
        token=token,
        user=user_out(user),
        space=space_out(spaces[0]),
        spaces=[space_out(item) for item in spaces],
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def logout(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> None:
    """删除当前 token。没带 token 也返回 204——登出应当永远"成功"。"""
    if credentials is not None:
        auth_service.logout(db, credentials.credentials)


@router.get("/me", response_model=MeOut)
def me(
    user: User = Depends(get_current_user),
    space: Space = Depends(get_current_space),
    db: Session = Depends(get_db),
) -> MeOut:
    spaces = auth_service.list_spaces(db, user.id)
    return MeOut(
        user=user_out(user),
        spaces=[space_out(item) for item in spaces],
        current_space=space_out(space),
    )
