"""主题与标签接口（FR-12）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from shared.api.deps import get_current_space
from shared.domain.models.entities import UNCATEGORIZED_ID, Space, Topic, TopicKind
from shared.infrastructure.db.seed import load_topic_packs, topic_seeded
from shared.infrastructure.db.session import get_db
from shared.repositories.sqlalchemy_repo import SqlAlchemyTopicRepository
from shared.schemas.topics import (
    ApplyPacksIn,
    ApplyPacksOut,
    PackOut,
    SeededOut,
    TopicCreate,
    TopicListOut,
    TopicOut,
    TopicUpdate,
)
import shared.services.topic_service as topic_service
from shared.services.topic_service import TopicConflict, TopicProtected

router = APIRouter(prefix="/topics", tags=["topics"])


def _out(topic: Topic) -> TopicOut:
    return TopicOut(
        id=topic.id,
        name=topic.name,
        kind=topic.kind.value,
        color=topic.color,
        sort_order=topic.sort_order,
        count=topic.count,
        is_builtin=topic.id == UNCATEGORIZED_ID,
    )


@router.get("", response_model=TopicListOut)
def list_topics(
    space: Space = Depends(get_current_space), db: Session = Depends(get_db)
) -> TopicListOut:
    repo = SqlAlchemyTopicRepository(db)
    return TopicListOut(
        topics=[_out(t) for t in repo.list(space.id, TopicKind.TOPIC)],
        tags=[_out(t) for t in repo.list(space.id, TopicKind.TAG)],
    )


@router.get("/seeded", response_model=SeededOut)
def seeded(
    space: Space = Depends(get_current_space), db: Session = Depends(get_db)
) -> SeededOut:
    return SeededOut(seeded=topic_seeded(db, space.id))


@router.get("/packs", response_model=list[PackOut])
def list_packs() -> list[PackOut]:
    data = load_topic_packs()
    return [
        PackOut(
            id=pack["id"],
            name=pack["name"],
            summary=pack.get("summary", ""),
            topics=[t["name"] for t in pack["topics"]],
        )
        for pack in data["packs"]
    ]


@router.post("/packs", response_model=ApplyPacksOut)
def apply_packs(
    payload: ApplyPacksIn,
    space: Space = Depends(get_current_space),
    db: Session = Depends(get_db),
) -> ApplyPacksOut:
    result = topic_service.seed_packs(db, payload.pack_ids, space.id)
    return ApplyPacksOut(**result)


@router.post("", response_model=TopicOut, status_code=201)
def create_topic(
    payload: TopicCreate,
    space: Space = Depends(get_current_space),
    db: Session = Depends(get_db),
) -> TopicOut:
    try:
        topic = topic_service.create_topic(
            db,
            space_id=space.id,
            name=payload.name,
            kind=TopicKind(payload.kind),
            color=payload.color,
        )
    except TopicConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _out(topic)


@router.patch("/{topic_id}", response_model=TopicOut)
def update_topic(
    topic_id: str,
    payload: TopicUpdate,
    space: Space = Depends(get_current_space),
    db: Session = Depends(get_db),
) -> TopicOut:
    try:
        topic = topic_service.update_topic(
            db,
            topic_id,
            space_id=space.id,
            name=payload.name,
            color=payload.color,
            sort_order=payload.sort_order,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TopicProtected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TopicConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _out(topic)


@router.delete("/{topic_id}")
def delete_topic(
    topic_id: str,
    space: Space = Depends(get_current_space),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return topic_service.delete_topic(db, topic_id, space.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TopicProtected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
