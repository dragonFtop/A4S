"""LLM 出题器：一个类按题型出题。

为什么按题型分开跑（而不是一次让模型出四种）：
- 选项数、答案格式、解析写法各不相同，分开写规则更清楚；
- 数量配比直接由配置决定，merge 时不用再二次分配；
- 某一种题型资料不足时，只影响这一种，其他题型照常出。

JSON 解析失败会带一条修复指令重试一次；仍失败则抛出，由服务层转成人话。
"""

from __future__ import annotations

from app.domain.models.entities import Question, QuestionType
from app.infrastructure.generators.base import (
    GenerationContext,
    QuestionGenConfig,
    QuestionGenerator,
)
from app.infrastructure.generators.parsing import normalize_question, parse_json_items
from app.infrastructure.llm.base import LLMProvider
from app.infrastructure.llm.openai_compat import build_llm
from app.services import practice_prompts


class GeneratorError(RuntimeError):
    """模型输出不可用（不是有效 JSON、调用失败等）。"""


class LLMTypedGenerator(QuestionGenerator):
    """按题型出题。题型由构造参数决定，四种题型共用同一套解析与去重逻辑。"""

    def __init__(self, question_type: QuestionType, llm: LLMProvider | None = None) -> None:
        self.question_type = question_type
        self._llm = llm

    async def generate(
        self, context: GenerationContext, config: QuestionGenConfig
    ) -> list[Question]:
        count = max(0, min(config.total, 30))
        if count == 0 or not context.chunks:
            return []

        llm = self._llm or build_llm()
        messages = practice_prompts.build_messages(
            self.question_type, context.material_text, count, config.difficulty
        )
        raw = await llm.chat(messages, temperature=0.35)
        items = parse_json_items(raw)
        if items is None:
            # 容错重试：只加一条修复指令，不重发材料（省 token，也避免模型换思路）
            retry = [
                *messages,
                {"role": "assistant", "content": raw[:2000]},
                {"role": "user", "content": practice_prompts.REPAIR_MESSAGE},
            ]
            raw = await llm.chat(retry, temperature=0.1)
            items = parse_json_items(raw)
        if items is None:
            raise GeneratorError(
                f"{practice_prompts.TYPE_SPECS[self.question_type]['label']}：模型返回的内容不是有效 JSON"
            )

        questions: list[Question] = []
        seen: set[str] = set()
        for item in items:
            question = normalize_question(item, self.question_type, context)
            if question is None:
                continue
            key = _stem_key(question.stem)
            if key in seen:
                continue
            seen.add(key)
            questions.append(question)
            if len(questions) >= count:
                break
        return questions


def _stem_key(stem: str) -> str:
    """去重键：去掉空白与标点，避免只改标点就算新题。"""
    return "".join(char for char in stem if char.isalnum())
