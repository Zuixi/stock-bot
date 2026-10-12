---
status:      active
scope:       投研工作台的信息架构与数据模型主线——10 个一级导航、5 个横切地基（原料/视图分离 · 信号与因子【复用既有基建】 · PIT · 多态关注池 · 传导图）+ 1 个推迟（事件日历→Wave 3）、原料→视图映射、8 个 Wave 的分期路线图与 **57 个 spec** 的拆分（评审后由 59 收缩：S2 由 6 个并为 4 个）
touches:     plans/, docs/specs/, docs/design/, docs/decisions/, docs/Changelog.md, backend/app/models/, backend/app/migrations/versions/, backend/app/api/v1/, backend/app/services/, backend/app/schemas/, backend/app/workers/, backend/app/scheduler/, backend/tests/, frontend/src/app/router/, frontend/src/app/layouts/, frontend/src/pages/, frontend/src/features/, frontend/src/shared/
updated:     2026-10-12
next-action: 执行 Wave 0 —— 按 [docs/specs/S0-platform-contracts.md](../docs/specs/S0-platform-contracts.md) 落 5 篇 ADR + IA 冻结 + 数据源复核 + 口径字典（§11 四项阻塞已定案）
---

# 投研工作台：信息架构与数据模型主线 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 stock bot 从"行情数据浏览器"升级为**投研工作台**——以**申万三级行业**为工作单元、以**可溯源事件**为原料、以**对比表与信号**为交付物，形成 `原料 → 研究 → 决策 → 执行 → 复盘` 的闭环。

**本计划的输入**：10 张 owner 手绘思维导图（市场 / 资讯 / 行业分析 / 公司分析 / 宏观分析 / 情绪分析 / 价值网图谱 / 策略引擎 / 风险管理 / 交易执行）+ 6 轮对齐讨论。原始截图不在仓库内，其结构已在 §3–§6 逐条转录，是本计划的事实来源。

**规格拆分（spec）**：本计划只回答"做什么、按什么顺序"。**每个模块的契约与验收判据在 [`docs/specs/`](../docs/specs/README.md)**——共 **57 个 spec**（S0 4 + S1 14 + S2 4 + S3 7 + S4 12 + S5 4 + S6 5 + S7 7），其中 S0–S3（Wave 0 · 1 · 1.5 · 2）已写详细契约，S4–S7（Wave 3–6）为 `catalog-only`，**启动前一周才细化**（渐进明细：Wave 1 的数据事实底座没落地前，Wave 5 的因子契约必然返工）。

| 规格文件 | 对应 Wave | 状态 |
|---|---|---|
| [S0-platform-contracts.md](../docs/specs/S0-platform-contracts.md) | Wave 0 平台契约（5 ADR + IA 冻结 + 数据源复核 + 口径字典）| detailed |
| [S1-market-sentiment.md](../docs/specs/S1-market-sentiment.md) | Wave 1 市场 + 情绪（14 spec）| detailed |
| [S2-signal-engine.md](../docs/specs/S2-signal-engine.md) | Wave 1.5 信号引擎（6 spec · 拱心石）| detailed |
| [S3-content.md](../docs/specs/S3-content.md) | Wave 2 资讯地基（7 spec）| detailed |
| S4 / S5 / S6 / S7 | Wave 3 / 4 / 5 / 6 | catalog-only |

---

## 1. 现状基线（代码实测，非推断）

| 维度 | 现状 | 差距 |
|---|---|---|
| 一级导航 | 5 个 tab：市场 / 投研 / 分类 / 标签 / 自选（`frontend/src/app/layouts/MainLayout.tsx:18-22`） | 目标 10 个业务导航 + 顶栏全局工具 |
| 路由 | 15 条，扁平（`frontend/src/app/router/index.tsx`） | 目标"head bar × 左侧折叠树 × 内容区"三层 |
| 已有能力 | 市场（行情/分类/热板块/概念）、行业投研工作台（`/research`，猪智投 P1–P6 已落地） | 10 个模块里**只有 市场 + 半个行业** |
| 关注模型 | `stock_user_tags(user_id, symbol, tag_name)`（`app/models/stock.py:16`）+ `user_watchlists`/`user_watchlist_items`（`app/models/watchlist.py`）——**两套表、都硬绑 `symbol`** | 需要多态（行业/指数/概念/产业链/策略/资讯） |
| 数据形态 | **数值型**为主（行情 + 分类统计 + 行业指标 `industry_metrics`） | 新增**文本型事件 + LLM 抽取 + 字段级对比对齐** |
| 时点数据 | `Stock.asof` 存在；财务/分类的披露日与快照语义未定义 | 回测可信度前提（见 §5.3） |
| 指标基建 | `industry_metrics` 单表 + registry（代码即配置）+ `industry_signals`（规则输出历史，`basis` 即证据链）+ `stock_features`（按 `asof_date` 分区的物化因子）| **基建已经在**，缺的是把 scope 从"行业"放宽到"市场/宏观"（加 2 列），**不是缺一张新表** |

**结论**：缺的不是 7 个页面，也不是 4 张新基建表，缺的是 **3 类新数据形态**（文本事件、多态关注、时点数据）+ **把既有信号/因子基建的 scope 放宽**。

### 1.1 已存在、必须复用的资产（评审补录）

初版计划把下面这些当成"要建的东西"，实际它们已经在仓库里：

| 既有资产 | 位置 | 被误当成 |
|---|---|---|
| `industry_metrics` | `backend/app/models/industry_research.py:30` | 曾打算新建 `metric_series` |
| `industry_signals` | 同上 `:102`（注释即 *"rules engine output history"*）| 曾打算新建 `signals` |
| `cycle_engine.py` | `backend/app/services/cycle_engine.py`（*"pure functions, no I/O；阈值调整改 registry"*）| 曾打算新建规则 DSL |
| `industry_registry.py` | `backend/app/services/industry_registry.py`（代码即配置）| 曾打算新建 `signal_rules` 表 |
| `stock_features` | `backend/app/models/feature.py`（`return_percentile` / `max_drawdown` / `trend_slope`…）| 曾打算新建 `factor_defs` |
| `concept_boards` 的 `symbol` 业务键 + 可空 `stock_id` + `unresolved_count` | `backend/app/models/concept.py:3` | 曾打算新建"通用人工裁定队列" |
| `SourceBadge` / `StateWrapper` / `EChart` / `PagedResponse` | `frontend/src/shared/` · `backend/app/schemas/` | 前端徽章组件、分页响应 |

> 规则：**新 spec 动手前先 `grep` 一遍这张表。** 这张表本身就是"rung 2（仓库里已经有了？）"的清单。

---

## 2. 目标与非目标

### 做
1. **10 个一级导航**的骨架 + 三层布局（head bar / 左侧树 / 内容区），旧路由全部保留重定向。
2. **5 个横切地基 + 1 个推迟**（§5）——本计划的核心，不在 10 个导航里但被多个模块共用。**地基优先复用既有基建（`cycle_engine` / `industry_signals` / `industry_registry` / `stock_features`），新增表控制在 8 张以内。**
3. **原料/视图分离**的数据模型，使同一批原料支撑多个视图（§6）。
4. 按**依赖倒序**分期：原料 → 研究 → 决策 → 执行（§7）。
5. 数据源可信度显式化：每个指标带 `source / as_of / freq / 口径说明`（复用既有 SourceBadge 模式）。

### 不做（明确边界）
| 不做 | 理由 |
|---|---|
| **自动下单 / 券商接口对接** | 10 张图中"交易执行"只有记录/复盘/持仓/风控通知，无下单；合规与事故成本极高。**Wave 0 用 ADR 固化这条边界** |
| 全市场"一张网"的产业链大图 | 无权威数据源，边的关系量 O(n²)；改为**按行业策展**（§7 Wave 4） |
| 新建付费数据源（L4） | 沿用 [ADR 0001](./../docs/decisions/0001-single-primary-source-tushare.md) 的单一主源纪律；L4 后置单独评估 |
| 实时行情推送（level-1/tick） | 现有 `market_sentiment_intraday` 已是盘中池快照；tick 级不在本主线 |
| "恐贪指数"命名 | Fear & Greed 是 CNN 专有名称且为美股口径；A 股自建应命名「市场情绪综合指数」 |
| 引用第三方机构情绪指数的算法复算 | 算法不公开，无法复算；仅作对照，**不进信号链路** |
| **自建规则 DSL / 规则引擎** | 既有 `cycle_engine.py` 已验证"纯函数规则 + registry 阈值"；解析器是最贵的代码而当前需求数为 0。见 §5.2 |
| **为 `industry_metrics` / `industry_signals` / `stock_features` 建平行表** | 4 处复制品是初版最大的浪费。**加列复用，不建新表**（§1.1 / §5）|
| **通用多态图节点表 `graph_nodes`** | 节点已是 `stocks`/`sw_industry`/`industry_knowledge` 的行；且已声明不做全市场一张网 |
| **通用人工裁定队列（服务 4 个消费方）** | 既有 `symbol` 业务键 + 可空外键 + `unresolved_count` 模式已解决同一问题；通用化留给 Wave 3+ |
| **全球事件日历 `/calendar`** | Wave 1 零消费方，推迟到 Wave 3（§5.6）|
| 预先发明权限 scope（`strategy:write` 等）| 三个写侧模块尚不存在，先全部"登录即可" |

---

## 3. 信息架构：10 个一级导航

### 3.1 分区（按"用户是读者还是决策者"）

| 分区 | 导航 | 用户角色 | 数据操作 |
|---|---|---|---|
| **研究层（只读）** | 市场 · 资讯 · 行业分析 · 公司分析 · 宏观分析 · 情绪分析 · 价值网图谱 | 读者 | 用户只读，系统写 |
| **决策层（写）** | 策略引擎 · 风险管理 | 决策者 | 系统产出信号；**风控产生动作** |
| **执行层（写）** | 交易执行 | 记录者 | **用户写**（交易记录 / 复盘） |

> 这是产品最重要的分界线：前 7 个是"看"，后 3 个是"做"。权限模型随之分层（沿用 [ADR 0008](../docs/decisions/0008-research-read-requires-permission.md) 的 router 级权限依赖做法）。

### 3.2 布局

```
┌─ head bar ─────────────────────────────────────────────────────────────┐
│ 市场 资讯 行业分析 公司分析 宏观分析 情绪分析 价值网图谱                  │
│ 策略引擎 风险管理 交易执行            🔍全局搜索  ★自选  📅日历  👤我的   │
└────────────────────────────────────────────────────────────────────────┘
        一级导航（10，纯净）              全局工具（横切，不进导航）
                                                     │
                                              /me 我的工作台
                                              /calendar 事件日历
```

**决策（owner 已拍板）**：自选/标签**不占一级导航位**，落为"顶栏全局工具 + `/me` 工作台"。

### 3.3 导航 × 子模块 × 依赖（10 张图的完整转录）

| # | 导航 | 子模块数 | 关键子模块 | 依赖的原料 |
|---|---|---|---|---|
| 1 | **市场** | 5 + 7 榜单 | 全球市场（美/港/其他）、大宗 29、外汇 5、A股市场 18、行业分类（申万 31→二级→三级→股票）、A股排行 7 榜 | 行情、分类、榜单计算 |
| 2 | **资讯** | 4 类 | 上市公司公告、政府官媒政策、研究资讯、市场资讯 | **文本原料源头** |
| 3 | **行业分析** | 3 | 行业看板（申万三级策展）、行业报告（Top5 财报 + 有效研报 → **对比表**）、行业比较（景气/估值分位/资金/涨跌） | 资讯 + 财务 + 行情 |
| 4 | **公司分析** | 9 卡 | 基本信息、**风险红线**、财务数据、商业模式、竞争力、估值、管理层、股东股权、事件驱动 | 4 卡定量自给，5 卡依赖资讯 |
| 5 | **宏观分析** | 7 | 顶层政策、货币政策、财政政策、产业政策、监管政策、政策日历、政策影响追踪 | 宏观数据 + 资讯（政策） |
| 6 | **情绪分析** | 10 | 交易活跃度、杠杆资金、投资者行为、市场宽度与动量、波动率与衍生品、资金流向、估值与风险溢价、散户舆情、综合情绪指数、**情绪预警与预判** | **纯数值，零资讯依赖** |
| 7 | **价值网图谱** | 3 | 供应/产业链（图谱·价值分配·**传导关系及时间**·议价权）、上市公司控股关系、产业链地图（上中下游 + 利润流向 + 替代品互补品） | 分类 + 公告 + 研报抽取 + 工商 |
| 8 | **策略引擎** | 5 | 选股（价值/成长/质量/多因子/事件驱动）、择时（技术/宏观/情绪）、信号系统（**置信度分级→仓位**）、组合构建、策略回测 | **消费以上全部** |
| 9 | **风险管理** | 4 | 个股风控、组合风控（VaR/CVaR/Beta/暴露）、系统性风险（大盘信号/流动性/黑天鹅/预案）、**风控规则引擎**（自动止损·仓位调整·风险预算·**日志与审计**） | **持仓数据**（来自交易执行）+ 情绪/宏观/资讯 |
| 10 | **交易执行** | 3 | **标的分类**（经行业分析筛出的长期追踪池）、交易日志（记录·复盘·**盈亏归因**·统计）、持仓管理 | 行情 + 行业分析 |

### 3.4 三处必须纠正的设计问题（写入 Wave 0）

| 问题 | 位置 | 处理 |
|---|---|---|
| `纳斯达克综合指数` 与 `纳斯达克100` 都标 NDX | 市场/美国市场 | 综合指数应为 **IXIC**；统一"名称 + 数据源代码"格式，避免串号 |
| 北向资金被 **2 处**依赖，而每日数据已取消披露 | 市场/外资画像 + 情绪/资金流向 | 改口径（§8.1），并按 [ADR 0004](../docs/decisions/0004-current-snapshot-not-backfilled.md) 的"快照型"纪律标注频率 |
| `标的分类` 节点名与批注不符 | 交易执行 | 批注说的是"经行业分析后具备长期追踪价值的**标的池**"——这是**关注池**，不是资产类型分类；拆为 `关注池` + `资产类型` |

---

## 4. 闭环：产品真正的形态

```
原料：行情 · 资讯/文本 · 财务 · 分类(申万) · 宏观/情绪数据 · 关系(股权/产业链)
   ↓
研究：市场 · 资讯 · 行业 · 公司 · 宏观 · 情绪 · 价值网
   ↓
决策：策略引擎（选股→择时→信号→组合→回测）→ 候选标的
   ↓
★ 关注池（经行业分析筛选、长期追踪、带来源与理由）
   ↓
执行：交易执行（记录 → 复盘 → 归因 → 改进）
   ↓
持仓数据
   ↓
风控：风险管理（个股 → 组合 → 系统性，三层 + 规则引擎）
   ↓ 反馈
策略引擎（参数优化） / 关注池（调整）
```

**两条回路是产品成败所在**（也是最容易被做成死路的地方）：
1. **研究 → 决策回路**：研究结论必须能落成候选池/信号，否则研究白做。
2. **执行 → 研究回路**：复盘/归因必须能反过来改进策略与选股，否则产品只是日记本。

> owner 图上的批注反复在说这件事：策略引擎"将前面所有模块的数据和分析转化为可执行的买卖信号"；交易执行"需要完整的「记录→复盘→归因→改进」闭环……复盘是提高交易成功率最有效的方法"。

---

## 5. 横切地基（**5 个真地基 + 1 个推迟**）

> **本节是本计划的核心。** 这些东西在 10 张图里反复出现，却被分别画在某个模块之下。若各模块自行实现，会写 N 遍代码且口径不一致——**而回测口径不一致会直接摧毁产品可信度**。

**评审修正（2026-10-12）**：初版列了 6 个地基、约 16 张新表，**其中 4 张是既有表的复制品，还引入了一个规则 DSL**。按"先爬梯子"重审后：

| 地基 | 原方案 | 修正后 | 依据 |
|---|---|---|---|
| F1 原料/视图分离 | 4 张新表 | **保留**（4 张新表，`content_metrics` 与 JSONB 二选一）| 10 张图 6 处自证 |
| F2 信号·因子·回测·触达 | 7 张新表 + **规则 DSL** | **缩**：`industry_signals` 加 4 列 · `industry_metrics` 加 2 列 · 因子复用 `stock_features` 物化模式 · **删 DSL** · **删 action 层** | 仓库已有 `cycle_engine.py`（纯函数规则）+ `industry_registry`（阈值配置）+ `industry_signals`（规则输出历史，`basis` 即证据链）|
| F3 PIT 时点数据 | 加列 + SCD2 | **保留**（真地基，回测可信度的唯一来源）| — |
| F4 多态关注池与标签 | 2 表取代既有 2 表 | **保留**（净减）| owner 拍板方案 A |
| ~~F5 事件日历~~ | `events` 表 + `/calendar` | **推迟到 Wave 3**（Wave 1 零消费方）| YAGNI：把没消费者的东西列成"地基"= 发免死金牌 |
| F5 传导图（原 F6）| `graph_nodes` + `graph_edges` | **缩**：删 `graph_nodes`，只留 `edges` | 节点已是 `stocks`/`sw_industry`/`industry_knowledge` 的行 |

**净效果**：新增表 **16 → 8**；`industry_metrics` +2 列、`industry_signals` +4 列；**无规则 DSL、无 action 层**。

### 5.1 F1 原料/视图分离

**证据（10 张图里 6 处自证）**：

| 原料 | 视图 1 | 视图 2 | 视图 3 |
|---|---|---|---|
| 政策文本 | 资讯·政府官媒政策 | 宏观·产业/监管政策 | 宏观·政策影响追踪 |
| 公告 | 资讯·上市公司公告 | 公司·事件驱动 / 风险红线 | 情绪·产业资本增减持 |
| 研报 | 资讯·研究资讯 | 行业·有效研报 | 宏观·政策预期差基准 · 价值网·产业链边 |
| 行情 | 市场 | 情绪（成交额/换手率） | 公司（K 线） |
| 财务 | 公司 | 行业（对比表） | 策略（价值/成长因子）· 价值网（价值分配） |
| 两融 | 情绪·杠杆资金 | 策略·资金因子 | — |

**数据模型**（4 张新表，契约见 [S3.1](../docs/specs/S3-content.md#s31-内容事实层四表)）：

- `content_items` — 统一内容/事件事实层
  `id, content_type(announcement|report|policy|news|association|third_party|inquiry), source_name, publisher_tier(legal|official|sellside|media), title, published_at, ingested_at, url, raw_hash, body_ref, extracted JSONB, extract_version, extract_confidence, review_status`
- `content_entities` — 内容 ↔ 实体（`content_id, entity_type, entity_id`）
- `content_metrics` — **从内容抽取的规范化指标**（对比表的原料，带 `source_span` 原文定位）
- `user_content_state` — 每用户 × 内容的状态机——状态是**用户维度**，不能写在 `content_items` 上

> **`publisher_tier` 而非 `source_tier`**：`industry_metrics.source_tier` 已被占用（`official|highfreq|calc|manual|derived`，描述**指标怎么来的**），本节描述**谁发布的**。同名不同义会让 `grep` 与 review 双双失效。
> **`extracted JSONB` 与 `content_metrics` 不重复存事实**：JSONB 只留 LLM 原始输出（供重抽/展示），规范化事实只进 `content_metrics`。

### 5.2 F2 指标 · 因子 · 信号 · 回测 · 触达（**复用既有基建**）

**证据（7 处同构）**：

| 模块 | 指标 | 规则 | 产出 |
|---|---|---|---|
| 情绪分析 | 换手率/两融/涨跌停… | 分位 >90% 或 <10% | 情绪预警 |
| 情绪分析 | 全指标池 | 领先性筛选（IC / 事件研究） | 领先指标 |
| 宏观分析 | PMI/CPI/M2/LPR… | 预期差（超预期/不及预期） | 政策预期差 |
| 宏观分析 | 历史同类政策 | 事件后板块涨跌幅 | 政策回测 |
| 公司分析 | 财务 / 公告 | **风险红线**命中 | 排雷预警 |
| 策略引擎 | 基本面+技术+情绪+资金 | 评分阈值 + **置信度分级** | 买卖信号 |
| 风险管理 | 持仓 / 暴露 / Beta | 止损 / 仓位调整 | 通知 + 待办 |

**管线的正确形态（复用优先）**：

```
industry_metrics（既有统一指标表，+published_at/+caliber_version）
  → 因子：复用 stock_features 的物化模式 + 纯函数；不建 factor_defs
  → 规则：复用 cycle_engine.py 的形态（纯函数 + registry 阈值）；不建 DSL
  → 信号：复用 industry_signals（+rule_key/+rule_version/+confidence/+severity）；basis 即证据链
      └→ 提醒：alert_subscriptions + alerts        ← 前 7 个模块到此为止
  ↘ 回测：backtest_runs（1 表，最小版）；无 backtest_events（读时算）
```

**为什么删 DSL**：`cycle_engine.py` 的模块注释已经写明 *"纯函数规则引擎，no I/O；规则可读性优先；阈值调整应改 industry_registry 的配置而非本文件逻辑"*——**这个模式在本仓库已验证**。而文本表达式 + 解析器 + 求值器是最贵的一类代码（错一次即静默算错），当前需求数为 **0**。

**为什么删 `action` 层**：ADR-D 已裁定不下单 → 所谓"动作"落到实现上就是**通知 + 一条待办**，与 `alert` 同一条路径。**不为此建一个只有一个实现的接口。**

**契约见 [S2](../docs/specs/S2-signal-engine.md)。** 表规模：**新增 3 张**（`backtest_runs` / `alert_subscriptions` / `alerts`）。

### 5.3 F3 PIT 时点数据

**为什么是地基**：`策略回测`、`情绪-收益历史回测`、`历史同类政策回测`、`风控有效性审计` 全部依赖它。**没有 PIT，回测会系统性偏高，而回测是这个产品唯一能自证价值的地方。**

| 陷阱 | 要求 |
|---|---|
| 用**报告期**而非**实际披露日**对齐财报 | 财务表加 `announced_at`（实际披露日）|
| 申万分类调整不留快照 | 分类归属改 SCD2（`effective_from / effective_to`）|
| 未处理退市股 | 证券主表保留退市标记与退市日（幸存者偏差）|
| 未处理复权因子 / 停牌 | 行情表显式带复权口径与停牌标记（既有 `daily_quotes` 需核对）|
| 参数优化无样本外验证 | 回测强制样本切分；walk-forward 留 Wave 5 |
| **口径修订**（M1 2024-12 修订、MLF 政策利率地位变化）| 指标带 `caliber_version`，历史序列断点显式标注 |

> `Stock.asof` 是好的开端；需要扩到财务、分类、宏观指标。

**两条 PIT 机械保证（全案最值钱的产出，见 [S2](../docs/specs/S2-signal-engine.md)）**
1. **信号护栏**：证据链里每个指标引用的 `published_at` 必须非空且 `≤ effective_date`，违反则信号不生成并告警——**绝不静默回退到 `period`**
2. **回测对照实验**：同一规则分别用「披露日」与「报告期」对齐，统计结果**必须产生显著差异**；差异为 0 即证明 PIT 未生效

### 5.4 F4 多态关注池与标签

**owner 已拍板方案 A：合并成一套多态模型。**

```
tag（标签定义，系统 + 用户同池）
  id, owner_user_id(可空=系统标签), name, kind(system|user),
  dimension(industry|concept|chain|strategy|watchlist|custom),
  parent_id(支持层级), color, sort_order, source_ref(回指申万 code / 概念 code)

user_target_tag（用户 × 对象 × 标签）
  user_id, target_type, target_id, tag_id,
  stage(watch|candidate|position|closed),   ← 关注池的状态
  source_module, reason,                     ← 「经行业分析后具备长期追踪价值」的来源与理由
  sort_order, notes, created_at
  UNIQUE(user_id, target_type, target_id, tag_id)

target_type ∈ {stock, industry, index, concept, chain_node, strategy, signal, content}
```

- **`自选` = `dimension='watchlist'` 的一个系统 tag**；多命名列表 = 该 tag 树的子节点。**两张表收敛为一张**（净减 1）。
- 同时服务：`/me·我的自选`、`交易执行·关注池`、`策略引擎·候选`、`资讯·我关注的`——四个视图一张表。

**迁移与兼容**（Wave 0 定 ADR）：
- `stock_user_tags` → `user_target_tag`（`target_type='stock'`）
- `user_watchlists` → `tag`（`dimension='watchlist'`）+ item → `user_target_tag`
- **前端 `useWatchlist` 的未登录本地存储模式必须同步改 key 结构**，否则登录前后的关注会分裂

### 5.5 F5 传导图（产业链 + 政策共用）

**证据（两处是同一个东西）**：
- 价值网图谱：`供应/产业链 → 传导关系及时间`
- 宏观分析：`政策传导路径与时效（政策→资金→行业→个股的传导链和延迟）`

**两个不同起点、同一条链**：价值网起点是**产业**（成本/价格传导），宏观起点是**政策**。

```sql
-- 只此一张表。节点已是 stocks / sw_industry / industry_knowledge 的行，
-- 多态 graph_nodes 是纯仪式（且 §2 已声明"不做全市场一张网"，更不需要通用图模型）。
graph_edges(
  from_type, from_id, to_type, to_id,
  edge_type(supply|equity|substitute|complement|policy_transmission),
  weight, lag_days,             -- 「传导关系及**时间**」
  confidence, source_ref, review_status(auto|pending|confirmed)
)
```

- `lag_days`（时滞）与 `confidence` 是必须字段——**没有时滞的传导图只是画着好看**
- 数据来源优先级：财报前五大客户/供应商 > LLM 从研报/招股书抽取 + 人工确认 > 申万三级粗粒度 > 工商数据（控股/参股）
- `review_status` 复用 F1 的 overlay 种子模式（[ADR 0005](../docs/decisions/0005-overlay-seed-files.md)）

### 5.6 ~~F5 事件日历~~ → 推迟到 Wave 3

**原方案**：`events` 表（会议/数据发布/财报/解禁/交割/政策到期）+ 全局 `/calendar` 路由，列为"地基"。

**为什么推迟**：**Wave 1 零消费方。** 最近的消费方是 Wave 3 的「政策到期提醒」（宏观）与「财报披露日」（公司）。把没有当期消费者的东西列成"地基"，等于给它发免死金牌——它会被优先建，然后空转两个 Wave。

**做法**：降到 Wave 3，与宏观 `政策日历` 一起落地（见 [S4 目录](../docs/specs/README.md#s4-行业--公司--宏观wave-3--catalog-only)）。届时若确认有 ≥2 个模块需要同一个事件轴，再升格为全局能力。

---

## 6. 原料 → 视图 映射（数据模型骨架）

| 原料表 | 写入方 | 视图（读） |
|---|---|---|
| `daily_quotes` 等行情 | 既有采集轨道 | 市场、情绪、公司、策略、风控 |
| `industry_metrics`（既有，+2 列） | 各采集器 / 计算器 | 情绪 10 组、宏观指标、行业指标、策略因子 |
| `content_items` + `content_metrics` | 资讯采集 + LLM 抽取 | 资讯、行业报告对比表、公司 5 卡、宏观政策、价值网边 |
| 财务（+`announced_at`） | 既有 | 公司、行业对比表、策略因子、价值网价值分配 |
| 分类 SCD2 | 既有 + 快照 | 全部 |
| `graph_edges` | 人工 + LLM 抽取 | 价值网图谱、宏观政策传导 |
| `events` | 各模块注册 | 全局日历、提醒、策略事件驱动 |
| `tag` / `user_target_tag` | 用户 | `/me`、交易执行关注池、各模块的"我的"过滤 |
| `signals` / `alerts` | F2 引擎 | 情绪预警、宏观预期差、公司红线、策略信号、风控动作 |

---

## 7. 分期路线图（依赖倒序）

> **顺序原则**：策略引擎自己的批注声明"消费前面所有模块"→ 实施必须倒着排。**先原料，再研究，最后决策。** 每 Wave 独立可交付、可验收、可停在安全点。

### Wave 0 — 契约与决策固化（无业务代码）

> **契约见 [docs/specs/S0-platform-contracts.md](../docs/specs/S0-platform-contracts.md)**（S0.1–S0.4 含每篇 ADR 的背景/决策/影响/备选）。本节的勾选项与 spec 一一对应。

**What to build**：把本计划的 6 个地基与 3 处纠错固化成 ADR；冻结 IA 与路由表；拍板 §11 的阻塞项（**已定案，见 §11**）。

- [ ] T0.1 新 ADR：**原料/视图分离**（`content_items` 事实层 vs 视图层；`status` 归用户维度）
- [ ] T0.2 新 ADR：**多态关注与标签模型**（`stock_user_tags` + `user_watchlists` → `tag` + `user_target_tag`；含本地存储 key 迁移）
- [ ] T0.3 新 ADR：**PIT 时点数据契约**（披露日 / 分类 SCD2 / 退市股 / 复权口径 / 指标口径版本）
- [ ] T0.4 新 ADR：**不做自动下单**（产品边界声明，阻止反复讨论）
- [ ] T0.5 新 ADR：**复用既有信号基建，不自建引擎**（规则=纯函数、阈值进 registry、信号复用 `industry_signals`、因子复用 `stock_features`；**不引入 DSL、不建新表**；`industry_metrics` **只加 2 列**）
- [ ] T0.6 把 §11 的**已定案四项**（徽标口径 / 兜底规则 / 外资画像换口径 / 模块边界）落成 `docs/design/metric-dictionary.md`
- [ ] T0.7 **IA 冻结**：10 导航 × 子模块 × 路由表写入 `docs/design/`；旧路由 redirect 映射表（`/market`、`/research`、`/tags`、`/tags/:tagName`、`/watchlist` 一个都不能直接删）
- [ ] T0.8 数据源可行性复核：把 §8 的风险逐条落到"可得 / 受限 / 需自建"三态结论

**Acceptance criteria**
- [ ] 5 篇 ADR 合入并登记 `docs/decisions/index.md`（编号连续，`doc_gate` [2/13] 绿）
- [ ] `docs/design/` 下 IA 与路由表评审通过，无"待定"残留（§11 已全部定案）
- [ ] **不新增任何表**的 ADR 已生效：`grep -rn "metric_series\|factor_defs\|signal_rules" docs/decisions/ docs/design/` 无命中（除明确的"已撤销"记录）
- [ ] `bash scripts/doc_gate.sh` 全绿

### Wave 1 — 市场 + 情绪（并行，**不依赖资讯**）

**为什么并行**：两者共用大量数据（成交额 / 换手率 / 涨跌家数 / 两融 / 涨跌停）；且**情绪分析是全项目唯一"纯数值、零资讯依赖"的分析模块**——它是策略引擎"情绪择时"与"四维评分中情绪因子"的**唯一来源**。

- [ ] T1.1 导航骨架重构：head bar 10 导航 + 顶栏全局工具（搜索 / 自选 / 我的）+ `/me` 骨架 + 旧路由全部 redirect（**不含 `/calendar`**，事件日历推迟到 Wave 3）
- [ ] T1.2 `industry_metrics` **加 2 列**（`published_at` / `caliber_version`）+ 采集/计算写路径 + 统一读 API（带 `source / as_of / freq / 口径`）——**不建 `metric_series`**
- [ ] T1.3 市场：主要指数（9）、个股+板块热力图（treemap，**对标 iFinD 的高信息密度**）、涨跌分布（作为情绪的只读快照）、行业主力净流入、**外资画像改口径**、A 股排行 7 榜
- [ ] T1.4 情绪（先做 5 组高性价比）：波动率与衍生品（期权波动率 / 期指升贴水）、市场宽度与动量（涨跌家数 / 涨跌停 / 20 日线占比 / 炸板率 / **补：连板高度 · 首板晋级率**）、杠杆资金、交易活跃度、估值与风险溢价（股债收益差）
- [ ] T1.5 情绪（后 5 组）：投资者行为、资金流向（改口径）、散户舆情、综合情绪指数（**自建 composite，不叫"恐贪"**）、情绪预警与预判（依赖 F2 → 落 Wave 1.5 之后）
- [ ] T1.6 数据源替代落地：北向资金退场 + ETF 份额变化 / 两融 / 产业资本增减持 / 南向 四件套上线

**Acceptance criteria**
- [ ] 10 个一级导航可点击进入（后 8 个为占位页并显示"规划中"），无 404、无死链
- [ ] 旧路由 5 条全部 301/redirect 到新位置，`/tags/:tagName` 与 `/watchlist` 的既有书签不失效
- [ ] 市场与情绪的所有指标带 `source / as_of` 徽章，口径说明可点开
- [ ] 情绪 5 组指标有 ≥3 年历史序列，分位计算与 UI 一致
- [ ] **不含任何"北向资金每日净买入"字段**（`grep` 可验），替代口径已在 UI 标注频率

### Wave 1.5 — 信号 · 因子 · 回测 · 提醒（F2，**复用既有基建，4 个 spec**）

> 契约见 [docs/specs/S2-signal-engine.md](../docs/specs/S2-signal-engine.md)。**本 Wave 只交付骨架 + 打通第一个消费方**（情绪分位越界 → 站内提醒），其余按消费方落地顺序增长。

- [ ] T1.5.1 因子：复用 `stock_features` 物化模式 + 纯函数（**不建 `factor_defs` 表**）
- [ ] T1.5.2 规则与信号：规则为纯函数（与 `cycle_engine.py` 同构，阈值进 registry）+ `industry_signals` **加 4 列**（`rule_key` / `rule_version` / `confidence` / `severity`）（**删 DSL、不建 `signal_rules`/`signals` 表**）
- [ ] T1.5.3 事件研究回测（**最小版**：`backtest_runs` 一表，无 `backtest_events`；PIT 强制 + 样本切分强制；参数优化/walk-forward 留 Wave 5）
- [ ] T1.5.4 `alert_subscriptions` + `alerts` + 站内触达（订阅管理 UI 推迟到消费方 #2）

**Acceptance criteria**
- [ ] **新增表 = 3 张**；`industry_signals` 加 4 列、`industry_metrics` 加 2 列；**无 `factor_defs` / `signal_rules` / `signals` / `backtest_events` / action 表**
- [ ] **无规则 DSL**：`grep -rn "eval(\|ast.parse" backend/app/services/signal_rules.py` 无命中；阈值全部来自 registry（改阈值不改代码）
- [ ] 情绪"历史分位 >90% / <10% 触发预警"端到端跑通：规则 → 信号 → 提醒 → 站内可查
- [ ] 每个信号可展开证据链（`basis`），含来源与 `published_at`
- [ ] **PIT 护栏测试 + PIT 对照实验双双通过**（构造"用报告期而非披露日"的对照，结果必须有显著差异 —— 证明 PIT 生效）
- [ ] `bash scripts/bench.sh` + `bash scripts/self_review.sh --full` 全绿

### Wave 2 — 资讯（地基）

**为什么单独一个 Wave**：资讯是 行业报告 / 公司 5 卡 / 宏观政策 / 事件驱动选股 / 价值网边抽取 的共同上游。**不做它，Wave 3 起全部落空。**

- [ ] T2.1 `content_items` + `content_entities` + `content_metrics` + `user_content_state` 建表
- [ ] T2.2 首批源（挑最可得的两个）：**上市公司公告**（cninfo）+ **财经快讯**
- [ ] T2.3 LLM 抽取管线：定期报告 → `主营业务收入 / 利润`；研报 → `核心观点`；带 `source_span` 原文定位
- [ ] T2.4 人工裁定队列（`review_status`）+ overlay 种子（沿用 [ADR 0005](../docs/decisions/0005-overlay-seed-files.md)）
- [ ] T2.5 资讯列表页（4 类 × 子类）+ 状态机 + 来源可溯（原文链接 / 发布主体 / 抓取时间）
- [ ] T2.6 "有效研报"判定规则（去重 / 行业相关 / 有数据支撑）——**必须显式定义并可人工推翻**

**Acceptance criteria**
- [ ] 公告与快讯可采集、可去重（`raw_hash`）、可增量（重跑幂等）
- [ ] 定期报告抽取出的 `主营业务收入 / 利润` 与财报原始数字一致率可测（抽样 ≥30 条人工核对）
- [ ] 每条内容都能点回原文 URL，且显示发布主体与抓取时间
- [ ] 状态机（未读/已读/已处理/忽略）在多用户下互不干扰
- [ ] 人工裁定队列可操作，裁定结果优先于自动结果

### Wave 3 — 行业分析 + 公司分析 + 宏观分析

- [ ] T3.1 行业看板（申万三级**策展**）+ 行业报告模板：Top5 公司定期报告 + 有效研报 → **对比表**（字段级横向对齐）
- [ ] T3.2 行业比较：景气度排名 / 估值分位对比 / 资金流向排行 / 涨跌幅对比（**"分位"要求历史回填深度**）
- [ ] T3.3 公司分析 9 卡：先 4 卡定量（基本信息 / 财务数据 / 估值 / 股东股权）+ **风险红线规则化**（可溯源到公告原文）；后 5 卡（商业模式 / 竞争力 / 管理层 / 事件驱动）依赖资讯
- [ ] T3.4 宏观（**不依赖资讯**的部分）：**新建成体系的经济指标树**（PMI / CPI / PPI / GDP / 社融 / 进出口 / 工业增加值 / 固投 / 社零）、货币供应与资金利率、财政政策、**海外与外部流动性**（美联储 / 美债 10Y / 美元指数 / 中美利差 / 汇率）
- [ ] T3.5 宏观（依赖资讯）：顶层政策 / 产业政策 / 监管政策视图、政策日历（→ 归 F5 全局日历）、政策影响追踪（预期差 / 历史同类回测 / 传导路径 → F6）
- [ ] T3.6 `其他分类` 兜底 + **人工分类机制**（次新股等未分类实体）；推广为通用"待人工裁定队列"

**Acceptance criteria**
- [ ] 行业报告页输出的是**对比表**（同字段同期多主体），不是列表
- [ ] 风险红线命中项 100% 可点回公告原文；每条红线有明确口径与命中理由
- [ ] 宏观指标树覆盖 ≥9 个核心指标，每个带`发布日 / 口径版本`；M1 断点显式标注
- [ ] "其他分类"兜底可自动归入 + 人工可覆盖，且人工结果持久优先
- [ ] 政策传导路径与价值网"传导关系及时间"**共用同一张 `graph_edges`**

### Wave 4 — 价值网图谱

- [ ] T4.1 `graph_edges` 建表（**无 `graph_nodes`**；含 `lag_days` / `confidence` / `review_status`）
- [ ] T4.2 传导图基础设施（产业链 + 政策共用）
- [ ] T4.3 **首批 3–5 个行业策展建图**（生猪养殖优先——链条短、环节清晰、猪智投已有基础）
- [ ] T4.4 产业链边抽取：财报前五大客户/供应商 → LLM 从研报/招股书抽取 → 人工确认
- [ ] T4.5 价值分配 / 利润分配流向（**合并为一处**）+ 替代品与互补品（跨产业链边）

**Acceptance criteria**
- [ ] 至少 3 个行业有可用传导图，边的来源与置信度可见
- [ ] 产业链上下游关系有 ≥2 个独立证据源（财报 + 抽取）才标 `confirmed`
- [ ] `传导关系及时间` 的 `lag_days` 有历史数据支撑（不是人工拍值），无支撑的标为 `pending`
- [ ] **明确不做全市场一张网**（已在 §2 非目标）

### Wave 5 — 策略引擎

- [ ] T5.1 选股：把"价值 / 成长 / 质量"翻译为因子组合（**"护城河"必须先定义代理指标**：ROIC 持续 >WACC 年数 / 毛利率稳定性 / 市占率趋势 / 单位成本优势）
- [ ] T5.2 择时：技术（趋势 / 均值回归 / 指标）+ 宏观（货币信用周期 / 美林时钟）+ 情绪（来自 Wave 1）
- [ ] T5.3 信号系统：四维评分（基本面+技术+情绪+资金）+ **置信度分级（高/中/低 → 不同仓位）**
- [ ] T5.4 组合构建：行业配置权重 + 个股仓位分配 + **消费风险预算**（由 Wave 6 定义，接口先约定）
- [ ] T5.5 回测：历史回测引擎（**PIT 强制**）+ 参数优化（walk-forward）+ 绩效评估（夏普 / 最大回撤 / 胜率）；蒙特卡洛与相关性优化**延后**

**Acceptance criteria**
- [ ] 每个选股策略可展开为具体因子与阈值（无"护城河"这类不可计算项裸露在 UI）
- [ ] 买入信号 = 评分 + 阈值；卖出信号 = 条件或（止盈 / 止损 / 趋势破坏 / 基本面恶化）——**不对称是有意的**
- [ ] 置信度分级真实影响建议仓位
- [ ] 回测结果附带 PIT 声明与样本切分；未过 walk-forward 的参数优化结果不展示
- [ ] 回测的"假收益"回归测试：故意注入报告期对齐（而非披露日）→ 结果应变化（证明 PIT 有效）

### Wave 6 — 交易执行 → 风险管理（**Wave 内部也倒序**）

> **导航顺序是"风险管理 → 交易执行"，但数据流是"交易执行（持仓）→ 风险管理（组合风控）"**。个股风控规则不依赖持仓，可先行。

- [ ] T6.1 关注池（F4 落地）：`标的分类`（资产类型）+ `关注池`（经行业分析筛选、带 `source_module` / `reason` / `stage`）
- [ ] T6.2 交易记录 + 持仓（`实时持仓与盈亏` / `资金使用率`）
- [ ] T6.3 交易日志闭环：记录 → **复盘（每笔逻辑回顾与评分）** → **盈亏归因**（需先定归因模型：Brinson / 因子 / 时间）→ 统计（胜率 / 盈亏比 / 平均持仓天数）
- [ ] T6.4 个股风控：止损规则（固定比例 / 技术位 / 波动率）、个股仓位上限、集中度限制
- [ ] T6.5 组合风控（**依赖 T6.2 持仓**）：最大回撤控制线、VaR / CVaR、行业暴露、Beta 暴露
- [ ] T6.6 系统性风险：大盘风险信号（← 情绪）、流动性危机预警（← 宏观+情绪）、黑天鹅监控（← 资讯）、极端行情应对预案（playbook）
- [ ] T6.7 风控规则引擎 + **风控日志与审计（append-only，不可篡改）**

**Acceptance criteria**
- [ ] 关注池的来源与理由在 UI 可见（"为什么关注它"），`stage` 可流转
- [ ] 交易记录的持仓可驱动风控计算（无手工重复录入）
- [ ] 盈亏归因有明确模型与基准（沪深300 / 行业指数），归因结果可解释
- [ ] 风控动作（含"应触发但未触发"）全部留痕，日志可审计且不可改
- [ ] **无任何下单/券商接口**（`grep` 可验；ADR 已固化边界）

---

## 8. 数据源风险与替代口径

### 8.1 已确认受限（必须换，阻塞 Wave 1）

| 数据 | 事实 | 影响 | 替代 |
|---|---|---|---|
| **北向资金** | 2024-05-13 起盘中实时成交额/额度取消；**2024-08-19 起每日成交额/净买入取消，改按季度披露持股**（[上海金融局](https://jrj.sh.gov.cn/SC212/20240819/7454806b9909434ea8309f8b566177fd.html)、[证券时报](https://www.stcn.com/article/detail/1203876.html)）| **市场/外资画像 + 情绪/资金流向 两处依赖** | **ETF 份额变化**（宽基 ETF 是"国家队"唯一可见痕迹，日频公开）+ 两融 + 产业资本增减持 + 南向 |
| 「香港中央结算持仓」当"外资"看 | 其中绝大部分是内地资金借道香港 | 方法论错误 | 改名「陆股通持仓（季度）」并降级，或退场 |

### 8.2 需复核（Wave 0 T0.8）

| 数据 | 风险 | 处理 |
|---|---|---|
| 新增散户开户数 | 口径与频率历史上多次调整（中国结算 vs 上交所口径不同） | 必须先实机验证再写适配器（沿用既有"先验证再写"纪律） |
| 公募基金仓位 | 季报披露，滞后 ~15 工作日 | 只能作背景，**不能当情绪指标用**；改用新成立基金份额 / ETF 份额 |
| 各媒体平台股市搜索热度 | 无稳定 API，需爬虫 + 反爬 | 差异化亮点，但**排在最后**；不做承诺 |
| M1 序列 | **2024-12 口径修订**：纳入个人活期存款 + 非银支付机构客户备付金，2025-01 起按新口径（[央行公告 2024 年第 17 号](https://www.gov.cn/zhengce/zhengceku/202412/content_6990650.htm)） | 历史断点显式标注，`口径版本`入表 |
| 政策利率锚 | 2024-07 起 MLF 改利率招标、地位弱化；**7 天 OMO 成为主要政策利率** | **补 7 天 OMO**；MLF 降级为"中期资金价格"。图上的 `资金利率` 缺 OMO |
| 央行新工具 | 买断式逆回购（2024-10）、国债买卖（2024-08） | `公开市场操作` 不能只写逆回购 |
| 产业链上下游 | **A 股无权威、免费、结构化的产业链数据** | 按行业策展 + 多源交叉（财报客户/供应商 > LLM 抽取 > 申万粗粒度 > 工商） |

---

## 9. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| **范围过大** | 10 模块 × 数十子模块，容易烂尾 | 8 Wave 分期，每 Wave 独立可交付、可停在安全点；Wave 0 先冻结 IA |
| **PIT 缺失导致假回测** | 摧毁产品唯一可信度来源 | PIT 列为 F3 地基，Wave 0 定 ADR，Wave 1 建表时就留字段 |
| **资讯数据成本最高** | Wave 3 起全部空转 | Wave 2 单独立项；首批只做最可得的 2 个源；"有效研报"规则显式化 |
| **信号逻辑被复制 N 遍** | 口径不一致 + 维护爆炸 | F2 提前到 Wave 1.5，**但复用既有 `cycle_engine` + `industry_signals`**（而不是自建一套）；ADR-E 固化"规则=纯函数、阈值进 registry、禁止自建" |
| **关注模型迁移破坏既有数据** | 用户自选/标签丢失 | Wave 0 定迁移 ADR + 本地存储 key 迁移；旧路由保留 redirect |
| **产业链无权威源** | 价值网做成"好看的草图" | 按行业策展；边必须有 ≥2 证据源才 `confirmed`；`lag_days` 无数据支撑标 `pending` |
| **自建信号引擎（DSL + 7 张新表）** | 与既有 `cycle_engine`/`industry_signals`/`stock_features` 平行，回测口径分裂 | §5.2 已缩为复用既有基建；ADR-E 明令禁止自建 |
| **表数量失控** | 16 张新表 = schema 翻倍，4 张是既有表复制品 | §5 评审修正：**16 → 8**，新增表逐张在 spec 里给出复用理由 |

---

## 10. 与既有计划的关系

| 计划 | 关系 |
|---|---|
| [industry-research-workbench.md](./industry-research-workbench.md)（猪智投，active） | **不取代**：它是 `行业分析` 模块的实现子计划，其 P1–P6 成果被本计划 Wave 3 吸收并泛化。本计划是上层 IA/数据模型主线 |
| [2026-09-14-limit-up-sentiment.md](./2026-09-14-limit-up-sentiment.md) | 其"连板梯队"归入 Wave 1 T1.4 情绪/市场宽度与动量 |
| [2026-09-17-market-sentiment-overhaul.md](./2026-09-17-market-sentiment-overhaul.md)（completed） | 市场数据可信度成果作为 Wave 1 基线 |
| [2026-09-18-concept-boards-and-new-stocks.md](./2026-09-18-concept-boards-and-new-stocks.md)（completed） | 其"次新股追踪"与 Wave 3 T3.6 `其他分类` 兜底**合并** |
| [2026-10-12-market-hotspot-persistence-and-trends.md](./2026-10-12-market-hotspot-persistence-and-trends.md)（draft） | 其落库/回补三判据与本计划 F3（PIT）同源；两者的 ADR 编号需**协调顺序**（该计划已预留 ADR 0009） |
| [2026-09-17-data-sync-self-healing.md](./2026-09-17-data-sync-self-healing.md)（active） | Wave 2 资讯采集复用其自愈对账模式 |

---

## 11. 原待拍板项 —— **已定案（2026-10-12）**

四项阻塞项均按建议定案，落点见 [S0.4 命名与口径字典](../docs/specs/S0-platform-contracts.md#s04-命名与口径字典)。

| # | 问题 | 定案 | 落点 |
|---|---|---|---|
| **A** | 7 个徽标数字（29 / 5 / 18 / 16 / 3 / 9 / 31）是否同一套语义 | **区分两种语义**：`已接入数`（`ingested_count`）与 `已策展数`（`curated_count`），各自命名。`A股市场 18` 与展开后的 5 个子节点及 `主要指数 9` 对不上 → **列为复核项，必须定一个语义或删除该徽标**，不允许以"大概是个数"存在 | [S0.4 §4.1](../docs/specs/S0-platform-contracts.md#41-徽标语义必须区分两种原图上-7-个数字混用) · [S1.1](../docs/specs/S1-market-sentiment.md#s11-导航骨架与路由迁移) |
| **B1** | 图上"2026年5月以后上市的公司"的时点语义 | **改为动态规则，不硬编码日期**：兜底条件 = 该实体在申万分类体系中无有效归类；处理顺序 = 自动归类 → 人工裁定 → 人工结果永久优先。**并推广为通用机制**（概念板块 / 产业链环节归属 / 研报有效性判定共用同一 review queue）| [S0.4 §4.6](../docs/specs/S0-platform-contracts.md#46-其他分类兜底规则修正原图硬编码日期) · [S3.5](../docs/specs/S3-content.md#s35-人工裁定队列) · [S4.12](../docs/specs/README.md#s4-行业--公司--宏观wave-3--catalog-only) |
| **B2** | 外资画像：改口径还是退场 | **换口径**：`资金流四件套` = ETF 份额变化 + 两融 + 产业资本增减持 + 南向。**「外资画像」改名**为「陆股通持仓（季度）」并降级进公司/股东股权。**不做**"北向每日净买入"（数据已不存在）；**不把**香港中央结算持仓称作"外资"（绝大部分是内地资金借道）| [S1.7](../docs/specs/S1-market-sentiment.md#s17-资金流四件套--外资画像重构) |
| **C** | 模块边界三处 | ① `A股涨跌分布` → **归情绪**（市场只做只读概览快照，引用情绪 API）；② `行业主力净流入` 与情绪 `资金流向` → **计算归一处**，按维度取数；③ 龙虎榜/大宗交易 → **资讯为原料**（`source_tier=official`），情绪可作视图；④ `价值分配` × `利润分配流向图` → **合并为一处**；⑤ 交易执行 `标的分类` 批注 → 实为**关注池**，拆为 `关注池` + `资产类型` | [S0.4 §4.5](../docs/specs/S0-platform-contracts.md#45-模块边界裁定原图-4-处重叠) |

> **不再有阻塞项。** Wave 0 可直接开工。

---

## 12. 决策日志

| 日期 | 决策 | 来源 |
|---|---|---|
| 2026-10-12 | **自选/标签 = 方案 A**：合并为多态 `tag` + `user_target_tag`，一级导航保持 10 个纯净，落为顶栏全局工具 + `/me` 工作台 | owner 拍板（第 5 轮对齐） |
| 2026-10-12 | **实施顺序 = 依赖倒序**：市场/情绪 → 资讯 → 行业/公司/宏观 → 价值网 → 策略 → 风险/交易 | owner 拍板（第 5 轮对齐） |
| 2026-10-12 | **原料/视图分离**确立（10 张图自证 6 处） | owner 认同（第 4 轮对齐） |
| 2026-10-12 | **四条蓝色批注升格为全局产品原则**：时序性 · 摘要性 · 可对比性 · 可溯源性 | owner 确认（第 4 轮） |
| 2026-10-12 | **规格拆分**：新建 `docs/specs/` 规格层（契约与验收），与 `plans/`（排期）、`architecture/`（当前态）、`decisions/`（为什么）分工；**初版 59 spec，评审后 57**（S2 6→4），S0–S3 detailed、S4–S7 catalog-only（渐进明细）| owner 要求（第 7 轮）|
| 2026-10-12 | **A 定案**：徽标区分 `已接入数` / `已策展数`；`A股市场 18` 列为复核项 | owner 采纳（§11）|
| 2026-10-12 | **B1 定案**：「其他分类」兜底改为**动态规则**（无有效归类的实体），不硬编码日期；推广为通用人工裁定队列 | owner 采纳（§11）|
| 2026-10-12 | **B2 定案**：外资画像**换口径**为资金流四件套；「外资画像」改名为「陆股通持仓（季度）」并降级；不做北向每日净买入 | owner 采纳（§11）|
| 2026-10-12 | **C 定案**：涨跌分布归情绪 · 资金流计算归一处 · 龙虎榜为资讯原料 · 价值分配与利润流向合并 · `标的分类` 拆为关注池 + 资产类型 | owner 采纳（§11）|
| 2026-10-12 | Wave 0 不再有阻塞项，可直接开工 | §11 全部定案 |

---

## 13. 实施记录

_（待 Wave 0 启动后填写）_
