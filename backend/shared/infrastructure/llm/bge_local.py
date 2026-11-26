"""Embedding 实现：本地 BGE（默认）与云端 API。

本地实现延迟导入 sentence-transformers，避免 M1 阶段强制安装 torch；
未安装时给出明确指引而不是堆栈报错。
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

import httpx

from shared.infrastructure.llm.base import EmbeddingProvider

logger = logging.getLogger(__name__)

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
        from shared.config import settings  # noqa: F401  确保 config 已加载

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
    """按配置构造 Embedding。带缓存——BGE 加载一次要几百 MB，不能每个请求都建。

    缓存键包含所有影响模型行为的配置项，配置中心改完后自动得到新实例。
    """
    from shared.config import settings

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

    带缓存——BGE 加载一次要几百 MB，不能每个请求都建。
    缓存键包含所有影响模型行为的参数，配置改完后自动得到新实例。
    provider 决定本地还是云端，别名归一到两个实现。
    """
    if provider in CLOUD_EMBEDDING_ALIASES:
        return CloudEmbedding(base_url=base_url, api_key=api_key, model=model, dimension=dimension)
    return LocalBgeEmbedding(model=model, dimension=dimension)
