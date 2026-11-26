"""共用路由：账号、空间、授权、内容。

这些接口本身与"数据存在哪"无关——它们只操作传进来的 Session。因此服务端
（托管小组公共库）和客户端（托管个人库）都会挂载它们，各自连自己的库。

客户端专属的功能（问答生成、练习、回顾、计划）不在这里，见 `client/api/router.py`。
"""

from __future__ import annotations

from fastapi import APIRouter

from shared.api import (
    auth,
    catalog,
    documents,
    health,
    notes,
    overview,
    reviews,
    shares,
    spaces,
    topics,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(spaces.router)
api_router.include_router(reviews.router)
api_router.include_router(catalog.router)
api_router.include_router(shares.router)
api_router.include_router(overview.router)
api_router.include_router(topics.router)
api_router.include_router(documents.router)
api_router.include_router(notes.router)
