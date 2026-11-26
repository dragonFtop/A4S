"""LLM / Embedding 端口定义。

应用层只依赖这两个抽象，不感知具体供应商。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


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
