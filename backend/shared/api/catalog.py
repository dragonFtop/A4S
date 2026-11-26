"""公共目录接口（P2 / FR-2.4 / AC-11）。

只有本组范围内可见：catalog 的可见性完全由 `catalog_service.build_catalog`
按「成员或被授权人」判定，组外用户查到的就是空列表。不需要额外参数——
问的人是谁，就只看得到他能看的那部分。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from shared.api.deps import get_current_user
from shared.domain.models.entities import User
from shared.infrastructure.db.session import get_db
from shared.schemas.catalog import CatalogEntryOut, CatalogListOut
from shared.services.catalog_service import build_catalog

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("", response_model=CatalogListOut)
def list_catalog(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CatalogListOut:
    """当前用户可见的公共目录（组内/被授权可见，组外为空）。"""
    entries = build_catalog(db, viewer_id=user.id)
    return CatalogListOut(
        items=[
            CatalogEntryOut(
                space_id=e.space_id,
                space_name=e.space_name,
                owner_id=e.owner_id,
                owner_name=e.owner_name,
                topic=e.topic,
                document_count=e.document_count,
                updated_at=e.updated_at,
                vector_version=e.vector_version,
                online=e.online,
                quota_remaining=e.quota_remaining,
            )
            for e in entries
        ],
        total=len(entries),
    )
