"""定时任务接口（FR-08）。

读写都落到 `.env`（与设置页同一份配置），执行交给 `infrastructure/scheduler.py`。
这里只做「回显状态 + 触发一次」，不自己持有调度对象。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from client.infrastructure import scheduler

router = APIRouter(prefix="/schedule", tags=["schedule"])


@router.get("")
def read_schedule() -> dict:
    return scheduler.state()


@router.post("/run")
def run_schedule_now() -> dict:
    """立即执行一次：生成报告并推送。

    用于验收「周期触发能收到邮件」——不必真等到周一，
    点这个按钮走的是与定时触发完全相同的代码路径。
    """
    try:
        result = scheduler.run_now()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"执行失败：{exc}") from exc

    if not scheduler.state()["enabled"]:
        # 允许执行，但要说清当前开关是关的，避免误以为定时已生效
        return {**result, "enabled": False, "message": "已执行一次（定时开关当前为关闭）"}
    return {**result, "enabled": True}
