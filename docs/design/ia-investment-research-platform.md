# 投研工作台 · 信息架构与路由契约（ia-investment-research-platform.md）

> 冻结日期：2026-10-12。**本文是 10 个一级导航与旧路由迁移的唯一权威**，Task 5 的前端导航骨架按本文实现。
> 上游契约：[`docs/specs/S0-platform-contracts.md`](../specs/S0-platform-contracts.md) §S0.2（路由表 / 分区 / redirect 契约）；
> 结构转录：[`plans/2026-10-12-investment-research-platform-ia.md`](../../plans/2026-10-12-investment-research-platform-ia.md) §3.1 / §3.2 / §3.3。
> 既有路由的唯一真相：[`frontend/src/app/router/index.tsx`](../../frontend/src/app/router/index.tsx)（15 条）；
> 既有导航：[`frontend/src/app/layouts/MainLayout.tsx`](../../frontend/src/app/layouts/MainLayout.tsx)（5 个 tab）。
>
> **本文不定义徽标计数语义**（`ingested_count` / `curated_count` 的含义不在本层）——徽标计数语义见 `docs/design/metric-dictionary.md` §4.1（Task 4 产出）。本文只定义「哪里放徽标、徽标数据从哪来」。
> **本文不定义各模块页面内部交互**，那是各模块自己的 spec。

---

## 1. 十个一级导航总表

顺序即顶栏默认排列。

| # | 导航名 | 路由前缀 | 分区 | 权限 | 落地波次（现状） |
|---|---|---|---|---|---|
| 1 | 市场 | `/market` | 研究（只读） | 登录即可 | Wave 1（既有 `/market` 扩展） |
| 2 | 资讯 | `/news` | 研究（只读） | 登录即可 | Wave 2（新建；Wave 1 零消费方） |
| 3 | 行业分析 | `/industry` | 研究（只读） | 登录即可 | Wave 3（吸收既有 `/research`） |
| 4 | 公司分析 | `/company` | 研究（只读） | 登录即可 | Wave 3（吸收既有 `/stock/:symbol`） |
| 5 | 宏观分析 | `/macro` | 研究（只读） | 登录即可 | Wave 3 |
| 6 | 情绪分析 | `/sentiment` | 研究（只读） | 登录即可 | Wave 1 |
| 7 | 价值网图谱 | `/valuegraph` | 研究（只读） | 登录即可 | Wave 4 |
| 8 | 策略引擎 | `/strategy` | 决策（写） | 登录即可 | Wave 5（不存在） |
| 9 | 风险管理 | `/risk` | 决策（写） | 登录即可 | Wave 6（不存在） |
| 10 | 交易执行 | `/trade` | 执行（写） | 登录即可 | Wave 6（不存在） |

**分区**（plan §3.1）：前 7 个是「看」，后 3 个是「做」。研究层用户只读、系统写；决策层由系统产出信号、风控产生动作；执行层是**用户写**（交易记录 / 复盘）。

**权限口径（冻结）**：**全部 10 个导航一律「登录即可」，不发明任何模块级 scope。**
- 三个写侧模块（策略 / 风险 / 交易）**尚不存在**，现在写 `strategy:write` / `risk:write` / `trade:write` 只是空字符串。
- 待模块真落地时，按既有做法（[ADR 0008](../decisions/0008-research-read-requires-permission.md)：后端 router 级权限依赖）逐个补，不在本表预先承诺。
- 与 spec §S0.2 表中「市场 = 公开、资讯 / 行业分析 / 公司分析 / 宏观分析 = `research:read`」的差异：本表按控制器裁定统一为「登录即可」；**「市场」的公开性与既有 `/research*` 的 `research:read` 守卫如何平移，见 §4 末的「开发项」**，未裁定前不在本表单方面改写。

---

## 2. 各导航的子模块树（逐条转录 plan §3.3，不增不减）

### 2.1 市场 `/market`（5 + 7 榜单）

| 子模块 | 内容 |
|---|---|
| 全球市场 | 美国 / 香港 / 其他 |
| 大宗商品 | 29 个标的 |
| 外汇市场 | 5 个标的 |
| A股市场 | 18 个条目 |
| 行业分类 | 申万 31 → 二级 → 三级 → 股票 |
| A股排行 | 7 个榜单 |

- 依赖的原料：行情、分类、榜单计算

### 2.2 资讯 `/news`（4 类）

| 子模块 | 内容 |
|---|---|
| 上市公司公告 | `publisher_tier=legal` |
| 政府官媒政策 | `publisher_tier=official` |
| 研究资讯 | `publisher_tier=sellside` |
| 市场资讯 | `publisher_tier=media` |

- 依赖的原料：**文本原料源头**（全产品文本上游）

### 2.3 行业分析 `/industry`（3）

| 子模块 | 内容 |
|---|---|
| 行业看板 | 申万三级策展 |
| 行业报告 | Top5 财报 + 有效研报 → **对比表** |
| 行业比较 | 景气 / 估值分位 / 资金 / 涨跌 |

- 依赖的原料：资讯 + 财务 + 行情

### 2.4 公司分析 `/company`（9 卡）

| 子模块 | 原料来源 |
|---|---|
| 基本信息 | 定量自给 |
| **风险红线** | 定量自给 |
| 财务数据 | 定量自给 |
| 商业模式 | 依赖资讯 |
| 竞争力 | 依赖资讯 |
| 估值 | 定量自给 |
| 管理层 | 依赖资讯 |
| 股东股权 | 依赖资讯 |
| 事件驱动 | 依赖资讯 |

- 依赖的原料：9 卡中 **4 卡定量自给（基本信息 / 风险红线 / 财务数据 / 估值），5 卡依赖资讯**

### 2.5 宏观分析 `/macro`（7）

顶层政策、货币政策、财政政策、产业政策、监管政策、政策日历、政策影响追踪。

- 依赖的原料：宏观数据 + 资讯（政策）

### 2.6 情绪分析 `/sentiment`（10）

交易活跃度、杠杆资金、投资者行为、市场宽度与动量、波动率与衍生品、资金流向、估值与风险溢价、散户舆情、综合情绪指数、**情绪预警与预判**。

- 依赖的原料：**纯数值，零资讯依赖**

### 2.7 价值网图谱 `/valuegraph`（3）

| 子模块 | 内容 |
|---|---|
| 供应 / 产业链 | 图谱 · 价值分配 · **传导关系及时间** · 议价权 |
| 上市公司控股关系 | — |
| 产业链地图 | 上中下游 + 利润流向 + 替代品互补品 |

- 依赖的原料：分类 + 公告 + 研报抽取 + 工商

### 2.8 策略引擎 `/strategy`（5）

选股（价值 / 成长 / 质量 / 多因子 / 事件驱动）、择时（技术 / 宏观 / 情绪）、信号系统（**置信度分级 → 仓位**）、组合构建、策略回测。

- 依赖的原料：**消费以上全部**

### 2.9 风险管理 `/risk`（4）

个股风控、组合风控（VaR / CVaR / Beta / 暴露）、系统性风险（大盘信号 / 流动性 / 黑天鹅 / 预案）、**风控规则引擎**（自动止损 · 仓位调整 · 风险预算 · **日志与审计**）。

- 依赖的原料：**持仓数据（来自交易执行）** + 情绪 / 宏观 / 资讯

### 2.10 交易执行 `/trade`（3）

**标的分类**（经行业分析筛出的长期追踪池）、交易日志（记录 · 复盘 · **盈亏归因** · 统计）、持仓管理。

- 依赖的原料：行情 + 行业分析
- **转录说明（plan §3.4 裁定，非新增子模块）**：原图「标的分类」的批注说的是「经行业分析后具备长期追踪价值的**标的池**」——那是**关注池**，不是资产类型；落地时拆为 **`关注池`** + **`资产类型`** 两项（子模块数仍按原图记为 3：标的分类 → 关注池与资产类型）。

---

## 3. 顶栏全局工具（不进一级导航）

| 工具 | 路由 | 说明 |
|---|---|---|
| 全局搜索 | `/search?q=` | 跨股票 / 行业 / 指数 / 概念 / 资讯 |
| 自选 | **抽屉（无独立路由）** | 任意页面可加自选 / 打标签，**不离开上下文** |
| 我的 | `/me` | 自选 · 标签 · 我的行业 · 提醒（`?tab=watchlist` / `?tab=tags`） |

**明确不含 `/calendar`。** 事件日历已推迟到 Wave 3：Wave 1 零消费方（最近的消费方是 Wave 3 的「政策到期提醒」与「财报披露日」），把没有当期消费者的东西当前置「地基」是给它发免死金牌。落到 Wave 3 的 `S4.12` 之一。

> owner 拍板：自选 / 标签**不占一级导航位**，落为「顶栏全局工具 + `/me` 工作台」。
> 顶栏 / 侧栏上的徽标（若显示）**只负责展示**，计数语义见 `docs/design/metric-dictionary.md` §4.1。

---

## 4. 完整路由表（既有 15 条，一条不漏）

旧路由集合以 `frontend/src/app/router/index.tsx` 为准（**15 条**）。spec §S0.2 的 redirect 表列了 13 条，**漏了 `/`（landing）与 `/login`**：这两条语义未变，按「保留（语义未变）」补入本表。**一条都不能直接删。**

| # | 旧路由 | 新位置 | 处理 |
|---|---|---|---|
| 1 | `/` | `/`（landing 宣传页，公开，不套 MainLayout） | 保留（语义未变） |
| 2 | `/login` | `/login` | 保留（语义未变） |
| 3 | `/market` | `/market` | 保留（语义未变） |
| 4 | `/market/category` | `/market/industry` | redirect |
| 5 | `/market/industry/:level1Code` | `/market/industry/:level1Code` | 保留 |
| 6 | `/market/industry/:level1Code/:level2Code` | `/market/industry/:level1Code/:level2Code` | 保留 |
| 7 | `/market/hot-sectors/:category` | `/market/heatmap?group=:category` | redirect |
| 8 | `/market/concept/:boardCode` | `/market/heatmap?concept=:boardCode` | redirect |
| 9 | `/index/:tsCode` | `/market/index/:tsCode` | redirect |
| 10 | `/stock/:symbol` | `/company/:symbol` | redirect |
| 11 | `/research` | `/industry` | redirect |
| 12 | `/research/:industryKey` | `/industry/:industryKey` | redirect |
| 13 | `/watchlist` | `/me?tab=watchlist` | redirect |
| 14 | `/tags` | `/me?tab=tags` | redirect |
| 15 | `/tags/:tagName` | `/me?tab=tags&name=:tagName` | redirect |

**参数名差异（转录校对，按既有代码用词）**：spec §S0.2 表里写 `/market/industry/:l1`、`/market/industry/:l1/:l2`（简写），既有代码是 `:level1Code` / `:level2Code`。**同一路由、同一语义**，本表按既有代码的参数名书写；redirect 目标（`/market/industry`、`/market/heatmap?...`）与 spec §S0.2 逐字一致。

**redirect 契约**：13 条 redirect 必须真跳转（301/302 或前端 `<Navigate replace>` 均可），**不得静默 404**；保留类路由语义不变。

**开发项（需 controller 裁定，本表不单方面改写）**
- 「市场」的公开性与既有 `/research*` 的 `research:read` 守卫如何平移：§1 按裁定全写「登录即可」，但既有 `/research*` 路由带 `RequireAuth permissions={["research:read"]}`（[ADR 0008](../decisions/0008-research-read-requires-permission.md)），新前缀为 `/industry`。`research:read` 是保留、收敛到 `/industry*`，还是按「登录即可」放开，属产品口径决策。
- `/me`、`/search`、`/market/heatmap`、`/market/index/:tsCode` 是**新的目标路由**，由 Task 5 建；本表只冻结目标形态。

---

## 5. 三层布局

```
┌─ head bar ────────────────────────────────────────────────────────────┐
│ 市场 资讯 行业分析 公司分析 宏观分析 情绪分析 价值网图谱 策略引擎       │
│ 风险管理 交易执行                     🔍全局搜索  ★自选  👤我的        │
├───────────────┬───────────────────────────────────────────────────────┤
│ 左侧折叠树     │ 右侧内容区                                             │
│ （模块内二/三级）│ （路由 Outlet）                                        │
└───────────────┴───────────────────────────────────────────────────────┘
```

1. **head bar**：10 个一级导航（顺序见 §1，纯净、不混入全局工具）+ 右侧全局工具（全局搜索 / 自选抽屉 / 我的，见 §3）。**不含 `/calendar`**。
2. **左侧折叠树**：当前一级导航**模块内的二级 / 三级导航**。切换一级导航时整棵树替换。
3. **右侧内容区**：路由 `Outlet`，只渲染 `head bar` 所选导航 + 左侧树所选节点的内容。

### 5.1 左侧树必须**数据驱动**（硬性约束）

- 树的**节点**（层级、标题、路由参数）与**徽标**由**配置 / 后端 payload 下发**，组件内**不得硬编码业务知识**——不得写死「申万一级 31 个」「产业链 5 环」这类事实。
- 理由：后续**申万三级树**（市场 / 行业分析）与**产业链节点**（价值网图谱）复用同一棵树组件，硬编码会让同一个组件长出两套业务知识。
- 组件职责边界：只做「渲染 + 折叠状态 + 跳转」，**不做分类体系的推断**。
- **徽标计数语义见 `docs/design/metric-dictionary.md` §4.1**（本文只定义「徽标数值从 payload 来」，不定义它是什么意思）。
- 越界兜底规则（外键留空 + 计数外露，不静默丢数据）见 spec §S0.4 §4.6，不在本层重复。

### 5.2 未实现导航的占位

10 个导航全部可点击进入；未落地模块显示「规划中」占位页，**无 404**（spec §S0.2 验收判据）。

---

## 附：本文的边界

| 不做什么 | 归属 |
|---|---|
| 不写前端代码、不改 `MainLayout.tsx` / `router/index.tsx` | Task 5（前端导航骨架） |
| 不定义徽标计数语义 | `docs/design/metric-dictionary.md` §4.1（Task 4） |
| 不定义各模块页面内部交互 | 各模块自己的 spec |
| 不新增 / 删减子模块 | plan §3.3 是转录源 |
| 不定义写侧模块的权限 scope | 模块落地时按 ADR 0008 做法逐个补 |
