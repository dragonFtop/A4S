"""OpenAI 兼容协议的 LLM 实现。

DeepSeek / 通义 / OpenAI 等均提供 OpenAI 兼容端点，因此一套实现 + base_url
即可接入多家，避免为每个供应商写一个 SDK 适配层。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from app.infrastructure.llm.base import LLMProvider

LLM_PRESETS: dict[str, dict[str, str]] = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    },
    "qwen": {
        "label": "通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
    "moonshot": {
        "label": "Moonshot",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
    },
}

_PRESET_BASE_URLS = {key: value["base_url"] for key, value in LLM_PRESETS.items()}


class OpenAICompatibleLLM(LLMProvider):
    name = "openai-compatible"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str = "",
        model: str = "deepseek-chat",
        temperature: float = 0.3,
        timeout: int = 60,
        provider: str = "openai",
    ) -> None:
        self.base_url = (base_url or _PRESET_BASE_URLS.get(provider, "")).rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout = timeout

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, messages: list[dict], stream: bool, **options) -> dict:
        return {
            "model": options.get("model", self.model),
            "messages": messages,
            "temperature": options.get("temperature", self.temperature),
            "stream": stream,
        }

    async def chat(self, messages: list[dict], **options) -> str:
        if not self.api_key:
            raise RuntimeError("未配置 LLM_API_KEY，请在 .env 或设置页填写")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers,
                json=self._payload(messages, stream=False, **options),
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    async def stream(self, messages: list[dict], **options) -> AsyncIterator[str]:
        if not self.api_key:
            raise RuntimeError("未配置 LLM_API_KEY，请在 .env 或设置页填写")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._headers,
                json=self._payload(messages, stream=True, **options),
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line.removeprefix("data:").strip()
                    if payload == "[DONE]":
                        break
                    try:
                        delta = json.loads(payload)["choices"][0]["delta"].get("content", "")
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    if delta:
                        yield delta


def build_llm() -> LLMProvider:
    """按当前配置构造 LLM 实例。

    所有供应商都走 OpenAI 兼容协议，差别只在 base_url 与默认模型，
    因此不需要按 provider 分支——直接把配置传进去即可。
    """
    from app.config import settings

    return OpenAICompatibleLLM(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout,
        provider=settings.llm_provider,
    )
