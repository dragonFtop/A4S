# 审核导读（2026-11-25 21:45 定稿）

> 用途：人工审核文档与代码时的导航。不是需求文档，不参与验收。
> 状态：**v0.1 全部完成，145 / 145**，PRD 第 10 章 13 条 P0 已逐条置为「通过」。

---

## 1. 快照

| 项目 | 状态 |
|---|---|
| 进度 | 145 / 145（100%），M1 → M6 全部完成 |
| 后端代码 | `app/` 9 738 行 Python（79 个文件） |
| 前端代码 | TS/TSX 约 8 200 行 + CSS 6 100 行 |
| 测试 | 6 个冒烟脚本，共 **309 项**断言（m2 / m25 / m3 / m4 / m4b / m5 / m6） |
| 后端服务 | `127.0.0.1:8000` |
| 前端服务 | `localhost:5173` |
| 开发库 | `backend/data/app.db`，当前为**干净状态**（0 资料 / 0 练习 / 0 报告 / 24 个预置主题） |
| 配置 | 项目根 `.env`，含 LLM / Embedding 共 23 个键，**密钥明文，勿外传**；SMTP 未配置 |
| 版本控制 | **仍未初始化 git**（`.gitignore` 就绪） |
| 截图 | `docs/screenshots/` 9 张（7 页 + 2 张深色） |

审核期间想跑起来：双击项目根 `start.bat`；或 `cd backend && .venv/Scripts/python.exe -m uvicorn app.main:app --port 8000` + `cd frontend && npm run dev`。

> 端口被占用时 uvicorn 会以 10048 静默退出：`netstat -ano | findstr :8000` 找 PID 再 `taskkill /F /PID <pid>`。

---

## 2. 三轮审核路线

### 第一轮：文档自洽（约 40 分钟）

**判据：四份文档互不矛盾，任务勾选与实际代码对得上。**

| 顺序 | 文件 | 看什么 |
|---|---|---|
| 1 | `docs/TASKS.md` | 145 项勾选是否都真做了；M5 / M6 块末尾的完成说明 |
| 2 | `docs/PRD.md` §5 | FR-01 ~ FR-13 描述是否符合预期 |
| 3 | `docs/PRD.md` §8 | 13 条决策变更是否认可（本轮新增 6 条） |
| 4 | `docs/PRD.md` §10 | 13 条 P0 的「通过」是否有依据 |
| 5 | `docs/BACKEND.md` | ⚠️ 更新于 M3 完成时，M4 / M5 章节缺失 |

### 第二轮：代码骨架（约 40 分钟）

只读 `BACKEND_CODE_GUIDE.md` 的 §1 启动链路、§13 仓储层、§19 API 层、§21 横切关注点，然后抽查：

- `api/router.py` — 12 个路由模块的边界
- `domain/models/entities.py`（261 行）— 纯 dataclass，无 ORM 污染
- `repositories/sqlalchemy_repo.py` — 20 行索引，真正的实现在同目录按实体拆成 6 个文件

### 第三轮：M4 + M5 重点（约 80 分钟）

按依赖顺序读，前三个是纯逻辑，最容易审出问题。

| 顺序 | 文件 | 行数 | 审什么 |
|---|---|---|---|
| 1 | `services/grading.py` | 132 | 纯函数；`answered` 与 `is_correct` 分离 |
| 2 | `generators/parsing.py` | 252 | 抠 JSON、答案归一、来源编号白名单 |
| 3 | `services/review_prompts.py` | 130 | 输出 JSON 的容错解析 |
| 4 | `services/practice_service.py` | 751 | 取材轮转、合并顺序、错误率分母 |
| 5 | `repositories/review_repo.py` | 268 | **时间窗聚合口径**（M5 新增） |
| 6 | `services/review_service.py` | 480 | **数字归程序、文字归模型**（M5 新增） |
| 7 | `infrastructure/scheduler.py` | 150 | 线程内 `asyncio.run`、热重载、不补发 |
| 8 | `infrastructure/mail/smtp_notifier.py` | 250 | 失败返回而非抛异常、内联样式 HTML |
| 9 | `repositories/stats_repo.py` | 262 | 概览投影（**修过 finished→submitted**） |
| 10 | `frontend/src/pages/Review.tsx` | 480 | 三态：生成中 / 列表 / 详情 |
| 11 | `frontend/src/pages/Settings.tsx` | 960 | SMTP 测试发送、调度热生效 |

---

## 3. 文档地图

| 文件 | 定位 | 建议 |
|---|---|---|
| `docs/PRD.md`（46 KB） | 需求唯一真相源，13 条决策变更 | **必读** §5、§8、§10 |
| `docs/TASKS.md`（19 KB） | 145 项任务与进度 | 必读 |
| `docs/BACKEND_CODE_GUIDE.md`（60 KB / 26 章） | 实现说明 | **按需查**，别通读 |
| `docs/BACKEND.md`（29 KB） | 设计导览，停更于 M3 | ⚠️ 有债，见下 |
| `docs/AUDIT_GUIDE.md`（本文） | 审核导航 | — |
| `README.md` | 用户视角：安装 / 上手 / FAQ / 已知限制 | 可当产品说明书读 |

**已知文档债**：`BACKEND.md` 头部写着「M3 完成后更新，M4 待开工」，M4 / M5 章节缺失。它与代码指南定位不同（设计意图 vs 逐章实现）但内容重叠。三种处理：补 M4/M5 章节 / 降级为「历史设计」/ 删除只留代码指南。**需要你定。**

---

## 4. 代码地图

```
backend/app/                    9 738 行
├── api/          12 个路由模块（1 469 → 2 000 行，新增 review 完整实现）
├── services/     入库/索引/笔记/问答/主题/练习/判分/回顾（2 558 → 3 200 行）
├── repositories/ 按实体拆分：topics/documents/chunks/notes/practice/wrong_answers
│                 + stats_repo（262）+ review_repo（268）+ 20 行索引
├── infrastructure/ db / llm / parsers / generators / mail / scheduler（2 444 行）
├── domain/       entities.py（261）
└── schemas/      出入参（615 行，9 个文件）

frontend/src/
├── pages/        概览 / 知识库 / 笔记 / 编辑器 / 问答 / 练习 / 错题本 / 回顾 / 设置
├── components/   ui（Button/Field/Modal/Toast/Feedback）+ kb / notes / practice
└── styles/       tokens.css（154）+ pages.css（3 500）
```

**本轮新增文件**（M5）：

```
backend/app/repositories/review_repo.py
backend/app/services/review_service.py
backend/app/services/review_prompts.py
backend/app/schemas/review.py
backend/app/infrastructure/scheduler.py
backend/app/infrastructure/mail/smtp_notifier.py
backend/scripts/smoke_m5.py
backend/scripts/smoke_m6.py
```

---

## 5. 关键口径（最容易审出偏差的 8 条）

若与你的教学直觉不符，请直接指出——它们是**设计选择**，不是实现 bug。

1. **未作答 ≠ 答错。** `answered=False` 时不进错题本，但仍按 0 分计，拉低正确率。
2. **多选题必须完全一致**，不给部分分。
3. **简答题不自动判分**，只看自评（`mastered` / `unsure`）。
4. **错误率分母 = 已提交练习的真实作答数**（按 `question.topic_id` 回扫），不是错题条数。
5. **错题本按 `question_id` 去重累加**，`wrong_count` 递增，不产生重复行。
6. **重练复用题目快照，不重新调模型**，零 token 成本。
7. **报告里数字全部由程序算，模型只写「知识点概括 / 薄弱点 / 建议」**。验收要求「报告错题与错题本一致」，数字经模型就无法保证。
8. **推送失败不阻断生成**：失败原因写 `metrics.push`，界面显示「上次推送失败」，报告保留。

另有两条工程约束：

- 出题材料下限 = 题目数 × 100 字；0 份资料时提示「请先导入」，有资料但不够时提示「现有 X / 需要 Y」。
- 取材按文档等距抽段，单文档 8 段、总量上限 60 段 / 18 000 字。

---

## 6. 占位与已知限制（不是 bug）

| 项 | 现状 |
|---|---|
| `NOTIFIER_REGISTRY` | 只有 `smtp` 一个实现（微信/系统通知是未来） |
| `RENDERER_REGISTRY` | 空。预留未用，v0.1 不需要多渲染器 |
| 扫描版 PDF | 无 OCR，解析为空并报错 |
| 笔记 | 不回灌知识库，不参与检索 |
| 定时任务 | 进程内，关机不补发（PRD §8 已明确放弃） |
| 单用户 | 无账号体系，`user_id` 固定 `local` |

**SMTP 未配置**：FR-08 第 1 条「收到测试邮件」无法在自动化里验证。填好授权码后在设置页点「发送测试邮件」即可完成该项，代码路径与定时推送一致。

---

## 7. 一个建议：现在初始化 git

项目有 1 万行后端 + 1.4 万行前端，**仍无版本控制**。`.gitignore` 已排除 `.env`、`backend/data/`、`node_modules/`、`frontend/dist/`。

```bash
cd "C:/Users/20156/Desktop/project v0.1"
git init
git add -A
git commit -m "chore: v0.1 complete (145/145)"
```

之后每加一个功能提交一次，改动就有对照物。**这是目前风险最高的一项**——1 万多行代码没有回退能力。

---

## 8. 审核勾选清单

- [ ] `TASKS.md` 145 项勾选与实际代码一致，无虚报
- [ ] `PRD.md` §5 功能需求描述符合预期
- [ ] `PRD.md` §8 十三条决策变更均认可
- [ ] `PRD.md` §10 十三条 P0 的「通过」有依据
- [ ] `BACKEND.md` 的 M4/M5 缺失如何处理
- [ ] 分层边界：API 层无业务逻辑，仓储层无 HTTP 概念
- [ ] §5 八条口径全部认可
- [ ] §6 占位项确认是「未做」而非「做错」
- [ ] SMTP 配好后验证「发送测试邮件」
- [ ] 是否初始化 git

---

## 9. 体量评估（22 k 行算不算多）

**结论：偏多，但根源不是「写得啰嗦」，而是第一版做了太多可选项。M5 已证明做减法可行。**

### 数字

业务代码约 **21 900 行**：后端 9 738 + 前端 TS 8 200 + CSS 6 100。
v0.1 收尾后做了一轮简化：删掉插件注册表与零引用 Protocol、合并空壳子类、
把 831 行仓储文件按实体拆分。功能未变，331 项冒烟测试全通过。

### 膨胀点

| 点 | 规模 | 判断 |
|---|---|---|
| CSS | 6 100 行（27%） | `pages.css` 一个文件 3 500 行，前缀类堆叠。**最该收敛**，与业务价值无关 |
| 多 provider 适配 | 约 900 行 | 支持 4 个 LLM + 4 种 embedding，实际只用 1 组 |
| 设置中心 | 前端 960 + 后端 240 | 打码、预设、切模型确认、索引重建。功能完整，对单人自用是奢侈品 |

**不算多的**：`scripts/` 1 720 行 smoke 测试是资产；文档 155 KB 是 SDD 流程的必然产物。

### 真正的成本不在行数

加一个功能要横穿 7 层。M4 加错题本动了 11 个文件；M5 加报告动了 **13 个**（多出 scheduler 与 notifier 两条基础设施线）。边际成本恒定在 11~13 个文件。

### 建议

现在不动结构。等项目真跑三个月，按「哪些功能你从没点开过」来删——那时的删减才有依据。若要动手，按性价比排序：CSS 收敛（省 1 200 行，风险低）> 题型砍到 2 种（省 400 行，需改 PRD）> provider 预设收敛（省 300 行）。

---

*本文与项目同步。若审核后改了实现，请同步更新 §5 口径与对应文档。*
