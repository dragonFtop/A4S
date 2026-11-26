"""共享（申请方视角）：申请别人的知识库、撤回申请、看共享总览。

主人视角的开放 / 审批 / 撤销放在 `api/spaces.py`——那些动作的对象是一个空间，
挂在 `/spaces/{id}/shares` 下更自然，也不必要求前端额外带 X-Space-Id。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from shared.api.deps import get_current_user
from shared.domain.models.entities import User
from shared.infrastructure.db.session import get_db
from shared.schemas.auth import ShareApplyIn, ShareBoardOut, ShareOut
import shared.services.share_service as share_service

router = APIRouter(prefix="/shares", tags=["shares"])


def _handle(exc: share_service.ShareError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


def _out(share) -> ShareOut:
    return ShareOut.model_validate(share)


@router.get("", response_model=ShareBoardOut)
def board(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ShareBoardOut:
    """三个视角一次给全：我开放的、共享给我的、我申请的。"""
    return ShareBoardOut(
        granted=[_out(item) for item in share_service.list_granted(db, user)],
        incoming=[_out(item) for item in share_service.list_incoming(db, user)],
        outgoing=[_out(item) for item in share_service.list_outgoing(db, user)],
    )


@router.post("/apply", response_model=ShareOut, status_code=status.HTTP_201_CREATED)
def apply(
    payload: ShareApplyIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ShareOut:
    try:
        return _out(share_service.apply(db, user, payload.username, payload.message))
    except share_service.ShareError as exc:
        raise _handle(exc) from exc


@router.delete("/{share_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def cancel(
    share_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    try:
        share_service.cancel(db, user, share_id)
    except share_service.ShareError as exc:
        raise _handle(exc) from exc
