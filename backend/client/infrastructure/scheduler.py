"""定时回顾的调度器（FR-08）。

选型说明
--------
用 APScheduler 的 **BackgroundScheduler**（独立线程）而不是 AsyncIOScheduler：

- 回顾任务本质是「一次 HTTP 请求级别的等待」——调模型 10~30 秒、发一封邮件。
  放在事件循环里跑，会把同一时刻的其它请求一起卡住；
- 独立线程里可以安全地 `asyncio.run()`，不必和 uvicorn 的循环抢执行权；
- 代价是线程里写 SQLite 可能与接口线程撞锁，所以每个任务都自带 try/except
  与一次重试，失败只记日志、不抛给调度器（否则 APScheduler 会静默吞掉）。

为什么不做「补发错过的报告」：v0.1 是单机应用，关机就是关机。
启动时补发一份三天前的回顾，对用户没有价值，还会让人以为时间错乱。
PRD 把它标成 P1，这里明确不做。
"""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from shared.config import settings

logger = logging.getLogger(__name__)

JOB_ID = "weekly_review"
WEEKDAY_CODES = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}

_scheduler: BackgroundScheduler | None = None


def _trigger() -> CronTrigger:
    day = settings.scheduler_cron_day_of_week
    if day not in WEEKDAY_CODES:
        day = "mon"
    return CronTrigger(
        day_of_week=day,
        hour=max(0, min(23, int(settings.scheduler_cron_hour))),
        minute=max(0, min(59, int(settings.scheduler_cron_minute))),
    )


def _run_job() -> None:
    """调度触发：**每个空间各生成一份** 7 天报告并推送。

    报告是空间级的（"你们小组这周学了什么"），所以不能只跑一个默认空间。
    单个空间失败不影响其他空间——一个小组的模型调用挂了不该拖垮所有人。
    """
    from shared.infrastructure.db.models import SpaceORM
    from shared.infrastructure.db.session import SessionLocal
    import client.services.review_service as review_service

    db = SessionLocal()
    try:
        space_ids = [row[0] for row in db.query(SpaceORM.id).all()]
        if not space_ids:
            logger.info("还没有任何空间，跳过定时回顾")
            return

        for space_id in space_ids:
            try:
                report = asyncio.run(
                    review_service.generate_report(db, space_id, 7, trigger="schedule")
                )
                logger.info("定时回顾已生成：%s（空间 %s）", report.id, space_id)

                if not settings.smtp_configured:
                    review_service.mark_push(
                        db, report, False, "尚未配置 SMTP，报告只保存在本地", space_id
                    )
                    continue

                from shared.infrastructure.mail.smtp_notifier import build_notifier

                result = build_notifier().send_report(report)
                review_service.mark_push(db, report, result.ok, result.detail, space_id)
                if result.ok:
                    logger.info("定时回顾已推送：%s", result.detail)
                else:
                    logger.warning("定时回顾推送失败：%s", result.detail)
            except Exception as exc:  # noqa: BLE001 - 单个空间失败不中断整轮
                logger.exception("空间 %s 的定时回顾失败：%s", space_id, exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("定时回顾执行失败：%s", exc)
    finally:
        db.close()


def start() -> None:
    """应用启动时调用：按配置装载，未开启就什么都不做。"""
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone="Asia/Shanghai", daemon=True)
    _scheduler.start()
    _sync_job()


def _sync_job() -> None:
    """让调度状态与当前配置一致（开启则注册/更新，关闭则移除）。"""
    if _scheduler is None:
        return
    existing = _scheduler.get_job(JOB_ID)
    if not settings.scheduler_enabled:
        if existing is not None:
            _scheduler.remove_job(JOB_ID)
            logger.info("定时回顾已关闭")
        return

    trigger = _trigger()
    if existing is None:
        _scheduler.add_job(
            _run_job,
            trigger=trigger,
            id=JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,  # 睡眠唤醒后错过多次只跑一次，不要连着补发
            misfire_grace_time=600,
        )
    else:
        _scheduler.reschedule_job(JOB_ID, trigger=trigger)
    logger.info("定时回顾已装载：%s", trigger)


def reload() -> dict:
    """配置变更后调用（保存设置时）。返回最新状态供接口回显。"""
    _sync_job()
    return state()


def state() -> dict:
    next_run = None
    if _scheduler is not None and settings.scheduler_enabled:
        job = _scheduler.get_job(JOB_ID)
        if job is not None and job.next_run_time is not None:
            next_run = job.next_run_time.isoformat()
    return {
        "enabled": bool(settings.scheduler_enabled),
        "job_registered": bool(_scheduler is not None and _scheduler.get_job(JOB_ID) is not None),
        "next_run": next_run,
        "cron": {
            "day_of_week": settings.scheduler_cron_day_of_week,
            "hour": settings.scheduler_cron_hour,
            "minute": settings.scheduler_cron_minute,
        },
        "note": "只在 A4S 运行时触发，关机期间不会补发",
    }


def run_now() -> dict:
    """立即执行一次（前端「立刻生成并推送」用）。同步执行，便于直接返回结果。"""
    _run_job()
    return {"ok": True, "message": "已执行一次定时回顾"}


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
