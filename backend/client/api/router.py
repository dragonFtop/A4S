"""客户端专属路由：问答、练习、回顾、学习计划、服务端连接状态。

这些功能读写的都是「个人数据」，放在本地库里；服务端不挂载它们。
"""

from __future__ import annotations

from fastapi import APIRouter

from client.api import chat, federation, plan, practice, review, schedule, server_link, settings

api_router = APIRouter()
api_router.include_router(chat.router)
api_router.include_router(federation.router)
api_router.include_router(plan.router)
api_router.include_router(practice.router)
api_router.include_router(review.router)
api_router.include_router(schedule.router)
api_router.include_router(server_link.router)
api_router.include_router(settings.router)
