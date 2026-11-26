"""账号与空间的出入参。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=6, max_length=128)
    display_name: str = Field(default="", max_length=64)


class LoginIn(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    display_name: str
    created_at: datetime
    last_login_at: datetime | None = None


class SpaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    kind: str
    owner_id: str
    member_count: int = 0
    # 我在这个空间里的角色，前端据此决定要不要显示"成员管理"
    my_role: str = "member"
    created_at: datetime


class AuthOut(BaseModel):
    """注册 / 登录成功后的完整现场：token + 用户 + 当前空间 + 全部空间。"""

    token: str
    user: UserOut
    space: SpaceOut
    spaces: list[SpaceOut] = []


class MeOut(BaseModel):
    user: UserOut
    spaces: list[SpaceOut]
    current_space: SpaceOut


class SpaceCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class SpacePatchIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class MemberAddIn(BaseModel):
    # 按用户名加人：单机/小团队场景下，比发邀请链接省掉一整条链路
    username: str = Field(min_length=1, max_length=64)


class MemberPatchIn(BaseModel):
    role: str = Field(pattern="^(owner|member)$")


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    username: str
    display_name: str
    role: str
    joined_at: datetime
