"""判分口径（FR-05）。

v0.1 只做三种自动判分 + 一种自评：

- 单选、判断：归一化后严格相等
- 多选：**集合完全一致**才得分（少选、多选都不得分）
- 简答：不做 LLM 判分，由用户自评「掌握 / 未掌握」

归一化不是为了宽容，而是把「格式差异」和「知识差异」分开：
`A、` 和 `A` 是同一个答案，不该扣分；`A` 和 `B` 不是，必须扣。
反过来说，多选只对一个选项必须扣——这是知识判断，不是格式问题。

判分集中在这一个文件，是为了让口径可以被单独测试、被单独审查。
散落在接口层的话，「多选到底宽松还是严格」就变成读代码才能知道的事。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from shared.domain.models.entities import Question, QuestionType

# 选项字母：只认 A~F，够覆盖四选一与多选；更长的选项模型自己也记不住
_LETTER = re.compile(r"[A-F]")
_WS = re.compile(r"\s+")

# 判断题的各种写法都要能对上：模型可能答「正确」，用户点的是「对」
_TRUE_TOKENS = {"正确", "对", "是", "真", "true", "t", "yes", "y", "√", "✓", "a", "1"}
_FALSE_TOKENS = {"错误", "错", "否", "假", "false", "f", "no", "n", "×", "✕", "x", "b", "0"}

TRUE_LABEL = "正确"
FALSE_LABEL = "错误"

# 简答题的自评结果：mastered 记为答对，unsure 记为答错
SELF_GRADE_MASTERED = "mastered"
SELF_GRADE_UNSURE = "unsure"


@dataclass(frozen=True, slots=True)
class GradeResult:
    question_id: str
    response: str  # 归一化后的作答，用于回显与错题记录
    correct_answer: str  # 归一化后的正确答案
    is_correct: bool
    answered: bool  # 未作答与「作答但答错」在错题本里必须区分
    self_graded: bool = False


def auto_graded(question_type: QuestionType) -> bool:
    """简答在 v0.1 由人自评，系统不假装能判。"""
    return question_type is not QuestionType.SHORT_ANSWER


def letters(value: str) -> str:
    """提取选项字母并排序去重：`"A、B、D"` / `"bad"` 都归一成 `"ABD"`。"""
    return "".join(sorted(set(_LETTER.findall((value or "").upper()))))


def normalize(question_type: QuestionType, value: str) -> str:
    text = _WS.sub(" ", (value or "").strip())
    if question_type is QuestionType.SINGLE_CHOICE:
        found = letters(text)
        # 只取第一个：单选出现多个字母是作答口径错误，按第一个算，避免整题弃判
        return found[:1]
    if question_type is QuestionType.MULTI_CHOICE:
        return letters(text)
    if question_type is QuestionType.TRUE_FALSE:
        lowered = text.lower()
        if lowered in _TRUE_TOKENS or text in _TRUE_TOKENS:
            return TRUE_LABEL
        if lowered in _FALSE_TOKENS or text in _FALSE_TOKENS:
            return FALSE_LABEL
        return text
    return text


def grade(
    question: Question,
    response: str,
    *,
    self_grade: str | None = None,
) -> GradeResult:
    """判一道题。`answered=False` 表示没作答——它不进错题本。"""
    normalized = normalize(question.type, response)
    expected = normalize(question.type, question.answer)

    if not auto_graded(question.type):
        # 简答：有作答才算答过；自评未给则按「未作答」处理，由前端强制选择
        answered = bool(normalized) or self_grade is not None
        is_correct = self_grade == SELF_GRADE_MASTERED
        return GradeResult(
            question_id=question.id,
            response=normalized,
            correct_answer=expected,
            is_correct=bool(answered and is_correct),
            answered=answered,
            self_graded=True,
        )

    answered = bool(normalized)
    return GradeResult(
        question_id=question.id,
        response=normalized,
        correct_answer=expected,
        is_correct=bool(answered and normalized == expected),
        answered=answered,
    )


@dataclass(frozen=True, slots=True)
class SessionScore:
    total: int
    answered: int
    correct: int
    accuracy: float  # 0~100，保留一位小数
    duration_seconds: float


def summarize(questions: list[Question], results: list[GradeResult], duration: float) -> SessionScore:
    """未作答计 0 分：分数反映「这套卷子你掌握了多少」，不是「答过的题对了多少」。"""
    total = len(questions)
    answered = sum(1 for item in results if item.answered)
    correct = sum(1 for item in results if item.is_correct)
    accuracy = round(correct / total * 100, 1) if total else 0.0
    return SessionScore(
        total=total,
        answered=answered,
        correct=correct,
        accuracy=accuracy,
        duration_seconds=round(max(0.0, duration), 1),
    )
