"""学习小组。

从 ragflow-plus 抄来的是**模型**，不是实现：团队不另建实体，就是「把别人加进
同一个归属容器」。所以这里没有 Team 表，只有 Space 和 SpaceMember。

几条规则集中在这里，路由不用重复判断：

- 个人空间不能改名、不能加人、不能删——它不是"小组"，是账号的家；
- 唯一 owner 不能被移除、不能降级，否则小组会变成没人能管的孤儿；
- 按用户名加人，不加邀请链接（单机/小团队，省一整条链路）。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from shared.domain.models.entities import (
    MemberRole,
    Space,
    SpaceKind,
    SpaceMember,
    User,
)
logger = logging.getLogger(__name__)

from shared.infrastructure.db.models import (  # noqa: E402
    ChunkORM,
    DocumentORM,
    DocumentTagORM,
    NoteORM,
    NoteSourceORM,
    NoteTagORM,
    PracticeSessionORM,
    ReviewReportORM,
    SpaceMemberORM,
    SpaceORM,
    StudyPlanORM,
    TopicORM,
    UserORM,
    WrongAnswerORM,
)


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
    from shared.infrastructure.db.seed import ensure_builtin

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
    _purge_space_data(db, space.id)
    db.delete(row)
    db.commit()


def _purge_space_data(db: Session, space_id: str) -> None:
    """清掉一个空间的全部业务数据，包括向量与原始文件。

    为什么必须显式做：SQLite 默认不启用外键约束（PRAGMA foreign_keys=0），
    所以删 spaces 那一行不会带走 documents / chunks / notes。留下的是
    「列表里看不到、但仍在向量表里占位并参与全表扫描」的孤儿数据——
    它们永远不会被返回，却一直拖慢别人的检索、并且检索结果里取不到正文。

    顺序从"叶子"往"根"删：向量/全文索引 → 关联表 → 片段 → 资料 → 业务表 → 主题。
    """
    chunk_ids = list(db.execute(select(ChunkORM.id).where(ChunkORM.space_id == space_id)).scalars())
    doc_rows = list(
        db.execute(select(DocumentORM.id, DocumentORM.storage_path).where(DocumentORM.space_id == space_id)).all()
    )
    doc_ids = [row[0] for row in doc_rows]
    paths = [row[1] for row in doc_rows if row[1]]

    if chunk_ids:
        try:
            from shared.infrastructure.db.session import get_vector_store

            get_vector_store().delete_chunks(chunk_ids)
        except Exception as exc:  # noqa: BLE001 - 向量清理失败不该挡住删空间
            logger.warning("删除空间的向量失败（空间 %s）：%s", space_id, exc)

        from shared.infrastructure.db import fts
        from shared.infrastructure.db.session import FTS_ENABLED

        if FTS_ENABLED:
            try:
                fts.remove_chunks(db.connection(), chunk_ids)
            except Exception as exc:  # noqa: BLE001
                logger.warning("删除空间的片段索引失败（空间 %s）：%s", space_id, exc)

    note_ids = list(db.execute(select(NoteORM.id).where(NoteORM.space_id == space_id)).scalars())
    if note_ids:
        db.execute(delete(NoteSourceORM).where(NoteSourceORM.note_id.in_(note_ids)))
        db.execute(delete(NoteTagORM).where(NoteTagORM.note_id.in_(note_ids)))
    if doc_ids:
        db.execute(delete(DocumentTagORM).where(DocumentTagORM.document_id.in_(doc_ids)))

    db.execute(delete(ChunkORM).where(ChunkORM.space_id == space_id))
    db.execute(delete(DocumentORM).where(DocumentORM.space_id == space_id))
    db.execute(delete(NoteORM).where(NoteORM.space_id == space_id))
    db.execute(delete(PracticeSessionORM).where(PracticeSessionORM.space_id == space_id))
    db.execute(delete(WrongAnswerORM).where(WrongAnswerORM.space_id == space_id))
    db.execute(delete(StudyPlanORM).where(StudyPlanORM.space_id == space_id))
    db.execute(delete(ReviewReportORM).where(ReviewReportORM.space_id == space_id))
    db.execute(delete(TopicORM).where(TopicORM.space_id == space_id))

    # 原始文件最后删：即使这里失败，数据库里已经没有引用它的记录了
    from pathlib import Path

    from shared.config import DATA_DIR

    for rel in paths:
        try:
            (Path(DATA_DIR) / rel).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("删除原始文件失败（忽略）：%s", exc)


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
