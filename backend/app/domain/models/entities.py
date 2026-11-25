"""业务对象（纯 Python，不依赖 SQLAlchemy / FastAPI）。

为什么表结构和这里要分两份定义
--------------------------------
`infrastructure/db/models.py` 的 ORM 类只管「怎么存」，这里的 dataclass 管
「代码里怎么用」。两者字段相近但不是同一批东西：

- `Question` / `SourceRef` / `AnswerRecord` / `WrongAnswer` 里的题目快照
  **根本不对应任何表**，它们被序列化成 JSON 存在 practice_sessions.questions
  和 wrong_answers.question 里。资料的题目一旦被删，历史练习与错题依然可读，
  靠的就是这份独立快照。
- 服务层拿 dataclass 干活，不碰 ORM 对象。ORM 对象绑定在数据库会话上，
  会话一关就取不到属性（detached），跨函数传递会突然报错。

所以这一层不是「为了分层而分层」：它同时承担了 API 出参、服务层数据结构、
JSON 快照三种用途。字段都以业务语义命名，读代码时不必先想数据库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def _now() -> datetime:
    return datetime.now(timezone.utc)


# 内置主题：不可删除、不可改名，未指定主题的资料/笔记都归到这里
UNCATEGORIZED_ID = "uncategorized"
UNCATEGORIZED_NAME = "未分类"


class DocumentStatus(str, Enum):
    """资料解析状态。"""

    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class TopicKind(str, Enum):
    """主题与标签的语义区分：主题单选归属，标签多值切面"""

    TOPIC = "topic"
    TAG = "tag"


class NoteStatus(str, Enum):
    """笔记状态"""
    DRAFT = "draft"
    CONFIRMED = "confirmed"


class NoteSourceType(str, Enum):
    """笔记来源类型"""
    MANUAL = "manual"
    DOCUMENT = "document"
    CHAT = "chat"
    WRONG_ANSWER = "wrong_answer"


class NoteTemplate(str, Enum):
    """笔记模板类型"""
    OUTLINE = "outline"  # 章节摘要
    CONCEPT = "concept"  # 概念卡
    CODE = "code"  # 代码笔记
    FORMULA = "formula"  # 公式清单


class QuestionType(str, Enum):
    """题目类型"""
    SINGLE_CHOICE = "single_choice"
    MULTI_CHOICE = "multi_choice"
    TRUE_FALSE = "true_false"
    SHORT_ANSWER = "short_answer"


class PracticeStatus(str, Enum):
    """练习状态"""
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"


@dataclass(slots=True)
class SourceRef:
    """题目/答案的来源定位，用于引用溯源"""

    document_id: str
    document_name: str
    chunk_id: str | None = None
    snippet: str = ""
    page: int | None = None


@dataclass(slots=True)
class Topic:
    """主题（kind=topic）或标签（kind=tag）。同一实体两种用途"""

    id: str
    name: str
    kind: TopicKind = TopicKind.TOPIC
    color: str = "#888780"
    sort_order: int = 0
    created_at: datetime = field(default_factory=_now)
    # v0.1 是单机单用户，恒为 "local"。数据库列仍然保留，将来做多用户时启用
    user_id: str = "local"
    # 统计投影，不持久化：主题下资料数，标签下引用数
    count: int = 0

    @property
    def is_builtin(self) -> bool:
        return self.id == UNCATEGORIZED_ID


@dataclass(slots=True)
class Chunk:
    """资料内容分块。每个 chunk 对应一段文本，按 document_id 关联到 Document 表。"""
    id: str
    document_id: str
    index: int
    text: str
    source_name: str
    page: int | None = None
    topic_id: str = UNCATEGORIZED_ID


@dataclass(slots=True)
class Document:
    """资料元信息。内容分块存储在Chunk表里，按document_id关联。"""
    id: str
    name: str
    ext: str
    size: int
    status: DocumentStatus = DocumentStatus.PENDING
    chunk_count: int = 0
    error: str | None = None
    created_at: datetime = field(default_factory=_now)
    ready_at: datetime | None = None
    topic_id: str = UNCATEGORIZED_ID
    tag_ids: list[str] = field(default_factory=list)
    storage_path: str | None = None
    user_id: str = "local"  # 预留：未来多用户隔离


@dataclass(slots=True)
class Question:
    """题目实体。题干、答案、解析、选项都存这里，方便错题本和练习卷快照。"""
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


@dataclass(slots=True)
class PracticeSession:
    """练习卷实体。题目列表和答题记录都存这里，方便续答和错题本快照。"""
    id: str
    questions: list[Question] = field(default_factory=list)
    answers: list[AnswerRecord] = field(default_factory=list)
    status: PracticeStatus = PracticeStatus.DRAFT
    score: float = 0.0
    started_at: datetime = field(default_factory=_now)
    finished_at: datetime | None = None
    user_id: str = "local"
    # 组卷参数快照（范围/题型配比/难度/告警），列表页与续答都靠它还原现场
    meta: dict = field(default_factory=dict)


@dataclass(slots=True)
class WrongAnswer:
    """错题条目：一题一条，重复答错只累加次数。

    题目整份快照留在 `question` 里——资料被删、题库不存在，历史错题仍然可读可重练。
    """

    question: Question
    # 缺省用题目 id：同一条错题在重练卷里 id 不变，去重才有依据
    id: str = ""
    wrong_count: int = 1
    first_wrong_at: datetime = field(default_factory=_now)
    last_wrong_at: datetime = field(default_factory=_now)
    topic_id: str | None = None
    document_id: str | None = None
    # 最近一次答错时写下的答案：错题本要能并排对比「你选的」和「正确答案」
    last_response: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = self.question.id
        if self.topic_id is None:
            self.topic_id = self.question.topic_id
        if self.document_id is None:
            self.document_id = self.question.document_id or (
                self.question.sources[0].document_id if self.question.sources else None
            )


@dataclass(slots=True)
class ReviewReport:
    """定时回顾报告。按周/月/季度生成，按主题聚合错题和笔记。"""
    id: str
    window_days: int
    period_start: datetime
    period_end: datetime
    markdown: str
    metrics: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=_now)


@dataclass(slots=True)
class NoteSource:
    """笔记来源。记录笔记的原始内容和关联信息。"""
    chunk_id: str
    snippet: str = ""
    document_id: str | None = None
    document_name: str | None = None


@dataclass(slots=True)
class Note:
    """电子笔记。AI 起草的是 draft，人工确认后才 confirmed。"""

    id: str
    title: str = ""
    markdown: str = ""
    topic_id: str = UNCATEGORIZED_ID
    status: NoteStatus = NoteStatus.DRAFT
    source_type: NoteSourceType = NoteSourceType.MANUAL
    template: NoteTemplate | None = None
    ai_model: str | None = None
    tag_ids: list[str] = field(default_factory=list)
    sources: list[NoteSource] = field(default_factory=list)
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    confirmed_at: datetime | None = None
    user_id: str = "local"

    @property
    def ai_written_count(self) -> int:
        """正文中标记为 AI 补写的段落数，用于前端提示条。"""
        return self.markdown.count("<!--ai-->")
