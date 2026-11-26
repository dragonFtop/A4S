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

# 首个用户注册时，库里已有的数据（迁移前留下的）统一挂在这个占位空间上，
# 注册流程会把它整批认领给新用户。之后这个 id 不会再被使用。
LEGACY_SPACE_ID = "default"


class DocumentStatus(str, Enum):
    """资料解析状态。"""

    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    # 小组投稿审核流程（P1.5）：member 上传后置此状态，只存文件与解析文本，
    # 不切分、不向量化、不进 FTS，等 owner 批准后才进入 PROCESSING → READY。
    PENDING_REVIEW = "pending_review"
    # owner 驳回：保留文件与理由，但永不参与检索，不计入 ready 统计。
    REJECTED = "rejected"


class TopicKind(str, Enum):
    """主题与标签的语义区分：主题单选归属，标签多值切面"""

    TOPIC = "topic"
    TAG = "tag"


class NoteStatus(str, Enum):
    """笔记状态"""
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    # 小组投稿审核态：member 投稿到 GROUP 空间先进 pending_review，
    # owner 批准后才变 confirmed 进入小组库；驳回则 rejected。
    # 个人空间不受影响（create 时只写 draft/confirmed）。
    PENDING_REVIEW = "pending_review"
    REJECTED = "rejected"


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
    # 归属空间：所有读写都按它隔离
    space_id: str = LEGACY_SPACE_ID

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
    # 归属空间：所有读写都按它隔离
    space_id: str = LEGACY_SPACE_ID


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
    # 归属空间：所有读写都按它隔离
    space_id: str = LEGACY_SPACE_ID


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
    # 归属空间：所有读写都按它隔离
    space_id: str = LEGACY_SPACE_ID


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
    # 归属空间：所有读写都按它隔离
    space_id: str = LEGACY_SPACE_ID

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
class StudyPlan:
    """一次规划会话的产物：一份 7 天计划 + 生成过程快照。

    `markdown` 是给人看的，`meta` 是给审计看的——里面按顺序记着每一步调了哪个工具、
    入参是什么、返回摘要是什么。计划里的每个数字都要能在 `meta["steps"]` 里找到出处，
    这是「不许自己算数」这条边界的落地方式。
    """

    id: str
    markdown: str
    goal: str = ""
    meta: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=_now)
    user_id: str = "local"
    # 归属空间：所有读写都按它隔离
    space_id: str = LEGACY_SPACE_ID


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
    # 归属空间：所有读写都按它隔离
    space_id: str = LEGACY_SPACE_ID


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
    # 归属空间：所有读写都按它隔离
    space_id: str = LEGACY_SPACE_ID

    @property
    def ai_written_count(self) -> int:
        """正文中标记为 AI 补写的段落数，用于前端提示条。"""
        return self.markdown.count("<!--ai-->")


# ---------------------------------------------------------------- 用户与空间

class SpaceKind(str, Enum):
    """空间类型：个人空间是注册时自动建的，学习小组是手动建的。"""

    PERSONAL = "personal"
    GROUP = "group"


class MemberRole(str, Enum):
    """我在某个空间里的身份。

    owner / member 来自「加入空间」（space_members），viewer 来自「被共享」
    （space_shares）——后者不加入空间，只能看和问，不能写。
    """

    OWNER = "owner"
    MEMBER = "member"
    VIEWER = "viewer"


@dataclass(slots=True)
class User:
    """登录账号。密码只存 scrypt 派生串，明文不落库、不出接口。"""

    id: str
    username: str
    password_hash: str = ""
    display_name: str = ""
    status: str = "active"
    created_at: datetime = field(default_factory=_now)
    last_login_at: datetime | None = None


@dataclass(slots=True)
class Space:
    """空间。个人空间与学习小组是同一个东西，只用 kind 区分。

    这是从 ragflow-plus 学来的关键一点：团队不另建模型，直接复用「归属容器」，
    个人空间把别人加进来就成了小组。少一层模型，就少一层同步和权限判断。
    """

    id: str
    name: str
    kind: SpaceKind = SpaceKind.PERSONAL
    owner_id: str = ""
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    # 投影：成员数 / 我在这个空间里的角色，查询时回填
    member_count: int = 0
    my_role: MemberRole = MemberRole.MEMBER
    # 是否允许外部申请检索。默认关——"我的库"不会因为注册过就自动对外。
    # 只有主人显式打开，才会进公共目录、才接受申请。
    share_enabled: bool = False

    @property
    def is_personal(self) -> bool:
        return self.kind is SpaceKind.PERSONAL


@dataclass(slots=True)
class SpaceMember:
    """空间成员。username / display_name 是投影，展示时不必再查用户表。"""

    space_id: str
    user_id: str
    role: MemberRole = MemberRole.MEMBER
    joined_at: datetime = field(default_factory=_now)
    username: str = ""
    display_name: str = ""

    @property
    def is_owner(self) -> bool:
        return self.role is MemberRole.OWNER


class ShareStatus(str, Enum):
    """共享授权状态。rejected 与 revoked 分开记，是为了让双方都能看到"发生过什么"。"""

    PENDING = "pending"     # 待对方处理
    ACTIVE = "active"       # 已生效
    REJECTED = "rejected"   # 被拒绝
    REVOKED = "revoked"     # 生效后撤销


class ShareOrigin(str, Enum):
    REQUEST = "request"     # 对方申请
    GRANT = "grant"         # 主人主动开放


@dataclass(slots=True)
class SpaceShare:
    """知识库共享：把整个空间以**只读**方式开给一个不在空间里的用户。

    与 SpaceMember 的区别：成员是"进来一起干"，共享是"在外面看一眼"。
    所以它是独立一张表——复用成员表会让"只读成员"污染所有权限判断。
    """

    id: str
    space_id: str
    grantee_id: str
    status: ShareStatus = ShareStatus.PENDING
    origin: ShareOrigin = ShareOrigin.REQUEST
    message: str = ""
    created_at: datetime = field(default_factory=_now)
    decided_at: datetime | None = None
    # 投影，展示时不必再查表
    space_name: str = ""
    owner_name: str = ""
    grantee_name: str = ""
    grantee_username: str = ""

    @property
    def is_active(self) -> bool:
        return self.status is ShareStatus.ACTIVE
