"""兼容入口（过渡期）。

`client.main` 才是客户端真正的入口。保留这个模块是为了让现有脚本与
`uvicorn app.main:app` 继续可用，等测试脚本迁完就删。
"""

from __future__ import annotations

from client.main import app

__all__ = ["app"]
