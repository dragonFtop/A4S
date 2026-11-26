"""问答接口（FR-03）。

POST /api/chat 返回 SSE 流：
- `sources`：引用列表（先生成，前端先渲染来源卡片再等正文）
- `delta`：增量正文
- `no_context`：没检索到依据，随后仍会用通用知识作答（`fallback=true`）；
  知识库为空时 `reason=empty`，有资料但本次没召回时 `reason=no_match`
- `error`：模型侧或配置侧错误
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from shared.api.deps import get_current_space, get_current_user
from shared.domain.models.entities import Space, User
from shared.schemas.chat import ChatIn
import client.services.chat_service as chat_service

router = APIRouter(prefix="/chat", tags=["chat"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    # 关掉代理/网关的缓冲，否则 delta 会攒成一坨才到前端
    "X-Accel-Buffering": "no",
}


@router.post("")
async def ask(
    payload: ChatIn,
    space: Space = Depends(get_current_space),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    body = payload.model_dump()
    body["space_id"] = space.id
    # 多源选源（P2）需要知道"问的人是谁"来判定 catalog 可见性
    body["user_id"] = user.id
    return StreamingResponse(
        chat_service.sse_chat(body),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
