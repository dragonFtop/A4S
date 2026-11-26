"""客户端与服务端的连接状态。

客户端是主体：没有服务端也要能完整使用自己的知识库。所以这里只做一件事——
告诉界面"服务器现在通不通"，而不是让任何功能依赖它。

判定策略：
- 没配 `SERVER_URL` → `disabled`，界面不显示联机入口（纯本地模式）
- 配了但 2 秒内没响应 → `offline`，界面显示"服务器离线"，联机功能置灰
- 通了 → `online`，带上延迟，方便判断是不是网络很差

结果缓存 15 秒：界面可能每秒刷新一次状态，不缓存会把健康检查打成压力测试。
"""

from __future__ import annotations

import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from shared.config import settings

logger = logging.getLogger(__name__)

CACHE_SECONDS = 15.0


@dataclass
class LinkStatus:
    state: str  # disabled | online | offline
    url: str = ""
    latency_ms: int | None = None
    detail: str = ""
    checked_at: float = field(default_factory=time.time)

    @property
    def online(self) -> bool:
        return self.state == "online"


_cache: LinkStatus | None = None


def status(*, force: bool = False) -> LinkStatus:
    global _cache
    if not force and _cache is not None:
        if time.time() - _cache.checked_at < CACHE_SECONDS:
            return _cache

    base = (settings.server_url or "").strip().rstrip("/")
    if not base:
        _cache = LinkStatus(state="disabled", detail="未配置服务端地址，当前为纯本地模式")
        return _cache

    probe = f"{base}/api/health"
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(probe, timeout=settings.server_status_timeout) as resp:
            ok = 200 <= resp.status < 300
        elapsed = int((time.perf_counter() - started) * 1000)
        _cache = LinkStatus(
            state="online" if ok else "offline",
            url=base,
            latency_ms=elapsed,
            detail="" if ok else f"健康检查返回 {resp.status}",
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        _cache = LinkStatus(
            state="offline",
            url=base,
            detail=f"连不上服务端：{exc}",
        )
    return _cache
