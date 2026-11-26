"""认证与空间上下文。

两条依赖：

- `get_current_user`：验 token，拿不到就 401；
- `get_current_space`：在用户所属的空间里选一个（默认个人空间，可用
  `X-Space-Id` 头指定），拿不到就是 403。

`get_current_space` 内部也依赖 `get_db`，FastAPI 在同一请求内会复用同一个
Session，所以路由同时声明两者不会开出两个连接。
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.domain.models.entities import MemberRole, Space, User
from app.infrastructure.db.session import get_db
from app.services import auth_service

_bearer = HTTPBearer(auto_error=False, description="登录后返回的 token")


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    token = credentials.credentials if credentials else ""
    user = auth_service.user_from_token(db, token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def get_current_space(
    x_space_id: str | None = Header(default=None, alias="X-Space-Id"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Space:
    spaces = auth_service.list_spaces(db, user.id)
    if not spaces:
        # 注册流程保证至少有一个个人空间，走到这里说明数据被改坏了
        raise HTTPException(status_code=403, detail="账号下没有任何空间")
    if x_space_id:
        for space in spaces:
            if space.id == x_space_id:
                return space
        # 不在成员表里的空间一律当作不存在，不泄露"这个空间存在但你进不去"
        raise HTTPException(status_code=403, detail="你不在这个空间里")
    return spaces[0]


def require_space_owner(space: Space = Depends(get_current_space)) -> Space:
    """成员管理、改空间名、删空间都只允许 owner。"""
    if space.my_role is not MemberRole.OWNER:
        raise HTTPException(status_code=403, detail="只有空间创建者能做这个操作")
    return space


def get_path_space(
    space_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Space:
    """从路径里的 space_id 取空间。

    `/spaces/{space_id}/...` 这类接口不该再要求前端同时发 `X-Space-Id` 头：
    路径已经指明了要对哪个空间动手，再让两个地方描述同一件事只会冲突。
    """
    for space in auth_service.list_spaces(db, user.id):
        if space.id == space_id:
            return space
    raise HTTPException(status_code=403, detail="你不在这个空间里")


def require_path_owner(space: Space = Depends(get_path_space)) -> Space:
    if space.my_role is not MemberRole.OWNER:
        raise HTTPException(status_code=403, detail="只有空间创建者能做这个操作")
    return space
