"""A4S 服务端入口（部署在服务器上）。

职责边界：
- 账号与登录（全局唯一身份）
- 空间关系与授权（成员、共享）
- 小组公共库的资料与向量
- 后续：公共目录 catalog、联邦查询中继

**不存个人知识库的内容**——那在用户自己的客户端上。
"""

from __future__ import annotations

import os

# 在 import shared.* 之前声明角色：shared.config 的 settings 是导入时单例，
# 之后再改 APP_ROLE 无效。显式 DATABASE_URL（测试/迁移/自定义）优先级更高。
os.environ.setdefault("APP_ROLE", "server")

from shared.app_factory import build_app
from server.api import federation as federation_api

app = build_app(
    title="A4S Server",
    description="公共服务：账号、小组、授权、公共目录与小组知识库。",
    extra_routers=[federation_api.router],
)
