"""大模型与 Embedding：端口定义 + 实现 + 工厂。

一个文件读完"怎么调模型"。分三块：

1. `LLMProvider` / `EmbeddingProvider`：应用层只依赖这两个抽象，不感知供应商。
   保留抽象是因为实现真的不止一个——对话有本地/云端 Embedding 两种，真会换。
2. `OpenAICompatibleLLM`：DeepSeek / 通义 / OpenAI / Moonshot 都提供 OpenAI
   兼容端点，所以一套实现 + `base_url` 就能接多家，不必为每家写 SDK 适配。
3. Embedding：本地 BGE（默认）与云端 API。本地实现延迟导入
   sentence-transformers，避免没装 torch 时启动就崩。
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from functools import lru_cache

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- 端口

class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def chat(self, messages: list[dict], **options) -> str:
        """一次性返回完整回答。"""

    @abstractmethod
    def stream(self, messages: list[dict], **options) -> AsyncIterator[str]:
        """流式返回增量文本。"""


class EmbeddingProvider(ABC):
    name: str = "base"
    dimension: int = 0

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """把文本批量转为向量。"""

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


# ---------------------------------------------------------------- 对话

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


# ---------------------------------------------------------------- Embedding

# 配置里可选的 embedding 提供方。local / bge 是同一实现的两个别名，
# cloud / openai 也是——别名存在是因为用户习惯不同的叫法。
LOCAL_EMBEDDING_ALIASES = ("local", "bge")
CLOUD_EMBEDDING_ALIASES = ("cloud", "openai")
EMBEDDING_PROVIDERS = LOCAL_EMBEDDING_ALIASES + CLOUD_EMBEDDING_ALIASES


class LocalBgeEmbedding(EmbeddingProvider):
    name = "local-bge"

    def __init__(
        self,
        model: str = "BAAI/bge-small-zh-v1.5",
        dimension: int = 512,
    ) -> None:
        self.model_name = model
        self.dimension = dimension
        self._model = None

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        # config 已在模块加载时设好 HF_ENDPOINT；这里再兜一次，防止被其他入口绕过
        from app.config import settings  # noqa: F401  确保 config 已加载

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - 环境相关
            raise RuntimeError(
                "本地 Embedding 需要 sentence-transformers。"
                "请先执行：pip install -r backend/requirements-rag.txt"
            ) from exc

        logger.info("正在加载 Embedding 模型 %s（首次运行需下载，请稍候）", self.model_name)
        self._model = SentenceTransformer(self.model_name)
        logger.info("Embedding 模型就绪：%s", self.model_name)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._ensure_model()
        vectors = model.encode(texts, normalize_embeddings=True)
        return [list(map(float, v)) for v in vectors]


class CloudEmbedding(EmbeddingProvider):
    name = "cloud-embedding"

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        model: str = "text-embedding-v3",
        dimension: int = 1024,
        timeout: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dimension = dimension
        self.timeout = timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not (self.base_url and self.api_key):
            raise RuntimeError("云端 Embedding 未配置 base_url / api_key")
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "input": texts},
            )
            resp.raise_for_status()
            data = resp.json()["data"]
            return [item["embedding"] for item in data]


def build_embedding() -> EmbeddingProvider:
    """按配置构造 Embedding。带缓存——BGE 加载一次要几百 MB，不能每个请求都建。"""
    from app.config import settings

    return build_embedding_for(
        provider=settings.embedding_provider,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
        base_url=settings.embedding_base_url,
        api_key=settings.embedding_api_key,
    )


@lru_cache(maxsize=4)
def build_embedding_for(
    provider: str, model: str, dimension: int, base_url: str, api_key: str
) -> EmbeddingProvider:
    """按显式参数构造 Embedding（配置中心测试连接时也用它）。

    缓存键包含所有影响模型行为的参数，配置改完后自动得到新实例。
    provider 决定本地还是云端，别名归一到两个实现。
    """
    if provider in CLOUD_EMBEDDING_ALIASES:
        return CloudEmbedding(base_url=base_url, api_key=api_key, model=model, dimension=dimension)
    return LocalBgeEmbedding(model=model, dimension=dimension)
