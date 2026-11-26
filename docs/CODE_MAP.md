# 代码地图

> 这份文档不讲设计理念，只回答两个问题：**一个请求是怎么流动的**、**我想改某处该打开哪个文件**。
> 配套：`BACKEND_CODE_GUIDE.md` 是逐模块详解（较长），本文是先看的那张地图。
> 想知道「为什么这里没有用 Agent 而那里该用」，看 `AGENT_DECISION.md`。
> 想知道「为什么这里没有用 Agent 而那里该用」，看 `AGENT_DECISION.md`。

---

## 1. 目录职责

后端只有一个服务进程，按「职责」分目录，不按「功能」分。这样每个目录只有一件事：

| 目录 | 行数 | 管什么 | 不该出现什么 |
|---|---|---|---|
| `app/api/` | ~1760 | **收请求、发响应**。解析参数、调服务、把结果转成 Pydantic 模型 | 不能写业务规则、不能直接查数据库 |
| `app/services/` | ~3900 | **业务规则**。什么算完成、怎么组卷、怎么判分、报告怎么聚合、计划怎么排 | 不能出现 `Request` / `HTTPException` |
| `app/repositories/` | ~1700 | **存取数据**。SQL 只写在这里 | 不能写业务判断 |
| `app/infrastructure/` | ~2400 | **外部能力**：数据库会话、LLM、解析器、向量库、邮件、定时器 | 不能写业务流程 |
| `app/domain/` | ~250 | **业务对象**（dataclass 实体 + 枚举） | 不能 import SQLAlchemy |
| `app/schemas/` | ~620 | **接口出入参**（Pydantic），前后端的契约 | 不能写业务逻辑 |
| `app/config.py` | 100 | 读 `.env`，给全局 `settings` | 不能写业务 |

一句话记法：**api 收发 → service 决策 → repository 存取 → infrastructure 干活。**

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
    ├─ LLMTypedGenerator(题型).generate()    ← 四种题型并发跑
    │      ├─ practice_prompts.build_messages()   ← 拼 prompt
    │      ├─ build_llm().chat()                  ← infrastructure/llm 真发请求
    │      └─ parsing.normalize_question()        ← 抠 JSON、校验来源、去重
    └─ SqlAlchemyPracticeRepository.save()   ← 存成 JSON 快照
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
    └─ 答错 → SqlAlchemyWrongAnswerRepository.upsert()
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
| 新增一种文件格式 | `infrastructure/parsers/builtin.py` | 写个类、加进末尾的 `PARSERS` |
| 换 LLM 供应商 | 设置页，或 `infrastructure/llm/openai_compat.py` 的 `LLM_PRESETS` | 都走 OpenAI 兼容协议 |
| 改某个接口的出入参 | `schemas/` + 对应 `api/*.py` | schema 是前后端的契约 |
| 加一张表 | `infrastructure/db/models.py` + 写一个迁移 | 迁移在 `migrations/versions/` |
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
2. `app/domain/models/entities.py`（260 行）—— 数据长什么样，业务对象有哪些
3. `app/services/grading.py`（132 行）—— 最干净的纯逻辑，看判分怎么算
4. `app/api/practice.py`（331 行）—— 看接口如何只做转发
5. `app/services/practice_service.py`（751 行）—— 最核心的业务编排，挑 `run_generation` 和 `submit_session` 两个函数读
6. `app/repositories/practice.py`（89 行）—— 看存数据有多简单

读完这 6 个文件，其余的都是同一套模式的重复。

**不要从 `infrastructure/` 开始读**，那里是技术细节（HTTP、PDF 解析、向量检索），
跟业务主线无关，需要时再看。

---

## 6. 实体和表结构为什么是两份

`domain/models/entities.py`（dataclass）与 `infrastructure/db/models.py`（ORM）
字段相近，看起来是重复。它们分工不同：

- **ORM** 只回答「怎么存进 SQLite」，带索引、外键、列类型。
- **实体** 回答「代码里怎么用」，同时承担三件事：
  1. 服务层的入参出参；
  2. API 响应的数据来源；
  3. **JSON 快照**——`Question` / `WrongAnswer` 里的题目内容压根不对应任何表，
     它们被序列化后存在 `practice_sessions.questions` 和 `wrong_answers.question`
     两个 JSON 列里。资料被删了，历史练习和错题仍然完整可读，靠的就是这份快照。

还有一条硬约束：ORM 对象绑定在数据库会话上，**会话一关就取不到属性**。
服务层如果把 ORM 对象往外传，调用方一访问字段就报 detached 错误。所以边界上
必须转成普通 dataclass。

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

**刻意保留的**：`parsers/base.py`（4 个解析器共享契约）、`llm/base.py`（3 个文件
共用）、`generators/base.py`（`parsing.py` 依赖它，合并会循环导入）、`schemas/`
的 9 个文件（每个 30～180 行，比合成一个 600 行的大文件好定位）。
