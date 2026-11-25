"""种子数据：内置「未分类」主题与预置学习方向包。

预置包数据在 app/seed/topic_packs.json（PRD 附录 B 的机器可读版本）。
导入策略是**按名称幂等**：已存在的主题/标签跳过，不报错、不覆盖用户改过的颜色。
"""

from __future__ import annotations

import json
import uuid
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models.entities import UNCATEGORIZED_ID, UNCATEGORIZED_NAME, TopicKind
from app.infrastructure.db.models import TopicORM

SEED_PATH = Path(__file__).resolve().parents[2] / "seed" / "topic_packs.json"

DEFAULT_TAG_COLOR = "#888780"


@lru_cache
def load_topic_packs() -> dict:
    """读取预置包（带缓存，文件很小）。"""
    with SEED_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def ensure_builtin(db: Session) -> None:
    """确保「未分类」主题存在。documents.topic_id 的默认值指向它。"""
    exists = db.get(TopicORM, UNCATEGORIZED_ID)
    if exists is not None:
        return
    db.add(
        TopicORM(
            id=UNCATEGORIZED_ID,
            name=UNCATEGORIZED_NAME,
            kind=TopicKind.TOPIC.value,
            color="#888780",
            sort_order=10_000,  # 永远排在最后
        )
    )
    db.commit()


def _existing_names(db: Session, kind: str) -> set[str]:
    rows = db.execute(select(TopicORM.name).where(TopicORM.kind == kind)).scalars()
    return set(rows)


def apply_topic_packs(db: Session, pack_ids: list[str]) -> dict:
    """按包创建主题与默认标签。返回新建数量与跳过的包。"""
    data = load_topic_packs()
    packs = {p["id"]: p for p in data["packs"]}

    created_topics = 0
    created_tags = 0
    skipped_packs: list[str] = []

    existing_topics = _existing_names(db, TopicKind.TOPIC.value)
    existing_tags = _existing_names(db, TopicKind.TAG.value)
    # 递增计数器：不能用 offset，否则多个包之间会撞号
    order_counter = len(existing_topics)

    for pack_id in pack_ids:
        pack = packs.get(pack_id)
        if pack is None:
            skipped_packs.append(pack_id)
            continue
        for item in pack["topics"]:
            if item["name"] in existing_topics:
                continue
            order_counter += 1
            db.add(
                TopicORM(
                    id=str(uuid.uuid4()),
                    name=item["name"],
                    kind=TopicKind.TOPIC.value,
                    color=item.get("color", DEFAULT_TAG_COLOR),
                    sort_order=order_counter,
                )
            )
            existing_topics.add(item["name"])
            created_topics += 1

    # 默认标签全局共用一份，不论选了哪个包
    for order, item in enumerate(data.get("default_tags", [])):
        if item["name"] in existing_tags:
            continue
        db.add(
            TopicORM(
                id=str(uuid.uuid4()),
                name=item["name"],
                kind=TopicKind.TAG.value,
                color=item.get("color", DEFAULT_TAG_COLOR),
                sort_order=order,
            )
        )
        existing_tags.add(item["name"])
        created_tags += 1

    db.commit()
    return {
        "created_topics": created_topics,
        "created_tags": created_tags,
        "skipped_packs": skipped_packs,
    }


def topic_seeded(db: Session) -> bool:
    """是否已有除「未分类」之外的主题——用于判断要不要展示引导层。"""
    row = db.execute(
        select(TopicORM.id)
        .where(TopicORM.kind == TopicKind.TOPIC.value)
        .where(TopicORM.id != UNCATEGORIZED_ID)
        .limit(1)
    ).first()
    return row is not None
