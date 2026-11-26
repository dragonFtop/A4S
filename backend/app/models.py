"""数据模型：表结构 + JSON 快照 + 枚举。

一张表只有一个类。以前 ORM 与 dataclass 各写一遍、再靠 `_to_xxx()` 逐字段搬运，
加一个字段要改五处（两边声明 + 转换 + add + update）；现在查出来就是能用的对象，
API 层直接取属性。

不是表的部分也在本文件，但明确标出：`Question` / `SourceRef` / `AnswerRecord`
被序列化成 JSON 存进 `practice_sessions.questions` 和 `wrong_answers.question`。
这是刻意的快照——资料被删之后，历史练习与错题仍然可读、可重练。

投影字段（`Document.tag_ids`、`Topic.count`、`Chunk.source_name`）不是列，
查询时回填、保存时由各自的服务函数显式写关联表。用 property 而不是
SQLAlchemy relationship，是因为它们存的是 id 列表，走 relationship 反倒要
先把 id 查成对象，绕一圈没有收益。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.orm.attributes import NO_VALUE


def _now() -> datetime:
    return datetime.now(timezone.utc)


# 内置主题：不可删除、不可改名，未指定主题的资料/笔记都归到这里
UNCATEGORIZED_ID = "uncategorized"
UNCATEGORIZED_NAME = "未分类"


# ---------------------------------------------------------------- 枚举

class DocumentStatus(str, Enum):
    """资料解析状态。"""

    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class TopicKind(str, Enum):
    """主题单选归属，标签多值切面。"""

    TOPIC = "topic"
    TAG = "tag"


class NoteStatus(str, Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"


class NoteSourceType(str, Enum):
    MANUAL = "manual"
    DOCUMENT = "document"
    CHAT = "chat"
    WRONG_ANSWER = "wrong_answer"


class NoteTemplate(str, Enum):
    OUTLINE = "outline"  # 章节摘要
    CONCEPT = "concept"  # 概念卡
    CODE = "code"        # 代码笔记
    FORMULA = "formula"  # 公式清单


class QuestionType(str, Enum):
    SINGLE_CHOICE = "single_choice"
    MULTI_CHOICE = "multi_choice"
    TRUE_FALSE = "true_false"
    SHORT_ANSWER = "short_answer"


class PracticeStatus(str, Enum):
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"


# ---------------------------------------------------------------- JSON 快照（无对应表）

@dataclass(slots=True)
class SourceRef:
    """题目/答案的来源定位，用于引用溯源。"""

    document_id: str
    document_name: str
    chunk_id: str | None = None
    snippet: str = ""
    page: int | None = None


@dataclass(slots=True)
class Question:
    """题目快照。题干、答案、解析、选项都在这里。

    不对应任何表：整份序列化进练习卷与错题本，资料删了也不影响回看。
    """

    id: str
    type: QuestionType
    stem: str
    answer: str
    explanation: str
    options: list[str] = field(default_factory=list)
    sources: list[SourceRef] = field(default_factory=list)
    document_id: str | None = None
    knowledge_point: str | None = None
    # 主来源所属主题：错题按主题聚合时直接取这里，不必回查资料表
    topic_id: str | None = None


@dataclass(slots=True)
class AnswerRecord:
    """练习卷里每道题的答题记录。"""

    question_id: str
    response: str
    is_correct: bool
    duration_seconds: float = 0.0


def question_to_dict(question: Question) -> dict:
    return {
        "id": question.id,
        "type": question.type.value,
        "stem": question.stem,
        "options": list(question.options),
        "answer": question.answer,
        "explanation": question.explanation,
        "document_id": question.document_id,
        "knowledge_point": question.knowledge_point,
        "topic_id": question.topic_id,
        "sources": [
            {
                "document_id": source.document_id,
                "document_name": source.document_name,
                "chunk_id": source.chunk_id,
                "snippet": source.snippet,
                "page": source.page,
            }
            for source in question.sources
        ],
    }


def question_from_dict(data: dict) -> Question:
    sources = [
        SourceRef(
            document_id=item.get("document_id") or "",
            document_name=item.get("document_name") or "",
            chunk_id=item.get("chunk_id"),
            snippet=item.get("snippet") or "",
            page=item.get("page"),
        )
        for item in (data.get("sources") or [])
        if isinstance(item, dict)
    ]
    try:
        question_type = QuestionType(data.get("type"))
    except ValueError:
        question_type = QuestionType.SHORT_ANSWER
    return Question(
        id=data.get("id") or "",
        type=question_type,
        stem=data.get("stem") or "",
        answer=data.get("answer") or "",
        explanation=data.get("explanation") or "",
        options=list(data.get("options") or []),
        sources=sources,
        document_id=data.get("document_id"),
        knowledge_point=data.get("knowledge_point"),
        topic_id=data.get("topic_id"),
    )


# ---------------------------------------------------------------- 声明基类

class Base(DeclarativeBase):
    """所有表模型的基类。

    构造时顺带填上列的默认值。SQLAlchemy 的 `default` 原本要等到 INSERT 才生效，
    而这里有地方会在落库前就读（测试造数据、SSE 边生成边输出），读到的会是 None。
    填上之后，行为和原来的 dataclass `field(default_factory=...)` 一致。

    只预填标量默认值与 DateTime：`default=list` 这类可调用默认值要 SQLAlchemy
    传执行上下文才能调，构造阶段没有上下文，硬调会炸，交给入库时处理。
    """

    def __init__(self, **kwargs: Any) -> None:
        # 先落用户给的键：questions / answers / tag_ids 这些是 property，
        # 会写到底层列上，必须让它们先执行完再补默认值，否则会被默认值盖掉。
        for key, value in kwargs.items():
            setattr(self, key, value)

        state = sa_inspect(self)
        for key, column in self.__mapper__.columns.items():
            if column.default is None:
                continue
            if state.attrs[key].loaded_value is not NO_VALUE:
                continue
            default = column.default
            if not default.is_callable:
                value: Any = default.arg
            elif isinstance(column.type, DateTime):
                value = default.arg(None)
            elif isinstance(column.type, JSON):
                # default=dict / default=list 都被 SQLAlchemy 包成 fn(ctx)
                value = default.arg(None)
            else:
                continue
            setattr(self, key, value)


def _enum_column(enum_cls: type[Enum], length: int = 16) -> SAEnum:
    """枚举列存 value（pending）而不是 name（PENDING），库里的字面量与老数据一致。

    读出来直接是枚举对象，所以调用方原有的 `x.status.value` 一行都不用改。
    """
    return SAEnum(
        enum_cls,
        values_callable=lambda cls: [item.value for item in cls],
        native_enum=False,
        length=length,
    )


# ---------------------------------------------------------------- 主题与标签

class Topic(Base):
    """主题与标签共用一张表，用 kind 区分。"""

    __tablename__ = "topics"
    __table_args__ = (UniqueConstraint("kind", "name", name="uq_topics_kind_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[TopicKind] = mapped_column(
        _enum_column(TopicKind, 8), nullable=False, default=TopicKind.TOPIC
    )
    color: Mapped[str] = mapped_column(String(16), nullable=False, default="#888780")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="local")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)

    @property
    def is_builtin(self) -> bool:
        return self.id == UNCATEGORIZED_ID

    @property
    def count(self) -> int:
        """投影：主题下资料数 / 标签下引用数。不是列，查询时回填。"""
        return getattr(self, "_count", 0)

    @count.setter
    def count(self, value: int) -> None:
        self._count = value


# ---------------------------------------------------------------- 资料与分块

class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    ext: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[DocumentStatus] = mapped_column(
        _enum_column(DocumentStatus), nullable=False, default=DocumentStatus.PENDING
    )
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 归属主题：必填，缺省指向内置的「未分类」
    topic_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("topics.id"), nullable=False, default=UNCATEGORIZED_ID, index=True
    )
    # 原始文件相对 data/uploads 的路径（粘贴文本为空）
    storage_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="local")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def tag_ids(self) -> list[str]:
        return getattr(self, "_tag_ids", [])

    @tag_ids.setter
    def tag_ids(self, value: list[str]) -> None:
        self._tag_ids = list(value)


class DocumentTag(Base):
    """资料 ↔ 标签（多对多）。主题不走这张表，走 documents.topic_id。"""

    __tablename__ = "document_tags"

    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
    )


class Chunk(Base):
    """片段正文与定位信息。向量存在 vec_chunks，用 id 关联。"""

    __tablename__ = "chunks"
    __table_args__ = (
        Index("ix_chunks_document", "document_id"),
        Index("ix_chunks_topic", "topic_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    # 属性叫 index、列还叫 chunk_index：调用方一直用 index，不动它
    index: Mapped[int] = mapped_column("chunk_index", Integer, nullable=False, default=0)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    topic_id: Mapped[str] = mapped_column(
        String(36), nullable=False, default=UNCATEGORIZED_ID
    )

    @property
    def source_name(self) -> str:
        """投影：所属资料名。不是列，检索时回填。"""
        return getattr(self, "_source_name", "")

    @source_name.setter
    def source_name(self, value: str) -> None:
        self._source_name = value


# ---------------------------------------------------------------- 笔记

class Note(Base):
    """电子笔记。draft → confirmed 两阶段，见 PRD FR-13。"""

    __tablename__ = "notes"
    __table_args__ = (Index("ix_notes_topic", "topic_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    topic_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("topics.id"), nullable=False, default=UNCATEGORIZED_ID
    )
    status: Mapped[NoteStatus] = mapped_column(
        _enum_column(NoteStatus), nullable=False, default=NoteStatus.DRAFT
    )
    source_type: Mapped[NoteSourceType] = mapped_column(
        _enum_column(NoteSourceType), nullable=False, default=NoteSourceType.MANUAL
    )
    template: Mapped[NoteTemplate | None] = mapped_column(
        _enum_column(NoteTemplate, 32), nullable=True
    )
    ai_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="local")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def tag_ids(self) -> list[str]:
        return getattr(self, "_tag_ids", [])

    @tag_ids.setter
    def tag_ids(self, value: list[str]) -> None:
        self._tag_ids = list(value)

    @property
    def sources(self) -> list[NoteSource]:
        return getattr(self, "_sources", [])

    @sources.setter
    def sources(self, value: list[NoteSource]) -> None:
        self._sources = list(value)

    @property
    def ai_written_count(self) -> int:
        """正文中标记为 AI 补写的段落数，用于前端提示条。"""
        return self.markdown.count("<!--ai-->")


class NoteSource(Base):
    """笔记段落的来源溯源：正文里的 [[chunk:xxx]] 与这张表双向一致。"""

    __tablename__ = "note_sources"

    note_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("notes.id", ondelete="CASCADE"), primary_key=True
    )
    chunk_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    snippet: Mapped[str] = mapped_column(Text, nullable=False, default="")

    @property
    def document_name(self) -> str | None:
        """投影：来源资料名，查询时 join 回填（资料删了就为空）。"""
        return getattr(self, "_document_name", None)

    @document_name.setter
    def document_name(self, value: str | None) -> None:
        self._document_name = value


class NoteTag(Base):
    __tablename__ = "note_tags"

    note_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("notes.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
    )


# ---------------------------------------------------------------- 练习与错题

class PracticeSession(Base):
    """练习卷。题目与作答以 JSON 快照存两列，不依赖题库表。"""

    __tablename__ = "practice_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[PracticeStatus] = mapped_column(
        _enum_column(PracticeStatus), nullable=False, default=PracticeStatus.DRAFT
    )
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    questions_json: Mapped[list] = mapped_column("questions", JSON, nullable=False, default=list)
    answers_json: Mapped[list] = mapped_column("answers", JSON, nullable=False, default=list)
    # 组卷参数快照（范围、配比、难度、告警）：题目本身在 questions 里
    meta: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="local")
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def questions(self) -> list[Question]:
        """读侧反序列化成 Question 快照；改完要整体赋值回来才会写库。"""
        return [question_from_dict(item) for item in (self.questions_json or [])]

    @questions.setter
    def questions(self, value: list[Question] | list[dict]) -> None:
        # 允许直接塞 JSON 形态（测试与迁移脚本常这么写），已经是 dict 就不再序列化
        self.questions_json = [
            item if isinstance(item, dict) else question_to_dict(item) for item in value
        ]

    @property
    def answers(self) -> list[AnswerRecord]:
        return [
            AnswerRecord(
                question_id=item.get("question_id", ""),
                response=item.get("response", ""),
                is_correct=bool(item.get("is_correct")),
                duration_seconds=float(item.get("duration_seconds") or 0.0),
            )
            for item in (self.answers_json or [])
        ]

    @answers.setter
    def answers(self, value: list[AnswerRecord] | list[dict]) -> None:
        # 同样允许直接塞 JSON 形态
        self.answers_json = [
            item
            if isinstance(item, dict)
            else {
                "question_id": item.question_id,
                "response": item.response,
                "is_correct": item.is_correct,
                "duration_seconds": item.duration_seconds,
            }
            for item in value
        ]


class WrongAnswer(Base):
    """错题条目：一题一条，重复答错只累加次数。

    去重键选 question_id 而不是题干：同一道题在重练卷里 id 不变，重练答错才能
    累加次数。跨卷子重新生成的近似题会各算一条——机器无法可靠判断「是不是同一道
    题」，与其用文本相似度猜，不如让这个边界清楚。
    """

    __tablename__ = "wrong_answers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    question_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    topic_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    question_json: Mapped[dict] = mapped_column("question", JSON, nullable=False)
    wrong_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 最近一次答错时写下的答案：错题本要能并排对比「你的答案 / 正确答案」
    last_response: Mapped[str] = mapped_column(Text, nullable=False, default="")
    first_wrong_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    last_wrong_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)

    @property
    def question(self) -> Question:
        return question_from_dict(self.question_json or {})

    def __init__(self, **kwargs) -> None:
        """由题目快照建条目时，顺带补齐从题目推导出来的字段。

        这原本是 dataclass 的 __post_init__ 干的活：id 缺省用题目 id（重练去重
        才有依据），主题与资料直接取题目的。也支持直接传 question_json。
        """
        question = kwargs.pop("question", None)
        if isinstance(question, dict):
            # 直接塞 JSON 形态（测试与迁移脚本常这么写）
            question = question_from_dict(question)
        if isinstance(question, Question):
            kwargs["question_json"] = question_to_dict(question)
            kwargs.setdefault("id", question.id)
            kwargs.setdefault("question_id", question.id)
            kwargs.setdefault("topic_id", question.topic_id)
            kwargs.setdefault(
                "document_id",
                question.document_id
                or (question.sources[0].document_id if question.sources else None),
            )
        kwargs.setdefault("wrong_count", 1)
        kwargs.setdefault("last_response", "")
        kwargs.setdefault("first_wrong_at", _now())
        kwargs.setdefault("last_wrong_at", _now())
        super().__init__(**kwargs)

    @classmethod
    def from_question(
        cls,
        question: Question,
        *,
        last_response: str = "",
        last_wrong_at: datetime | None = None,
    ) -> WrongAnswer:
        return cls(
            question=question,
            last_response=last_response,
            last_wrong_at=last_wrong_at or _now(),
        )


# ---------------------------------------------------------------- 计划与回顾

class StudyPlan(Base):
    """学习计划。一行一次规划会话：markdown 是产物，meta 存过程。

    `meta["steps"]` 按顺序记着每一步调了哪个工具、入参是什么、返回摘要是什么，
    计划里的每个数字都要能在里面找到出处。
    """

    __tablename__ = "study_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # 用户这次补充的学习目标，可为空
    goal: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 过程快照：steps / stats / practice_session_id / model / elapsed_seconds
    meta: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="local")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class ReviewReport(Base):
    """定时回顾报告。按周/月/季度生成，按主题聚合错题和笔记。"""

    __tablename__ = "review_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    period_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
