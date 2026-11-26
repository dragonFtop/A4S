"""出题器端口。

一种题型一个实现：题型之间的选项规则、答案格式、解析写法差异很大，
分开调用才能分别控制数量，也才能在某一题型资料不足时给出精确提示。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from shared.domain.models.entities import Question, QuestionType


@dataclass(slots=True)
class QuestionGenConfig:
    total: int = 10
    types: list[QuestionType] | None = None
    difficulty: str = "medium"


@dataclass(slots=True)
class GenerationChunk:
    """出题材料的一个片段：正文 + 定位 + 归属。"""

    chunk_id: str
    text: str
    document_id: str
    document_name: str
    topic_id: str = "uncategorized"
    page: int | None = None


@dataclass(slots=True)
class GenerationContext:
    """出题上下文：来自知识库的片段。

    chunks 是结构化材料（校验来源时要用），material_text 是拼给模型的正文，
    已经带 `[chunk:id]` 编号——两者同一批片段，不要各取一份。
    """

    chunks: list[GenerationChunk] = field(default_factory=list)
    material_text: str = ""
    topic_names: list[str] = field(default_factory=list)

    def allowed(self) -> dict[str, GenerationChunk]:
        return {chunk.chunk_id: chunk for chunk in self.chunks}


class QuestionGenerator(ABC):
    name: str = "base"
    question_type: QuestionType

    @abstractmethod
    async def generate(
        self, context: GenerationContext, config: QuestionGenConfig
    ) -> list[Question]:
        """基于上下文生成题目；资料不足时应返回空列表而非编题。"""
