"""出题：材料上下文 → 调模型 → 解析校验成题目。

前半是本模块自己的数据结构（上下文与配置），后半是模型输出的解析与校验。

模型不会永远听话：可能包一层代码块、可能在 JSON 前后写解释、可能把选项前缀
带进正文、可能编一个不存在的片段编号。这里逐项兜住，兜不住的题直接丢弃。

调用方只看到「合格题目列表」，不关心这些细节。
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.llm import LLMProvider, build_llm
from app.models import Question, QuestionType, SourceRef
from app.services import practice_prompts

SNIPPET_CHARS = 240
MAX_OPTIONS = 6
MIN_OPTIONS = 2


# ---------------------------------------------------------------- 上下文与配置

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

_TRUE = {"正确", "对", "是", "true", "t", "yes", "y", "√", "1"}
_FALSE = {"错误", "错", "否", "不对", "false", "f", "no", "n", "×", "x", "0"}

# 选项前缀："A." "A、" "A)" "A：" 等
_OPTION_PREFIX = re.compile(r"^\s*[A-Za-z]\s*[\.．、:：\)）]\s*")
_ANSWER_PREFIX = re.compile(r"^\s*([A-Za-z])\s*(?:[\.．、:：\)）]\s*|\s*$)")
_ANSWER_LETTERS = re.compile(r"^\s*([A-Za-z]{1,6})\s*$")
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def parse_json_items(raw: str) -> list[dict] | None:
    """从模型输出里抠出 JSON 数组；抠不出返回 None（调用方据此重试）。"""
    text = _FENCE.sub("", (raw or "").strip())
    if not text:
        return None

    candidates = [text]
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    # 有的模型会包成 {"questions": [...]}
    brace_start, brace_end = text.find("{"), text.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        candidates.append(text[brace_start : brace_end + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, list):
            items = [item for item in data if isinstance(item, dict)]
            if items:
                return items
        elif isinstance(data, dict):
            for key in ("questions", "items", "data", "list"):
                value = data.get(key)
                if isinstance(value, list):
                    items = [item for item in value if isinstance(item, dict)]
                    if items:
                        return items
    return None


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _strip_label(text: str, labels: tuple[str, ...]) -> str:
    """模型爱写「解析：…」「参考答案：…」，而界面已经有标签，重复一遍只剩噪音。"""
    for label in labels:
        if text.startswith(label):
            return text[len(label) :].lstrip("：: ").strip()
    return text


def _normalize_options(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    options: list[str] = []
    for item in raw:
        text = _OPTION_PREFIX.sub("", _clean_text(item))
        if text:
            options.append(text[:300])
    return options[:MAX_OPTIONS]


def _letter(index: int) -> str:
    return chr(65 + index)


def _options_exact(answer: str, options: list[str]) -> str | None:
    """答案直接写了选项原文的情况。"""
    cleaned = _clean_text(answer).strip("。.；; ")
    for index, option in enumerate(options):
        if cleaned and cleaned.lower() == option.strip().lower():
            return _letter(index)
    return None


def _answer_letters(answer: Any, options: list[str], *, multi: bool) -> str | None:
    """把各种写法的答案统一成字母串（单题返回单字母，多选返回升序字母串）。"""
    if isinstance(answer, (list, tuple)):
        answer = "".join(str(item) for item in answer)
    text = _clean_text(answer)
    if not text:
        return None

    matched = _options_exact(text, options)
    if matched:
        return matched

    # 多选常见写法：「A、B、D」「A,B」「A B」。先按分隔符拆，
    # 否则下面的单字母前缀规则会把 "A、B、D" 读成 "A"。
    parts = [part for part in re.split(r"[\s,，、;；/]+", text) if part]
    if len(parts) >= 2 and all(re.fullmatch(r"[A-Za-z]", part) for part in parts):
        letters = {part.upper() for part in parts}
        if all(ord(char) - 65 < len(options) for char in letters):
            ordered = "".join(sorted(letters))
            if multi and len(ordered) >= 2:
                return ordered
            if not multi and len(ordered) == 1:
                return ordered

    prefix = _ANSWER_PREFIX.match(text)
    if prefix and not _ANSWER_LETTERS.match(text):
        letter = prefix.group(1).upper()
        if ord(letter) - 65 < len(options):
            return letter

    short = _ANSWER_LETTERS.match(text)
    if short:
        letters = {char.upper() for char in short.group(1)}
        if all(ord(char) - 65 < len(options) for char in letters):
            ordered = "".join(sorted(letters))
            if multi and len(ordered) < 2:
                return None
            if not multi and len(ordered) != 1:
                return None
            return ordered

    return None


def _true_false_answer(answer: Any) -> str | None:
    text = _clean_text(answer).lower().strip("。.")
    if text in _TRUE:
        return "正确"
    if text in _FALSE:
        return "错误"
    return None


def _source_ids(item: dict) -> list[str]:
    raw = item.get("source_chunk_ids")
    if raw is None:
        raw = item.get("source_chunk_id") or item.get("chunk_ids") or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    ids: list[str] = []
    for value in raw:
        text = _clean_text(value)
        if text.startswith("[chunk:"):
            text = text.removeprefix("[chunk:").rstrip("]")
        if text and text not in ids:
            ids.append(text)
    return ids


def normalize_question(
    item: dict, question_type: QuestionType, context: GenerationContext
) -> Question | None:
    """把一条模型输出变成领域题目；任何一项不达标就返回 None。"""
    allowed = context.allowed()
    sources: list[SourceRef] = []
    for chunk_id in _source_ids(item):
        chunk = allowed.get(chunk_id)
        if chunk is None:
            continue  # 编造的编号：直接丢掉，不写进来源
        sources.append(_source_ref(chunk))
    if not sources:
        # 没来源 = 无法核对，宁可不出
        return None

    stem = _clean_text(item.get("stem"))[:800]
    if len(stem) < 4:
        return None

    options: list[str] = []
    answer: str | None
    if question_type is QuestionType.TRUE_FALSE:
        answer = _true_false_answer(item.get("answer"))
    elif question_type is QuestionType.SHORT_ANSWER:
        answer = _strip_label(
            _clean_text(item.get("answer"))[:2000], ("参考答案", "答案")
        ) or None
    else:
        options = _normalize_options(item.get("options"))
        if len(options) < MIN_OPTIONS:
            return None
        answer = _answer_letters(
            item.get("answer"),
            options,
            multi=question_type is QuestionType.MULTI_CHOICE,
        )
    if not answer:
        return None

    explanation = _strip_label(_clean_text(item.get("explanation"))[:2000], ("解析", "解释"))
    if not explanation:
        # 解析缺失时退回材料原文，至少让人能核对，而不是空着
        explanation = f"材料原文：{sources[0].snippet}"

    knowledge_point = _clean_text(item.get("knowledge_point"))[:60] or None
    primary = sources[0]

    return Question(
        id=str(uuid.uuid4()),
        type=question_type,
        stem=stem,
        answer=answer,
        explanation=explanation,
        options=options,
        sources=sources,
        document_id=primary.document_id,
        knowledge_point=knowledge_point,
        topic_id=_first_topic(context, sources),
    )


def _source_ref(chunk: GenerationChunk) -> SourceRef:
    return SourceRef(
        document_id=chunk.document_id,
        document_name=chunk.document_name,
        chunk_id=chunk.chunk_id,
        snippet=(chunk.text or "").strip()[:SNIPPET_CHARS],
    )


def _first_topic(context: GenerationContext, sources: list[SourceRef]) -> str | None:
    allowed = context.allowed()
    for source in sources:
        chunk = allowed.get(source.chunk_id or "")
        if chunk is not None:
            return chunk.topic_id
    return None


# ---------------------------------------------------------------- 出题

class GeneratorError(RuntimeError):
    """模型输出不可用（不是有效 JSON、调用失败等）。"""


async def generate_questions(
    question_type: QuestionType,
    context: GenerationContext,
    config: QuestionGenConfig,
    llm: LLMProvider | None = None,
) -> list[Question]:
    """按题型出题。

    题型分开跑而不是一次让模型出四种：选项数、答案格式、解析写法各不相同，
    分开写规则更清楚；数量配比由配置决定，merge 时不用二次分配；某一种题型
    资料不足时只影响这一种，其他题型照常出。

    JSON 解析失败会带一条修复指令重试一次；仍失败则抛 GeneratorError，
    由服务层转成人话。
    """
    count = max(0, min(config.total, 30))
    if count == 0 or not context.chunks:
        return []

    client = llm or build_llm()
    messages = practice_prompts.build_messages(
        question_type, context.material_text, count, config.difficulty
    )
    raw = await client.chat(messages, temperature=0.35)
    items = parse_json_items(raw)
    if items is None:
        # 容错重试：只加一条修复指令，不重发材料（省 token，也避免模型换思路）
        retry = [
            *messages,
            {"role": "assistant", "content": raw[:2000]},
            {"role": "user", "content": practice_prompts.REPAIR_MESSAGE},
        ]
        raw = await client.chat(retry, temperature=0.1)
        items = parse_json_items(raw)
    if items is None:
        raise GeneratorError(
            f"{practice_prompts.TYPE_SPECS[question_type]['label']}：模型返回的内容不是有效 JSON"
        )

    questions: list[Question] = []
    seen: set[str] = set()
    for item in items:
        question = normalize_question(item, question_type, context)
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
