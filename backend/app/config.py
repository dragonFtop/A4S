"""应用配置。

集中管理"易变的外部依赖配置"（模型供应商、数据库、邮件），
使业务代码不直接读取环境变量，也便于未来切换实现。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> app -> backend -> 项目根
BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent
DATA_DIR = BACKEND_DIR / "data"




class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "dev"
    log_level: str = "INFO"

    # 关系库：默认 SQLite，未来只需改这一行为 postgresql://...
    database_url: str = "sqlite:///./data/app.db"

    # LLM（OpenAI 兼容协议）
    llm_provider: str = "deepseek"
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.3
    llm_timeout: int = 60

    # Embedding
    embedding_provider: str = "local"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_dimension: int = 512

    # HuggingFace 下载源。国内直连 huggingface.co 常超时，默认走镜像；
    # 置空则使用 huggingface_hub 自身默认（适合海外或已配代理的环境）。
    hf_endpoint: str = "https://hf-mirror.com"

    # SMTP
    smtp_host: str = "smtp.qq.com"
    smtp_port: int = 465
    smtp_use_ssl: bool = True
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_to: str = ""

    # 定时回顾
    scheduler_enabled: bool = False
    scheduler_cron_day_of_week: str = "mon"
    scheduler_cron_hour: int = 9
    scheduler_cron_minute: int = 0

    @property
    def resolved_database_url(self) -> str:
        """把 sqlite 相对路径解析到 backend/ 下，避免依赖启动目录。"""
        url = self.database_url
        if url.startswith("sqlite:///./"):
            rel = url.removeprefix("sqlite:///./")
            return f"sqlite:///{(BACKEND_DIR / rel).as_posix()}"
        return url

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key and self.llm_base_url and self.llm_model)

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password and self.smtp_to)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

# 必须早于任何 huggingface_hub / sentence_transformers 的 import：
# 它们在校验 endpoint 时把 HF_ENDPOINT 读成模块级常量，之后再设无效。
# config 是项目里最早被导入的模块，放在这里最保险。
if settings.hf_endpoint:
    os.environ.setdefault("HF_ENDPOINT", settings.hf_endpoint)
