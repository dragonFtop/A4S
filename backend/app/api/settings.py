"""设置接口：读取、保存、连通性测试（对应 PRD FR-09）。

职责边界：
- 只做"配置的读写与体检"，不承载任何业务逻辑；
- 写盘交给 infrastructure.config_store，保证单一配置源。
"""

from __future__ import annotations

import smtplib
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import APP_VERSION, ROOT_DIR, settings
from app.config_store import (
    ConfigWriteError,
    ENV_PATH,
    merged_values,
    save_settings,
)
from app.db import get_db
from app.llm import EMBEDDING_PROVIDERS, build_embedding_for
from app.llm import LLM_PRESETS, OpenAICompatibleLLM
from app.schemas.settings import ConnectionTestRequest, SettingsUpdate, TestMailRequest
from app.services import index_service
from app.services.index_service import RebuildError

router = APIRouter(prefix="/settings", tags=["settings"])


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:3]}***{value[-3:]}"


def _snapshot() -> dict[str, Any]:
    """当前生效配置的对外视图（密钥打码）。"""
    return {
        "llm": {
            "provider": settings.llm_provider,
            "base_url": settings.llm_base_url,
            "model": settings.llm_model,
            "temperature": settings.llm_temperature,
            "timeout": settings.llm_timeout,
            "api_key": _mask(settings.llm_api_key),
            "configured": settings.llm_configured,
        },
        "embedding": {
            "provider": settings.embedding_provider,
            "model": settings.embedding_model,
            "base_url": settings.embedding_base_url,
            "dimension": settings.embedding_dimension,
            "api_key": _mask(settings.embedding_api_key),
            "configured": settings.embedding_provider == "local"
            or bool(settings.embedding_base_url and settings.embedding_api_key),
        },
        "smtp": {
            "host": settings.smtp_host,
            "port": settings.smtp_port,
            "use_ssl": settings.smtp_use_ssl,
            "user": settings.smtp_user,
            "password": _mask(settings.smtp_password),
            "from_addr": settings.smtp_from,
            "to": settings.smtp_to,
            "configured": settings.smtp_configured,
        },
        "scheduler": {
            "enabled": settings.scheduler_enabled,
            "day_of_week": settings.scheduler_cron_day_of_week,
            "hour": settings.scheduler_cron_hour,
            "minute": settings.scheduler_cron_minute,
            **_scheduler_runtime(),
        },
        "runtime": {
            "app_env": settings.app_env,
            "version": APP_VERSION,
            "database_url": _mask_database_url(settings.database_url),
            "data_dir": str(ROOT_DIR / "backend" / "data"),
            "env_file": str(ENV_PATH),
            "env_file_exists": ENV_PATH.exists(),
        },
        # 前端下拉框的可选项：主名即可，别名（bge / openai）仍能被后端识别
        "llm_providers": list(LLM_PRESETS),
        "embedding_providers": ["local", "cloud"],
        "llm_presets": [
            {"provider": key, **value} for key, value in LLM_PRESETS.items()
        ],
    }


def _mask_database_url(url: str) -> str:
    """数据库连接串可能含密码，展示时去掉。"""
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        return f"{scheme}://***@{rest.split('@', 1)[1]}"
    return url


def _scheduler_runtime() -> dict[str, Any]:
    """调度器的运行时信息（下次触发时间）。导入放函数内，避免与 scheduler 互相依赖。"""
    from app import scheduler

    state = scheduler.state()
    return {"next_run": state["next_run"], "job_registered": state["job_registered"]}


@router.get("")
def read_settings() -> dict:
    return _snapshot()


@router.put("")
def update_settings(payload: SettingsUpdate, db: Session = Depends(get_db)) -> dict:
    changes = payload.model_dump(exclude_unset=True)
    try:
        changed = save_settings(changes)
    except ConfigWriteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 定时配置一变，调度器要跟着重装：否则界面上"已开启"，后台其实没任务
    schedule_changed = any(key.startswith("scheduler_") for key in changed)
    if schedule_changed:
        from app import scheduler

        scheduler.reload()

    # 向量相关配置一改，索引就可能失效——顺手把判断结果带回去，前端不用再猜
    touched_embedding = any(key.startswith("embedding_") for key in changed)
    return {
        "saved": changed,
        "settings": _snapshot(),
        "index": index_service.index_status(db) if touched_embedding else None,
    }


@router.get("/index")
def read_index_status(db: Session = Depends(get_db)) -> dict:
    return index_service.index_status(db)


@router.post("/index/rebuild")
def rebuild_index(background: BackgroundTasks) -> dict:
    try:
        job = index_service.start_rebuild()
    except RebuildError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background.add_task(index_service.run_rebuild)
    return {"ok": True, "job": job}


@router.post("/test")
async def test_connection(payload: ConnectionTestRequest) -> dict:
    try:
        values = merged_values(payload.overrides)
    except ConfigWriteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if payload.target == "llm":
        return await _test_llm(values)
    if payload.target == "embedding":
        return _test_embedding(values)
    return _test_smtp(values)


@router.post("/test-mail")
def test_mail(payload: TestMailRequest) -> dict:
    """真发一封测试邮件（FR-08 的第一条验收）。

    与 `/test` 的 smtp 目标区别：那个只验证「能不能登录」，
    这个验证「登录 + 组信 + 投递」全链路——真正的坑通常在后两步。
    """
    try:
        values = merged_values(payload.overrides)
    except ConfigWriteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    from app.mailer import build_notifier

    result = build_notifier(values).send_test()
    return {"ok": result.ok, "message": result.detail}


async def _test_llm(values: dict[str, Any]) -> dict:
    provider = values["llm_provider"]
    if provider not in LLM_PRESETS:
        return {"ok": False, "message": f"未注册的 LLM 供应商：{provider}"}
    if not values["llm_api_key"]:
        return {"ok": False, "message": "尚未填写 API Key"}

    llm = OpenAICompatibleLLM(
        base_url=values["llm_base_url"],
        api_key=values["llm_api_key"],
        model=values["llm_model"],
        temperature=0,
        timeout=20,
        provider=provider,
    )
    try:
        reply = await llm.chat(
            [{"role": "user", "content": "回复两个字：可用"}],
        )
    except Exception as exc:  # noqa: BLE001 - 需要把供应商的真实报错透出给用户
        return {"ok": False, "message": _explain_llm_error(exc)}
    return {
        "ok": True,
        "message": f"连接成功，模型 {values['llm_model']} 已响应",
        "detail": reply.strip()[:80],
    }


def _explain_llm_error(exc: Exception) -> str:
    text = str(exc)
    lowered = text.lower()
    if "401" in text or "invalid_api_key" in lowered or "unauthorized" in lowered:
        return "鉴权失败（401）：请检查 API Key 是否正确、是否与所选供应商匹配"
    if "404" in text:
        return "接口不存在（404）：请检查 Base URL 是否包含 /v1，以及模型名是否存在"
    if "429" in text:
        return "请求过多或额度不足（429）：请稍后再试或检查账户余额"
    if "timeout" in lowered or "timed out" in lowered:
        return "连接超时：请检查网络、代理或 Base URL 是否可访问"
    if "connect" in lowered:
        return f"无法连接：{text[:160]}"
    return text[:200] or "未知错误"


def _test_embedding(values: dict[str, Any]) -> dict:
    provider = values["embedding_provider"]
    if provider not in EMBEDDING_PROVIDERS:
        return {"ok": False, "message": f"未注册的 Embedding 供应商：{provider}"}

    emb = build_embedding_for(
        provider=provider,
        model=values["embedding_model"],
        dimension=values["embedding_dimension"],
        base_url=values["embedding_base_url"],
        api_key=values["embedding_api_key"],
    )
    try:
        vector = emb.embed_one("A4S 向量化连通性测试")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"{type(exc).__name__}: {str(exc)[:200]}"}

    hint = "（本地模型首次使用会先下载权重，耗时较久属正常）" if provider == "local" else ""
    return {
        "ok": True,
        "message": f"向量化可用，维度 {len(vector)}{hint}",
        "detail": f"dim={len(vector)}",
    }


def _test_smtp(values: dict[str, Any]) -> dict:
    host, port = values["smtp_host"], int(values["smtp_port"])
    user, password = values["smtp_user"], values["smtp_password"]
    if not (host and user and password):
        return {"ok": False, "message": "请先填写 SMTP 服务器、账号与授权码"}

    try:
        if values["smtp_use_ssl"]:
            server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=12)
        else:
            server = smtplib.SMTP(host, port, timeout=12)
            server.starttls()
        server.login(user, password)
        server.quit()
    except smtplib.SMTPAuthenticationError:
        return {"ok": False, "message": "账号或授权码错误（注意多数邮箱需使用「SMTP 授权码」而非登录密码）"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"{type(exc).__name__}: {str(exc)[:180]}"}

    return {"ok": True, "message": f"SMTP 登录成功（{host}:{port}）"}
