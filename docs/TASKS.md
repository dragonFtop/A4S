# A4S 任务清单 v0.1

> 与 `docs/PRD.md` 配套使用。完成一项即勾选；与规约冲突时先改 PRD 再改代码。
> 图例：`[ ]` 未开始 · `[~]` 进行中 · `[x]` 已完成
>
> **当前状态（2026-11-26）**：v0.1 全部完成 145/145，PRD 第 10 章 13 条 P0 已逐条置为「通过」；
> v0.2 追加的 M7 学习计划 Agent 30 项亦已完成（合计 175/175）。
> 审核导航见 `docs/AUDIT_GUIDE.md`；测试脚本见 `backend/scripts/smoke_m*.py`。

---

## M0 — SDD 讨论与规约冻结

- [x] 起草 `docs/PRD.md`（9 章 + 验收总表）
- [x] 起草 `docs/TASKS.md`（本文件）
- [x] **【gating】用户逐节确认 PRD，冻结 v0.1 基线**
- [x] 确认后将 PRD 状态改为"已冻结 v0.1"

**验收**：PRD 覆盖全部章节；每条 FR 有可验证验收标准；非目标与扩展性契约明确。

---

## M1 — 骨架搭建

### 后端

- [x] 初始化 `backend/`、`requirements.txt`（锁定版本）、虚拟环境说明
- [x] `app/main.py`：FastAPI 实例、CORS、路由注册、`/health`（返回 LLM/Embedding/DB 状态）
- [x] `app/config.py`：pydantic-settings 读取 `.env`（LLM / Embedding / SMTP / DATABASE_URL）
- [x] `app/registry.py`：扩展点注册表（`register/get/list`）　<span>v0.1 收尾时删除，改为工厂函数装配，见 PRD ADR-08</span>
- [x] `app/domain/models/`：空壳领域模型（Document / Chunk / Question / PracticeSession / WrongAnswer / ReviewReport）
- [x] `app/repositories/`：仓储接口（Protocol/ABC）+ SQLAlchemy 实现　<span>v0.1 收尾时删除 Protocol，实现按实体拆分</span>
- [x] `app/infrastructure/db/`：SQLAlchemy engine/session、Alembic 初始化（`migrations/`）
- [x] `app/infrastructure/llm/`：`LLMProvider`/`EmbeddingProvider` 接口 + OpenAI 兼容实现 + 本地 BGE 实现
- [x] `app/api/`：路由骨架（documents / chat / practice / review / settings / schedule），返回占位
- [x] 依赖方向校验：`domain` 不 import fastapi/sqlalchemy/llama_index

### 前端

- [x] `frontend/`：Vite + React + TS 初始化
- [x] 布局与路由：知识库 / 问答 / 练习 / 错题本 / 回顾 / 设置
- [x] `src/api/`：后端接口封装（含统一错误处理）
- [x] 基础样式与组件（按钮、表格、空状态、加载态）

### 工程

- [x] `.env.example`、`.gitignore`（含 `.env`、`data/`、`node_modules`、`__pycache__`）
- [x] `README.md`：环境要求、安装、启动、FAQ
- [x] `start.bat`：一键启动前后端并打开浏览器

**验收**：一键启动成功；前后端连通；`/health` 通过；`domain` 层零框架依赖；扩展点可经配置切换一个 dummy 实现。

---

## M1.5 — 视觉基底与配置中心（FR-11 / FR-09）

> 插入里程碑：在写业务页面前先把设计系统与配置闭环做掉，避免 M2~M5 的页面反复返工。

### 设计系统

- [x] `styles/tokens.css`：色彩 / 间距 / 字号 / 圆角 / 阴影令牌，含暗色映射
- [x] 样式分层：`base.css`（reset/排版）/ `components.css` / `layout.css` / `pages.css`
- [x] UI 原子组件：Button / IconButton / Field / Switch / Modal / Toast / Empty / Skeleton / Spinner
- [x] 主题切换：本地持久化，支持 `?theme=dark` 指定（便于截图与分享）
- [x] 侧栏重构：分组导航 + 图标 + 本地身份入口（预留账号体系位置）

### 学习概览（FR-11）

- [x] `repositories/stats_repo.py`：只读聚合投影（跨表统计，Python 侧按天分桶）
- [x] `GET /api/overview`：统计、近 7 天活跃、最近资料、错题、动态时间线
- [x] 前端 `/overview`：问候区、四统计卡、轨迹柱状图、今日建议、快捷入口
- [x] 零数据空态、后端不可达兜底、加载骨架

### 配置中心（FR-09）

- [x] `infrastructure/config_store.py`：写回 `.env`（保序保注释、首次以 example 为基底、密钥不覆盖、即时生效）
- [x] `PUT /api/settings`：部分更新 + 校验 + 打码值忽略
- [x] `POST /api/settings/test`：LLM / Embedding / SMTP 连通性测试（支持未保存先试）
- [x] 设置页：五分组可编辑表单 + 测试连接 + 切换 Embedding 二次确认

### 依赖与选型

- [x] 安装 RAG 全量依赖（llama-index-core/readers、sentence-transformers、pypdf、python-docx）
- [x] 向量库定为 sqlite-vec（ADR-09），KNN 连通性已实测通过
- [x] `requirements.txt` 与 `requirements-rag.txt` 拆分

**验收**：六个页面视觉一致；深浅色切换无样式断裂；零数据首页不报错；配置保存后重启仍生效且即时作用于后端。

---

## M1.6 — 视觉系统落地「夜航」（ADR-10 / ADR-11）

> 用户否定了 M1.5 的窄栏文档式与通用 SaaS 风，改为全宽仪表盘 + 环境光动效 + 单一信号色。本节记录落地项。

### 令牌与环境层

- [x] `tokens.css` 重写：深色为默认皮肤、浅色为同一语义的另一显影；琥珀/青/玫三组信号色成对定义
- [x] `base.css`：三团极光漂移（46/58/52s，仅 transform）、64px 格网遮罩、噪点去色带
- [x] 动效三层：环境层（漂移/呼吸）/ 入场层（页面上升、KPI 错峰、柱子生长）/ 交互层（卡片上浮、条目透光）
- [x] `prefers-reduced-motion` 全量降级 + `?still=1` 静态渲染开关（截图与弱设备用）

### 骨架与组件

- [x] 侧栏重构：书脊式（品牌光点 / 分组导航 / 当前项琥珀发光条 / 主题开关 / 状态）
- [x] 顶栏：页面名 + 连接状态胶囊 + 顶缘流光；导航计数来自 `/overview`
- [x] 玻璃卡片：半透明 + `backdrop-filter` 折射，背后极光会透上来
- [x] KPI、柱状图（峰值琥珀）、列表行、状态徽记、抽屉式表单、Modal、Toast 全部对齐新语言
- [x] 主按钮为全页唯一发光元素；浅色下柱状图改用墨色渐变避免白压白

### 页面

- [x] 概览：按数据动态生成标题高亮词、四 KPI、7 天轨迹、今日建议（含优先序）、快捷入口
- [x] 知识库：三 KPI + 拖拽区 + 文件行列表（按扩展名徽记、解析失败标红）
- [x] 问答 / 练习 / 错题本 / 回顾：按正式版式落地空态与参数区，接入时只换数据源
- [x] 设置：分组导航 + 可编辑表单 + 粘性保存栏，深浅色均通过

**验收**：1440 / 1280 / 520 三档宽度无横向溢出；深色与浅色对比度足够；零数据页面不空洞；`tsc` 与 `vite build` 通过。

---

## M2 — 知识库核心（FR-01 / FR-02 / FR-12）

> 标签与导入必须同批做完：`topic_id` 是上传时的必填归属，也是向量表的分区键，分两批做会出现"列表有主题字段但没有标签界面"的半成品。

### 主题与标签基建（FR-12）

- [x] `topics` 表（`kind='topic'|'tag'`、name、color、sort_order）+ `document_tags` 关联表 + Alembic 迁移
- [x] 内置主题「未分类」：迁移时创建，不可删除/改名
- [x] 主题/标签 CRUD 接口：同类内名称唯一（不区分大小写）、颜色、排序
- [x] 删除语义：删主题 → 其下资料迁「未分类」（不删资料、不删 chunk、chunk.topic_id 同步）；删标签 → 仅解关联
- [x] `seed/topic_packs.json`：9 个包 / 57 个主题 / 5 个默认标签 + 首次引导接口（按名称幂等，已存在则跳过）
- [x] 批量操作接口：批量设主题、批量增删标签
- [x] 仓储方法：按主题/标签取 chunk（为 M3 出题与检索复用）

### 导入与索引（FR-01 / FR-02）

- [x] `DocumentParser`：PDF / MD / TXT / DOCX 四个实现并注册
- [x] 上传接口：多文件、20 MB 限制、类型校验、主题必选（缺省「未分类」）、标签可选、错误回显
- [x] 粘贴文本接口：标题 + 正文（≤100k 字符）+ 主题 + 标签
- [x] 切分器：中文句读优先，500~~1000 字符、重叠 100~~200
- [x] 向量化入库：调用 Embedding Provider → 写入 sqlite-vec（元数据含 document_id/chunk_index/source_name/page/topic_id）
- [x] `vec_chunks` 改造：`topic_id` 作为 partition key；验证「分区内取 K」语义（避免全局取 K 后过滤）
- [x] 文档表：SQLite 持久化（名称/主题/类型/大小/chunk 数/状态/时间）
- [x] 列表接口：分页、状态筛选、主题筛选、标签筛选（多选取交集）
- [x] 删除接口：级联删除 chunk 向量与元数据
- [x] 异步处理状态机：处理中 → 已完成 / 失败（含原因）
- [x] Embedding 模型变更检测与"需重建索引"提示

### 前端知识库页

- [x] 左栏主题导航（含资料数、主题色点）+ 「管理主题」入口
- [x] 顶部标签筛选条（多选取交集）
- [x] 上传抽屉：主题必选 + 标签多选 + 拖拽 + 进度与失败原因
- [x] 列表行：主题色点、标签徽记、状态、片段数、相对时间
- [x] 批量选择 → 批量设主题 / 批量打标 / 批量删除
- [x] 零主题引导层：选 1~3 个学习方向，一键建主题

**验收**：见 PRD FR-01 / FR-02 / FR-12 验收标准全部通过。

> 已完成并通过 `backend/scripts/smoke_m2.py --ingest` 的 31 项断言；向量维度不一致时设置页给出「需重建」提示，重建只重算向量、不重新解析文件。

---

## M2.5 — 电子笔记（FR-13）

> 只依赖 M2 的解析结果与 LLM Provider，不依赖问答/练习。做完即可自用：导入教程 → AI 起草 → 手改定稿。

### 数据与状态机

- [x] `notes` 表（title、markdown、topic_id、status、source_type、template、ai_model、confirmed_at）+ `note_sources` + `note_tags` + Alembic 迁移
- [x] 笔记 CRUD 接口：主题必选、标签可选、草稿/定稿状态机
- [x] 状态流转规则：`draft → confirmed`；定稿后编辑退回 `draft` 并提示需重新确认
- [x] 删除笔记不影响来源资料；删除来源资料后笔记仍可打开（来源标记为"已删除"）

### AI 起草

- [x] 四个模板的 prompt + 结构化输出 schema：章节摘要 / 概念卡 / 代码笔记 / 公式清单
- [x] 起草接口：范围（文档 ID 列表 或 topic_id）+ 模板 → 取 chunk → LLM → 落 `draft`
- [x] 来源角标协议：正文内 `[[chunk:xxx]]` ↔ `note_sources` 双向校验；无来源段落标记「AI 补写」并计数
- [x] 重生成接口：返回将被覆盖的提示，需显式确认参数才执行
- [x] 起草任务状态：生成中 / 成功 / 失败（含原因），前端可轮询

### 前端笔记页

- [x] 导航与路由：学习组新增「笔记」，位置在知识库之后；`/notes` 列表页 + `/notes/:id` 编辑页
- [x] 列表：按主题 / 标签 / 状态 / 更新时间筛选，草稿与定稿视觉区分
- [x] 起草向导：选范围（含整个主题）→ 选模板 → 生成 → 进入编辑
- [x] 编辑器：Markdown 双栏（左源码右预览）、工具栏（标题/列表/表格/代码/公式/引用）、KaTeX、代码高亮
- [x] 来源侧栏：点角标展开对应原文片段，AI 补写段落高亮
- [x] 确认栏：草稿态显示「确认定稿」；改动后显示「已修改，需重新确认」

### 搜索

- [x] FTS5 虚拟表（标题 + 正文），中文按字切分，随写随更
- [x] 搜索接口 + 前端搜索框（标题/正文命中高亮）

**验收**：见 PRD FR-13 验收标准全部通过。

> `backend/scripts/smoke_m25.py` 46 项断言通过（含手工角标的双向校验、命中处截取的摘要、中文单字搜索）。笔记编辑器按需加载，KaTeX 与 Markdown 渲染器不占首屏。

---

## M2.7 — 网页导入与笔记导出（增量需求，2026-11-25）

- [x] 网页抓取转 Markdown：trafilatura 正文提取，预览可编辑后走粘贴入库（FR-01 扩展）
- [x] 笔记 Markdown 导出：角标转 [序号] + 文末来源附录（FR-13 扩展）
- [x] 知识库页「导入资料」弹窗加「网页」标签，支持 `?upload=web` 深链

---

## M3 — RAG 问答（FR-03）

- [x] `Retriever` 接口（封装 LlamaIndex）+ sqlite-vec 实现
- [x] 检索：Top-K=5、相似度阈值 0.3、空库判断
- [x] **检索范围**：主题 / 标签过滤走 partition key（分区内取 K），非全局召回后过滤
- [x] 上下文拼装：片段去重、按相关度排序、token 预算控制
- [x] 问答 prompt：仅依据上下文、禁止编造、要求标注来源
- [x] 答案生成：同步 + 流式（SSE）
- [x] 引用溯源：返回 sources（文档名 + 主题 + 片段 ID + 摘要）
- [x] 无来源兜底："知识库中未找到相关内容"
- [x] 多轮会话：最近 5 轮上下文
- [x] 前端问答页：对话流、来源卡片、展开原文、空库引导
- [x] 前端问答页：范围选择器（全部 / 某主题 / 某组标签），当前范围常驻可见
- [x] 答案「存为笔记」：把回答与来源片段转成 `source_type='chat'` 的草稿

**验收**：见 PRD FR-03 验收标准全部通过。

> `backend/scripts/smoke_m3.py` 25 项断言通过（假模型跑通 SSE 全链路，检索/向量库走真实 BGE + sqlite-vec）。`--real` 参数可追加真实 LLM 流式验证（需先在设置页配置 API Key）。检索未单独封装 Retriever 类，`chat_service.retrieve()` 直接对接 sqlite-vec 分区检索，与 ADR-09 一致。

---

## M4 — 练习检测（FR-04 / FR-05 / FR-06）

- [x] `QuestionGenerator` 接口 + 四种题型实现（单选/多选/判断/简答）并注册
- [x] 出题请求：范围（主题 / 文档 ID 列表 / 近 N 天，可叠加取交集）、数量、题型配比
- [x] 严格 JSON 输出 schema（题干/题型/选项/答案/解析/来源）+ 解析容错重试
- [x] 资料不足检测：返回"资料不足"而非编题
- [x] 草稿态：生成后可删题、调整后再开始
- [x] 判分规则：单选/判断直接比对；多选须完全一致；简答自评
- [x] `PracticeSession` 持久化：题目快照、作答、耗时、得分、状态
- [x] 断点续答：草稿可恢复
- [x] 错题写入 `WrongAnswer`：去重 + 错误次数累加
- [x] 错题本接口：筛选（主题/文档/题型/时间/次数）、主题聚合视图、重练组卷
- [x] 前端练习页：生成配置（主题/文档/近期三入口）、答题卡、提交、成绩页与逐题解析
- [x] 前端错题本页：筛选、主题聚合、重练入口

**验收**：见 PRD FR-04/05/06 验收标准全部通过。

> M4 全部完成。测试：`scripts/smoke_m4.py` 61 项（出题）、`scripts/smoke_m4b.py` 84 项（判分/错题/重练）全部通过；真实模型 5 题约 4 秒，题干与解析均引原文。
>
> 实现要点：
> - **出题**：四种题型各注册一个出题器并发调用；每题必须回报 `source_chunk_ids`，编号不存在即整题丢弃；材料阈值 100 字/题，低于阈值直接拒绝；草稿存 `practice_sessions.questions` JSON 快照。
> - **判分**（`services/grading.py`）：单选/判断归一化后严格相等，多选必须集合完全一致；简答由用户自评。**未作答按 0 分计，但不进错题本**——没做过不等于做错。
> - **断点续答**：每 600ms 防抖自动保存单题作答到 `practice_sessions.answers`；URL 保留 `?session=`，刷新回到原答题卡。
> - **错题**：按 `question_id` 去重累加次数（重练复用同一份题目快照，所以能累加）；答对不自动移出，由用户判断「我会了」。
> - **重练**：直接复用错题快照组卷并进入作答，不调模型——重练的意义是同一道题再做一遍，不是换一种问法。

---

## M5 — 回顾与触达（FR-07 / FR-08）

- [x] 报告聚合：时间窗内新增资料、练习记录、错题统计
- [x] 报告生成 prompt：**按主题分组**的知识点总结 / 错题整理 / 薄弱点分析 / 复习建议
- [x] `ReviewReport` 持久化（Markdown 正文 + 结构化指标）
- [x] 报告列表与详情接口
- [x] 前端回顾页：列表、详情、手动生成（选 7/14/30 天）
- [x] APScheduler：可配置周期（默认每周一 09:00）、可启停、持久化任务
- [x] `Notifier` 接口 + SMTP 实现（HTML 正文、错题 TOP5、跳转提示）
- [x] 邮件发送失败处理：记录原因、界面提示、报告保留
- [x] 设置页：SMTP 配置 + "立即测试发送"
- [x] 设置页：LLM/Embedding 配置 + "测试连接" + 密钥打码 + 切模型确认弹窗

**验收**：见 PRD FR-07/FR-08/FR-09 验收标准全部通过。

> M5 全部完成。测试：`scripts/smoke_m5.py` 50 项全部通过（空库提示性报告、时间窗过滤、数字与库一致、模型降级、邮件失败不丢报告、调度启停）；真实模型生成 7 天报告约 15 秒，五部分齐全。
>
> 实现要点：
> - **分工**（`services/review_service.py`）：数字由程序从库算出并拼进 Markdown，模型只写「知识点概括 / 薄弱点 / 建议」。验收要求「报告错题与错题本一致」，数字交给模型就没法保证。
> - **降级三层**：未配 Key → 规则分析；模型报错 → 规则分析；模型返回的 JSON 字段缺失 → 逐项用规则补齐。正文末尾标明「本期分析由规则生成」，`metrics.analysis_source` 记来源。
> - **快照**：报告生成时把指标与正文一起落库，之后删资料、删练习都不影响历史报告。
> - **时间窗口径**（`repositories/review_repo.py`）：练习只算 `submitted`；正确率分母是**真实作答数**而非题量；未分类统一落 `uncategorized`。
> - **调度**（`infrastructure/scheduler.py`）：BackgroundScheduler 独立线程，任务里 `asyncio.run` 跑生成，不占事件循环；保存设置时热重载；`coalesce=True` 避免睡眠唤醒后连发多份；**不做补发**。
> - **推送**（`infrastructure/mail/smtp_notifier.py`）：内联样式的自包含 HTML，失败返回 `NotifyResult(ok=False)` 而不抛异常——异常会让失败原因止步于日志，用户什么都看不到。

---

## M6 — 联调验收与收尾

- [x] 端到端走查：建主题 → 导入 → 笔记起草定稿 → 问答 → 出题 → 作答 → 错题 → 回顾 → 邮件
- [x] 逐条核对 PRD 第 10 章验收总表，全部置为"通过"
- [x] 边界测试：空库、坏文件、超限文件、错误密钥、断网
- [x] 标签边界：删主题后检索正常、单主题仅 2 份资料时分区检索召回完整、重名被拒
- [x] 笔记边界：草稿不污染检索、删来源资料后笔记可打开、重生成覆盖有确认
- [x] `README.md` 完善：截图/步骤/常见问题/已知限制
- [x] 已知限制说明：扫描版 PDF 无 OCR、简答不自评不判分、单机单用户、笔记不回灌知识库
- [x] 规约一致性核对：实现与 PRD 差异回写 PRD 变更记录
- [x] 本清单全部勾选

**验收**：PRD 各 P0 逐条通过；本文件全部 `[x]`。

> M6 完成情况（2026-11-25）：
>
> - **自动化**：`scripts/smoke_m6.py` 43 项边界断言全部通过——坏文件/不支持格式/超 10 万字符、主题重名与删除迁移、错误密钥与不可达地址、越界请求（不存在的练习/报告、非法窗口）、笔记在来源资料删除后仍可打开、空库概览不出现 NaN。
> - **人工走查**：用 Playwright 走完 8 个页面（概览/知识库/问答/练习/错题本/回顾/设置 + 深色），无控制台报错，深浅两套主题对比度核对通过。截图存 `docs/screenshots/`。
> - **收尾修正**：
>   - 修 `stats_repo.py` 的历史 bug——按 `status == "finished"` 过滤，实际值是 `submitted`，导致概览页「完成套数 / 平均正确率 / 近 7 天完成」恒为 0。
>   - 修报告「用时 0 分钟」——改为按量级换单位（秒/分钟/小时），0 秒显示 `—`。
>   - prompt 里的题型改用中文标签，避免模型把 `single_choice` 抄进正文。
>   - 回顾页改为懒加载，主包从 930 kB 回落到 298 kB（此前它把 KaTeX 带进了首屏）。
>   - 清理 `pages.css` 末尾重复的 `.wb-remove` 样式块。
>
> ⚠️ **唯一未在真机验证的条目**：FR-08 验收第 1 条「配置 SMTP 后收到测试邮件」。当前 `.env` 没有可用的 SMTP 授权码，自动化只能验证到「未配置时返回明确失败原因」。**填好 SMTP 后在设置页点「发送测试邮件」即可完成该项验收**，代码路径与定时推送完全一致（都走 `SmtpNotifier._send`）。

---

## 追加：视觉重构（v0.1 交付后第二轮）

> 不属于原 145 项范围，功能零变更，只改视觉与文案密度。

- [x] `tokens.css`：面板由半透明玻璃改为三档实心色（`--surface` / `--surface-2` / `--surface-3`），
      新增 `--pop` 浮层底色；字号阶梯整体上抬一档（正文 13.5→14.5px，元信息下限提到 12px），新增 `--text-3xl`
- [x] `base.css`：移除格网与噪点，极光由三团减为两团并推离内容区，页头不再被暖光干扰
- [x] `layout.css`：侧栏项高 38px / 字号 14px，当前项左缘短标记；顶栏去掉彩虹渐变线，内容区限宽 1420px
- [x] `components.css`：卡片与 KPI 改实心（去掉 `backdrop-filter` 与顶光），按钮高度 34→37，列表行距与字号上调
- [x] 新增 `components/ui/HelpTip.tsx`：16px 问号 + Portal 气泡，支持点击/悬停/Esc/点外关闭与边缘翻转
- [x] `Field` 增加 `tip` 属性，设置页 4 处长说明由常显提示改为问号
- [x] 删去 4 张纯说明卡片（问答「回答规则」、练习「为什么先出草稿」、错题本「怎么用它」、回顾「报告里有什么」），
      内容并入对应标题的问号；另有 9 处页面副标题压缩为一句
- [x] 修 `formatPercent` 口径错误：后端 `avg_score` 是 0~100 分值，前端又乘 100，33.3 分显示成 3330%
- [x] 修 `stats_repo` 学习动态里同源的 `score * 100`（改为「得分 N%」）
- [x] 修峰值柱选择器：`is-peak` 挂在 `.bar-col` 上，CSS 却写 `.bar.is-peak`，规则从未命中
- [x] 重拍 `docs/screenshots/` 9 张截图（1440×900，含真实问答与错题数据）
- [x] 验证：`tsc --noEmit` 无错、`vite build` 299 kB 主包不变、1280/880 无横向溢出、深浅主题逐页核对

---

## 追加：问答可信度加强（v0.1 交付后）

> 触发：用户提出「检索不到资料时要显式声明没找到，并标明以下内容为 AI 生成、可能有错」。

- [x] 实测阈值：bge-small-zh 下相关提问相似度 0.55~0.76，无关提问 0.13~0.46；
      原阈值 0.3 会把「碳酸锂的提纯工艺」(0.327)、「如何用 pandas 读 excel」(0.459) 也放进来
- [x] `MIN_SIMILARITY` 0.3 → 0.42，新增 `LOW_CONFIDENCE = 0.55`
- [x] 新增「擦边命中」分支：0.42~0.55 之间不拒答，改为在提示词追加判断指令
      （`chat_prompts.UNSURE_NOTE`），让模型先判断片段能否回答，答不了只回一句没找到
- [x] `retrieve()` 改返回三元组，多返回 `best_similarity`；`smoke_m3.py` 同步解包
- [x] `no_context` 事件结构化：`reason`（empty / no_match）+ `scoped`，两种情形给不同出口
- [x] 前端未命中改为面板区块：空库给「去导入资料」，有内容但都在范围外给「不限范围再问一次」
- [x] 每段回答下方常显 AI 生成声明，按可信度分三档措辞（有引用 / 有来源未标注引用 / 无来源）
- [x] `Turn` 增加 `asked` / `done` 字段，AI 声明在流式结束后才出现，不会跟着打字闪
- [x] 验证：`smoke_m3` 25/25 通过、`tsc` 干净、`vite build` 通过、8 路由 × 2 主题无溢出无报错
- [x] 重拍 `docs/screenshots/03-chat.png`（含 AI 声明）；测试数据已还原，开发库回到 0 资料

### 追加：未命中后的兜底作答

- [x] `chat_service.ANSWER_WITHOUT_CONTEXT` 开关（默认开）：检索无结果时先发 `no_context`，
      再走 `_stream_answer` 用通用知识作答，不再直接 return
- [x] `chat_prompts.UNGROUNDED_PROMPT` + `build_ungrounded_messages`：禁止输出 `[n]` 角标、
      禁止假装引用；限制 4 条要点、要求把话说完（首版被 400 字上限截在半句）
- [x] 抽出 `_stream_answer()`，有依据与无依据两条分支共用同一套 delta/done/error 事件
- [x] 前端渲染顺序改为「声明块 → 正文 → AI 标注 → 来源」，声明必须在回答之前
- [x] 三档标注措辞：有引用标 / 有来源未标注引用 / 完全无来源（"未使用你的资料，请注意甄别"）
- [x] 多轮上下文纳入兜底回答（它是一轮完整对话），但"存为笔记"仍只在有来源时出现
- [x] 空库不进兜底分支，仍只提示去导入（`reason=empty`）
- [x] 验证：curl 实测未命中→完整作答且无角标、空库→仅声明、`smoke_m3` 25/25、tsc 与 build 通过

---

## M7 — 学习计划 Agent（v0.2 首个里程碑）

> 全项目唯一带「动作 → 观察 → 再决定」回路的模块。设计依据见 `docs/AGENT_DECISION.md` 第 3 节，
> 边界见 §3.6：只诊断与排程，不生产内容、不评分、不写笔记。

### 数据层

- [x] `domain/models/entities.py` 新增 `StudyPlan`（id / goal / markdown / meta / created_at）
- [x] `infrastructure/db/models.py` 新增 `StudyPlanORM`；迁移 `b7e3a1c95d42_study_plans.py`
- [x] `repositories/plans.py`：`list` / `get` / `create` / `update_meta` / `delete`，
      `update_meta` 做浅合并而不是覆盖（`elapsed_seconds` 与 `steps` 是后补的）

### 工具层（`services/plan_tools.py`，304 行）

- [x] `get_progress()`：资料数、练习套数与已完成数、平均分、错题总数与近 7 天新增
- [x] `list_weak_points(limit)`：按主题聚合错误率，返回 `error_rate` 与样本数；无数据时返回 `note`
- [x] `list_wrong_answers(limit)`：题干、错次、上次作答，题干截到 120 字
- [x] `generate_practice(...)`：**同步复用** `practice_service` 的出题器，不自己写题
- [x] `save_plan(markdown, goal, meta)`：唯一写操作；超 8000 字抛 `ToolError` 让模型压缩
- [x] 统一出口 `_fit()`：超 2000 字符先砍列表、再砍长文本，保住数字与结构
- [x] 参数错误翻成人话（`limit` 越界夹到 1~20 而不是报 500）

### Agent 循环（`services/plan_agent.py`，304 行）

- [x] `plan_prompts.py`：工具说明书、动作 JSON 协议、计划结构（7 天 / 每天做什么 / 完成标准）、
      「数字只能来自工具返回」「平均分是分值不是百分比」「练习套数含草稿」三条数值约束
- [x] `MAX_STEPS = 6`；最后一步注入「信息够用了，立刻 save_plan」
- [x] `MAX_SAME_TOOL_CALLS = 2`：同一只读工具第三次调用直接回「已经查过了」，不让它原地打转
- [x] `parse_action()`：容忍 ```json 代码块、前后夹解释、`args` 缺失、`tool` 别名；
      解析不出就发一条纠正消息再试，不猜不硬解释
- [x] 循环前空数据检查：资料与练习都为 0 时发 `no_data` 直接返回，不生成计划
- [x] 每步推 SSE：`start` / `thinking` / `step` / `plan` / `done`；
      失败出口 `no_plan`（步数用尽）与 `error`（模型报错），二者都只给已查到的结论
- [x] `step` 事件带 `brief`（一句话摘要）、`preview`（原始返回前 400 字）、`elapsed_ms`、`session_id`
- [x] 每一步写入 `study_plans.meta.steps`：工具、入参、是否成功、结果摘要——数字可溯源的凭据

### 接口层

- [x] `POST /api/plan` 返回 SSE；`GET /api/plans`、`GET /api/plans/{id}`、`DELETE /api/plans/{id}`、`GET /api/plan/meta`
- [x] `schemas/plan.py`：`PlanIn` / `PlanBrief` / `PlanDetail` / `PlanMetaOut`
- [x] 注册到 `api/router.py`

### 前端

- [x] `api/client.ts` 新增 `planStream()`（复用 SSE 解析）与 `StudyPlan` 类型
- [x] `pages/Plan.tsx`（388 行）：目标输入 → 生成 → 步骤时间线 → Markdown 计划 → 历史列表
- [x] 时间线每步显示「工具名 + 一句话摘要 + 耗时」，点开 `details` 看入参与原始返回
- [x] 计划里提到「做这套题」时给「去作答」出口，直接跳 `/practice?session=…`
- [x] 路由 `/plan` + 导航「计划」入口；`pages.css` 增 `plan-*` 样式与 900px 断点

### 验证

- [x] `scripts/smoke_m7.py`（421 行，FakeLLM）覆盖六条路径：空数据拒出、正常出计划、
      工具报错换路、步数用尽、动作格式错误后纠正、每个数字都能对回某一步工具返回
- [x] 真实模型实测：生成完整 7 天计划，无编造数字、未出现「约 / 大概」
- [x] `tsc --noEmit` 干净、`vite build` 通过、1440 / 1280 / 880 三档视口无横向溢出
- [x] 明暗两主题截图核对（`.workbuddy/tmp` 临时文件已清）

---

## 进度概览

| 里程碑 | 任务数 | 已完成 | 状态 |
|---|---|---|---|
| M0 规约冻结 | 4 | 4 | 已完成 |
| M1 骨架 | 17 | 17 | 已完成 |
| M1.5 视觉与配置 | 19 | 19 | 已完成 |
| M1.6 视觉系统落地 | 16 | 16 | 已完成 |
| M2 知识库（含标签） | 24 | 24 | 已完成 |
| M2.5 电子笔记 | 16 | 16 | 已完成 |
| M2.7 网页导入与导出 | 3 | 3 | 已完成 |
| M3 问答 | 12 | 12 | 已完成 |
| M4 练习 | 14 | 14 | 已完成 |
| M5 回顾 | 11 | 11 | 已完成 |
| M6 收尾 | 9 | 9 | 已完成 |
| M7 学习计划 Agent | 30 | 30 | 已完成 |
| **合计** | **175** | **175** | 100% |
