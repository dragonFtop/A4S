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
    # 我在这个空间里的角色，前端据此决定要不要显示"成员管理"与写操作
    my_role: str = "member"
    # 是否允许外部申请检索（默认关）。只读空间据此隐藏"共享"入口。
    share_enabled: bool = False
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
    name: str | None = Field(default=None, min_length=1, max_length=64)
    # 打开后才会进公共目录、才接受别人的申请
    share_enabled: bool | None = None


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


# ---------------------------------------------------------------- 共享


class ShareApplyIn(BaseModel):
    # 按用户名申请对方的个人知识库
    username: str = Field(min_length=1, max_length=64)
    message: str = Field(default="", max_length=200)


class ShareGrantIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    message: str = Field(default="", max_length=200)


class ShareDecideIn(BaseModel):
    action: str = Field(pattern="^(approve|reject)$")


class ShareOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    space_id: str
    space_name: str = ""
    owner_name: str = ""
    grantee_id: str
    grantee_username: str = ""
    grantee_name: str = ""
    status: str
    origin: str
    message: str = ""
    created_at: datetime
    decided_at: datetime | None = None


class ShareBoardOut(BaseModel):
    """共享总览：我开放的 / 共享给我的 / 我申请的。"""

    granted: list[ShareOut] = []
    incoming: list[ShareOut] = []
    outgoing: list[ShareOut] = []
