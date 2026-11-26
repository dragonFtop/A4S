"""练习的请求 / 响应模型（FR-04）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.models import QuestionType
from app.schemas.common import UtcDateTime


class GenerateIn(BaseModel):
    """组卷请求。三种范围可叠加，服务端取交集。"""

    topic_ids: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    # 最近 N 天导入的资料；与上面两项同时给定时是「且」的关系
    days: int | None = Field(default=None, ge=1, le=365)
    counts: dict[QuestionType, int] = Field(default_factory=dict)
    difficulty: Literal["easy", "medium", "hard"] = "medium"

    @field_validator("counts")
    @classmethod
    def _check_counts(cls, value: dict[QuestionType, int]) -> dict[QuestionType, int]:
        total = 0
        for count in value.values():
            if count < 0 or count > 30:
                raise ValueError("每种题型的数量需在 0~30 之间")
            total += count
        if total > 30:
            raise ValueError("单次最多生成 30 道题")
        return value


class SourceOut(BaseModel):
    document_id: str
    document_name: str
    chunk_id: str | None = None
    snippet: str = ""
    page: int | None = None


class QuestionOut(BaseModel):
    id: str
    type: QuestionType
    stem: str
    options: list[str] = Field(default_factory=list)
    answer: str
    explanation: str
    knowledge_point: str | None = None
    document_id: str | None = None
    topic_id: str | None = None
    sources: list[SourceOut] = Field(default_factory=list)


class GenerationJobOut(BaseModel):
    id: str
    state: Literal["running", "done", "failed"]
    session_id: str | None = None
    message: str = ""
    warnings: list[str] = Field(default_factory=list)
    requested: int = 0
    produced: int = 0
    started_at: str | None = None
    finished_at: str | None = None


class SessionOut(BaseModel):
    id: str
    status: Literal["draft", "in_progress", "submitted"]
    score: float = 0.0
    questions: list[QuestionOut] = Field(default_factory=list)
    # 已作答的记录：进行中用于回显进度，提交后用于渲染逐题回顾
    answers: list[AnswerOut] = Field(default_factory=list)
    stats: SessionStatsOut | None = None
    started_at: UtcDateTime
    finished_at: UtcDateTime | None = None
    meta: dict = Field(default_factory=dict)


class SessionBriefOut(BaseModel):
    """列表行：不带题目正文，避免一次拉回几十套卷子。"""

    id: str
    status: Literal["draft", "in_progress", "submitted"]
    score: float = 0.0
    question_count: int
    answered_count: int
    started_at: UtcDateTime
    finished_at: UtcDateTime | None = None
    meta: dict = Field(default_factory=dict)


class QuestionTypeOut(BaseModel):
    key: QuestionType
    label: str
    auto_graded: bool
    hint: str


class PracticeMetaOut(BaseModel):
    types: list[QuestionTypeOut]
    difficulties: list[dict[str, str]]
    max_questions: int
    min_chars_per_question: int


# ---------- 作答与判分（FR-05） ----------


class AnswerIn(BaseModel):
    question_id: str
    response: str = ""
    duration_seconds: float = Field(default=0.0, ge=0)
    # 简答题专用：mastered 记为答对，unsure 记为答错
    self_grade: Literal["mastered", "unsure"] | None = None


class SubmitIn(BaseModel):
    """整卷提交：前端发全量作答，服务端再与自动保存的记录合并。"""

    answers: list[AnswerIn] = Field(default_factory=list)


class AnswerOut(BaseModel):
    question_id: str
    response: str
    is_correct: bool
    duration_seconds: float = 0.0


class SessionStatsOut(BaseModel):
    total: int
    answered: int
    correct: int
    accuracy: float
    duration_seconds: float


# ---------- 错题本（FR-06） ----------


class WrongOut(BaseModel):
    id: str
    question: QuestionOut
    wrong_count: int
    last_response: str = ""
    first_wrong_at: UtcDateTime
    last_wrong_at: UtcDateTime
    topic_id: str | None = None
    topic_name: str = "未分类"
    document_name: str = ""


class WrongTopicOut(BaseModel):
    topic_id: str
    name: str
    color: str
    wrong_count: int
    question_count: int
    answered: int
    correct: int
    error_rate: float


class WrongTypeOut(BaseModel):
    type: QuestionType
    label: str
    count: int


class WrongSummaryOut(BaseModel):
    total: int
    total_wrong_count: int
    repeat_count: int
    by_topic: list[WrongTopicOut] = Field(default_factory=list)
    by_type: list[WrongTypeOut] = Field(default_factory=list)


class RetryIn(BaseModel):
    wrong_ids: list[str] = Field(default_factory=list)
    limit: int = Field(default=10, ge=1, le=30)
