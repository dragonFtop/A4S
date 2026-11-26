"""应用配置。

集中管理"易变的外部依赖配置"（模型供应商、数据库、邮件），
使业务代码不直接读取环境变量，也便于未来切换实现。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/shared.config.py -> app -> backend -> 项目根
BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent
DATA_DIR = BACKEND_DIR / "data"

# P1 数据分家：客户端与服务端各自一个库文件，互不干扰。
# 入口在 import shared 之前通过 APP_ROLE 声明角色；显式 DATABASE_URL 永远优先
# （测试脚本、迁移、自定义部署都走这条覆盖路径）。
ROLE_DEFAULT_DATABASE_URL = {
    "client": "sqlite:///./data/client.db",
    "server": "sqlite:///./data/server.db",
}

# 版本号只有这一处。改版本时改这里，健康检查、设置页、OpenAPI 会一起跟上。
APP_VERSION = "0.2.0"




class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "dev"
    log_level: str = "INFO"

    # 运行角色：由入口在 import shared 之前通过 APP_ROLE 环境变量指定
    # （"client" / "server"）。仅在未显式设置 DATABASE_URL 时决定默认库文件。
    app_role: str = "default"

    # 服务端地址（客户端专用，可留空）。
    # 留空 = 纯本地模式：账号在本地注册，所有功能不依赖网络。
    # 填了 = 联机模式：客户端可以向它申请查询小组公共库、别人的知识库。
    server_url: str = ""
    # 探测服务端是否在线用的超时。宁可快速判定离线，也不要让界面卡住。
    server_status_timeout: float = 2.0

    # ---- 联邦（P3）----
    # 本节点 id（留空则用 主机名-进程号 自动生成）；同一进程需稳定。
    fed_node_id: str = ""
    # 对外提供检索的空间；留空则自动选"有 ready 资料的空间"。
    fed_serving_space_id: str = ""
    # server 持住请求等待目标节点领取的时长；超时即返回"来源离线"（C6）。
    fed_hold_seconds: float = 15.0
    # 节点在线判定窗口：last_seen 在此秒数内算在线。
    fed_node_online_seconds: float = 30.0
    # 每个请求方节点每日联邦配额上限。
    fed_daily_quota: int = 50
    # 出站轮询间隔与心跳间隔（秒）。
    fed_poll_interval: float = 1.0
    fed_heartbeat_interval: float = 3.0

    # ---- 备份（P4）----
    # 快照目录；留空则用 backend/data/backups。
    backup_dir: str = ""
    # 保留份数，超出清理最旧。
    backup_keep: int = 7
    # 异地副本目录（可选）；配置后每次快照额外复制一份过去。
    backup_offsite_dir: str = ""

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
        """把 sqlite 相对路径解析到 backend/ 下，避免依赖启动目录。

        未显式设置 DATABASE_URL 时，按入口角色（APP_ROLE）选默认库文件；
        只要环境里显式给了 DATABASE_URL，就完全尊重它（测试/迁移/自定义）。
        """
        url = self.database_url
        if "DATABASE_URL" not in os.environ and self.app_role in ROLE_DEFAULT_DATABASE_URL:
            url = ROLE_DEFAULT_DATABASE_URL[self.app_role]
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
