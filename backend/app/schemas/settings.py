"""设置相关的请求/响应模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SettingsUpdate(BaseModel):
    """部分更新：只提交发生了改动的字段。

    密钥字段若传回打码值（含 `***`）会被忽略，传 `__CLEAR__` 表示清空。
    """

    llm_provider: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_temperature: float | None = Field(default=None, ge=0, le=2)
    llm_timeout: int | None = Field(default=None, ge=5, le=300)

    embedding_provider: str | None = None
    embedding_model: str | None = None
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    embedding_dimension: int | None = Field(default=None, ge=64, le=4096)

    smtp_host: str | None = None
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_use_ssl: bool | None = None
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_to: str | None = None

    scheduler_enabled: bool | None = None
    scheduler_cron_day_of_week: str | None = None
    scheduler_cron_hour: int | None = Field(default=None, ge=0, le=23)
    scheduler_cron_minute: int | None = Field(default=None, ge=0, le=59)


class ConnectionTestRequest(BaseModel):
    """连通性测试：overrides 允许"未保存先试"，仅内存生效、不写盘。"""

    target: Literal["llm", "embedding", "smtp"]
    overrides: dict[str, Any] = Field(default_factory=dict)


class TestMailRequest(BaseModel):
    """立即测试发送：真的发一封邮件，不只是登录。

    `overrides` 与连通性测试同一套语义——允许用户先试后存。
    """

    overrides: dict[str, Any] = Field(default_factory=dict)
