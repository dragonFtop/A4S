"""账号与会话。

三个决定，都为了"个人项目不引入多余依赖"：

1. **密码用标准库 `hashlib.scrypt`**，格式 `scrypt$salt$hash`，不装 passlib/bcrypt。
2. **token 存库**而不是签 JWT：退出即失效、能看谁在线。代价是每次请求多一次
   主键查询，SQLite 上可以忽略。JWT 的好处（无状态、多服务共享）这里用不上。
3. **注册即建个人空间**，并把迁移遗留的无主数据（space_id='default'）认领给
   第一个用户——单机项目升级后不该出现"数据还在但看不见"。
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from shared.domain.models.entities import (
    LEGACY_SPACE_ID,
    MemberRole,
    Space,
    SpaceKind,
    SpaceMember,
    ShareStatus,
    User,
)
from shared.infrastructure.db.models import (
    AuthTokenORM,
    SpaceMemberORM,
    SpaceORM,
    SpaceShareORM,
    UserORM,
)

# 会话有效期。单机项目不追求强安全，但也别让 token 永不过期。
TOKEN_TTL_DAYS = 14
# scrypt 开销：n=2^14, r=8 约 16 MB 内存，登录一次几十毫秒，够用。
_SCRYPT_N = 16384
_SCRYPT_R = 8
_SCRYPT_P = 1
_MAX_MEM = 64 * 1024 * 1024

# 认领时按这个顺序改，先子表后父表（其实 UPDATE 无外键级联，顺序只为可读）
_LEGACY_TABLES = (
    "topics",
    "documents",
    "chunks",
    "notes",
    "practice_sessions",
    "wrong_answers",
    "study_plans",
    "review_reports",
)


class AuthError(Exception):
    """账号相关的可预期失败（用户名重复、密码不对等），由接口层转成 4xx。"""

    def __init__(self, detail: str, *, code: str = "auth_error") -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------- 密码

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        dklen=32, maxmem=_MAX_MEM,
    )
    return "scrypt${}${}".format(
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(derived).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, hash_b64 = stored.split("$")
    except (AttributeError, ValueError):
        return False
    if scheme != "scrypt":
        return False
    try:
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        dklen=len(expected), maxmem=_MAX_MEM,
    )
    return secrets.compare_digest(derived, expected)


# ---------------------------------------------------------------- 转换

def _to_user(row: UserORM) -> User:
    return User(
        id=row.id,
        username=row.username,
        password_hash=row.password_hash,
        display_name=row.display_name,
        status=row.status,
        created_at=row.created_at,
        last_login_at=row.last_login_at,
    )


def _to_space(row: SpaceORM, *, my_role: MemberRole, member_count: int) -> Space:
    return Space(
        id=row.id,
        name=row.name,
        kind=SpaceKind(row.kind),
        owner_id=row.owner_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        member_count=member_count,
        my_role=my_role,
        share_enabled=bool(row.share_enabled),
    )


# ---------------------------------------------------------------- 校验

def _clean_username(raw: str) -> str:
    name = (raw or "").strip()
    if not 3 <= len(name) <= 32:
        raise AuthError("用户名需要 3～32 个字符", code="invalid_username")
    if not all(ch.isalnum() or ch in "_-" for ch in name):
        raise AuthError("用户名只能包含字母、数字、下划线和短横线", code="invalid_username")
    return name


def _clean_password(raw: str) -> str:
    if not raw or len(raw) < 6:
        raise AuthError("密码至少 6 位", code="invalid_password")
    if len(raw) > 128:
        raise AuthError("密码过长", code="invalid_password")
    return raw


# ---------------------------------------------------------------- 注册 / 登录

def register(
    db: Session,
    *,
    username: str,
    password: str,
    display_name: str = "",
) -> tuple[User, Space, str]:
    """注册并返回 (用户, 个人空间, token)。第一个用户会认领遗留数据。"""
    name = _clean_username(username)
    pwd = _clean_password(password)

    exists = db.execute(select(UserORM.id).where(UserORM.username == name)).first()
    if exists is not None:
        raise AuthError("这个用户名已经被占用", code="username_taken")

    is_first = int(db.execute(select(func.count()).select_from(UserORM)).scalar() or 0) == 0
    now = _now()

    user_row = UserORM(
        id=str(uuid.uuid4()),
        username=name,
        password_hash=hash_password(pwd),
        display_name=(display_name or name).strip()[:64],
        status="active",
        created_at=now,
    )
    db.add(user_row)
    db.flush()

    space_row = SpaceORM(
        id=str(uuid.uuid4()),
        name=f"{user_row.display_name}的空间",
        kind=SpaceKind.PERSONAL.value,
        owner_id=user_row.id,
        created_at=now,
        updated_at=now,
    )
    db.add(space_row)
    db.flush()
    db.add(
        SpaceMemberORM(
            space_id=space_row.id,
            user_id=user_row.id,
            role=MemberRole.OWNER.value,
            joined_at=now,
        )
    )
    # 每个空间都要有自己的「未分类」主题
    from shared.infrastructure.db.seed import ensure_builtin

    ensure_builtin(db, space_row.id)

    if is_first:
        # 升级前留下的数据没有主人，第一个注册的人就是它主人。
        # 但 topics 里的「未分类」要跳过：新空间刚刚建好了自己那份，
        # 把旧的挪过来会在 (space_id, id) 主键上撞车。旧数据里引用
        # topic_id='uncategorized' 的纪录，正好指向新空间这份，语义也对。
        for table in _LEGACY_TABLES:
            if table == "topics":
                db.execute(
                    text(
                        "UPDATE topics SET space_id = :sid "
                        "WHERE space_id = :legacy AND id != 'uncategorized'"
                    ),
                    {"sid": space_row.id, "legacy": LEGACY_SPACE_ID},
                )
            else:
                db.execute(
                    text(f"UPDATE {table} SET space_id = :sid WHERE space_id = :legacy"),
                    {"sid": space_row.id, "legacy": LEGACY_SPACE_ID},
                )

    token = _issue_token(db, user_row.id)
    db.commit()
    db.refresh(user_row)
    db.refresh(space_row)

    return (
        _to_user(user_row),
        _to_space(space_row, my_role=MemberRole.OWNER, member_count=1),
        token,
    )


def login(db: Session, *, username: str, password: str) -> tuple[User, str]:
    row = db.execute(
        select(UserORM).where(UserORM.username == (username or "").strip())
    ).scalar_one_or_none()
    if row is None or not verify_password(password or "", row.password_hash):
        # 不区分"用户不存在"和"密码错误"，避免被用来枚举账号
        raise AuthError("用户名或密码不对", code="bad_credentials")
    if row.status != "active":
        raise AuthError("这个账号已被停用", code="account_disabled")

    row.last_login_at = _now()
    token = _issue_token(db, row.id)
    db.commit()
    db.refresh(row)
    return _to_user(row), token


def logout(db: Session, token: str) -> None:
    row = db.get(AuthTokenORM, token)
    if row is not None:
        db.delete(row)
        db.commit()


def _issue_token(db: Session, user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    db.add(
        AuthTokenORM(
            token=token,
            user_id=user_id,
            created_at=now,
            expires_at=now + timedelta(days=TOKEN_TTL_DAYS),
        )
    )
    db.flush()
    return token


# ---------------------------------------------------------------- 会话查询

def user_from_token(db: Session, token: str) -> User | None:
    """校验 token 并返回用户。过期的 token 顺手删掉。"""
    if not token:
        return None
    row = db.get(AuthTokenORM, token)
    if row is None:
        return None
    if row.expires_at <= _now():
        db.delete(row)
        db.commit()
        return None
    user_row = db.get(UserORM, row.user_id)
    if user_row is None or user_row.status != "active":
        return None
    return _to_user(user_row)


def list_spaces(db: Session, user_id: str) -> list[Space]:
    """我能进的全部空间：加入的（owner/member）+ 别人共享给我的（viewer）。

    两者混在一个列表里，是因为对界面来说「要显示哪些空间」是同一个问题；
    区别只体现在 my_role 上，写权限由依赖层统一拦。
    """
    joined = db.execute(
        select(SpaceORM, SpaceMemberORM.role)
        .join(SpaceMemberORM, SpaceMemberORM.space_id == SpaceORM.id)
        .where(SpaceMemberORM.user_id == user_id)
        .order_by(SpaceORM.created_at)
    ).all()

    shared = db.execute(
        select(SpaceORM)
        .join(SpaceShareORM, SpaceShareORM.space_id == SpaceORM.id)
        .where(
            SpaceShareORM.grantee_id == user_id,
            SpaceShareORM.status == ShareStatus.ACTIVE.value,
        )
        .order_by(SpaceORM.created_at)
    ).scalars().all()

    if not joined and not shared:
        return []

    ids = [row[0].id for row in joined] + [space.id for space in shared]
    counts = dict(
        db.execute(
            select(SpaceMemberORM.space_id, func.count())
            .where(SpaceMemberORM.space_id.in_(ids))
            .group_by(SpaceMemberORM.space_id)
        ).all()
    )

    spaces: list[Space] = []
    seen: set[str] = set()
    for space, role in joined:
        spaces.append(
            _to_space(space, my_role=MemberRole(role), member_count=int(counts.get(space.id, 0)))
        )
        seen.add(space.id)
    for space in shared:
        if space.id in seen:
            continue  # 双重身份（既是成员又被单独共享）按成员算
        spaces.append(
            _to_space(space, my_role=MemberRole.VIEWER, member_count=int(counts.get(space.id, 0)))
        )

    # 个人空间最前 → 我加入的小组 → 共享给我的（只读的最后，避免误当成自己的）
    def sort_key(space: Space):
        group = 0 if space.kind is SpaceKind.PERSONAL else (1 if space.my_role is not MemberRole.VIEWER else 2)
        return (group, space.created_at)

    spaces.sort(key=sort_key)
    return spaces


def personal_space(db: Session, user_id: str) -> Space | None:
    for space in list_spaces(db, user_id):
        if space.kind is SpaceKind.PERSONAL:
            return space
    return None


def member_role(db: Session, space_id: str, user_id: str) -> MemberRole | None:
    row = db.get(SpaceMemberORM, (space_id, user_id))
    if row is None:
        return None
    return MemberRole(row.role)


def list_members(db: Session, space_id: str) -> list[SpaceMember]:
    rows = db.execute(
        select(SpaceMemberORM, UserORM)
        .join(UserORM, UserORM.id == SpaceMemberORM.user_id)
        .where(SpaceMemberORM.space_id == space_id)
        .order_by(SpaceMemberORM.joined_at)
    ).all()
    return [
        SpaceMember(
            space_id=member.space_id,
            user_id=member.user_id,
            role=MemberRole(member.role),
            joined_at=member.joined_at,
            username=user.username,
            display_name=user.display_name,
        )
        for member, user in rows
    ]
