"""服务端状态接口。

不需要登录：登录页也要能显示"服务器离线"，否则用户会以为是密码错了。
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from client import server_link

router = APIRouter(prefix="/server", tags=["server"])


@router.get("/status")
def status(force: bool = Query(default=False, description="跳过 15 秒缓存重新探测")) -> dict:
    item = server_link.status(force=force)
    return {
        "state": item.state,
        "online": item.online,
        "url": item.url,
        "latency_ms": item.latency_ms,
        "detail": item.detail,
    }
