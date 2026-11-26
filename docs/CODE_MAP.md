# 代码地图

> 这份文档不讲设计理念，只回答两个问题：**一个请求是怎么流动的**、**我想改某处该打开哪个文件**。
> 配套：`BACKEND_CODE_GUIDE.md` 是逐模块详解（较长），本文是先看的那张地图。
> 想知道「为什么这里没有用 Agent 而那里该用」，看 `AGENT_DECISION.md`。
> 想知道「为什么这里没有用 Agent 而那里该用」，看 `AGENT_DECISION.md`。

---

## 1. 目录职责

后端只有一个服务进程，按「职责」分目录，不按「功能」分。这样每个目录只有一件事：

| 位置 | 行数 | 管什么 | 不该出现什么 |
|---|---|---|---|
| `app/api/` | ~1760 | **收请求、发响应**。解析参数、调服务、把结果转成 Pydantic 模型 | 不能写业务规则、不能直接查数据库 |
| `app/services/` | ~3900 | **业务规则**。什么算完成、怎么组卷、怎么判分、报告怎么聚合、计划怎么排 | 不能出现 `Request` / `HTTPException` |
| `app/schemas/` | ~620 | **接口出入参**（Pydantic），前后端的契约 | 不能写业务逻辑 |
| `app/store.py` | ~580 | **存取数据**。一个实体一个类（Topics/Documents/Chunks/Notes/PracticeSessions/WrongAnswers/Plans） | 不能写业务判断 |
| `app/models.py` | ~600 | **全部表结构 + 枚举 + JSON 快照**。一张表一个类 | 不能写业务流程 |
| `app/stats.py` / `app/reviews.py` | ~270 / ~280 | **跨表只读聚合**（概览统计、回顾时间窗） | 不能写业务流程 |
| `app/llm.py` | ~280 | 大模型与 Embedding：端口 + 实现 + 工厂 | 不能写业务判断 |
| `app/parsers.py` / `app/splitter.py` / `app/vectorstore.py` / `app/fts.py` | ~150 / 140 / 220 / 160 | 解析、切分、向量表、全文检索 | 不能写业务流程 |
| `app/db.py` | ~110 | 引擎、会话、迁移、向量库单例 | 不能写业务 |
| `app/main.py` / `app/config.py` | ~60 / 100 | 启动编排、读 `.env` | 不能写业务 |

一句话记法：**api 收发 → service 决策 → store 存取 → 各能力模块干活。**

> v0.2 把原来的 `domain/` + `repositories/` + `infrastructure/` 三层压平了：以前一个实体
> 要写 ORM 类、dataclass、以及两者之间的 `_to_xxx()` 转换，加一个字段改五处；现在
> `models.py` 里一张表就一个类，查出来直接能用。分层的收益是"隔离变化"，但个人项目
> 只有一个存储、一个用户、一套部署，没有需要隔离的变化。

---

## 2. 一个请求的完整路径

以「生成一套练习」为例，从点按钮到出题完成：

```
前端 POST /api/practice/generate
    ↓
app/api/practice.py: generate()              ← 只做参数校验，立刻返回 202 + job_id
    ↓ 交给后台任务，不让请求等着模型
app/services/practice_service.py: start_generation()
    ↓
practice_service.py: run_generation()        ← 真正的编排：取材 → 出题 → 存库
    ├─ _resolve_documents()                  ← 按主题/资料/天数取交集
    ├─ _build_context()                      ← 抽样 60 段，拼成带 [chunk:id] 的正文
    ├─ generate_questions(题型, …)            ← 四种题型并发跑
    │      ├─ practice_prompts.build_messages()   ← 拼 prompt
    │      ├─ build_llm().chat()                  ← app/llm.py 真发请求
    │      └─ normalize_question()                ← 抠 JSON、校验来源、去重
    └─ PracticeSessions.save()   ← 存成 JSON 快照
    ↓
前端每秒轮询 GET /api/practice/jobs/{id}
```

**为什么出题要异步**：一次出题要 10～30 秒，同步会卡住浏览器。所以接口先返回
`job_id`，前端轮询进度。这是全项目唯一用后台任务的地方。

再看一个更简单的「提交答卷」：

```
POST /api/practice/sessions/{id}/submit
    ↓
app/api/practice.py: submit()
    ↓
practice_service.py: submit_session()
    ├─ grading.grade()          ← 纯函数：判断对错，不碰数据库
    ├─ grading.summarize()      ← 纯函数：算分
    └─ 答错 → WrongAnswers.upsert()
```

`grading.py`（132 行）是**纯函数**，输入题目和答案、输出对错，没有副作用。
想搞懂判分口径，只读这一个文件就够，不用管数据库。

---

## 3. 为什么是三层，而不是一层

单人项目确实可以 `api` 里直接写 SQL，代码能少一半。这里没这么做，是因为两个功能
逼出了分层：

1. **出题要用后台任务**。后台任务拿不到请求里的数据库会话，必须能自己建会话、
   自己调服务。业务逻辑如果写在 `api` 里，后台任务就没法复用。所以业务必须
   沉到 `service`，`api` 和后台任务都能调它。
2. **数据要在两处复用**。错题本要读练习记录，回顾报告也要读练习记录。如果查询
   写在 `api` 里，这两个接口就得各写一遍。放进 `repository`，谁都能用。

所以分层不是为了"看起来专业"，是**有第二个调用方**才拆出来的。

---

## 4. 「我想改某处，该动哪里」

| 我想… | 打开 | 说明 |
|---|---|---|
| 改出题的 prompt | `services/practice_prompts.py` | 四种题型的规则都在这里 |
| 改判分口径（比如多选漏选算不算错） | `services/grading.py` | 纯函数，改完跑 `smoke_m4b.py` |
| 改报告里写什么 | `services/review_service.py` + `review_prompts.py` | 数字由程序拼，文字由模型写 |
| 新增一种文件格式 | `app/parsers.py` | 写个类、加进末尾的 `PARSERS` |
| 换 LLM 供应商 | 设置页，或 `app/llm.py` 的 `LLM_PRESETS` | 都走 OpenAI 兼容协议 |
| 改某个接口的出入参 | `schemas/` + 对应 `api/*.py` | schema 是前后端的契约 |
| 加一张表 | `app/models.py` + 写一个迁移 | 迁移在 `migrations/versions/` |
| 加一个字段 | `app/models.py` | 只有一处：一张表一个类，没有第二份副本要同步 |
| 改某条查询 / 排序 | `app/store.py` 对应实体的类 | 跨表聚合在 `stats.py` / `reviews.py` |
| 改业务规则（比如未答算不算错） | 对应 `services/*.py` | 不要改 `api`，那里只做转发 |
| 改前端调用的字段名 | 先改 `schemas/`，再改 `frontend/src/api/client.ts` | 两边要一致 |
| 改计划 Agent 能查什么 | `services/plan_tools.py` 的 `TOOLS` | 加一个函数 + 登记即可，循环不用改 |
| 改计划 Agent 的提示词（几天、写什么） | `services/plan_prompts.py` | 工具说明书与计划结构都在这里 |
| 改 Agent 的步数上限 / 防打转 | `services/plan_agent.py` 的 `MAX_STEPS`、`MAX_SAME_TOOL_CALLS` | 循环本体 30 行，别往这里加业务 |
| 看清「它为什么这么排」 | 计划详情里的 `meta.steps` | 每步的工具名、入参、结果摘要都在里面 |

---

## 5. 建议的阅读顺序

如果只是想弄懂这个项目在干什么，按这个顺序读，大约 1 小时：

1. `app/main.py`（74 行）—— 服务怎么起来
2. `app/models.py`（600 行）—— 数据长什么样：表结构、枚举、JSON 快照都在这里
3. `app/services/grading.py`（132 行）—— 最干净的纯逻辑，看判分怎么算
4. `app/api/practice.py`（331 行）—— 看接口如何只做转发
5. `app/services/practice_service.py`（751 行）—— 最核心的业务编排，挑 `run_generation` 和 `submit_session` 两个函数读
6. `app/store.py`（580 行）—— 看存取数据有多直白：一个实体一个类，方法就是那条 SQL

读完这 6 个文件，其余的都是同一套模式的重复。

**不要从 `llm.py` / `vectorstore.py` / `parsers.py` 开始读**，那里是技术细节（HTTP、PDF 解析、向量检索），
跟业务主线无关，需要时再看。

---

## 6. 表结构只有一份，JSON 快照另算

`app/models.py` 里一张表就一个类。以前这里是两份（ORM 一份、dataclass 一份），
中间靠 `_to_xxx()` 逐字段搬运——**加一个字段要改五处**（两边声明 + 转换 + add + update）。
v0.2 合并了。

但有一类东西仍然不对应表，而且不该对应表：

`Question` / `SourceRef` / `AnswerRecord` 被序列化成 JSON，存进
`practice_sessions.questions` 与 `wrong_answers.question` 两列。资料删了之后，
历史练习和错题仍然完整可读、可重练，靠的就是这份快照。它们和表放在 `models.py`
同一个文件里，但文件开头明确写了哪几个是表、哪几个是快照。

> 当初分成两份的理由之一是"ORM 对象绑定在会话上，会话一关就取不到属性"。
> 这个项目里不成立：SSE 接口（问答、计划）自己开 `SessionLocal()`，数据在生成器
> 生命周期内用完；`sessionmaker` 设了 `expire_on_commit=False`；现有代码也从不把
> ORM 对象传出会话。所以不再为它单独保留一层。

投影字段（`Document.tag_ids`、`Topic.count`、`Chunk.source_name`）不是列，
查询时由 `store.py` 回填。用 property 而不是 relationship，是因为它们存的是 id
列表或者 join 出来的名字，走 relationship 要先把 id 查成对象，反而绕。

---

## 7. v0.1 收尾时做的简化

为了让代码更好读，最后一轮做了这些减法（功能行为完全不变，331 项冒烟测试全通过）：

| 改动 | 原因 |
|---|---|
| 删除 `repositories/base.py`（158 行 Protocol） | 全项目零引用，纯粹是声明式噪音 |
| 删除 `registry.py` + `bootstrap.py`（90 行插件注册表） | 不做插件，装饰器注册让"选哪个模型"多绕三层 |
| 合并 4 个出题器子类为 1 个类 + 题型参数 | 4 个子类各只有 2 行，只为传一个枚举 |
| 删除 `parsers/dummy.py` | 早期验证注册表用的，已无引用 |
| 删除前端「已注册扩展点」卡片 | 那是插件机制的展示，没有实际用途 |
| `sqlalchemy_repo.py` 831 行拆成 6 个文件 | 按实体拆，每个 60～190 行，找方法不用滚屏 |
| 通知器的抽象基类并入实现文件 | 只有一个实现，单独开文件只是多一次跳转 |

### v0.2：后端压平

| 改动 | 原因 |
|---|---|
| `domain/models/entities.py` 与 `infrastructure/db/models.py` 合并为 `app/models.py` | 一个实体两份定义、加字段改五处；业务规则并没有真的住在 domain 层（278 行里只有 3 个方法） |
| `repositories/` 12 个文件合并为 `app/store.py` 的 7 个类 | 类名去掉 `SqlAlchemy` 前缀——只有一个实现，不必在名字里声明 |
| `infrastructure/` 全部拆到 `app/` 顶层 | 目录层级从 5 层降到 2 层，找文件少跳两次 |
| `llm/` 三个文件合并为 `app/llm.py`、`parsers/` 合并为 `app/parsers.py` | 端口与实现在一个文件里读完 |
| `generators/` 三文件合并为 `app/practice_generate.py`，`LLMTypedGenerator` 改成 `generate_questions()` | 只有一个实现，类的壳没有换来任何东西 |
| 删除 `sqlalchemy_repo.py`（纯转发）与 `_converters.py`（已并入 models） | 39 行只做 `from ... import` |

规模从 85 个文件 / 10861 行变成 59 个文件 / 10492 行；行数只少 3%，但**改一个字段的
成本从 5 处降到 1 处**，目录从 5 层降到 2 层。380 项冒烟测试全部通过。

**刻意保留的**：`parsers/base.py`（4 个解析器共享契约）、`llm/base.py`（3 个文件
共用）、`generators/base.py`（`parsing.py` 依赖它，合并会循环导入）、`schemas/`
的 9 个文件（每个 30～180 行，比合成一个 600 行的大文件好定位）。
