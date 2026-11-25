# A4S 后端代码讲解（逐模块）

> 写给要学习与审查代码的你。配套的设计总览见 `BACKEND.md`，那份讲"为什么这么设计"，这份讲"每一部分具体是什么、怎么实现的"。
> 对应版本：2026-11-25（M1 → M5 全部完成，`app/` 约 9 740 行 Python）。
> 每个模块的讲解顺序：**它是什么 → 关键实现 → 为什么这么写 → 审查时看什么**。

---

## 目录

1. [启动链路：main → config → 能力装配](#1-启动链路)
2. [领域层：domain/models/entities.py](#2-领域层)
3. [配置基础设施：config_store.py（.env 读写）](#3-配置持久化)
4. [数据库会话：db/session.py](#4-数据库会话)
5. [ORM 表定义：db/models.py](#5-orm-表定义)
6. [笔记全文检索：db/fts.py](#6-全文检索-fts5)
7. [种子数据：db/seed.py](#7-种子数据)
8. [向量库：vectorstore/sqlite_vec_store.py](#8-向量库)
9. [LLM 端口与实现：llm/base、openai_compat、bge_local](#9-llm-与-embedding)
10. [文档解析器：parsers/](#10-文档解析器)
11. [文本切分器：splitters/text_splitter.py](#11-文本切分器)
12. [网页抓取：webfetch.py + webfetch_service.py](#12-网页抓取)
13. [仓储层：repositories/](#13-仓储层)
14. [入库流水线：ingest_service.py](#14-入库流水线)
15. [索引维护：index_service.py](#15-索引维护)
16. [笔记业务：note_service.py + note_prompts.py](#16-笔记业务)
17. [问答业务：chat_service.py + chat_prompts.py](#17-问答业务)
18. [主题业务：topic_service.py](#18-主题业务)
19. [API 层全部路由](#19-api-层)
20. [统计投影：stats_repo.py](#20-统计投影)
21. [横切关注点：错误、时间、线程、异步](#21-横切关注点)
22. [测试脚本与迁移](#22-测试脚本与迁移)
23. [练习出题：practice_service + generators（M4）](#23-练习出题m4)
24. [作答、判分与错题本：grading.py + practice_service（M4）](#24-作答判分与错题本m4)
25. [回顾报告与推送：review_repo + review_service + scheduler + smtp（M5）](#25-回顾报告与推送m5)
26. [审查清单](#26-审查清单)

---

## 1. 启动链路

### 1.1 `app/main.py` — 入口

整个文件 74 行，做四件事：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    (DATA_DIR / "uploads").mkdir(...)  # ① 准备上传目录
    init_db()                          # ② 建表 / 升级表结构
    with SessionLocal() as db:
        ensure_builtin(db)             # ③ 确保「未分类」主题存在
    scheduler.start()                  # ④ 装载定时回顾任务
    yield                              # ← 应用在此运行
    scheduler.shutdown()               # 关闭时停掉后台线程
```

- **为什么用 lifespan 而不是模块级代码**：FastAPI 的 lifespan 在事件循环就绪前执行同步准备，且关闭时有明确的清理边界。定时任务必须在这里启停——放在模块级就没有"关闭"这个时机。
- **`ensure_builtin` 的必要性**：`documents.topic_id` 列默认值是 `"uncategorized"`，这个 id 指向的主题行如果不存在，外键就悬空。所以每次启动都幂等地补一次。
- CORS 只放行 `localhost:5173`（Vite 开发端口），`allow_credentials=True` 为将来加登录预留。
- 根路径 `/` 返回一个自述 JSON，`/docs` 是 FastAPI 自动生成的交互式 API 文档（开发时非常好用）。

> **v0.1 收尾时的改动**：启动流程里原有一步 `register_builtin_extensions()`，把实现类注册进
> `registry.py` 的插件表。v0.1 不做插件，这一步和注册表本身都删除了。现在各个能力由
> 工厂函数直接构造（见 1.3）。

### 1.2 `app/config.py` — 单一配置源

`pydantic_settings.BaseSettings` 的标准用法，但有三个容易漏看的细节：

1. **路径推导**（第 16-18 行）：`BACKEND_DIR = Path(__file__).resolve().parents[1]`——从文件位置反推，不依赖启动目录。这就是为什么从任意目录 `uvicorn app.main:app` 都能找到 `data/`。

2. **`resolved_database_url`**（第 71-78 行）：把 `sqlite:///./data/app.db` 的相对路径锚定到 `backend/` 下。SQLite 相对路径是相对**进程工作目录**的，不锚定的话换个目录启动就会静默新建一个空库。

3. **HF_ENDPOINT 必须在 import 前设置**（第 96-100 行）：

```python
if settings.hf_endpoint:
    os.environ.setdefault("HF_ENDPOINT", settings.hf_endpoint)
```

`huggingface_hub` 在**模块导入时**把 `HF_ENDPOINT` 读成模块级常量，之后再设环境变量无效。所以这段代码必须位于"任何 sentence_transformers 导入"之前——config 是全项目最早被导入的模块，放这里最保险。国内直连 huggingface.co 常超时，默认走 `hf-mirror.com` 镜像。

`settings = get_settings()` 是模块级单例（`@lru_cache` 保证）。`config_store.save_settings()` 会原地 `setattr(settings, ...)`，所以界面改配置不需要重启——这是全项目"配置即时生效"的枢纽。

### 1.3 能力是怎么接上的

v0.1 不做插件，所以没有注册表。每个可替换能力由**一个工厂函数**负责构造，
调用方只认函数、不认具体类：

| 能力 | 工厂函数 | 位置 | 怎么选实现 |
|---|---|---|---|
| 对话模型 | `build_llm()` | `infrastructure/llm/openai_compat.py` | 所有供应商都讲 OpenAI 兼容协议，差别只在 `base_url` + 默认模型，查 `LLM_PRESETS` |
| 向量化 | `build_embedding()` | `infrastructure/llm/bge_local.py` | 看 `embedding_provider`：`local`/`bge` 走本地模型，`cloud`/`openai` 走 HTTP |
| 文档解析 | `resolve_parser(ext)` | `services/ingest_service.py` | 遍历 `builtin.PARSERS`，用 `supports(ext)` 命中 |
| 出题 | `LLMTypedGenerator(type)` | `infrastructure/generators/llm_generator.py` | 题型作构造参数，四种题型共用一套解析与去重 |
| 邮件推送 | `build_notifier()` | `infrastructure/mail/smtp_notifier.py` | 目前只有 SMTP 一种实现 |

这样写的好处是**看完一个函数就知道会走哪条路**，不需要先查注册表里有哪些键、
哪个类挂在哪个键上。代价是新增供应商要改一个 if 或字典——单机项目这是划算的。

> 删掉注册表之前，`build_llm()` 的实现是 `LLM_REGISTRY.create(settings.llm_provider, ...)`，
> 而 `deepseek` / `qwen` / `openai` / `moonshot` 四个键全部指向同一个类
> `OpenAICompatibleLLM`，默认 base_url 又在 `LLM_PRESETS` 里已经定义过一次——
> 同一份信息写了两遍，这是删掉它的直接原因。

---

## 2. 领域层

`app/domain/models/entities.py` — 纯 Python dataclass，不依赖任何框架。

**为什么三套模型分开**（领域实体 / ORM / API DTO）：领域实体是业务语义的载体，ORM 只管怎么存，DTO 管进出 API 的形状。三者耦合的典型恶果是"改一个字段，数据库、接口、业务全被动跟着改"。

这里还有两个更硬的理由，写在 `entities.py` 的文件头里：

1. **JSON 快照**：`Question` / `SourceRef` / `AnswerRecord` / `WrongAnswer` 里的题目内容
   根本不对应任何表，它们被序列化成 JSON 存进 `practice_sessions.questions` 和
   `wrong_answers.question`。资料删了，历史练习与错题仍然完整可读，靠的就是这份独立快照。
2. **会话边界**：ORM 对象绑定在数据库会话上，会话一关属性就取不到（detached）。
   服务层如果直接传 ORM 对象出去，调用方访问字段会突然报错。所以在边界上必须转成普通 dataclass。

关键定义逐个说：

| 定义 | 语义 | 审查点 |
|---|---|---|
| `UNCATEGORIZED_ID = "uncategorized"` | 内置主题的固定 id，全项目到处引用 | 它是常量不是 UUID，所以能写进列默认值 |
| `DocumentStatus` | pending → processing → ready / failed 四态状态机 | 迁移只在 `run_ingest` 里发生 |
| `TopicKind` | topic=单选归属，tag=多值切面 | 同一张表两种用途，靠 kind 区分 |
| `NoteStatus` | draft / confirmed 两阶段 | confirmed 后一改就退回 draft（规则在 service） |
| `NoteSourceType` | manual / document / chat / wrong_answer | 记录笔记从哪来；chat 是 M3 新增 |
| `NoteTemplate` | outline / concept / code / formula | 提示词骨架一一对应 |
| `SourceRef` | 题目/答案的来源定位 | M4 出题时用 |
| `Chunk.topic_id` | 片段冗余主题 id | **这是向量分区键的数据来源**，搬主题必须同步 |
| `Note.ai_written_count` | `markdown.count("<!--ai-->")` 的 property | 不持久化，前端提示条用它 |
| `user_id: str = "local"` | 所有实体都有，预留多用户 | 目前永远是 "local" |

所有时间戳统一 `datetime.now(timezone.utc)` 生成；写 SQLite 时**去掉 tzinfo 存成 naive UTC**（见 §21）。

---

## 3. 配置持久化

`app/infrastructure/config_store.py` — 设置页"保存"背后的全部逻辑。

**核心问题**：界面改了配置，怎么保证"不重启就生效、重启后也不丢、`.env` 里的注释不丢、打码的密钥不被写回去"？

实现要点：

1. **白名单 `WRITABLE_FIELDS`**（第 22-47 行）：只放行 LLM/Embedding/SMTP/调度相关键。`DATABASE_URL` 这类需要重启的键不在白名单里，传了直接 400。`_normalize()` 是第一道闸。

2. **密钥三态处理**（`_normalize` 内）：

```python
if key in _SECRET_FIELDS:
    if raw == CLEAR_SENTINEL:  cleaned[key] = ""   # 显式清除
    if _is_masked(raw):        continue            # 打码值 = 用户没改，跳过
```

前端拿到的 api_key 是 `sk-***abc` 形式。如果用户没动它就保存，回传的还是打码值——**绝不能把 `sk-***abc` 写进 .env**。`__CLEAR__` 是约定的哨兵值，表示"用户点了清除"。

3. **保序写入**（`save_settings` 第 154-181 行）：逐行扫描旧 `.env`，记录每个键所在行号，原地替换该行；新键追加到末尾。**注释和空行原样保留**——这就是为什么改完配置 `.env` 依然可读。首次写入时以 `.env.example` 为基底，避免生成残缺配置。

4. **即时生效**（最后一行）：`setattr(settings, key, value)` 原地更新单例。全项目所有 `from app.config import settings` 拿的都是同一个对象引用，所以下一个请求立刻用上新值。

5. `merged_values(overrides)` 供"测试连接"用：把界面临时填的值覆盖到当前配置上，**不写盘**——用户可以先测试再决定保存。

---

## 4. 数据库会话

`app/infrastructure/db/session.py` — 全项目最关键的"接线板"。

```python
engine = create_engine(url, connect_args={"check_same_thread": False}, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
```

- `check_same_thread=False`：FastAPI 的同步端点跑在线程池里，不同请求可能用不同线程访问同一连接池；SQLite 默认禁止，必须关掉。安全性由"每请求独立 Session"保证。
- `expire_on_commit=False`：commit 后 ORM 对象不失效，不需要再查一次库就能读属性。配合"写后回读"模式（§13.4）。
- `get_db()` 是 FastAPI 依赖：yield 一个 Session，请求结束 finally close。

`init_db()`：跑 Alembic 迁移到 head，**不用 create_all**——迁移链才是结构的唯一事实来源，create_all 会让两者脱节。迁移失败时兜底 create_all（首次启动没 alembic 版本表时可能发生），保证应用起得来。

后面三段是"装配时序"的精髓：

```python
VEC_ENABLED = attach_sqlite_vec(engine)   # ① 给 engine 挂连接事件：每条新连接加载 sqlite-vec 扩展
FTS_ENABLED = fts.ensure_schema(engine)   # ② 建笔记 FTS5 虚表；失败则降级 LIKE
```

`get_vector_store()` 是**带维度缓存的单例**：

```python
def get_vector_store() -> SqliteVecStore:
    dimension = settings.embedding_dimension
    if _vector_store is None or _vector_store_dim != dimension:
        _vector_store = SqliteVecStore(engine, dimension)
        ...
```

注释里写了踩过的坑：改了 `embedding_dimension` 配置后必须重建实例，否则旧实例拿旧维度去比对会得出错误结论。这里走过的弯路是——曾经写死单例后改配置不生效，排查了很久才发现是缓存维度没跟着变。

---

## 5. ORM 表定义

`app/infrastructure/db/models.py` — 10 张表，只管"怎么存"。

| 表 | 用途 | 关键设计 |
|---|---|---|
| `topics` | 主题+标签共用 | `UniqueConstraint("kind","name")`；「未分类」id 固定为 `"uncategorized"` |
| `documents` | 资料 | `topic_id` 外键默认 "uncategorized"；`storage_path` 存相对路径 |
| `document_tags` | 资料↔标签多对多 | 联合主键，ondelete CASCADE |
| `chunks` | 片段正文 | **`topic_id` 冗余存储**（无外键）——它要进向量库当分区键，文档删了它也没了 |
| `notes` | 笔记主表 | status/source_type/template/ai_model 全是字符串列，读出时由仓储转回枚举 |
| `note_sources` | 笔记来源 | **联合主键 (note_id, chunk_id)**；snippet 存 240 字快照；**对 chunks 无外键**——资料删了来源行还在，靠 join 文档名 join 不上来标记 missing |
| `note_tags` | 笔记↔标签 | 同 document_tags |
| `practice_sessions` | 练习 | questions/answers 存 JSON 列（M4 用） |
| `wrong_answers` | 错题 | question 整体存 JSON 快照——题目随练习删掉后错题本还能显示题面 |
| `review_reports` | 回顾报告 | markdown + metrics JSON（M5 用） |

**两张不在 ORM 里的虚表**：`vec_chunks`（sqlite-vec 的 vec0 虚表）和 `notes_fts`（FTS5 虚表）。SQLAlchemy 没有对应方言支持，分别由 vectorstore 和 fts 模块用裸 DDL 管理。

**审查点**：`note_sources` 不设外键是刻意的（快照语义，不是引用语义）；`chunks.topic_id` 不设外键也是刻意的（它是投影不是归属，真实归属在 documents 上）。

---

## 6. 全文检索 FTS5

`app/infrastructure/db/fts.py` — 106 行，解决一个很具体的问题：**FTS5 默认搜不了中文**。

**问题本质**：`unicode61` 分词器把连续汉字当成一个 token——"向量索引"存成一个词，用户搜"索引"永远命中不了。

**解法：按字切分**。

```python
def char_split(value: str) -> str:
    pieces = _CJK.sub(lambda m: f" {m.group(0)} ", value)   # 汉字两侧插空格
    return _SPACE.sub(" ", pieces).strip()
```

入库时把标题正文都做"向 量 索 引"式拆分；查询时同样拆，然后**包成短语查询**：

```python
def _phrase(keyword: str) -> str:
    tokens = char_split(keyword).split()
    return '"' + " ".join(tokens) + '"'   # "向 量 索 引" 要求四字连续
```

短语查询要求字符**连续出现**，所以"引索"不会误命中"索引"。英文/数字保持整词不受影响。

**bm25 权重**：`ORDER BY bm25(notes_fts, 4.0, 1.0)` —— title 列权重 4.0、body 1.0，标题命中的排前面。

**同事务同步**（这是审查重点）：FTS 行不由"后台定时同步"维护，而是由笔记仓储在 add/update/delete 时**用同一个 Session 的 connection** 调 `upsert/remove`。这杜绝了"笔记删了但索引还在"这类只在搜索时暴露的脏数据。

`ensure_schema` 失败（比如编译的 SQLite 没有 FTS5）返回 False，全局 `FTS_ENABLED=False`，笔记搜索自动降级为 `LIKE`——能搜，只是没有单字中文能力。

---

## 7. 种子数据

`app/infrastructure/db/seed.py`：

- `ensure_builtin`：幂等创建「未分类」，`sort_order=10_000` 保证永远排最后。
- `apply_topic_packs`：从 `app/seed/topic_packs.json` 读预置学习方向包（计组、密码学、LangChain 等），**按名称幂等**——已存在的跳过，不覆盖用户改过的颜色。`sort_order` 用递增计数器而不是 offset，避免多个包之间撞号。
- `topic_seeded`：查是否存在非内置主题，前端据此决定展示引导层还是正常界面。

---

## 8. 向量库

`app/infrastructure/vectorstore/sqlite_vec_store.py` — sqlite-vec 的完整封装，225 行。

### 8.1 扩展加载（`attach_sqlite_vec`）

```python
@event.listens_for(engine, "connect")
def _on_connect(dbapi_conn, _record):
    dbapi_conn.enable_load_extension(True)
    sqlite_vec.load(dbapi_conn)
    dbapi_conn.enable_load_extension(False)
```

sqlite-vec 是 C 扩展，**每条新连接**都要加载一次。挂到 SQLAlchemy 的 connect 事件上，之后全项目所有连接自动具备 vec0 语法。返回 False 时（非 SQLite / 未安装）调用方降级而不是崩。

### 8.2 表结构

```sql
CREATE VIRTUAL TABLE vec_chunks USING vec0(
    chunk_id TEXT PRIMARY KEY,
    embedding FLOAT[512] distance_metric=cosine,
    topic_id TEXT partition key
);
```

- **`topic_id` 是 partition key**——这是 FR-02 规则 7 的落地：按主题检索时在分区内取 Top-K，而不是"全库取 K 再过滤"。后者在分区内容少时严重召回不足（大主题占满 K 个名额）。
- 向量维度写死在建表语句里。`_existing_dimension()` 用正则从 `sqlite_master.sql` 里把 `FLOAT[(\d+)]` 抠出来，作为"索引真实维度"的唯一来源。**配置说不算，表结构说了才算**。

### 8.3 关键方法

- **`add`**：`INSERT OR REPLACE`，向量用 `sqlite_vec.serialize_float32()` 序列化。写前检查 `is_compatible()`，维度不符抛 `VecDimensionMismatch`——宁可拒绝写入也不静默写坏索引。
- **`search`**：

```python
oversample = 4 if allowed_chunk_ids else 1
k = max(top_k * oversample, top_k)
sql = "... WHERE embedding MATCH :v AND k = :k"
if topic_id: sql += " AND topic_id = :topic"
```

`allowed_chunk_ids`（按标签圈定）无法下推到分区，只能检索后过滤——所以**4 倍超采再截断**，避免过滤后不足 Top-K。
- **`repartition`**：vec0 不支持原地 UPDATE partition key（0.1.9 会报错），但向量可以读回来再插入。改主题 = SELECT 原向量 → DELETE → INSERT 新分区。**不需要重新 embedding**，这是"改归属不重算"的关键。
- **`reset`**：DROP + 重建。只在用户明确点"重建索引"时调用——绝不自动删表，那会静默丢数据。

---

## 9. LLM 与 Embedding

### 9.1 端口 `llm/base.py`

```python
class LLMProvider(ABC):
    async def chat(self, messages, **options) -> str          # 一次性
    def stream(self, messages, **options) -> AsyncIterator[str]  # 流式

class EmbeddingProvider(ABC):
    def embed(self, texts) -> list[list[float]]
    def embed_one(self, text) -> list[float]                  # 默认实现
```

应用层（service）只依赖这两个抽象。

### 9.2 `openai_compat.py` — 一套实现接多家

DeepSeek/通义/OpenAI/Moonshot 都提供 OpenAI 兼容端点，所以**一个类注册四个键**：

```python
@LLM_REGISTRY.register("openai")
@LLM_REGISTRY.register("deepseek")
@LLM_REGISTRY.register("qwen")
@LLM_REGISTRY.register("moonshot")
class OpenAICompatibleLLM(LLMProvider): ...
```

`LLM_PRESETS` 提供 base_url/model 预设（设置页下拉框的数据源）。换供应商 = 换一个配置键，不写一行新代码。

`chat()`：httpx 异步 POST `/chat/completions`，取 `choices[0].message.content`。
`stream()`：`client.stream()` + `aiter_lines()` 逐行解析 SSE——只处理 `data:` 开头的行，`[DONE]` 终止，`choices[0].delta.content` 为增量。解析失败的行（心跳、注释）静默跳过。

`build_llm()` 工厂：注册表 + settings 组装。**每次调用新建实例**（httpx client 不复用，单机低频场景够用）。

### 9.3 `bge_local.py` — 本地 Embedding

- `LocalBgeEmbedding`：`sentence_transformers` **延迟导入**（`_ensure_model` 里才 import），M1 阶段不装 torch 也能起服务；未安装时给人话指引。模型 `BAAI/bge-small-zh-v1.5`（512 维，中文优化），`normalize_embeddings=True` 归一化——归一化后内积等价余弦，sqlite-vec 的 cosine 度量才准确。
- `CloudEmbedding`：走 `/embeddings` 端点（通义/OpenAI），预留。
- `build_embedding()` 用 `@lru_cache(maxsize=4)` 缓存实例——BGE 加载要几百 MB 内存和数秒时间，**绝不能每个请求都建**。缓存键包含全部影响行为的配置项，配置改了自动得到新实例。

---

## 10. 文档解析器

`app/infrastructure/parsers/`：

- `base.py`：`DocumentParser` ABC + `ParsedDocument(text, pages, meta)`。`supports(ext)` 判定能力。
- `builtin.py` 四个实现，注册键分别是 pdf / docx / markdown / text：

| 解析器 | 要点 |
|---|---|
| `PdfParser` | pypdf 逐页抽文本，**保留 (页码, 文本)** 供片段标注来源页码；加密 PDF 试空密码解密；个别损坏页 warning 后跳过，不毁整份；全文为空时报"可能是扫描版，不支持 OCR" |
| `DocxParser` | python-docx 抽段落 + **表格按 ` | ` 拼接**——表格内容也是知识 |
| `MarkdownParser` | 原样保留（标题层级对切分和出题有用） |
| `TextParser` | 原样保留 |

- **编码探测**：`read_text_with_fallback` 按 `utf-8-sig → utf-8 → gb18030` 顺序尝试——中文 txt 常见 GBK/GB18030，全兜住，最后兜底 `errors="ignore"`。
- `resolve_parser(ext)`（在 ingest_service 里）：遍历注册表逐个 `supports()` 判定。**不靠键名对应扩展名**，靠能力声明——未来一个解析器支持多扩展名也不用改调用方。

---

## 11. 文本切分器

`app/infrastructure/splitters/text_splitter.py` — 纯函数、无 IO，可单测。

**策略**（PRD FR-02 规则 2）：

1. **语义单元**：先按空行切段落；段落超长（>max_size−overlap）再按单行拆；行还超长就按**中文句读** `(?<=[。！？；!?;])` 切，再不行硬切。
2. **贪心合并**：单元累加到 target_size（800）；不足 min_size（500）时允许超目标继续吸收（不超过 max_size 1000）——**碎片片段检索质量差，宁可超目标也要凑够下限**。
3. **重叠**： flush 时把上一片尾部 150 字带进新片，避免答案恰好被切在边界上。重叠长度按 `max_size - len(unit) - 1` 的预算收缩——单元顶到上限时重叠必然放不下，先算预算再截。

```python
unit_limit = max(200, cfg.max_size - cfg.overlap)  # 单元上限扣掉重叠空间
```

这个 `unit_limit` 是修过的 bug：最初单元按 max_size 切，加上重叠后新片超限。

`split_document(pages, text)`：PDF 有分页信息时**逐页切**，每片带页码；否则整篇切。

---

## 12. 网页抓取

### 12.1 `infrastructure/webfetch.py` — 抓取与正文提取

**选 trafilatura 的原因**：正文提取的难点是去掉导航/侧栏/广告/页脚，trafilatura 的启发式在中文文档站、博客上效果最好，且 2.x 直接 `output_format="markdown"`，省掉 HTML→MD 二次转换。

`fetch_page(url)` 的防线依次是：

1. **异常翻译**：Timeout / ProxyError / ConnectError / HTTPError 各配一句人话（ProxyError 单列，因为本机代理环境的报错和普通连接错误长得不一样）。
2. **状态码**：非 200 直接报。
3. **类型判定**：content-type 含 pdf 或 URL 以 .pdf 结尾 → 提示改走文件上传；非 HTML 类型 → 报不支持。
4. **大小上限**：5 MB——教程页没有更大的合理正文。
5. **提取**：`trafilatura.extract(favor_recall=True, include_tables/links/formatting)`——`favor_recall` 偏向多提取正文（宁可多带边角料，交给用户在预览里删）。提取异常吞掉返回空串（提取器崩不该崩接口）。
6. `_pick_title`：标题优先取**正文首个一级标题**（更贴近内容），退回 `<title>` 标签。

### 12.2 `services/webfetch_service.py` — 预览服务（不入库）

`fetch_for_preview(url)`：

- `_validate`：长度上限 2048、必须 http/https、必须带 netloc、**netloc 带 @ 拒绝**（userinfo 形态在单机应用没有合理场景，顺手防了 SSRF 的一种花样）。
- `MIN_MARKDOWN_CHARS = 200`：正文低于 200 字大概率是 JS 渲染页提取失败，报错让用户改走粘贴——**不硬撑**。
- 返回 `{url, title, markdown, chars}` 供预览。**这里不写库**——用户在预览里可编辑，确认后才走 `/paste`，与普通资料同一条入库路径（见 §14）。两步走是因为正文提取不完美，"确认"这步不能省。

---

## 13. 仓储层

`app/repositories/`：

按实体拆开，一张表一个文件，都在 60～190 行之间：

| 文件 | 内容 |
|---|---|
| `topics.py` | 主题 / 标签（同一张表，`kind` 区分） |
| `documents.py` | 资料元信息、标签关联、主题迁移、删除级联 |
| `chunks.py` | 资料分块 |
| `notes.py` | 电子笔记（写入时同步维护 FTS） |
| `practice.py` | 练习卷（题目与作答存 JSON 快照） |
| `wrong_answers.py` | 错题本 |
| `_converters.py` | 题目快照的序列化（练习与错题本共用） |
| `sqlalchemy_repo.py` | **只是索引**，把上面这些 re-export 出去，20 行 |

调用方统一 `from app.repositories.sqlalchemy_repo import SqlAlchemyXxxRepository`，
不直接依赖具体文件——换存储实现时只需要改索引里的指向。

> 拆分之前这里是单个 831 行的文件，六个仓储类挤在一起。拆开只改了文件组织，
> 逻辑一行未动，331 项冒烟测试验证等价。

### 13.1 转换函数

每个实体文件顶部都有一个 `_to_xxx(row) -> 实体` 转换函数，负责 ORM → 领域实体。
注意 `_to_chunk` 里 `source_name=""`——**文档名由调用方按需补**，避免每次列表查询都 join。
题目快照的序列化（`question_to_dict` / `question_from_dict`）被练习和错题本共用，
单独放在 `_converters.py`。

### 13.2 标签/来源的批量装配

`_tags_map(note_ids)` / `_sources_map(note_ids)`：一次 `IN` 查询把整页笔记的关联行查回来，在 Python 侧按 note_id 分组。**避免 N+1**——50 条笔记逐条查标签就是 51 条 SQL。

`_sources_map` 里那句 outerjoin 是修过的缺陷：

```python
select(NoteSourceORM, DocumentORM.name)
    .outerjoin(DocumentORM, DocumentORM.id == NoteSourceORM.document_id)
```

来源的 `document_name` 只能靠 join 拿到；LEFT JOIN 保证资料删除后来源行仍在（document_name=None），上层据此标 `missing=true` 显示「来源已删除」。

### 13.3 FTS 与列表

- `add/update` 里调用 `_sync_fts(note)`：用**当前事务的 connection** 同步 FTS 行（§6 的同事务原则）。
- `list(keyword=...)`：FTS 可用时先 `fts.search()` 拿按相关度排好的 note_id 列表，`WHERE id IN (…)` 过滤后**按 FTS 顺序重排**；FTS 不可用或无命中退回 `LIKE`。
- `_replace_relations`：标签与来源都是**先删后插**（delete-all-then-insert），配合 snippet 截 500 字。全量替换比 diff 简单得多，且天然幂等。

### 13.4 "写后回读"模式

服务层 create/update 落库后**统一再 `repo.get()` 一次**：

```python
repo.add(note)
return repo.get(note.id) or note   # 来源的文档名是 join 出来的，内存对象上没有
```

这是修过的真缺陷：写入路径组装的领域对象没有 `document_name`（它只存在于 join 里），直接序列化返回会显示「来源已删除」。回读一次让**读路径成为序列化数据的唯一来源**。

---

## 14. 入库流水线

`app/services/ingest_service.py` — FR-01/FR-02 的核心，232 行。

### 14.1 统一路径

`create_from_upload`（文件）与 `create_from_text`（粘贴/网页）最终**都落成 `data/uploads/YYYY/MM/<uuid>.<ext>` 文件**，粘贴文本存 .txt。好处：解析、重跑、删除、溯源只有一条路径，不为"无文件资料"写分支。

校验：扩展名白名单 `{pdf,docx,md,markdown,txt}`、非空、20 MB / 10 万字符上限。标题缺省时 `_guess_title` 取正文首个非空行（去 `#`）。

### 14.2 状态机与后台任务

```python
def run_ingest(document_id: str) -> None:
    db = SessionLocal()          # ← 自己开 Session！
```

**为什么不用请求的 Session**：这个函数由 `BackgroundTasks` 在请求结束后执行，此时请求的 Session 已关闭；而且它在另一个线程。后台任务一律自开自关。

流程：`get → PROCESSING → _process() → READY(带 chunk_count)`；`_process` 抛任何异常 → FAILED + 错误文案截 500 字入库。前端轮询列表就能看到状态流转。

### 14.3 `_process` 的七步

```
① 检查原始文件存在 → ② resolve_parser 解析 → ③ split_document 切分
→ ④ _embed_batched 向量化（批 32）→ ⑤ store.is_compatible() 维度检查
→ ⑥ 清旧片段+旧向量（幂等）→ ⑦ 写 chunks + 写向量
```

- **幂等重跑**：处理前先删掉上一次的片段与向量，所以 `POST /{id}/reingest` 天然安全。
- `_embed_batched` 按批 32 条喂模型（单批太大内存爆、太小效率低），并校验向量数 == 片段数。

### 14.4 删除与搬主题

```python
def delete_document(db, document_id):
    # 向量 → 片段 → 关联 → 文档 → 原始文件，顺序不能反
```

先删向量（拿得到 chunk_id 列表的最后机会），再删关系行（CASCADE 兜底），最后删文件（失败仅 warning——文件删不掉不该让整次删除报错）。

```python
def move_document_topic(db, document_id, topic_id):
    repo.set_topic(document_id, topic_id)       # 文档 + chunks 的 topic_id 一起改
    store.repartition(chunk_ids, topic_id)      # 向量分区键同步搬移
```

**审查点**：资料改主题必须三处同步（documents.topic_id、chunks.topic_id、vec_chunks 分区键），漏任何一处按主题检索就会漏掉这份资料。这是本项目最容易出错的联动，`topic_service.delete_topic` 删主题迁移资料时逐个调用它。

---

## 15. 索引维护

`app/services/index_service.py` — 只回答两个问题：索引还能不能用？要重建怎么重建？

**`index_status(db)`** 返回对照视图：

```python
dimension_ok = index_dimension is None or index_dimension == configured
count_ok = vector_count == chunk_count
needs_rebuild = VEC_ENABLED and (not dimension_ok or not count_ok)
```

空库首启（索引表不存在）不算不一致——下次入库会按配置建表。

**重建流程 `run_rebuild`**：

1. **探针测维度**：先 embed 一句 `"A4S 索引重建探针"`，用**实测维度**回写配置（`save_settings`）再建表。原理：`embedding_dimension` 是手填的，容易和模型真实输出不一致——**配置应当描述事实，而不是反过来**。
2. `get_vector_store()` 重新取单例（维度可能刚改过）→ `store.reset()` 删表重建。
3. 分页（200/页）遍历 chunks，批 32 向量化写入，`_set_job(done=…)` 持续更新进度。

任务状态 `_JOB` 是进程内字典 + `threading.Lock`——`start_rebuild` 只登记状态并防并发（running 时再点返回 409），真正的活在 BackgroundTasks 里跑。

**为什么重建不重新解析原文件**：片段正文已在 chunks 表里；重新解析会引入切分差异（切分器改过就对不上），还会冲掉用户改过的主题归属。所以只做"拿现有正文重新算向量"。

---

## 16. 笔记业务

### 16.1 `note_service.py` — 三条铁律

模块 docstring 就是契约：

1. **两阶段**：AI 生成是 draft，人工确认才 confirmed；**定稿后一改就退回 draft**（否则"定稿"无意义）。
2. **可溯源**：正文 `[[chunk:id]]` 必须能在 note_sources 找到；编造的 id 一律剔除。
3. **AI 补写留痕**：无来源段落打 `<!--ai-->`。

### 16.2 角标双向校验 `_resolve_cites`

```python
def _resolve_cites(db, markdown) -> tuple[str, list[NoteSource]]:
    # 收集正文里所有 chunk_id（保序去重）
    # 逐个查 chunks 表 → found 字典
    # dropped = 编造的 id → 从正文 sub 掉
    # sources = 真实片段按出现顺序建表（snippet 截 240 字）
```

- **create 和 update（正文变化时）都调用**——不只起草时。这是修过的缺陷：最初只有 AI 起草校验，人手编辑写角标完全不落来源表，前端把角标当死引用划掉。
- 与起草路径的差别：起草时模型只拿到 `allowed` 白名单，编造 id 在 `_assemble` 就被剔除；手工路径没有"给定范围"，以**片段是否真实存在**为准。

### 16.3 AI 补写标记 `_mark_ai_blocks`

按空行分块扫描整篇 Markdown：

- 代码块围栏内的行不参与分块（`in_fence` 状态机）；
- 满足"非标题、非代码、不含角标、未标过、不是表格分隔线"的内容块 → 尾部补 `<!--ai-->`；
- 纯代码块**不标**——代码的出处由它上面的要点承担，否则代码笔记满篇"AI 补写"。

### 16.4 异步起草任务

```python
_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}      # 进程内任务表，_JOB_KEEP=20 防无限增长

def start_draft(...) -> dict      # ① 校验 Key/范围/资料 → 登记任务 → 返回公开快照
async def run_draft(job_id)       # ② BackgroundTasks 执行：LLM 生成 → 清洗 → 落库 → 更新任务
def job_snapshot(job_id)          # ③ 前端轮询这个
```

- **为什么异步**：LLM 生成要几十秒，同步 HTTP 会卡死请求；前端拿到 job_id 轮询，中途切页不影响。
- **为什么 `run_draft` 是 async**：`llm.chat()` 是异步的（httpx.AsyncClient），整条链路 async 才不阻塞事件循环。
- 内部载荷用 `_template` 前划线命名，`_public()` 过滤后才是对外快照——**接口不会把材料全文漏出去**。
- 起草完成走 `create()` 或 `update()`，所以两条铁律（角标剔除、AI 标记）对 AI 内容同样生效。
- 失败时 `_explain` 把 401/429/timeout 翻译成人话（三处 service 各有一份相似实现，是刻意的轻量重复，没抽公共模块）。

### 16.5 `note_prompts.py` — 输出契约

四模板（outline/concept/code/formula）骨架写死在代码里。**为什么写死**：模板是固定骨架不是风格选项——结构稳定，两份笔记才能对比，重新生成不会每次形状不同。

SYSTEM_PROMPT 五条硬规则的核心：资料没有的不许补；必须补的单独成段并标 `<!--ai-->`；角标 id 只能取自给定片段；第一行 `# 标题`。

---

## 17. 问答业务

### 17.1 `chat_prompts.py` — 输出契约

SYSTEM_PROMPT 与笔记共享同一铁律（只依据资料回答），差别在输出形态：直接回答问题，句尾 `[n]` 引用，编号只能取自片段标记，一句话最多两个编号，回答 400 字内。

**设计取舍**（docstring 写明）：问答**落库前不再做剔除校验**——引用错了顶多显示"没有这条来源"，比在流式输出里改写正文安全得多。这与笔记路径（落库前校验）不同，因为笔记是"一份可长期保留的文档"，问答是一次性对话。

### 17.2 `chat_service.py` — 检索与 SSE

**常量即参数**：`TOP_K=5 / MIN_SIMILARITY=0.3 / MAX_CONTEXT_CHARS=12000 / MAX_HISTORY_ROUNDS=5 / SNIPPET_CHARS=240`。

**`retrieve` 的两条过滤路径**：

```python
if document_ids or tag_ids:
    # 标签 → 资料 → 片段白名单（多标签取并集）
    allowed = {chunk_id, ...}

vector = _embed_query(question)
hits = store.search(vector, top_k, topic_id=topic_id, allowed_chunk_ids=allowed)
```

- `topic_id` → 下推到 vec0 partition key，**分区内取 Top-K**；
- `tag_ids/document_ids` → 先查片段白名单，检索时过滤（store 内部 4 倍超采兜底）；
- 同时给定时：先分区检索，再按白名单过滤（交集）。

**相似度阈值**：sqlite-vec 返回 cosine distance，`相似度 = 1 - distance`，阈值 0.3（distance ≤ 0.7）。低于阈值说明问题与库内内容基本无关——**宁可答"没找到"，也不让模型对着无关片段硬答**。

引用与上下文在同一个循环里组装：预算 `MAX_CONTEXT_CHARS` 超限即停；每个片段产出 `[n] 《文档名》第 x 页\n正文` 喂模型 + 一条 citation 给前端。

**`sse_chat` 事件协议**（一次 POST 完成，前端不需要"先检索再提问"两步）：

```
no_context  ← 空库或范围内无相关内容（不调模型）
error       ← 未配 Key / 模型侧错误
sources     ← 引用列表（先发！前端先渲染来源卡片再等正文）
delta       ← 正文增量（带 answer_id）
done        ← 结束
```

**空库判断放在 Key 检查之前**——先导入资料是用户的第一步，提示要指向真正卡住的地方。

**`_trim_history`**：只留最近 5 轮（10 条），且开头必须是 user（OpenAI 兼容端点不强制，但更稳）。

**SSE 格式**：`_event(name, data)` 产出 `event: xxx\ndata: {...}\n\n`，JSON `ensure_ascii=False` 保中文可读。

### 17.3 `api/chat.py` — SSE 接口

```python
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",   # 关掉代理/网关缓冲，否则 delta 攒成一坨才到
}
return StreamingResponse(chat_service.sse_chat(body), media_type="text/event-stream")
```

整个接口 35 行——检索、提示词、流式全在 service，API 层只管协议。

---

## 18. 主题业务

`app/services/topic_service.py` — FR-12 的业务规则：

- 同类内名称唯一（`get_by_name` 查重 → 409）；「未分类」不可改名/删除（`TopicProtected`）。
- 新主题颜色从 8 色调色板按"现有数量取模"轮转分配。
- **删主题**：资料逐个 `move_document_topic` 迁到未分类（联动向量分区），笔记 `reassign_notes` 批量迁，标签只解关联。返回 `moved_documents` 让前端提示"已迁移 N 份资料"。
- `seed_packs` 直接透传给 seed.py 的 `apply_topic_packs`。

---

## 19. API 层

全部路由在 `api/router.py` 聚合，挂在 `/api` 前缀下。当前端点全景：

| 路由 | 方法 | 作用 |
|---|---|---|
| `/health` | GET | 健康检查：服务、数据库、LLM 是否配好 |
| `/overview` | GET | 首页聚合数据（stats_repo） |
| `/topics` | GET/POST | 主题标签列表 / 新建 |
| `/topics/{id}` | PATCH/DELETE | 改 / 删（删返回迁移数） |
| `/topics/packs` | GET/POST | 预置方向包 |
| `/documents` | GET | 列表（topic/tag/status/keyword 过滤） |
| `/documents/stats` | GET | 四态计数 + 片段总数 |
| `/documents/upload` | POST | 多文件上传（multipart，单个失败不中断整批） |
| `/documents/webfetch` | POST | 网页抓取预览（不入库） |
| `/documents/paste` | POST | 粘贴文本入库（网页确认后也走这） |
| `/documents/batch` | POST | 批量设主题/加/去标签 |
| `/documents/{id}/chunks` | GET | 片段列表（编辑器来源抽屉用） |
| `/documents/{id}/reingest` | POST | 重新处理（幂等） |
| `/documents/{id}` | DELETE | 删资料（向量→片段→行→文件） |
| `/notes` | GET/POST | 列表（FTS 相关度排序）/ 新建（角标校验） |
| `/notes/templates` | GET | 四模板元数据 |
| `/notes/draft` | POST→202 | 登记起草任务（BackgroundTasks） |
| `/notes/drafts/{job_id}` | GET | 轮询任务状态 |
| `/notes/{id}` | GET/PATCH/DELETE | 详情 / 更新（正文变→重校验角标）/ 删 |
| `/notes/{id}/confirm` | POST | 确认定稿 |
| `/notes/{id}/regenerate` | POST | 重新起草（必须 `confirm=true` 显式确认覆盖） |
| `/notes/{id}/export` | GET | 下载 Markdown（角标转 [n] + 来源附录） |
| `/notes/{id}/sources/{chunk_id}` | GET | 角标二次点击取完整原文 |
| `/chat` | POST | SSE 问答流 |
| `/settings` | GET/PUT | 配置读取（密钥打码）/ 保存（写 .env + 即时生效） |
| `/settings/index` | GET | 索引状态对照 |
| `/settings/index/rebuild` | POST→202 | 重建索引 |
| `/settings/test` | POST | LLM / Embedding / SMTP 连通性测试 |
| `/practice`, `/reviews`, `/schedule` | GET | M4/M5 占位，返回空 |

**错误映射约定**（全项目一致）：

| service 异常 | HTTP |
|---|---|
| `NoteNotFound` / 404 类 LookupError | 404 |
| `NoteError` / `IngestError` / `WebImportError` / `TopicConflict`(409) / `ConfigWriteError` | 400/409 |
| `RebuildError`（已有任务在跑） | 409 |

service 层抛的异常文案**直接进 UI**，所以全是完整人话句子，不带堆栈。

**两个接口级细节**：

1. `export_note` 的 Content-Disposition 双文件名：

```python
f"attachment; filename=\"note-{note_id[:8]}.md\"; filename*=UTF-8''{quote(filename)}"
```

`filename` 是 ASCII 兜底（老浏览器），`filename*` 才是 RFC 5987 的 UTF-8 中文名（中文文件名正确显示的关键）。

2. `_export_markdown`（api/notes.py）：角标 `[[chunk:id]]` → `[序号]`（按来源表顺序，正文有而来源表没有的兜底剔除）；正文没有一级标题就补 `# 标题`；文末追加 `## 来源` 清单；文件名做非法字符清洗。注意 `_MARK` 在这个文件里是**带捕获组**版本（导出要取 id），而 service 里那份只做整匹配删除——同名正则两处定义，审查时容易踩。

**列表摘要 `_excerpt`**（api/notes.py，47 行函数）：三级处理——剔结构（代码块/图片/标题/表格/分隔线）、保内容（公式留正文去 `$` 符号、列表项间插 `·`）、**窗口跟随**（有 keyword 时若命中位置超过前 1/3，窗口挪到命中处前 36 字符——否则命中在第三段用户只看到一屏无关内容）。

---

## 20. 统计投影

`app/repositories/stats_repo.py` — 只读 read model，概览页一次请求拿全：

- `summary()`：documents/practice/wrong/review/notes 五组计数 + 近 7 天增量。
- `_daily(7)`：近 7 天每日活跃度。**刻意在 Python 侧按天分桶**而不是用 SQL 日期函数——SQLite 与 PostgreSQL 的日期截断方言不同，Python 保证未来切库行为一致。
- `_activity(6)`：三类事件（导入/练习/报告）合成时间线倒序。
- `_iso()`：**给 naive UTC 补上时区再序列化**。裸 naive ISO 会被浏览器当本地时间解析，"刚导入的资料显示 8 小时前"就是这么来的。

---

## 21. 横切关注点

### 21.1 错误处理模式

```
infrastructure 抛 XxxError(ValueError)   ← 文案即 UI 文案
service 捕获/包装 → 抛业务异常（NoteError / IngestError…）
API 层 except → HTTPException(status, detail=str(exc))
```

三层各司其职：基础设施不认识 HTTP，service 不构造 Response，API 不写业务规则。所有面向用户的异常都继承 ValueError 且文案完整可读——**审查时任何 `raise HTTPException(500)` 或裸字符串 detail 都值得警惕**。

### 21.2 时间处理

- 生成时间：`datetime.now(timezone.utc)`（aware）。
- 入 SQLite：`.replace(tzinfo=None)` 转朴素 UTC（SQLite DateTime 列不存时区）。
- 读出序列化：`_iso()` 补回 UTC 时区。
- 按天分桶：`astimezone()` 转本地时区取日期键。

三步不能混：aware 写进 SQLite 会报错，naive 直接给前端会错 8 小时。

### 21.3 线程与异步的边界

| 场景 | 做法 |
|---|---|
| 同步端点（绝大多数 CRUD） | FastAPI 自动跑线程池，`Depends(get_db)` 每请求一个 Session |
| BackgroundTasks 的同步函数 | 跑在线程池；**自开 SessionLocal**，不碰请求 Session |
| BackgroundTasks 的 async 函数（run_draft） | 跑在事件循环；async 链路调 async LLM |
| SSE（sse_chat） | async generator；**自开 Session 并在 finally 关**——响应可能比请求活得久 |
| 进程内任务表（_JOBS/_JOB） | `threading.Lock` 保护；重启即丢（v0.1 已知限制） |

### 21.4 安全边界（单机个人应用的合理范围）

- CORS 白名单只放 Vite 端口。
- 密钥：API 响应永远打码；打码值回传被忽略；`__CLEAR__` 哨兵显式清除。
- SQL：所有动态表名/列名是**代码内常量**（非用户输入）；查询参数全部绑定变量。
- 网页抓取：协议白名单、大小/超时上限、userinfo 拒绝。
- 未做：认证鉴权（单用户 local）、速率限制、CSRF（无 cookie 会话）。多用户化时这些是第一优先级。

---

## 22. 测试脚本与迁移

### 22.1 冒烟脚本（`scripts/`）

| 脚本 | 覆盖 |
|---|---|
| `smoke_m2.py` | 资料入库全链路：上传/粘贴/解析/切分/向量化/检索/重跑/删除 |
| `smoke_m25.py` | 笔记 46 项断言：CRUD、角标双向校验（含手写路径）、起草任务、FTS 搜索（含中文单字）、摘要窗口跟随 |
| `smoke_m3.py` | 问答 25 项断言：**假 LLM 注入**跑通 SSE 全链路，检索/向量库走真实 BGE + sqlite-vec；`--real` 追加真实流式验证 |
| `smoke_m4.py` | 出题 61 项：范围三入口、题型配比、来源校验、材料阈值 |
| `smoke_m4b.py` | 作答判分与错题本 84 项：14 条判分纯函数断言、断点续答、错题去重累加、重练 |
| `smoke_m5.py` | 回顾报告 50 项：空库提示性报告、时间窗过滤、数字与库一致、模型降级、邮件失败不丢报告、调度启停 |
| `smoke_m6.py` | 边界 43 项：坏文件/不支持格式/超限、主题重名与删除迁移、错误密钥与断网、越界请求、笔记在资料删除后仍可打开 |

冒烟的共同模式：`check(name, condition, detail)` 收集断言而不是 assert 短路——一次跑完看到全部结果；用临时数据库目录，不碰 `data/app.db`。**假 LLM 注入**是在 TestClient 上 override 依赖/monkeypatch `build_llm`，让 SSE 协议、引用组装、事件顺序这些"协议正确性"与"供应商可用性"解耦。

### 22.2 迁移（`migrations/`）

单版本 `9fdd17ddd198_initial_schema.py` 建 10 张表。虚表（vec_chunks / notes_fts）不在迁移里——它们由 session.py 启动时 ensure（虚表 DDL 无法用 Alembic 的 op API 表达，且维度/可用性都是运行时属性）。

### 22.3 运行

```bash
cd backend
.venv/Scripts/python.exe scripts/smoke_m2.py
.venv/Scripts/python.exe scripts/smoke_m25.py
.venv/Scripts/python.exe scripts/smoke_m3.py          # 假 LLM
.venv/Scripts/python.exe scripts/smoke_m3.py --real   # 真实流式（需已配 Key）
```

---

## 23. 练习出题（M4）

### 23.1 它是什么

`practice_service.py` 负责组卷：确定范围 → 取片段 → 分题型让模型出题 → 校验来源 → 存草稿。
`infrastructure/generators/` 是出题器：`base.py` 定义端口与三个数据结构（异步），
`llm_generator.py` 里一个 `LLMTypedGenerator` 按题型出题（题型是构造参数，
四种题型共用同一套 JSON 解析与去重），`parsing.py` 负责把不可控的模型输出洗成可入库的题目。

### 23.2 关键实现

**范围解析（`_resolve_documents`）**：主题、资料 ID、近 N 天三条约束依次过滤，最后只留
`status=ready` 且 `chunk_count>0` 的资料。没有「空范围=全库」的默认值——出题是最贵的操作，
不该因为漏传参数就把整个库跑一遍。

**取材（`_build_context`）**：每份资料等距抽 8 段（`_sample`），再按「第 1 段轮完再取第 2 段」
轮转合并，最多 60 段 / 18000 字。顺序拼接会让排在前面的大文档吃掉全部配额，轮转保证每份资料
都进入题目。

**材料下限**：`total * 100` 字。低于阈值直接 400 并给出具体数字（现有字数 / 需要字数）。
这是把 PRD「50 字资料出 30 题」的验收标准写成可执行规则——比让模型「尽量少出」可靠。

**四题型并发**：每种题型一个出题器，`asyncio.gather(..., return_exceptions=True)` 并发调用。
单个题型失败只产生一条 warning，其余题型照常入库；某题型少出几道也写进 warning，
前端据此提示「材料只支持 N 道」。

**来源校验（`parsing.normalize_question`）**：模型必须回报 `source_chunk_ids`，服务端对照
本次取材的白名单逐个映射；一个都不匹配的题整题丢弃，编造的编号也被剔除。这是防幻觉的硬闸：
模型可以胡写题干（人能在草稿里看出来），但编造的出处一旦进入错题本就没法追溯。

**输出清洗**：去代码块围栏、从混合文本里抠 JSON、答案归一（选项前缀剥离、"A、B、D" → "ABD"、
正确/错误同义词）、题干去重（去标点后比对）、解析缺失时回退为材料原文、剥离「解析：」前缀。

**容错重试**：JSON 解析失败时追加一条修复指令 + 上次输出重发一次，温度降到 0.1；仍失败则抛
`GeneratorError`，由任务状态转成人话。不重发材料，省 token 也避免模型换思路。

**持久化**：题目整体序列化为 JSON 快照存 `practice_sessions.questions`（含来源、主题、知识点），
组卷参数存新增的 `meta` 列。资料日后被删，历史练习仍能完整回看。

### 23.3 为什么这么写

- **为什么不用一次请求出全部题型**：混在一起时数量配比只能靠模型自觉，而且没法回答
  「哪种题型资料不足」。拆开后配比是代码保证的，failure 也能定位到题型。
- **为什么后台任务 + 轮询**：与笔记起草同构。30 道题的真实耗时在十几到几十秒，
  长 HTTP 请求会被浏览器/代理掐断，也让用户以为页面卡死。
- **为什么题目快照不引用题库表**：题目是一次性产物，没有「同一道题被多场练习复用」的需求
  （错题重练直接用错题里的快照）。存 JSON 省掉三张关联表，也让历史记录不可变。

### 23.4 审查时看什么

1. `sources` 是否全部来自白名单；`normalize_question` 有没有「无来源也放行」的分支。
2. 数量是否可能超过 `MAX_QUESTIONS`；`plan_counts` 与 Pydantic 校验是两道独立闸。
3. 轮询任务失败后 job 是否会永远停在 running（前端 catch 里清 job）。
4. 删题是否只在 `DRAFT` 状态允许（已提交的练习是成绩的一部分）。
5. 新增迁移 `5c1f7a9d2b04` 是否与 `models.py` 的 `meta` 列一致。

---

## 24. 作答、判分与错题本（M4）

### 24.1 它是什么

三块合起来才构成「练习」闭环：

- `services/grading.py`：纯函数判分。输入题目与作答，输出 `GradeResult`（是否作答、是否正确、归一化后的答案）。
- `services/practice_service.py` 的三段：`start_session`（草稿 → 作答中）、`save_answer`（单题自动保存）、
  `submit_session`（判分 + 落库 + 写错题）。
- `SqlAlchemyWrongAnswerRepository`：错题收敛与查询；`start_retry` 用错题快照直接组卷。

### 24.2 关键实现

**归一化（`grading.normalize`）**：单选只取第一个字母（`"A、"` → `"A"`）；多选提取全部字母并排序去重
（`"B、A"` → `"AB"`）；判断题把 `对/是/true/T/1/√` 统一成 `正确`，`错/否/false/F/0/×` 统一成 `错误`。
简答只折叠空白。

**判分口径（`grading.grade`）**：

| 题型 | 判定 |
|---|---|
| 单选 | 归一化后严格相等 |
| 多选 | **集合完全一致**（少选、多选都不得分） |
| 判断 | 归一化为统一标签后严格相等 |
| 简答 | 不看文本，只看 `self_grade`（`mastered` 得分） |

`answered` 与 `is_correct` 是**两个独立字段**：未作答时 `answered=False`、`is_correct=False`，
错题收录只认 `answered and not is_correct`。没做过不等于做错。

**自动保存（`save_answer`）**：写 `practice_sessions.answers`（JSON 数组），同题覆盖不新增；
传空 `response` 等于撤销作答（记录被删掉）。首次写入时顺带把状态从 `DRAFT` 推到 `IN_PROGRESS`
并记 `meta.answer_started_at`——「开始作答」这个动作可以只体现在第一次点选项上。

**提交（`submit_session`）**：以本次提交的作答为准，**提交里缺失但之前自动保存过的沿用旧记录**。
前端发全量时这条不起作用；它兜的是「答到一半关页面、重开只补两题就交卷」的情况——
不合并的话前面答对的题会被判成未答。

**用时**：优先 `finished_at - meta.answer_started_at`；缺失（旧数据、异常）时退化为逐题耗时求和。
不用 `started_at`，因为它是草稿生成时间，可能比作答早几小时。

**错题收敛（`upsert`）**：去重键是 `question_id`，命中则 `wrong_count += 1` + 更新
`last_wrong_at` / `last_response` / 题目快照；未命中才插入。条目主键 `id` 默认等于 `question.id`。

**重练（`start_retry`）**：把错题快照直接组成一场 `IN_PROGRESS` 的练习，**不调模型**。
题目 id 保持不变，所以重练答错能正确累加到同一条错题上。

**主题聚合（`wrong_summary`）**：错误率的分母不是错题条数，而是从已提交练习里回扫出的真实作答数：
遍历 `session.answers`，按 `question.topic_id` 累计 `answered` / `correct`。
只给错题条数会骗人——只练过一个主题的人，那个主题必然排第一。

### 24.3 为什么这么写

- **判分为什么单独成文件**：「多选是宽松还是严格」这个口径散落在接口层就只能靠读代码得知。集中后它是
  可单测、可审查、可替换的纯函数，`smoke_m4b.py` 前半段直接对它做 14 条断言。
- **为什么未作答不算错题**：错题本的使用场景是「我错过，要重点看」。把没做的题塞进去，
  一次没答完的练习就能让错题本翻倍，越用越厚——而错题本的价值恰恰在于变薄。
- **为什么不用 LLM 判简答**：v0.1 没有可靠的评分基准；用模型判模型出的题，等于让同一个模型自己给自己打分。
  自评至少把判断权交回给用户，且错误会体现在「自己标了未掌握」上。
- **为什么重练不重新出题**：重练的目的是验证「同一道题现在会不会」。重新生成只会换一种问法，
  表面做对可能只是换了个记忆线索。复用快照才能让 `wrong_count` 有可比性。
- **为什么跨卷子不去重**：重新生成的近似题无法可靠判定是否「同一道题」。用文本相似度猜会误合并，
  让错误次数失真。宁可边界清楚：一个卷子里的一题算一条。

### 24.4 审查时看什么

1. `grade` 里 `answered` 与 `is_correct` 是否仍然分开；有没有把「空作答」当成答错写进错题的分支。
2. `submit_session` 的合并顺序：提交内容 > 自动保存 > 视为未答。顺序错了会让续答的题丢分。
3. 已提交的会话是否真的锁死：`save_answer` / `submit` / `delete_question` 三处都要拦 `SUBMITTED`。
4. `upsert` 是否按 `question_id` 查而不是按 `id`；重练卷里的题目 id 必须与原题一致。
5. `wrong_summary` 的错误率分母是否来自 `answers`，而不是 `wrong_count` 之和。
6. 迁移 `c4d8e1a63b52`（`wrong_answers.last_response`）与 `models.py` 是否一致；旧行默认空串。

---

## 25. 回顾报告与推送（M5）

### 25.1 模块地图

| 文件 | 职责 |
|---|---|
| `repositories/review_repo.py` | 时间窗聚合（`collect_window`）+ 报告持久化（`SqlAlchemyReviewRepository`） |
| `services/review_prompts.py` | prompt 与模型输出的 JSON 解析 |
| `services/review_service.py` | 编排：算数 → 写文字 → 拼 Markdown → 落库；推送状态回写 |
| `api/review.py` | 6 个端点：列表 / 详情 / 生成 / 删除 / 推送 / 推送状态 |
| `infrastructure/scheduler.py` | APScheduler 装载与热重载 |
| `infrastructure/mail/smtp_notifier.py` | 注册到 `NOTIFIER_REGISTRY` 的 SMTP 实现 |

### 25.2 核心分工：数字归程序，文字归模型

报告正文有五个部分，两类内容严格分开：

```
新增资料（程序算：份数/片段数/文件名）
练习情况（程序算：套数/题量/正确率/按主题按题型）
错题整理（程序算：条数/次数/高频错题的题干与答案）
薄弱点分析（模型写）
复习建议（模型写）
```

**为什么不让模型写全部**：验收要求「报告中的错题与错题本数据一致」。只要数字经过模型，它就有机会改写、四舍五入、漏掉一条——而一份数字对不上的漂亮报告毫无价值。反过来，模型擅长的恰恰是程序写不出的部分：判断「这个错题分布说明什么」。

实现上，`_section_practice` / `_section_wrong` 用 `metrics` 直接拼 Markdown 表格；`_analyze` 只请求 `{topic_summaries, weak_points, suggestions}` 三段文字。

### 25.3 降级三层

任何一层失败都要能出报告：

| 情况 | 处理 |
|---|---|
| 未配置 LLM Key | 直接走 `_rule_based_analysis`，`analysis_source="rule"` |
| 模型调用抛异常（401 / 超时 / 网络） | 同一降级，异常只进日志 |
| 返回的 JSON 缺字段 / 不是 JSON | 逐字段回退到规则版，`parse_analysis` 返回空列表而非抛错 |

规则版不是敷衍的占位：它同样看正确率最低的主题、错得最多的主题、本期新增但没练过的资料，措辞只是朴素一些。正文末尾会加一行「本期分析由规则生成（模型未配置或调用失败），数字部分不受影响」——**用户有权知道这段文字是谁写的**。

`metrics.analysis_source` 同时给前端做徽标（模型分析 / 规则分析 / 数据不足）。

### 25.4 时间窗口径

`collect_window(db, window_days)` 返回 `(start, end, metrics)`：

- `start = utcnow_naive() - timedelta(days=window_days)`，`end = utcnow_naive()`；
- 资料按 `documents.created_at` 过滤，按 `topic_id` 分组；主题被删的归到 `uncategorized`；
- 练习只算 `status == submitted` 且 `finished_at` 落在窗口内；
- 正确率分母是 **answers 条数**，不是 `questions` 长度——跳过的题不该拉低正确率（与 §24 同一口径）；
- 错题复用 `wrong_summary(db, days=window_days)`，不另写一套，避免两处算出不同错误率。

**metrics 是快照**：生成时整份写进 `review_reports.metrics`。之后删资料、删练习、删主题，历史报告都不会变空——回顾的价值是「当时的判断」。

### 25.5 调度器

`BackgroundScheduler`（独立线程）而不是 `AsyncIOScheduler`：生成报告要等模型 10~30 秒，放在事件循环里会把同一时刻的其它请求一起卡住。独立线程里 `asyncio.run(...)` 是安全的。

- `start()` 在 lifespan 里调用，`shutdown()` 在 yield 之后；
- `_sync_job()` 按 `scheduler_enabled` 注册或移除任务，`settings.update` 保存后调用 `reload()` 热生效；
- `coalesce=True`：睡眠唤醒后错过多次只跑一次，不连着补发；
- `max_instances=1`：上一轮没跑完不叠加；
- 任务体整体包 `try/except`，异常只记日志——抛出去会被 APScheduler 静默吞掉，反而更难查。

**不做补发**：v0.1 明确放弃（PRD §8 变更记录）。启动时补一份三天前的报告会让人误判时间。

### 25.6 邮件

`SmtpNotifier.send_report()` 的失败语义是**返回值**而不是异常：

```python
result = build_notifier().send_report(report)   # NotifyResult(ok, detail)
review_service.mark_push(db, report, result.ok, result.detail)
```

失败原因写进 `metrics.push`，界面读 `/reviews/push-state` 显示「上次推送失败：xxx」。**报告一定保留**。

正文用内联样式的自包含 HTML：邮件客户端普遍会剥离 `<style>` 与外链 CSS，写 class 在最需要好看的场景里反而全丢。正文只放四个关键数字 + 错题 TOP5 + 一个跳转按钮，不整份 Markdown——邮件是提醒，不是阅读器。

`build_notifier(values)` 支持临时覆盖，供设置页「发送测试邮件」用未保存的配置试发。

### 25.7 审查点

1. `_section_practice` / `_section_wrong` 的数字是否全部来自 `metrics`，有没有手写死值或再算一遍。
2. `_analyze` 的异常分支是否真的兜住了（`except Exception`），以及 `source` 是否正确标记为 `rule`。
3. `collect_window` 里练习的过滤条件是否 `submitted`（不是 `finished`——历史 bug，见 §20）。
4. `scheduler.reload()` 是否在 `PUT /settings` 里被调用；`scheduler_enabled` 为 false 时是否真的移除了 job。
5. `smtp_notifier._send` 的收件人切分是否过滤空串（`a@x.com,` 这种尾逗号）。
6. `api/review.py` 里 `/push-state` 是否注册在 `/{report_id}` **之前**（否则被当成 id）。
7. `mark_push` 用的是 `metrics` 副本还是原地改（前者才能保证 SQLAlchemy 认为 JSON 变了）。

---

## 26. 审查清单

按重要性排序的审查切入点（每条都对应代码里的具体位置）：

1. **联动一致性**：资料改主题涉及 documents/chunks/vec_chunks 三处（§14.4）；笔记正文与 note_sources 的双向一致（§16.2）；笔记行与 notes_fts 同事务（§6）。任何绕过 service 直接操作仓储的新代码都可能破坏这些不变量。
2. **Session 生命周期**：所有后台任务/SSE 必须自开自关 Session（§21.3）。请求 Session 跨线程是本项目明令禁止的模式。
3. **维度以实测为准**：索引维度看 `sqlite_master`（§8.2），embedding 维度用探针回写（§15）。配置值只是意图，不是事实。
4. **"写后回读"**：service 的 create/update 返回值必须来自 `repo.get()`（§13.4）——直接返回内存对象会丢 join 出来的字段。
5. **密钥路径**：任何新接口输出配置都要过 `_mask`；任何写入都要过 `_normalize` 的打码过滤（§3）。
6. **幂等性**：入库先清后写（§14.3）、标签来源先删后插（§13.3）、FTS 先删后插（§6）。重复请求/重跑应当安全。
7. **错误文案**：面向用户的异常必须是人话句子；供应商原始报错经 `_explain` 翻译，不裸透堆栈（§16.4/§17.2）。
8. **已知限制**（v0.1 有意为之）：任务状态进程内不持久化；单用户；OCR 不支持；FTS5/sqlite-vec 不可用时静默降级而非报错。

---

*本文与代码同步维护：改了某个模块的机制，请同步更新对应小节。设计动机层面的问题看 `BACKEND.md`。*
