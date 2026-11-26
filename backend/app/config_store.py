"""配置持久化：把界面修改写回项目根目录的 `.env`。

设计取舍
--------
1. **单一配置源**：只维护 `.env` 一份文件，不额外引入"界面配置文件"，
   避免出现"界面改了没生效""重启后被打回"的双源问题。
2. **保序写入**：逐行替换目标键，保留注释、空行与未知键，改完的 `.env` 依然可读。
3. **即时生效**：写盘后原地更新 `settings` 单例（全应用共享同一对象），无需重启。
4. **密钥不覆盖**：界面上传回的打码值一律忽略，避免把 `sk-***abc` 写进配置。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import ROOT_DIR, settings

ENV_PATH: Path = ROOT_DIR / ".env"

# 允许从界面修改的键（其余键如 DATABASE_URL 涉及重启，不开放界面编辑）
WRITABLE_FIELDS: frozenset[str] = frozenset(
    {
        "llm_provider",
        "llm_base_url",
        "llm_api_key",
        "llm_model",
        "llm_temperature",
        "llm_timeout",
        "embedding_provider",
        "embedding_model",
        "embedding_base_url",
        "embedding_api_key",
        "embedding_dimension",
        "smtp_host",
        "smtp_port",
        "smtp_use_ssl",
        "smtp_user",
        "smtp_password",
        "smtp_from",
        "smtp_to",
        "scheduler_enabled",
        "scheduler_cron_day_of_week",
        "scheduler_cron_hour",
        "scheduler_cron_minute",
    }
)

_SECRET_FIELDS: frozenset[str] = frozenset({"llm_api_key", "embedding_api_key", "smtp_password"})
_BOOL_FIELDS: frozenset[str] = frozenset({"smtp_use_ssl", "scheduler_enabled"})
_INT_FIELDS: frozenset[str] = frozenset(
    {"smtp_port", "embedding_dimension", "scheduler_cron_hour", "scheduler_cron_minute", "llm_timeout"}
)
_FLOAT_FIELDS: frozenset[str] = frozenset({"llm_temperature"})

# 前端"清除密钥"的显式信号
CLEAR_SENTINEL = "__CLEAR__"


class ConfigWriteError(ValueError):
    """配置写入失败（不可写、键不合法等）。"""


def _is_masked(value: str) -> bool:
    return "***" in value


def _format(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    # 含空格、引号或 # 的值加双引号，避免 .env 解析歧义
    if any(ch in text for ch in (" ", '"', "'", "#")):
        return '"' + text.replace('"', '\\"') + '"'
    return text


def _split_env_line(line: str) -> tuple[str, int] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key = stripped.split("=", 1)[0].strip()
    return key.upper(), line.find("=")


def read_env_pairs() -> dict[str, str]:
    """读取 .env 的原始键值（不做类型转换，仅用于诊断与比较）。"""
    if not ENV_PATH.exists():
        return {}
    pairs: dict[str, str] = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        parsed = _split_env_line(line)
        if parsed is None:
            continue
        key, eq_index = parsed
        value = line[eq_index + 1 :].strip().strip('"')
        pairs[key] = value
    return pairs


def _coerce(key: str, value: Any) -> Any:
    if key in _BOOL_FIELDS:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    if key in _INT_FIELDS:
        return int(value)
    if key in _FLOAT_FIELDS:
        return float(value)
    return str(value)


def _normalize(changes: dict[str, Any]) -> dict[str, Any]:
    """过滤非法键与"未修改的密钥"，返回可直接写盘与生效的值。"""
    cleaned: dict[str, Any] = {}
    unknown = [key for key in changes if key not in WRITABLE_FIELDS]
    if unknown:
        raise ConfigWriteError(f"不支持修改的配置项：{', '.join(sorted(unknown))}")

    for key, raw in changes.items():
        if key in _SECRET_FIELDS:
            if raw is None or raw == "" or raw == CLEAR_SENTINEL:
                if raw == CLEAR_SENTINEL:
                    cleaned[key] = ""
                continue
            if _is_masked(str(raw)):
                # 前端回传的是打码值，说明用户没改，跳过
                continue
        if raw is None:
            continue
        try:
            cleaned[key] = _coerce(key, raw)
        except (TypeError, ValueError) as exc:
            raise ConfigWriteError(f"配置项 {key} 的取值不合法：{raw!r}") from exc
    return cleaned


def merged_values(overrides: dict[str, Any]) -> dict[str, Any]:
    """把"当前生效配置"与"界面临时覆盖值"合并，供连通性测试使用（不写盘）。"""
    base: dict[str, Any] = {key: getattr(settings, key) for key in WRITABLE_FIELDS}
    base.update(_normalize(overrides))
    return base


def save_settings(changes: dict[str, Any]) -> list[str]:
    """写入 .env 并即时更新内存中的配置。

    返回实际变更的键列表；空列表表示没有任何改动。
    """
    cleaned = _normalize(changes)
    if not cleaned:
        return []

    lines: list[str] = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    else:
        # 首次写入：以 .env.example 为基底，保留注释与全部可配置项，避免生成"残缺"配置
        example = ROOT_DIR / ".env.example"
        if example.exists():
            lines = example.read_text(encoding="utf-8").splitlines()

    positions: dict[str, int] = {}
    for index, line in enumerate(lines):
        parsed = _split_env_line(line)
        if parsed is not None:
            positions[parsed[0]] = index

    appended: list[str] = []
    for key, value in cleaned.items():
        env_key = key.upper()
        new_line = f"{env_key}={_format(value)}"
        if env_key in positions:
            lines[positions[env_key]] = new_line
        else:
            appended.append(new_line)

    if appended:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(appended)

    try:
        ENV_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    except OSError as exc:
        raise ConfigWriteError(f"无法写入配置文件 {ENV_PATH}：{exc}") from exc

    # 原地更新单例：所有 `from app.config import settings` 的引用同步生效
    for key, value in cleaned.items():
        setattr(settings, key, value)

    return sorted(cleaned)
