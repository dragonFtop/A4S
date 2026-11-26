"""客户端联邦问答接口（P3 / FR-3.4）。

请求方视角：把自己的问题交给 server 中继，等目标节点回传 citations，
再用本机模型重排生成答案。未配置服务端时返回 400。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from shared.api.deps import get_current_user
from shared.domain.models.entities import User
import client.infrastructure.federation as fed_node
import client.services.federation_service as federation_service

router = APIRouter(prefix="/fed", tags=["federation"])


class FedAskIn(BaseModel):
    target_node_id: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=2000)


class FedAskOut(BaseModel):
    status: str
    answer: str = ""
    citations: list[dict] = Field(default_factory=list)
    target_node_id: str
    latency_ms: int = 0


@router.post("/ask", response_model=FedAskOut)
def ask(
    payload: FedAskIn,
    user: User = Depends(get_current_user),
) -> FedAskOut:
    try:
        result = federation_service.ask(
            payload.target_node_id,
            payload.question,
            requester_node_id=fed_node.node_id(),
        )
    except federation_service.FedUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FedAskOut(**result)
