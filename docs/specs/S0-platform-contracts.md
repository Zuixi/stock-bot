# S0 平台契约（Wave 0 · 无业务代码）

> 本文件是**契约**，不是排期。执行顺序与勾选清单在 [`plans/2026-10-12-investment-research-platform-ia.md`](../../plans/2026-10-12-investment-research-platform-ia.md) §7 Wave 0。
> 上游输入：10 张 owner 思维导图（结构转录见主线计划 §3.3）。

**Wave 0 的唯一目标**：把后续 7 个 Wave 会反复用到的**跨模块约定**一次性钉死。Wave 0 不写业务代码，但**它的产出质量决定后面所有 Wave 是否返工**。

---

## S0.1 平台决策 ADR 批次（5 篇）

**范围**：做完之后，任何 agent 面对"要不要为这个模块单独建一张表 / 单独实现一遍阈值判断"时，能查到既有裁定。

**交付物**：`docs/decisions/` 下 5 篇新 ADR（编号连续，单篇 ≤40 行）+ `docs/decisions/index.md` 登记。

**本文件只给"裁定什么"**——理由与备选与否决**只写进 ADR**，不在本文件复制（否则同一决策两份权威，必有一份腐烂）。

| ADR | 标题 | 裁定（一行）| 一句话理由 |
|---|---|---|---|
| **ADR-A** | 原料/视图分离 | 采一层、存一层、多视图读；事实层只由采集/抽取写入；**用户状态（已读/已处理）必须落用户维度表**，不得写在事实行上 | 10 张图里同一批原料被多处消费（政策/公告/研报/行情/财务/两融 6 处自证）|
| **ADR-B** | 多态关注与标签 | `stock_user_tags` + `user_watchlists`/`user_watchlist_items` **收敛为** `tag` + `user_target_tag`（带 `stage` / `source_module` / `reason`）；`自选` = `dimension='watchlist'` 的系统 tag | 两张旧表都硬绑 `symbol`，无法承载行业/指数/概念/产业链/策略/资讯 |
| **ADR-C** | PIT 时点数据契约 | 进入回测的序列必须带 `published_at`（实际可得时点）；财务按**披露日**对齐；分类归属改 SCD2；证券主表保留退市标记；上游口径修订须留断点 | 没有 PIT，回测系统性偏高，而回测是本产品唯一能自证价值的地方 |
| **ADR-D** | 不做自动下单 | 产品定位为**决策支持 + 记录复盘**，不接受/生成/转发任何交易指令；所有"动作"止于**通知 + 待办** | 10 张图全无下单与券商对接；合规与事故成本极高，但讨论会反复提议 |
| **ADR-E** | 复用既有信号基建，不自建引擎 | **规则 = Python 纯函数**（与 `cycle_engine.py` 同构，阈值进 `industry_registry`）；**信号复用 `industry_signals`**（`basis` 即证据链）；**因子复用 `stock_features` 物化模式**；**不引入规则 DSL、不建 `factor_defs`/`signal_rules`/`signals` 新表** | 仓库已跑通"纯函数规则 + registry 阈值"；文本 DSL 需要一个解析器，而它当前要解决的需求数为 0 |

**ADR-E 的附带裁定（原"`metric_series` vs `industry_metrics` 二选一"已撤销）**：
`industry_metrics` 的形状（`scope + metric_key + source + source_tier + freq + period + value + unit + extra` + 6 列唯一键 + 2 索引）**已经**是统一指标表。**加 2 列复用，不建新表**：
```sql
alter table industry_metrics
  add column published_at    timestamptz,   -- ★ PIT（ADR-C）
  add column caliber_version text;          -- 口径版本；上游口径修订时必须填
```
> `ponytail: industry_key 现被当作通用 scope 键（'pig' / 'market'），语义已泛化。第二次接入非行业 scope 时改名为 scope_key。`

**验收判据**
- 数据契约：5 篇 ADR 存在且编号连续；`docs/decisions/index.md` 已登记
- 数据契约：`industry_metrics` 迁移**只加 2 列**，`grep -rn "create_table.*metric_series" backend/app/migrations/` 无命中
- 行为（废弃项只扫实现层，**不扫撤销记录本身**）：`grep -rn "metric_series" backend/app/` 无命中；`docs/decisions/` 命中仅限「已撤销 / 不建」的撤销记录（ADR 0013），其余文档层（`docs/specs/`、`docs/Changelog.md` 等）是历史记录，不在本判据范围内
- 门禁：`bash scripts/doc_gate.sh` 全绿（含 [1/13] ADR 只增不改、[2/13] 头部与编号）

---

## S0.2 IA 冻结与路由契约

**范围**：做完之后，前端开发可以按一张表建路由，不需要再做信息架构决策。

**交付物**：`docs/design/ia-investment-research-platform.md`（10 导航 × 子模块 × 路由 × 依赖）+ 本文件 §路由表。

### 一级导航（10 个，顺序即默认排列）

| # | 导航 | 路由前缀 | 分区 | 权限（三态，沿用既有口径）|
|---|---|---|---|---|
| 1 | 市场 | `/market` | 研究（只读）| 公开（免登录）|
| 2 | 资讯 | `/news` | 研究（只读）| 待定 |
| 3 | 行业分析 | `/industry` | 研究（只读）| `research:read` |
| 4 | 公司分析 | `/company` | 研究（只读）| 公开（免登录）|
| 5 | 宏观分析 | `/macro` | 研究（只读）| 待定 |
| 6 | 情绪分析 | `/sentiment` | 研究（只读）| 待定 |
| 7 | 价值网图谱 | `/valuegraph` | 研究（只读）| 待定 |
| 8 | 策略引擎 | `/strategy` | 决策（写）| 待定 |
| 9 | 风险管理 | `/risk` | 决策（写）| 待定 |
| 10 | 交易执行 | `/trade` | 执行（写）| 待定 |

> **权限列的语义是「沿用既有口径，不得新增 scope」，不是「无需权限」。** 三态各有依据，且**都是照抄现状、不是新决策**：
> - **公开（免登录）**：市场 `/market`（含全部子路由）与公司分析 `/company`（原 `/stock/:symbol`）——[ADR 0008](../../docs/decisions/0008-research-read-requires-permission.md)「公开行情不受影响：`/market/**`、`/stock/:symbol`、`/index/:tsCode` 维持免登录」。
> - **`research:read`**：行业分析 `/industry`（原 `/research*`）——ADR 0008 正文明文，`backend/app/api/v1/industries.py` 的 router 级依赖；普通注册账号（`trader`）得 403。
> - **待定**（资讯 / 宏观 / 情绪 / 价值网图谱 / 策略 / 风险 / 交易）：后端尚不存在，此刻写死就是假契约。**模块落地时按数据敏感度定；在此之前导航对已登录用户可见，后端未就绪故无门禁。**
> - 顶栏「我的 `/me`」（原 `/watchlist`、`/tags`）为**登录即可**（既有 `RequireAuth`，无具体权限）。
>
> **含义**：本列**不得新增任何模块级写权限 scope**（`*:write` 之类）——为不存在的模块预置 scope 只会得到空字符串。待模块真落地时按 [ADR 0008](../../docs/decisions/0008-research-read-requires-permission.md) 的既有做法（后端 router 级权限依赖）逐个加。

### 顶栏全局工具（不进一级导航）

| 工具 | 路由 | 说明 |
|---|---|---|
| 全局搜索 | `/search?q=` | 跨股票 / 行业 / 指数 / 概念 / 资讯 |
| 自选 | 抽屉（无独立路由）| 任意页面可加自选/打标签，**不离开上下文** |
| 我的 | `/me` | 自选 · 标签 · 我的行业 · 提醒 |

> **「事件日历 `/calendar`」已推迟**：Wave 1 零消费方（最近的消费方是 Wave 3 的「政策到期提醒」与「财报披露日」）。**把它当成"地基"就等于给一个没有当期消费者的东西发免死金牌。** 降到 Wave 3 的 `S4.12` 之一并落地。

### 旧路由 redirect 契约（**一条都不能直接删**）

| 旧路由 | 新位置 | 处理 |
|---|---|---|
| `/market` | `/market` | 保留（语义未变）|
| `/market/category` | `/market/industry` | redirect |
| `/market/industry/:l1` | `/market/industry/:l1` | 保留 |
| `/market/industry/:l1/:l2` | `/market/industry/:l1/:l2` | 保留 |
| `/market/hot-sectors/:category` | `/market/heatmap?group=:category` | redirect |
| `/market/concept/:boardCode` | `/market/heatmap?concept=:boardCode` | redirect |
| `/index/:tsCode` | `/market/index/:tsCode` | redirect |
| `/stock/:symbol` | `/company/:symbol` | redirect |
| `/research` | `/industry` | redirect |
| `/research/:industryKey` | `/industry/:industryKey` | redirect |
| `/tags` | `/me?tab=tags` | redirect |
| `/tags/:tagName` | `/me?tab=tags&name=:tagName` | redirect |
| `/watchlist` | `/me?tab=watchlist` | redirect |

**验收判据**
- 数据契约：路由表覆盖全部既有路由（`frontend/src/app/router/index.tsx` 逐条对照，无遗漏）
- 行为：13 条旧路由逐条访问返回 200 且落在预期新位置（可脚本化：`curl -sI` 或 Playwright）
- 行为：10 个导航全部可点击进入，未实现的显示"规划中"占位页，**无 404**
- 门禁：`npx tsc --noEmit` + `npm run build` 通过（`frontend/` 下）

---

## S0.3 数据源可行性复核

**范围**：做完之后，每个待接数据源都有一个明确的三态结论，不再有"应该能拿到吧"。

**交付物**：`docs/design/data-source.md` 新增章节（逐源：来源 / 频率 / 历史深度 / 三态结论 / 替代方案）。

**方法**（沿用仓库既有纪律"先实机验证再写适配器"）：每源必须**真实调用一次**并落证据，禁止仅凭文档推断。

| # | 数据 | 已确认事实 | 目标结论 |
|---|---|---|---|
| 1 | 北向资金 | 2024-05-13 取消盘中实时；**2024-08-19 取消每日净买入，改按季度披露持股** | **受限** → 走 S1.7 替代口径 |
| 2 | 陆股通持仓（香港中央结算）| 季度频率；且持仓主体绝大部分是内地资金借道，**不是"外资"** | **可得但降级** → 具名「陆股通持仓（季度）」，移入公司分析 |
| 3 | 新增散户开户数 | 口径与频率历史上多次调整（中国结算 vs 上交所口径不同）| 待定，需实机验证 |
| 4 | 公募基金仓位 | 季报披露，滞后 ~15 工作日 | **可得但不可当情绪指标** |
| 5 | 媒体平台搜索热度 | 无稳定 API，需爬虫 + 反爬 | 待定，**排在末位** |
| 6 | 7 天 OMO 利率 | 2024-07 后 MLF 地位弱化，**OMO 成为主要政策利率** | 可得，**必须补入** |
| 7 | 产业链上下游关系 | A 股无权威、免费、结构化的产业链数据 | **需自建**（人工 + 抽取，见 S5.3）|

**验收判据**
- 行为：7 项每项有「来源 URL / 实机调用记录 / 三态结论 / 替代方案」四栏，无空栏
- 数据契约：`docs/design/data-source.md` 中受限源已被显式标注，且 S1.7 的替代口径与之对应
- 门禁：`bash scripts/doc_gate.sh` 全绿

---

## S0.4 命名与口径字典

**范围**：做完之后，跨模块复用同一套 `metric_key` 命名与徽标语义，不再出现"同一个数字两种含义"。

**交付物**：`docs/design/metric-dictionary.md`（或并入 `docs/design/data-source.md` 的口径章节）。

### 4.1 徽标语义**必须区分两种**（原图上 7 个数字混用）

| 语义 | 定义 | 命名 | 图上对应 |
|---|---|---|---|
| **已接入数** | 该入口下已有真实数据可展示的条目数 | `ingested_count` | 大宗商品 29 · 外汇市场 5 · 主要指数 9 · 申万一级 31 |
| **已策展数** | 该入口下已人工策展/建成完整体验的条目数 | `curated_count` | 行业看板 3 · 行业分类 16（待 owner 复核归类）|

> **`A股市场 18`** 与展开后的子节点（5 个）及 `主要指数 9` 均对不上——归入本节复核项，**必须定一个语义或删除该徽标**，不允许以"大概是个数"存在。

### 4.2 `metric_key` 命名

- 全小写 + 下划线，**语义完整、不自造缩写**：`idx_turnover_ratio_sse`（对上证换手率）、`margin_financing_balance`、`erp_10y`（股债收益差）
- 单位与频率随 `metric_key` 在 registry 固定，**不在展示层换算**
- 与既有 `industry_metrics` 的 `metric_key` 保持同一命名空间

### 4.3 `publisher_tier`（发布主体可信度分层）

> **命名注意**：`industry_metrics.source_tier` **已被占用**，取值是 `official | highfreq | calc | manual | derived`——描述的是**指标怎么来的**。本节的 `official` 指的是**谁发布的**，两者语义不同。**同名不同义会让 `grep` 与 code review 双双失效**，故本层改用 `publisher_tier`。

| tier | 含义 | 例 |
|---|---|---|
| `legal` | 法定披露 | 上市公司公告、定期报告、问询函 |
| `official` | 官方/政府 | 国务院、部委、地方政府、监管处罚、统计局、央行 |
| `sellside` | 卖方研究 | 券商研报、盈利预测 |
| `media` | 媒体与舆情 | 财经快讯、搜索热度 |

> 资讯列表默认按 `legal > official > sellside > media` 排序或分组。

### 4.4 指标口径版本

任何上游统计口径发生修订的指标，序列上必须带 `口径版本` 与断点标记。**已知案例**：M1 于 2024-12 修订（2025-01 起纳入个人活期存款 + 非银行支付机构客户备付金）。

### 4.5 模块边界裁定（原图 4 处重叠）

| 重叠 | 裁定 |
|---|---|
| 市场 `A股涨跌分布` ↔ 情绪 `市场宽度与动量` | **口径与计算归情绪**；市场只做只读概览快照，引用情绪 API |
| 市场 `行业主力净流入` ↔ 情绪 `资金流向` | 计算归一处，按维度取数（行业级 / 全市场级）|
| `龙虎榜数据` / `大宗交易` | **资讯是原料**（`publisher_tier=official`，交易所口径），情绪可作视图 |
| 价值网 `价值分配` ↔ `利润分配流向图` | **合并为一处**（各环节利润占比可视化）|
| 交易执行 `标的分类` 批注 | 批注说的是**关注池**（经行业分析筛选、带理由），不是资产类型；拆为 `关注池` + `资产类型` |

### 4.6 「其他分类」兜底（原图的日期是**数据事实**，不是业务规则）

原图写"将 **2026年5月** 以后上市的公司放在此处"。**这个日期在代码里已经存在**：

> `backend/app/models/concept.py:3` —— *"`stocks` 名录会滞后（**实测冻结在 2026-05-08**，66 只新上市股票缺行），若以 stock_id 为键，名录滞后会静默丢掉成分股"*

**同一个日期。** owner 描述的是真实的**数据陈旧度**，不是拍脑袋的业务规则。而仓库已经用更省的办法解决了这个问题：

```
既有模式（复用）：
  业务键取 symbol（不取 stock_id）
  stock_id 降级为可空解析列
  缺失量以 unresolved_count 对外暴露（不静默丢数据）
```

**裁定**：新分类场景**复用上述模式**（业务键取稳定标识 + 外键可空 + 缺失量外露），**不建"服务 4 个消费方的通用人工裁定队列"**——那是 Wave 3+ 的事，现在建就是为不存在的需求写框架。

```
兜底条件：该实体在目标分类体系中无有效归类
处理顺序：① 可自动归类 → 自动归入
          ② 不可自动归类 → 外键留空 + 计数外露（不阻塞采集、不静默丢）
          ③ 人工覆盖（overlay 种子）优先于自动结果
```

**验收判据**
- 数据契约：`docs/design/metric-dictionary.md` 存在，§4.1–4.6 六节齐全
- 行为：`grep -rn "已接入数\|已策展数" docs/` 能查到两个术语的定义与各自归属
- 行为：`grep -rn "publisher_tier" docs/ backend/app/` 与 `grep -rn "source_tier" docs/ backend/app/` 两者语义不混（各自有明确定义）
- 行为：兜底规则以**条件表达式**形式存在，`grep -rn "2026年5月\|2026-05-08" docs/specs/` **不得**出现在业务规则语境（只能出现在引用既有代码事实的语境）
- 门禁：`bash scripts/doc_gate.sh` 全绿

---

## Wave 0 整体完成判据

- [ ] S0.1 五篇 ADR 合入并登记，`doc_gate` [1/13] [2/13] 绿
- [ ] S0.2 路由表覆盖全部既有路由，旧路由 redirect 无一遗漏
- [ ] S0.3 七个数据源全部有三态结论，无"待定"超过一项
- [ ] S0.4 徽标语义、`metric_key` 命名、`publisher_tier`、口径版本、模块边界、兜底规则六节齐备
- [ ] `bash scripts/doc_gate.sh` + `bash scripts/self_review.sh` 全绿
