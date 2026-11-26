"""A4S 客户端入口（跑在用户自己的电脑上）。

它必须**不依赖服务端就能跑**：账号可以本地注册，数据库是本机文件，
生成模型由用户自己配置。服务端只是可选的上层——用来同步小组、目录与联邦。

所以这里刻意不 import 任何 `server.*`；共享能力全部来自 `shared.*`。
"""

from __future__ import annotations

import os

# 在 import shared.* 之前声明角色：shared.config 的 settings 是导入时单例，
# 之后再改 APP_ROLE 无效。显式 DATABASE_URL（测试/迁移/自定义）优先级更高。
os.environ.setdefault("APP_ROLE", "client")

from client.api.router import api_router as client_router
from client.infrastructure import federation as federation_node
from client.infrastructure import scheduler
from shared.app_factory import build_app

app = build_app(
    title="A4S Client",
    description="本地知识库：资料、笔记、问答、练习。可脱离服务端独立运行。",
    extra_routers=[client_router],
    scheduler_module=scheduler,
    # 配置了 SERVER_URL 才启动联邦节点轮询；纯本地模式静默跳过
    on_startup=federation_node.start,
)
