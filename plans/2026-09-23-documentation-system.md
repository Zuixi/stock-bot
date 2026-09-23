# 文档系统建设（分层知识库 · 决策记录 · 操作层）

```
status:      completed（B1–B6 已实施，偏离项见 §十）
scope:       仓库文档系统整体设计：分层模型、docs/decisions/、docs/evolution.md、docs/features.md、
             docs/deployment/、docs/testing/、plans 与 best-practices 的结构化、文档门禁
touches:     docs/** · plans/**（仅补文件头与 index）· README.md · AGENTS.md · scripts/doc_gate.sh · scripts/self_review.sh
updated:     2026-09-23
next-action: -（后续项见 §十）
```

> 目标：让"项目是什么 / 有哪些功能 / 架构怎么一步步走到今天 / 当时为什么这么选 / 怎么部署 / 怎么测"这六件事，**在仓库内有唯一权威副本、且能被人和 agent 同时读懂**；并让这套文档**不会腐烂**——靠分层维护规则 + 机械校验，而不是靠自觉。
>
> 参考 OpenAI《Harness engineering》的核心可迁移结论：*give a map, not a 1000-page manual*（渐进披露）+ *文档新鲜度必须机械校验* + *技术债持续小额偿还*。**不照抄**其"放松 merge gate""自实现上游依赖""0 手写代码"三条（前提与我们的规模和风险结构相反，见 §八）。

## 一、现状证据（决定动作范围）

| 事实 | 数据 |
|---|---|
| 文档总量 | 588 个 `.md`；`plans/` 20 个文件 14.6k 行；`docs/Changelog.md` 1060 行 |
| 根 `AGENTS.md` | 65 行，已是"地图"形态 ✅（本次只做指向补充，不膨胀） |
| `product.md` | 170 行，描述的是**早期 CLI 目标**（交易所爬虫 + 行为聚类），与现状（gateway/auth/投研工作台）完全不是一回事 → "项目是干什么的"实为缺位 |
| `docs/plan.md` | **0 行空文件** |
| `docs/architecture/frontend/ARCHITERTURE.md` | **2 行空壳**（文件名拼写错误） |
| `docs/designs/index.md` | 空；与 `docs/design/` 职责重复 |
| `docs/build.md` | 512 行混装**生产发布 / 本地开发 / 数据迁移 / 日常运维**四类受众；被 10 处引用 |
| 测试文档 | **不存在**（仅 `docs/frontend-architecture.md §11.2` 一小节）；测试资产 67 个文件 / `e2e`+`bench` markers / `fixtures/eastmoney` 快照 / `frontend/e2e` + `playwright.config.ts` / `scripts/bench.sh` + `benchmarks/baseline.json` 全无文档 |
| 决策记录 | **不存在**。`Changelog.md` 是时间流水，回答不了"为什么现在这样、当时否掉了什么" |
| 知识沉淀现状 | `docs/references/best-practices.md` 286 行、8 分类、只增不删、条目含大量带日期的实测细节 → 正在变成"巨型手册/墓碑文件" |
| 已有机械门禁 | CI 四段（ruff/mypy/pytest/tsc）+ pre-commit（含 `self_review.sh`）+ bench Tier 1 基线；**但文档侧只有一条启发式告警**（"改了 md 没动 Changelog"），交叉引用一致性零校验——而项目已多次踩过端口/`metric_key`/表名不同步 |

## 二、分层模型（每层腐烂方式不同 → 维护规则必须不同）

| 层 | 文件 | 维护规则 | 读者 |
|---|---|---|---|
| **入口** | `README.md` · `AGENTS.md` · `docs/index.md` · `plans/index.md` | 必须最新、被机械校验、不写细节；只做路由 | 人与 agent |
| **说明** | `docs/overview.md` · `docs/features.md` · `docs/ARCHITECTURE.md` · `docs/architecture/**` | 只描述**当前态**，改代码时同步；**禁止写历史与观点** | 人为主，agent 按需 |
| **历史** | `docs/evolution.md` · `docs/decisions/**` · `plans/**` · `docs/Changelog.md` | **只增不改**；ADR 落地后除 `status:` 外禁编辑 | 人为主，agent 被问"为什么"时读 |
| **操作** | `docs/deployment/**` · `docs/testing/**` | 讲"怎么做/怎么排障"；**不复制命令，指向脚本**；权威副本唯一 | 人与 agent 并重 |
| **参考** | `docs/references/**` · `docs/design/**` | 外部资料与设计稿，不参与交叉引用校验 | 按需 |

三条关键分布决策：
1. **"当前态"与"为什么"必须分文件**：architecture 一旦混入历史叙述，改配置时就没人敢动，最终整体过期。
2. **agent 默认不读历史层**：按 `docs/index.md` 找 1–2 个当前态文件即可；只有被问"为什么/当初为何不选 X"时才下钻 `decisions/`。
3. **同一事实只允许一处权威副本**，其余交叉引用（尤其服务表/端口表/命令）。

## 三、目标目录树

```
stock-bot/
├── README.md                             ~  人入口：一句话 + 快速启动 + 指向 docs/index.md
├── AGENTS.md                             ~  地图（≤80 行：铁律/命令/自检门禁 + 指向 docs/index.md）
├── CLAUDE.md                             =  与 AGENTS.md 同源
├── backend/AGENTS.md · frontend/AGENTS.md =  各层"目录 + 铁律"
│
├── docs/
│   ├── index.md                          ++ 入口层：按任务找文档（加数据源/加队列/改图表/改端口 → 文件清单）
│   │
│   ├── overview.md                       ++ 说明层：项目是干什么的（为谁 / 当前阶段 / 明确的非目标）
│   ├── features.md                       ++ 说明层：功能清单（功能 | 一句话 | 代码入口链接）—可机械校验
│   ├── ARCHITECTURE.md                   ~  说明层：容器与服务拓扑（讲"是什么"）
│   ├── architecture/
│   │   ├── database-architecture.md      =  当前态：库表与约束
│   │   ├── authentication-and-gateway.md =  当前态：鉴权链路
│   │   ├── auth-data-model.md            =  当前态：账号库
│   │   ├── backend/ARCHITECTURE.md       ~  当前态：后端分层与依赖方向（顺带修文件名拼写）
│   │   └── frontend/ARCHITECTURE.md      ~~ 当前态：前端 feature-sliced（现为空壳 2 行）
│   │
│   ├── deployment/                       ++ 操作层：怎么部署（原 build.md 拆解）
│   │   ├── index.md                      ++   部署总览 + **服务/端口权威表**（门禁校验对象）
│   │   ├── local-dev.md                  ~    本地开发 / 依赖 / 迁移 / lint 唯一口径
│   │   ├── production.md                 ~    发布流程（tag→CI→ghcr→服务器→prod override→Caddy/Traefik 两跳）
│   │   ├── data-migration.md             ~    数据迁移：导出→传输→恢复→对账→开机回填 footgun
│   │   └── operations.md                 ~    日常运维 / 迁移命令 / 故障排查清单
│   ├── testing/                          ++ 操作层：怎么测（全新）
│   │   ├── index.md                      ++   **门禁矩阵**：什么检查 / 何时跑 / 是否阻断 / 失败怎么办
│   │   ├── backend.md                    ++   单测与集成：markers、conftest、fixtures 快照规范、TUSHARE_TOKEN= 口径
│   │   ├── frontend-e2e.md               ++   Playwright：起栈、断言规范、失败产物、与后端 e2e marker 的区别
│   │   └── benchmarks.md                 ++   Tier 分级、bench.sh 用法、baseline 刷新与契约变更判据
│   │
│   ├── evolution.md                      ++ 历史层：里程碑时间轴（trigger / before→after / 残骸 / 关联 ADR）
│   ├── decisions/                        ++ 历史层：决策记录 —— **只增不改**
│   │   ├── README.md                     ++   模板 + 写入门槛（"未来有人会问为什么不选 X" 才写）
│   │   ├── index.md                      ++   状态表 active/superseded
│   │   ├── 0001-single-primary-source-tushare.md       ++
│   │   ├── 0002-split-auth-service-and-forward-auth.md ++
│   │   ├── 0003-concept-membership-symbol-as-key.md    ++
│   │   ├── 0004-current-snapshot-not-backfilled.md     ++
│   │   ├── 0005-overlay-seed-files.md                  ++
│   │   ├── 0006-no-eslint-tsc-as-static-check.md       ++
│   │   ├── 0007-tiered-bench-gate-and-e2e-excluded.md  ++
│   │   └── archive/                      ++   每季度把 superseded 的归入此处
│   ├── Changelog.md                      =  历史层：追加式流水（保留原样，不重构）
│   │
│   ├── design/                           =  参考层：原型 / UX 规格 / 数据源调研
│   ├── designs/                          -- 内容并入 design/ 后归档（index.md 为空）
│   ├── references/
│   │   ├── index.md                      =  外部资料索引（tushare/ · sw/ · cninfo/）
│   │   ├── best-practices.md             ~  瘦身为"探测器映射表 + 链接"
│   │   └── best-practices/               ++   按 8 分类拆分；单条 ≤3 行，能机器检测的升级为 lint 后删除
│   ├── build.md                          ~  压成 5 行转发文件（承接历史引用，见 §六）
│   ├── frontend-*.md · web-prd.md · build.md =  产品与 UX 规格
│   ├── src.md                            -- 早期 CLI 组件说明 → 并入 evolution 的 v0 段
│   └── plan.md                           xx 空文件，删除
│
├── plans/
│   ├── index.md                          ++ 状态表：status / scope / touches / updated / next-action
│   └── *.md                              ~  仅补 5 行文件头 + superseded 标注，正文不改
│
├── product.md                            -- 标 archived，指向 overview.md
├── benchmarks/baseline.json              =  Tier 1 性能基线门禁
└── scripts/
    ├── self_review.sh                    ~  扩展 [3/4] 段：调用 doc_gate.sh（现仅启发式告警）
    ├── doc_gate.sh                       ++ 文档系统门禁（§七）
    └── (slop_scan.sh · doctor.sh · dev_up.sh) =  工程环境类，不在本计划（见 §九）
```

图例：`++` 新建 · `~~` 空壳重写 · `~` 修订 · `=` 保持 · `--` 归档 · `xx` 删除

**净增约 26 个文件**（7 个 ADR、9 篇操作层、3 篇说明层、2 个 index、2 个模板、1 个门禁脚本、best-practices 拆分），删除 1 个、归档 4 个、重写 3 个空壳/陈旧文件。

## 四、交付物明细

### 4.1 入口层
- **`docs/index.md`**：一张"我要做 X → 读哪些文件"的路由表（加数据源 / 加队列 / 改迁移 / 改前端图表 / 改端口 / 改鉴权 / 算指标 / 排数据缺口 …）。根 `AGENTS.md` 保持 ≤80 行，只加一行指向它。
- **`plans/index.md` + plan 文件头契约**：20 个 plan / 14.6k 行，目前 agent 无法判断哪份是当前事实——而项目已经付过代价（`best-practices` 记录：按 main 动手结果把并行分支已实现的一整套功能重复实现）。做法：每个 plan 补 5 行头（`status: active|completed|superseded` / `scope` / `touches` / `updated` / `next-action`），index 只列这 5 行。

### 4.2 说明层
- **`docs/overview.md`**：为谁解决什么 / 当前阶段 / **明确的非目标**（不做投资建议、不做交易下单——agent 最易越界的边界）。
- **`docs/features.md`**：功能清单表（功能 | 一句话 | **代码入口**：页面路径/API 前缀/表名/`metric_key`）。写法要求：**每条必须带可解析的代码入口**，使其成为可执行契约（删功能不删文档 → 门禁红）。
- **`architecture/` 修复**：重写前端 2 行空壳、修 `ARCHITERTURE` 拼写、`backend/ARCHITECTURE.md` 补分层依赖方向（作为门禁的结构化依据）。

### 4.3 历史层
- **`docs/decisions/`**（本计划的核心新增）
  - 纪律：**只增不改**（落地后仅可改 `status:`/`superseded-by`，正文禁编辑）· 单篇 ≤40 行（Context / Decision / Consequences / Alternatives（否掉的原因））· 仅在"未来有人会问为什么不选 X"时才写 · 编号 + index 状态表 + 季度归档。
  - **backfill 只补 7 条**（都是"当时可以不这么做"的真实选择）：单一主源 TuShare / 拆 auth-service+forward-auth / 概念成分以 `symbol` 为业务键 / 只做当前快照不回算历史（反前视与幸存者偏差） / overlay 种子文件 / 前端不引 eslint 而用 tsc 作静态校验 / e2e 默认排除 + bench 分级硬门禁。
  - 不给 20 个 plan 各写 ADR。
- **`docs/evolution.md`**：里程碑时间轴，每段固定四问——`trigger（为什么不得不变） / before→after / 留下的残骸 / 关联 ADR 编号`。来源是 `Changelog.md` + 20 个 plan 的**一次性提炼**（之后靠 ADR 增量，不指望每次提交同步手写）。粗粒度：`v0 CLI 原型（src/ 交易所 crawler）→ v1 FastAPI + TuShare 统一源 → v2 申万分类 + antd 前端 → v3 投研工作台（猪智投）→ v4 auth/gateway 拆分 → v5 市场情绪与概念板块`。残骸要写实（`src/`、`sw_seed_raw.sql`），否则新人与 agent 会去动它们。

### 4.4 操作层（部署 + 测试）

**`docs/deployment/`（原 `build.md` 拆解）**
- `index.md`：环境矩阵 + **服务/端口权威表**（与 `docker-compose*.yml` 门禁一致）+ 路由表
- `local-dev.md`：首次搭环境（前端先本地 `npm ci && npm run build`、根 `.env` 与 `backend/.env` 两个文件角色不同）+ 只起基建/迁移/热重载 + lint 唯一口径
- `production.md`：合并 main→CI 绿→`git tag v*`→CD→ghcr→服务器拉取 + prod override（**不要复制 override 到服务器**）+ 边缘层**两跳**（Caddy→Traefik + forward-auth 注入与 CSRF）+ 镜像构建优化点
- `data-migration.md`：全量 vs 精简导出（含排除项与体积/耗时）+ 传输两种方式 + 恢复三步 + 对账（行数/Alembic 版本/认证库）+ **开机回填 footgun**
- `operations.md`：容器状态/日志/重启/停止（含清卷删库警告）+ 迁移与回滚 + 排障清单（卷权限、`backend/.dockerignore` 缺 `data/*`、overlay 种子、一次性容器顺序）

**`docs/testing/`（全新）**
- `index.md`：**门禁矩阵**（检查项 · 命令来源 · 何时跑 · 是否阻断 · 失败怎么办），行集合 = ci.yml job ∪ `self_review.sh` 步骤 ∪ `bench.sh`；三档划分：pre-commit 快检 / PR CI / 手动全量 `--full`
- `backend.md`：纯单测边界（不碰网络/DB）+ 需 DB 的 fixture + markers 语义（`e2e` 需 docker 栈 / `bench`）+ **`TUSHARE_TOKEN=` 口径**（本机真 token 会掩盖"单测偷用真 client"，曾造成本地全绿 CI 全红）+ `fixtures/eastmoney/` 快照规范与更新流程 + 新增数据源的分层测试清单
- `frontend-e2e.md`：与**后端 `e2e` marker 的区别**（同名不同物，必须消歧）+ 起栈前提 + 断言稳定性约定 + 何时用 e2e、何时用组件级/接口级断言替代
- `benchmarks.md`：Tier 1 硬门禁 / Tier 2 信息性的划分 + `bench.sh` 三种用法 + **baseline 契约变更判据**（何时允许刷新 `baseline.json` 并随 PR 入库）+ 禁止把 wall-clock 绝对值写死

### 4.5 参考层瘦身
- **`best-practices.md` 拆分 + 写入门槛**：拆到 `docs/references/best-practices/<8 分类>.md`，顶层只留探测器映射表 + 链接。新条目规则：**≤3 行 + 必须回答"能否机械检测"**——能 → 升级为 lint/测试并从文档删除；不能（纯人工判断）→ 才留文档。这是阻止它变成"墓碑文件"的唯一低成本办法（它现在 286 行、只增不删，正是 Harness engineering 警告的形态）。

## 五、`docs/build.md` 处置

- 内容拆入 `deployment/` 后压成 **5 行转发文件**（标题 + "已迁移至 `docs/deployment/index.md`" + 保留原因）
- **活引用改新路径（4 处）**：`README.md` · `AGENTS.md` · `docs/ARCHITECTURE.md` · `scripts/self_review.sh`
- **历史引用不动**：`docs/Changelog.md` · `plans/*.md` · `.superpowers/**`（改了就是篡改记录）
- `AGENTS.md` 中"部署权威文档 = docs/build.md"表述同步改为 `docs/deployment/index.md`

## 六、门禁：`scripts/doc_gate.sh`（挂进 `self_review.sh` 的 [3/4] 段）

| # | 检查 | 失败条件 | 故意破坏验证（每项必须做一次） |
|---|---|---|---|
| 1 | **ADR 只增不改** | 已入库 ADR 出现非 `status:`/`superseded-by` 行的改动 | 改一条 ADR 正文，确认报错 |
| 2 | ADR 头部与编号 | 缺 `status/date/supersedes/superseded-by`；编号不连续；superseded 未双向引用 | 删一个字段 / 断号 |
| 3 | **features 契约** | `features.md` 条目的代码入口路径不存在，或 API 前缀在 `app/api` 查无此 router，或页面目录无路由引用 | 指向不存在的页面 |
| 4 | **门禁矩阵 ↔ CI 单向一致** | `testing/index.md` 检查项 ≠ ci.yml job ∪ self_review 步骤 | 给 ci.yml 临时加 job |
| 5 | **端口/服务名权威** | `deployment/index.md` 端口表 ≠ compose 实际映射 | 改一个映射端口 |
| 6 | plans 头部 | `plans/*.md` 缺 5 行头，或 completed/superseded 未出现在 `plans/index.md` | 新增一个无头部 plan |
| 7 | 脚本身份 | 文档中引用的脚本路径不存在（防"文档承诺了不存在的脚本"，项目已有同类坑） | 引用一个不存在的脚本 |
| 8 | 当前态口径一致 | architecture/deployment/testing 中提到的表名 / `metric_key` / 队列名 ↔ `app/models` / `app/core/mq.py` 不一致 | 改一个 `metric_key` 字面量 |
| 9 | e2e 术语歧义 | 文档裸用 `e2e` 未限定前后端 | 临时插一句裸用 |

**规则：每个检查必须在脚本注释里写下破坏方式与预期输出**——否则 checker 自己会腐烂（已有先例：`self_review` 只扫工作区导致漏检已提交文件）。
`evolution.md` 覆盖度检查为**告警**：出现新的里程碑级 ADR 而未在 evolution 追加段落。

## 七、实施顺序（每批独立验收，可停在任何一批）

| 批次 | 内容 | 验收 |
|---|---|---|
| **B1** | `docs/decisions/`：README 模板 + 判据 + 7 条补录 + `index.md` | 门禁 1、2 通过并完成故意破坏验证 |
| **B2** | 入口层：`docs/index.md` + `plans/index.md` + 20 个 plan 补 5 行头（正文不动） | 门禁 6 通过 |
| **B3** | 操作层：`deployment/` 拆解（含 build.md 转发与 4 处活引用改路径）→ `testing/` 4 篇 | 无死链；门禁 4、5、7、9 通过 |
| **B4** | 说明层：`overview.md`（归档 `product.md`）→ `features.md` → 修 `architecture/` 空壳与拼写 | 门禁 3、8 通过 |
| **B5** | 历史层：`evolution.md` 一次性提炼 + `references/best-practices` 拆分与写入门槛 | 人工评审：best-practices 顶层 ≤60 行 |
| **B6** | 清理与收口：删 `docs/plan.md`、归档 `docs/designs/` 与 `src.md`、`doc_gate.sh` 并入 `self_review.sh` | `bash scripts/self_review.sh` 全绿 |

## 八、验收判据与度量

**验收**
- 目录树中所有 `++/~/~~/--/xx` 项均已处置，无死链，`docs/build.md` 为 5 行转发
- `doc_gate.sh` 九项检查各自通过一次故意破坏验证；`self_review.sh` 全绿
- 三份说明层文档合计 ≤600 行（防止再造第 589 个巨型 md）
- 除"首次搭环境/数据恢复"外，命令均以脚本路径引用，无抄写式重复

**度量（判断这套东西是否真有用）**
- 门禁拦截次数/月：先升后降（升 = 真能抓，降 = 已成习惯）
- `self_review.sh` 一次通过率
- `best-practices` **同因不同域复发次数**（目标 0，唯一硬指标）
- 每任务"人来回确认"轮数

## 九、明确不做

- **不写第 589 个巨型 md**：说明层 3 篇 + 操作层 9 篇是净增主体，其余全是修空壳/拆臃肿
- **不按代码目录镜像文档**（不建 `decisions/backend/`、`testing/unit/`）；`deployment/` ≤5 篇、`testing/` ≤4 篇
- **不让 ADR 承担设计文档职责**（原型/字段表/API 细节留在 `design/`、`architecture/`）
- **不搞常驻 doc-gardening agent**：每季度一次人工 GC（空壳归零、superseded 入 `archive/`）
- **不给每个 plan 补 ADR**
- **不照抄 Harness engineering 三条**：① 不放松 merge gate（其前提是"agent 吞吐 >> 人类注意力"，我们相反，CI 是唯一防线）；② 不自实现上游库（其收益来自 OTel 深度集成，我们只会自养维护面）；③ 不追求"0 手写代码"叙事
- **不在本计划内改**：任何后端/前端代码、`Changelog.md` 正文、`plans/**` 正文（只补文件头）
- **工程环境类不在本计划**（另立）：`slop_scan.sh`（把 best-practices 探测器表变成可执行扫描）、`doctor.sh`（一页数据新鲜度/端点健康，替代可观测栈）、`dev_up.sh`（按 worktree 派生端口，根治 AGENTS.md 记录的服务端口漂移）、`test_architecture.py`（分层依赖方向结构测试）

## 十、实施记录（2026-09-23）

B1–B6 全部实施，交付与计划一致，除下列 **3 处有意偏离**（均为缩小 diff 或让门禁可用，已同步进文档与 Changelog）：

1. **B2 的「20 份 plan 补 5 行头」未做**：改为只在 `plans/index.md` 维护状态表 + `plans/README.md` 定契约，门禁只对 **2026-09-23 及之后新建**的日期前缀计划生效。
   *理由*：实测 20 份计划里仅 4 份在正文写明状态，给其余 16 份补 `status: unverified` 是纯噪声。状态表只用可核对事实（文中声明 / compose 与代码现状）填写，未知显式标 `unverified`——明确未知优于错误状态。
2. **`doc_gate.sh` 第 9 项（e2e 术语歧义）改为告警且限定作用域**（只查入口/说明/操作/历史层，不查 `Changelog`/`design`/`archive`/`references`）。
   *理由*：全仓硬校验会命中历史文档里大量合法的裸用（如 ADR 文件名 `...-and-e2e-excluded.md`、`.dockerignore` 里的 `e2e/` 目录），把真问题淹没在噪声里。
3. **`docs/architecture/*/ARCHITECTURE.md` 是重写而非仅改拼写**：原后端文档的队列表、API 列表、数据模型均已过期（缺 5 个队列、缺 5 个路由前缀、缺投研相关表）。
   *理由*：说明层的职责就是"当前态"，留着过期内容等于把门禁的"口径一致"检查变成假绿。
4. **计划里的"`docs/designs/` 内容并入 `docs/design/` 后归档"改为直接删除**：复核后发现 `docs/designs/` 的两个文件都是 0 字节（我搬迁的 `webpage.md` 也是空的），无内容可并。
   *后续*：已新增门禁第 10 项（`docs/` 与 `plans/` 下不得有 0 字节文件），并顺带删除同样为空的 `src/main.py`（无引用）。

**门禁抓到的真实缺陷（已修）**：① `features.md` 与后端架构文档误把 `/api/v1/quotes|features|financials` 当顶层前缀，实际挂在 `/exchanges/{exchange}/stocks/{symbol}/...` 下；② `npm run lint` 是死脚本（未安装 eslint、无配置），而 `AGENTS.md`/`README.md`/原 `build.md` 三处都把它当常用命令 → 统一为 `npx tsc --noEmit`；③ `doc_gate.sh` 自身的端口检查取错端口（取了容器侧而非宿主机侧）。

**验证**：`doc_gate.sh` 9 项各自通过一次故意破坏验证（9/9 变红，第 9 项为告警）；`bash scripts/self_review.sh` 绿；`bash scripts/doc_gate.sh` 绿（告警 0）。

## 十一、原始待确认项（已按上述决定处理）

1. **ADR 是否坚持"只增不改 + 机械校验"**（否：退化为普通笔记目录，那不如只写 `Changelog`）
2. `deployment/` 拆 5 篇是否偏多（可合并 `production.md` + `data-migration.md` 为 4 篇）
3. `benchmarks.md` 放 `docs/testing/`，还是让 `plans/2026-09-09-benchmark-tiers.md` 继续当唯一权威
4. `docs/build.md`：转发文件（保历史链）还是彻底删除并回改历史引用（需接受篡改记录）
5. `plans/**` 只补 5 行头是否可接受（20 个文件各加头部会形成一次较大的 diff，但不动正文）
6. 批次顺序是否认可从 **B1（decisions）** 起 —— 成本最低、最快验证"只增不改"可机械执行
7. `best-practices` 拆分后是否允许**删除已能被 lint 覆盖的条目**（否则文档永远减不下来）
