# 客户端 / 服务端拆分

> 状态：**P0 ~ P4 全部已完成**（物理拆分、数据分家、投稿审核、公共目录、联邦执行、打包与运维；回归 513 项断言全过）
> 最后更新：2026-11-27（补充 P3 周定时体检复盘；本文件只描述边界，界面规范见 PRD ADR-10 / ADR-16）

---

## 1. 需求与硬约束

这一轮的目标不是加功能，而是**改边界**：把单体后端拆成「跑在用户电脑上的客户端」和「部署在服务器上的公共服务端」。

### 1.1 用户确定的约束

| # | 约束 | 落在哪 |
|---|---|---|
| C1 | **没有服务端，客户端依旧能运行** | client 不 import 任何 `server.*`；账号可在本地注册 |
| C2 | 服务端在线时可用公共服务；不在线时**界面显示离线即可** | `GET /api/server/status` → `disabled / online / offline` |
| C3 | 个人知识库（资料、笔记、向量）**只存本地** | client 自己的库文件，服务器不存 |
| C4 | 小组公共库在服务器上，成员可上传资料与笔记，**需 admin 审核后入库** | server 侧，未开始 |
| C5 | 共享 = 授权别人的库**只读可问答**；只有主人**显式打开**的库才能被申请 | `spaces.share_enabled`（默认关），已实现 |
| C6 | 联邦查询：对方不在线**直接过期**，不做离线补发 | 不做持久任务队列，一次超时 RPC |
| C7 | 联邦只返回**检索片段**，答案由请求方用自己的模型生成 | 检索在 owner，生成在 requester |
| C8 | 客户端可自由配生成模型；服务器小组库的 embedding 由 admin 统一 | 跨源不比分数，只在文本层重排 |

### 1.2 由约束推导出的设计原则

- **客户端是主体，服务端是可选上行。** 任何功能都不能因为"服务器没开"而不可用。
- **服务端不存个人资料。** 它只持有身份、关系（空间/成员/授权）、公共目录与小组内容。
- **跨源不融合分数。** 不同客户端的 embedding 模型可能不同，向量不可比；联邦检索返回文本，请求方用自己的模型重排。

---

## 2. 架构

```
用户电脑                                 服务器
┌──────────────────────────────┐        ┌──────────────────────────┐
│ 前端（React，:5173）          │        │ 账号 · 空间 · 授权        │
│   │ 只连本地 client           │        │ 公共目录 catalog          │
│   ▼                          │        │ 小组公共库 + 向量         │
│ 客户端 client（:8000）        │───────▶│ 联邦中继                  │
│   ├ 本地库 client.db          │  可选   │                          │
│   ├ 向量（按 space 分区）     │ 上行   │ server（:8100，server.db）│
│   └ 生成模型（用户自配）      │        └──────────────────────────┘
└──────────────────────────────┘
```

**为什么前端只连客户端**：前端不需要知道"某个功能是本地的还是远端的"。客户端作为网关，本地有就直接答，需要远端就转发；服务器离线时，网关返回明确的离线状态而不是超时。这样前端只有一套 base URL，也不用处理双连接。

---

## 3. 目录结构（当前实际）

```
backend/
├── shared/                      # 两端共用，零业务角色假设
│   ├── app_factory.py           # 应用工厂：建表、CORS、挂路由、装调度（调度由入口注入）
│   ├── config.py                # 配置（含 server_url，可留空）
│   ├── identity.py              # 密码哈希、token 签发与校验、空间查询
│   ├── domain/                  # 领域对象与枚举
│   ├── schemas/                 # 出入参
│   ├── repositories/            # 数据访问（一张表一个文件）
│   ├── infrastructure/          # db / llm / parsers / splitter / vectorstore / fts / mail
│   ├── services/                # 内容栈：ingest / index / note / topic / grading / prompts / space / share
│   ├── api/                     # 账号、空间、授权、资料、笔记、主题、概览
│   └── seed/topic_packs.json
│
├── client/                      # 本地应用（可脱离服务端）
│   ├── main.py                  # 入口：shared + client 路由 + 调度器
│   ├── server_link.py           # 服务端在线探测（15 秒缓存）
│   ├── api/                     # chat / practice / review / plan / schedule / settings / server_link
│   ├── services/                # chat_service / practice_service / review_service / plan_agent / plan_tools
│   └── infrastructure/scheduler.py
│
├── server/                      # 公共服务端
│   └── main.py                  # 入口：只挂 shared 路由（目录 / 中继 / 审核待加）
│
└── app/main.py                  # 过渡兼容：等价于 client.main（脚本仍在用，迁完即删）
```

### 3.1 模块归属对照

| 原位置 | 现位置 | 理由 |
|---|---|---|
| `app/domain` `app/schemas` `app/repositories` `app/infrastructure` `app/config.py` | `shared/` | 两端数据结构与能力完全一致 |
| `app/api/deps.py` | `shared/api/deps.py` | 认证与空间上下文对两端同构 |
| `app/api/{auth,spaces,shares,documents,notes,topics,overview,health}` | `shared/api/` | 只操作传进来的 Session，与"数据在哪"无关 |
| `app/services/auth_service.py` | `shared/identity.py` | 密码/token 是通用能力 |
| `app/services/{ingest,index,note,topic,webfetch,grading,*_prompts}` | `shared/services/` | 内容栈两端都要（个人库 / 小组库） |
| `app/services/{space,share}_service.py` | `shared/services/` | 空间与授权是通用关系模型 |
| `app/api/{chat,practice,review,plan,settings,schedule}` | `client/api/` | 个人学习功能，服务端不挂 |
| `app/services/{chat,practice,review,plan_*}_service.py` | `client/services/` | 同上 |
| `app/infrastructure/scheduler.py` | `client/infrastructure/` | 定时回顾是本地功能 |
| `app/api/router.py` | `shared/api/router.py` + `client/api/router.py` | 一个共用前缀，一个客户端增量 |

### 3.2 依赖红线（可自动校验）

```bash
# 客户端不得依赖服务端
grep -rn "from server\|import server" --include="*.py" client shared
# 共享层不得依赖任何一端（调度器由入口注入，不在此 import）
grep -rn "from client\|import client" --include="*.py" shared
```

两条都应为空。当前已验证为空。

---

## 4. 数据归属

### 4.1 目标（P1 已落地）

| 库 | 位置 | 表 |
|---|---|---|
| `client.db` | 用户电脑 `data/` | users（本地账号）/ spaces / space_members / documents / chunks / notes / practice_sessions / wrong_answers / study_plans / review_reports / topics |
| `server.db` | 服务器 | users / spaces / space_members / space_shares / catalog / 小组的 documents / chunks / notes / topics |

### 4.2 当前状态

**两个入口各自连独立的库文件**（P1 数据分家已完成）：

| 库 | 位置 | 内容 |
|---|---|---|
| `client.db` | 用户电脑 `data/` | 全部个人数据（从旧 `app.db` 迁移而来） |
| `server.db` | 服务器 `data/` | 当前为空；小组公共库 / catalog 等 P1.5+ 才写入 |

- 旧单体库 `app.db` 已归档为 `app.db.bak_pre_split`，把它改回 `app.db` 即可回滚。
- 客户端与服务端可**同时运行**，各自写各自的库，不再有 SQLite 锁冲突。
- 迁移由 `scripts/migrate_split_db.py` 完成（幂等，支持 `--dry-run` 预校验）。
- Alembic 仍是**单条链**：两端启动各跑 `alembic upgrade head` 到自己的库。schema 尚未分化（小组库等表还没加），暂不需要拆两条链；等 P1.5/P2 给 server 加专属表时再分。

---

## 5. 接口

| 接口 | 归属 | 说明 |
|---|---|---|
| `GET /api/server/status` | client | 返回 `disabled / online / offline`、延迟、原因；**不需要登录**（登录页也要能显示离线） |
| `POST /shares/apply` | shared | 申请别人的库（目标库必须 `share_enabled`） |
| `GET /shares` | shared | 三视角：我开放的 / 共享给我的 / 我申请的 |
| `POST /spaces/{id}/shares` | shared | 主人直接开放 |
| `PATCH /spaces/{id}/shares/{sid}` | shared | 批准 / 拒绝 |
| `DELETE /spaces/{id}/shares/{sid}` | shared | 撤销 |

**只读保护**集中在 `shared/api/deps.py::_assert_can_write`：`viewer` 角色除 `POST /api/chat` 外，所有写方法一律 403。新增接口自动受约束，不需要逐个补。

---

## 6. 任务与进度

### P0 物理拆分（已完成）

- [x] `git mv` 抽出 `shared/`（domain / schemas / repositories / infrastructure / config），保留历史
- [x] 按归属拆分 `api` 与 `services`，改写 100+ 文件的导入路径
- [x] `shared/app_factory.py` 应用工厂，两端共用启动流程
- [x] `client/main.py` / `server/main.py` 两个独立入口
- [x] 消除反向依赖：`shared` 不再 import `client`（调度器改为入口注入）
- [x] `app/main.py` 保留为兼容入口（脚本仍 `from app.main import app`）
- [x] 回归：513 项断言全过

### P1 数据分家（已完成）

- [x] `client.db` / `server.db` 两个库文件，配置分离（入口通过 `APP_ROLE` 选默认库；`DATABASE_URL` 显式优先）
- [x] 迁移脚本 `scripts/migrate_split_db.py`：把旧 `app.db` 内容搬到 `client.db`，原库归档为 `.bak`（可回滚）
- [x] Alembic 单条链两端共用：两端启动各跑 `alembic upgrade head` 到自己的库（schema 未分化，无需拆链）
- [x] 验证：client 保留 3 用户 / 6 空间 / 25 主题；server 空库 health 200；两端并发无锁冲突；513 项断言全过

### P1.5 小组投稿与审核（已完成）

- [x] `documents.status` 增加 `pending_review / rejected`
- [x] 审核前只存文件与解析文本，**不切分、不 embedding、不进 FTS**（省算力，也防绕过）
- [x] admin 预览原文 → 批准（触发向量化）/ 驳回（带理由，通知上传者）
- [x] 笔记投稿同样走审核；上传 = 复制到小组，保留作者归属
- [x] 审核人不能审自己上传的内容（owner 自传免审直入，member 投稿必经 owner 审核）

### P2 公共目录 + Agent 选源（已完成）

- [x] `catalog` 由"已生效的共享授权"派生，不手工维护
- [x] 字段：owner、空间、主题名、资料数、更新时间、`vector_version`、在线状态、配额余量
- [x] 默认不进目录；批准或主动开放后才出现，撤销即消失
- [x] Agent 读 catalog 决定问谁；来源数上限 3（本地 → 组内 → 联邦）；允许"不知道"
- [x] 目录只在本组范围内可见（加入小组 = 目录对组内可见，加入时需明示）

> **实现决策**：catalog 不落表，`shared/services/catalog_service.py::build_catalog` 读取时实时派生（逻辑视图）——
> 行存在 = `spaces.share_enabled=true` ∩ 存在 `space_shares.status='active'`；共享批准/撤销无需钩子重建，永不漂移，
> 也**无需为 server 拆 Alembic 链**。可见性 = 空间成员（`space_members`）或 active 共享被授权人；组外为空。
> "加入即同意目录可见"（成员由 owner 拉入），本轮不加同意记录列。在线状态/配额为 P2 占位（`unknown` / 常量），P3 接节点注册与审计后填真实值。
> Agent 选源：`chat` 增加 `multi_source` 开关（默认关，保持既有单空间行为）；开启后本地→组内→联邦、合计 ≤3 源，
> 跨源仅按文本层（字符二元组命中率）重排，不融合向量分（C8）；全部无命中则回答"不知道"、不伪造 `[n]`。

### P3 联邦执行（已完成）

- [x] 节点注册 + **出站轮询**（不要求公网 IP，绕开 NAT）
- [x] 服务器持住请求 ~15 秒等节点来领；超时即返回"来源离线"（C6）
- [x] 节点只返回 citations（文本 + 出处 + 分数），不生成、不返回向量
- [x] 请求方用自己的重排（文本层）对候选重排（C8），再生成
- [x] 配额 + 审计（防抽取攻击）

> **实现说明**：中继在 `server/federation/relay.py`（线程安全内存任务表 + `Future` 跨线程唤醒），
> 接口在 `server/api/federation.py` 并只挂服务端；客户端不 import `server`（C1）。
> 目标节点线程 `client/infrastructure/federation.py` 心跳注册 + 出站 `claim`，领到任务后本机 `retrieve` 只回传
> `chunk_id/document_name/snippet/page/score`（无向量、无答案）。请求方 `client/services/federation_service.py`
> 收到 citations 后用自身 `_text_score` 文本层重排（不融合远端向量分，C8），再用本机模型生成并标注 [n]；
> 新增客户端 `POST /api/fed/ask`。审计表 `fed_audit`、节点表 `fed_nodes` 走单条 Alembic 链（两端 upgrade 均建表，空表无害）。
> 三进程验收脚本：`scripts/integration_p3_federation.py`（client:8000 / client2:8001 / server:8100）。

### P4 打包与运维（已完成）

- [x] 客户端打包（Tauri 2 外壳：托盘、窗口；开机自启留作可选插件）
- [x] 备份：`VACUUM INTO` 一致性快照 + 保留 N 份 + 推异地
- [x] 前端：离线徽标、共享管理界面、只读空间隐藏写按钮

> **实现说明**：打包骨架在 `frontend/src-tauri/`（`BUILD.md` 含步骤）。本机无 Rust 工具链，
> `tauri dev/build` 未在此环境执行，属明确环境阻塞。备份见 `client/services/backup_service.py`
> 与 `scripts/backup_client_db.py`。前端三项：`Layout.tsx` 顶栏服务端徽标（disabled/online/offline，
> 10s 轮询 `/api/server/status`）；`pages/Shares.tsx` + `/shares` 共享管理（批准/拒绝/撤销/申请/撤回/主动开放）；
> `useCanWrite()` 在 viewer 空间隐藏知识库与笔记的写按钮（后端写接口仍 403）。

---

## 7. 验证

### 7.1 拆分后回归（全部通过）

| 脚本 | 断言 | 覆盖 |
|---|---|---|
| `smoke_m2` | 22 | 知识库 |
| `smoke_m25` | 46 | 电子笔记 |
| `smoke_m3` | 31 | 问答与检索范围 |
| `smoke_m4` | 61 | 练习 |
| `smoke_m4b` | 84 | 练习（边界） |
| `smoke_m5` | 56 | 回顾与推送（含周定时体检） |
| `smoke_m6` | 43 | 设置与索引 |
| `smoke_m7` | 69 | 学习计划多智能体 |
| `smoke_m8_health` | 38 | 知识库体检道（含探针召回） |
| `smoke_rag` | 18 | 混合检索与隔离 |
| `smoke_auth` | 31 | 账号与空间隔离 |
| `smoke_share` | 32 | 共享授权与只读保护 |
| **合计** | **513** | |

### 7.2 两端独立可用性

```
client：89 条路由；连 data/client.db；不配置 SERVER_URL 时 /api/server/status → disabled
server：54 条路由；连 data/server.db（空）；/api/health 200；/api/practice/sessions 404
```

并发：client(:8000) 与 server(:8100) 同时启动，各自 health 200，无 SQLite 锁冲突。

### 7.3 测试即边界验收器

- `smoke_auth` / `smoke_share`：身份与授权，跑在 client 上（本地也确实需要账号）
- `smoke_m*` / `smoke_rag`：内容栈，跑在 client 上
- 若某个脚本两边都能跑通，说明边界没切干净——这是比看目录更可靠的检查

---

## 8. 风险与待决策

| 风险 | 说明 | 处理 |
|---|---|---|
| 双进程共用一个库 | P1 前不能同时跑 client 与 server 写操作 | ✅ 已解决：拆为 client.db / server.db |
| 小组问答的检索在哪做 | 拆分后 server 需要检索能力；当前 `chat_service` 在 client | P1 时把 `retrieve()` 抽到 `shared/services/retrieval.py` |
| 本地账号与云端账号关系 | 目前是两套独立身份，未绑定 | 待定：注册时可选"绑定云端账号" |
| Alembic 单链假设 | 两库后是否需要两条迁移链 | P1 决定：schema 未分化前共用单链，两端各跑 `upgrade head`；server 加专属表时再拆 |
| `app.main` 兼容层 | 脚本仍指向它 | 脚本迁完后删除 |
| 前端未加离线徽标 | `server/status` 已有，界面未消费 | ✅ 已在 P4 消费并显示三态徽标 |
| 文档落后于实现 | 旧版说明还在写单体目录、深色主题与单用户 | ✅ 2026-11-26 全量更新 README、PRD、本文件与截图 |

---

## 9. 运行方式

```bash
# 客户端（本地，必须能独立跑）
cd backend && .venv/Scripts/python.exe -m uvicorn client.main:app --port 8000 --reload

# 服务端（可选上行）
cd backend && .venv/Scripts/python.exe -m uvicorn server.main:app --port 8100 --reload

# 前端（仍代理到 8000）
cd frontend && npm run dev
```

在 `backend/.env` 里填 `SERVER_URL=http://<服务器>:8100` 即进入联机模式；留空为纯本地模式。

> **从旧版升级**：首次启动前先跑一次迁移，把旧单体库 `data/app.db` 的数据搬到 `data/client.db`：
> ```bash
> cd backend && .venv/Scripts/python.exe scripts/migrate_split_db.py --dry-run   # 先预校验
> cd backend && .venv/Scripts/python.exe scripts/migrate_split_db.py           # 执行（归档原库，可回滚）
> ```
> `APP_ROLE` 由入口（`client.main` / `server.main`）自动设置，无需手动配；只有自定义库位置时才显式设 `DATABASE_URL`（显式设置永远优先）。
