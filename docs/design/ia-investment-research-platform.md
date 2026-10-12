# 投研工作台 · 信息架构与路由契约（ia-investment-research-platform.md）

> 冻结日期：2026-10-12。**本文是 10 个一级导航与旧路由迁移的唯一权威**，Task 5 的前端导航骨架按本文实现。
> 上游契约：[`docs/specs/S0-platform-contracts.md`](../specs/S0-platform-contracts.md) §S0.2（路由表 / 分区 / redirect 契约）；
> 结构转录：[`plans/2026-10-12-investment-research-platform-ia.md`](../../plans/2026-10-12-investment-research-platform-ia.md) §3.1 / §3.2 / §3.3。
> 既有路由的唯一真相：[`frontend/src/app/router/route-manifest.ts`](../../frontend/src/app/router/route-manifest.ts)（15 条；`index.tsx` 由它生成 `<Route>`）；
> 既有导航：[`frontend/src/app/layouts/MainLayout.tsx`](../../frontend/src/app/layouts/MainLayout.tsx)（5 个 tab）。
>
> **本文不定义徽标计数语义**（`ingested_count` / `curated_count` 的含义不在本层）——徽标计数语义见 `docs/design/metric-dictionary.md` §4.1（Task 4 产出）。本文只定义「哪里放徽标、徽标数据从哪来」。
> **本文不定义各模块页面内部交互**，那是各模块自己的 spec。

---

## 1. 十个一级导航总表

顺序即顶栏默认排列。

| # | 导航名 | 路由前缀 | 分区 | 权限（三态，沿用既有口径） | 落地波次（现状） |
|---|---|---|---|---|---|
| 1 | 市场 | `/market` | 研究（只读） | **公开（免登录）** | Wave 1（既有 `/market` 扩展） |
| 2 | 资讯 | `/news` | 研究（只读） | **待定** | Wave 2（新建；Wave 1 零消费方） |
| 3 | 行业分析 | `/industry` | 研究（只读） | **`research:read`** | Wave 3（吸收既有 `/research`） |
| 4 | 公司分析 | `/company` | 研究（只读） | **公开（免登录）** | Wave 3（吸收既有 `/stock/:symbol`） |
| 5 | 宏观分析 | `/macro` | 研究（只读） | **待定** | Wave 3 |
| 6 | 情绪分析 | `/sentiment` | 研究（只读） | **待定** | Wave 1 |
| 7 | 价值网图谱 | `/valuegraph` | 研究（只读） | **待定** | Wave 4 |
| 8 | 策略引擎 | `/strategy` | 决策（写） | **待定** | Wave 5（不存在） |
| 9 | 风险管理 | `/risk` | 决策（写） | **待定** | Wave 6（不存在） |
| 10 | 交易执行 | `/trade` | 执行（写） | **待定** | Wave 6（不存在） |

**分区**（plan §3.1）：前 7 个是「看」，后 3 个是「做」。研究层用户只读、系统写；决策层由系统产出信号、风控产生动作；执行层是**用户写**（交易记录 / 复盘）。

**权限口径（冻结）——三态不是新决策，而是沿用既有口径（[ADR 0008](../decisions/0008-research-read-requires-permission.md)，accepted）+ 不发明 scope：**

1. **公开（免登录）**：市场 `/market`（含全部子路由）· 公司分析 `/company`（原 `/stock/:symbol`）。
   依据 ADR 0008 决策第 3 条原文：「**公开行情不受影响**：`/market/**`、`/stock/:symbol`、`/index/:tsCode` 维持免登录」。本表沿用，**不收紧**。
2. **`research:read`**：行业分析 `/industry`（原 `/research` / `/research/:industryKey`）。
   依据 ADR 0008 决策第 1、2 条：`backend/app/api/v1/industries.py` 的 `APIRouter(dependencies=[require_permissions("research:read")])`；前端 `/research*` 用 `RequireAuth permissions={["research:read"]}`。**沿用，不放宽**——ADR 0008 明文否决过「只要求登录」：注册全开放且新账号固定 `trader` 角色，「只要求登录」等于公网自助注册即可读投研。故 §4 的 `/research*` → `/industry*` redirect **必须继续兑现该权限**（无权限者得 403 页，而非放开）。
3. **待定**：资讯 `/news` · 宏观 `/macro` · 情绪 `/sentiment` · 价值网图谱 `/valuegraph` · 策略 `/strategy` · 风险 `/risk` · 交易 `/trade`。
   **后端尚不存在，此刻写死就是假契约。模块落地时按数据敏感度定；在此之前导航对已登录用户可见，后端未就绪故无门禁。**
4. **顶栏「我的 `/me`」（原 `/watchlist`、`/tags`）= 登录即可**：沿用既有 `RequireAuth`（无具体权限参数）。

**严禁发明模块级写权限 scope**：三个写侧模块（策略 / 风险 / 交易）**尚不存在**，现在为不存在的模块预置 `*:write` scope，得到的只是空字符串，故本表**不写出任何写侧 scope 名**；模块落地时按 ADR 0008 的既有做法（后端 router 级权限依赖）逐个加。
**本列语义 = 「沿用既有口径，不得新增 scope」，不是「无需权限」。**

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

### 2.10 交易执行 `/trade`（原图 3 → 按 plan §3.4 拆为 4）

原图：**标的分类**（经行业分析筛出的长期追踪池）、交易日志（记录 · 复盘 · **盈亏归因** · 统计）、持仓管理。

- 依赖的原料：行情 + 行业分析
- **转录说明（plan §3.4 裁定）**：原图「标的分类」的批注说的是「经行业分析后具备长期追踪价值的**标的池**」——那是**关注池**，不是资产类型。故**原图 3 个子模块 → 落地为 4 个**：**`关注池`** · **`资产类型`**（由「标的分类」拆出）· 交易日志 · 持仓管理。数字差异来自 plan §3.4 的既定拆分，**不是本次转录新增子模块**。

---

## 3. 顶栏全局工具（不进一级导航）

| 工具 | 路由 | 权限（沿用既有口径） | 说明 |
|---|---|---|---|
| 全局搜索 | `/search?q=` | **待定** | 跨股票 / 行业 / 指数 / 概念 / 资讯。**可见性待定**：它不在 10 个导航内，且结果面横跨「资讯」，而资讯本身是待定；按裁定只能记待定，不得由既有实现反推。**资讯结果面按资讯模块最终权限收敛**——既有顶栏 `SearchBar` 走免登录的 `/api/v1/stocks`、且已渲染在公开的 `/market` 上，这只作先例参考，**不构成新路由的授权依据** |
| 自选 | **抽屉（无独立路由）** | 登录即可 | 任意页面可加自选 / 打标签，**不离开上下文** |
| 我的 | `/me` | **登录即可** | 自选 · 标签 · 我的行业 · 提醒（`?tab=watchlist` / `?tab=tags`）；沿用既有 `RequireAuth`（无具体权限参数） |

**明确不含 `/calendar`。** 事件日历已推迟到 Wave 3：Wave 1 零消费方（最近的消费方是 Wave 3 的「政策到期提醒」与「财报披露日」），把没有当期消费者的东西当前置「地基」是给它发免死金牌。落到 Wave 3 的 `S4.12` 之一。

> owner 拍板：自选 / 标签**不占一级导航位**，落为「顶栏全局工具 + `/me` 工作台」。
> 顶栏 / 侧栏上的徽标（若显示）**只负责展示**，计数语义见 `docs/design/metric-dictionary.md` §4.1。

---

## 4. 完整路由表（既有 15 条，一条不漏）

旧路由集合以 `frontend/src/app/router/route-manifest.ts` 为准（**合计 15 条**）。spec §S0.2 的 redirect 表列了 13 条，**漏了 `/`（landing）与 `/login`**：这两条语义未变，按「保留（语义未变）」补入本表。**一条都不能直接删。**

| # | 旧路由 | 新位置 | 处理 |
|---|---|---|---|
| 1 | `/` | `/`（landing 宣传页，公开，不套 MainLayout） | 保留（语义未变） |
| 2 | `/login` | `/login` | 保留（语义未变） |
| 3 | `/market` | `/market` | 保留（语义未变） |
| 4 | `/market/category` | `/market/industry` | redirect |
| 5 | `/market/industry/:level1Code` | `/market/industry/:level1Code` | 保留 |
| 6 | `/market/industry/:level1Code/:level2Code` | `/market/industry/:level1Code/:level2Code` | 保留 |
| 7 | `/market/hot-sectors/:category` | `/market/hot-sectors/:category` | 保留（语义未变，Ruling 30 从 redirect 退回） |
| 8 | `/market/concept/:boardCode` | `/market/concept/:boardCode` | 保留（语义未变，Ruling 30 从 redirect 退回） |
| 9 | `/index/:tsCode` | `/market/index/:tsCode` | redirect |
| 10 | `/stock/:symbol` | `/company/:symbol` | redirect |
| 11 | `/research` | `/industry` | redirect |
| 12 | `/research/:industryKey` | `/industry/:industryKey` | redirect |
| 13 | `/watchlist` | `/me?tab=watchlist` | redirect |
| 14 | `/tags` | `/me?tab=tags` | redirect |
| 15 | `/tags/:tagName` | `/me?tab=tags&name=:tagName` | redirect |

**参数名差异（转录校对，按既有代码用词）**：spec §S0.2 表里写 `/market/industry/:l1`、`/market/industry/:l1/:l2`（简写），既有代码是 `:level1Code` / `:level2Code`。**同一路由、同一语义**，本表按既有代码的参数名书写；redirect 目标（现仅 `/market/industry`）与 spec §S0.2 逐字一致。

**redirect 契约（Ruling 30 修正后：8 redirect + 7 保留）**：本表 15 条旧路由中 **8 条 redirect、7 条保留**（「13 条」是 spec §S0.2 表的行数 = `8 redirect + 3 保留` 再 `+2` 补入项，**不是** redirect 条数——13 这个数会把保留误读成 redirect）。8 条 redirect 必须真跳转（301/302 或前端 `<Navigate replace>` 均可），**不得静默 404**；保留类路由语义不变。

> **Ruling 30（redirect 不得把"今天可用的页面"换成"今天的占位页"）**：`/market/hot-sectors/:category` 与 `/market/concept/:boardCode` 原本被判为 redirect 到 `/market/heatmap`，但热力图在 Task 5 时点**只是「规划中」占位页**——两条来源路由却是**今天就有数据的真页面**（且有站内入口指向它们）。照原表实施 = 把可用页面降级成占位页，属**功能性回退**；同时 redirect 由前端重建目标串，会**静默丢掉来源 URL 的其它 query 参数**（实测 `SectorHeatmap` 的 `?board=` 就被吞掉）。故这两行退回「保留（语义未变）」，**合并视图推迟到 S1.5**（真热力图落地时由该任务决定是否吸收这两个视图）。
>
> **新增规则（对一切 redirect 生效）**：**任何 redirect 必须保留来源 URL 的其它 query 参数，不得只构造目标自己的 query 串。**（本条为预防性规则；当前 8 条 redirect 均无额外 query——凡来源无 query 者不受影响。机械检测**暂缓**：静态判定"哪些 query 属于目标、哪些属于来源"需要运行期语义，硬写会产出脆弱门禁。）

**redirect 后各路由的权限（沿用既有口径，[ADR 0008](../decisions/0008-research-read-requires-permission.md)；无权限者 403 页，不是放开）**

| 旧路由 | 处理后权限 | 依据 |
|---|---|---|
| `/market`、`/market/category`、`/market/industry/:level1Code`、`/market/industry/:level1Code/:level2Code`、`/market/hot-sectors/:category`、`/market/concept/:boardCode` | **公开（免登录）** | ADR 0008「`/market/**` 维持免登录」 |
| `/stock/:symbol` → `/company/:symbol` | **公开（免登录）** | ADR 0008「`/stock/:symbol` 维持免登录」，redirect 不改变可见性 |
| `/index/:tsCode` → `/market/index/:tsCode` | **公开（免登录）** | ADR 0008「`/index/:tsCode` 维持免登录」（且新位置在 `/market/**` 之下） |
| `/research`、`/research/:industryKey` → `/industry*` | **`research:read`**（必须继续兑现，**不得放宽为「登录即可」**） | ADR 0008 明文：router 级 `require_permissions("research:read")` + 前端 `RequireAuth permissions={["research:read"]}`；「只要求登录」已被 ADR 0008 否决 |
| `/watchlist`、`/tags`、`/tags/:tagName` → `/me*` | **登录即可**（无具体权限） | 既有 `RequireAuth` |
| `/`（landing）、`/login` | 公开（免登录） | 既有：landing 不套 `MainLayout`，登录页本身必须可匿名访问 |

**新目标路由的建立归属（Task 5；本节是「今天是 404」的完整清单，逐条核对完毕）**

下表 = 上面 15 条旧路由里**所有今天尚不存在的跳转目标**（**8 行 = 8 条 redirect 的目标；`/search` 那行是顶栏工具目标页、非 redirect**）。按 `frontend/src/app/router/index.tsx` 实测，该文件当前只有 `/`、`/login`、`/market`、`/research`、`/research/:industryKey`、`/market/category`、`/market/industry/:level1Code`、`/market/industry/:level1Code/:level2Code`、`/market/hot-sectors/:category`、`/market/concept/:boardCode`、`/index/:tsCode`、`/stock/:symbol`、`/watchlist`、`/tags`、`/tags/:tagName` 这 15 条）：

| # | 今天不存在的目标（§4 表中的新位置） | 服务的旧路由 | 说明 |
|---|---|---|---|
| 1 | `/market/industry` | `/market/category` | **裸路径**：既有只有 `/market/industry/:level1Code`，裸路径**不存在**，必须单独建 |
| 2 | `/market/index/:tsCode` | `/index/:tsCode` | 参数名保持 `:tsCode` |
| 3 | `/company` | `/stock/:symbol` | 新前缀根路径；与下一条带参变体**都要存在** |
| 4 | `/company/:symbol` | `/stock/:symbol` | 带参变体；`/stock/:symbol` 的 redirect 落到这里 |
| 5 | `/industry` | `/research` | **裸路径**；须兑现 `research:read`（见上表） |
| 6 | `/industry/:industryKey` | `/research/:industryKey` | 带参变体；同样须兑现 `research:read` |
| 7 | `/me`（接收 `?tab=watchlist` / `?tab=tags` / `?tab=tags&name=`） | `/watchlist`、`/tags`、`/tags/:tagName` | 三个 tab 变体都要能渲染 |
| 8 | `/search`（接收 `?q=`） | —（§3 顶栏全局工具，非 redirect） | 与 `/me` 同属顶栏工具的目标页；可见性**待定** |

> **`/market/heatmap` 不在本表内（Ruling 30）**：它是 S1.5 的新页面，**不是任何 redirect 的目标**；Task 5 只把路由与「规划中」占位建好（供 S1.5 替换实现），**不把任何今天可用的页面 redirect 到它**。

> **硬性要求**：**未落地的目标与其 redirect 必须在同一个 commit 里一起交付**，且目标页以「规划中」占位路由承载（见 §5.2）。**只加 redirect、目标页留空 = 把 404 从旧路径搬到新路径**，spec §S0.2「旧路由逐条访问返回 200」的验收依然过不了——本表的冻结意义正是让 Task 5 一次交付就能过该验收。
> 已存在的目标（`/market`、`/market/industry/:level1Code`、`/market/industry/:level1Code/:level2Code`、`/`、`/login`）**不需要新建**，只在需要时改语义。
> **Ruling 30 追加**：`/market/hot-sectors/:category` 与 `/market/concept/:boardCode` 也不再是「需要新目标的旧路由」——它们**原地保留**（今天就有真页面），故不进本表。

> **redirect 净效果（供 Task 5 逐条照做，不得静默加严）**：**8 条 redirect，一条不多**，且**没有一条被放宽或收紧**——它们恰好就是 §4 表中标 `redirect` 的那 8 行，逐组如下（每条都写成 `旧 → 新`，便于机械比对）：
> - **1 条**（市场）：`/market/category` → `/market/industry` —— 处理后仍 **公开（免登录）**（ADR 0008「`/market/**` 维持免登录」）；
> - **2 条**（个股 / 指数）：`/stock/:symbol` → `/company/:symbol`、`/index/:tsCode` → `/market/index/:tsCode` —— **公开（免登录）** 不变（ADR 0008）；
> - **2 条**（投研）：`/research` → `/industry`、`/research/:industryKey` → `/industry/:industryKey` —— **`research:read`** 不变，**不得放宽为「登录即可」**；
> - **3 条**（自选 / 标签）：`/watchlist` → `/me?tab=watchlist`、`/tags` → `/me?tab=tags`、`/tags/:tagName` → `/me?tab=tags&name=:tagName` —— **登录即可**，与现状相同（既有这三条本就带 `RequireAuth`，未登录者同样被引导登录）。
>
> 合计 **1 + 2 + 2 + 3 = 8**（= §4 表 `redirect` 行数）。
> **另有 7 条保留**（语义未变，**不是 redirect、不在上面 8 条内**）：spec §S0.2 表原有的 **3 条**——`/market`、`/market/industry/:level1Code`（spec 写作 `/market/industry/:l1`）、`/market/industry/:level1Code/:level2Code`（spec 写作 `/market/industry/:l1/:l2`）——**加上 Ruling 8 补入的 2 条**：`/`（landing）与 `/login`（这两条 spec 表未列，因语义未变而补为「保留（语义未变）」）——**加上 Ruling 30 退回的 2 条**：`/market/hot-sectors/:category`、`/market/concept/:boardCode`（它们今天就有真页面，不 redirect）。
> 合计 **3 + 2 + 2 = 7**（= §4 表 `保留` 行数 = `15 − 8`）。

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

### 5.2 未实现导航与未建成目标的占位（**no-404 契约**）

**占位范围 = 10 个导航根 + 全部 redirect 目标（含裸路径与带参变体）+ `/search`（顶栏工具，非 redirect）+ `/market/heatmap`（S1.5 的未来页面，Task 5 先建占位路由；它**不是**任何 redirect 的目标，见 §4 Ruling 30），不是只覆盖 10 个根。** 只给导航根建占位、让 redirect 目标仍 404，等于把 404 从旧路径搬到新路径，spec §S0.2「13 条旧路由逐条访问返回 200」照样过不了。

1. **10 个导航根**：`/market`、`/news`、`/industry`、`/company`、`/macro`、`/sentiment`、`/valuegraph`、`/strategy`、`/risk`、`/trade` 全部可点击进入；未落地模块显示「规划中」占位页，**无 404**。
2. **redirect 目标与顶栏工具目标的裸路径 / 带参变体同样要有占位路由**（完整清单见 §4「新目标路由的建立归属」共 8 条：`/market/industry`（裸）· `/market/index/:tsCode` · `/company` + `/company/:symbol` · `/industry`（裸）+ `/industry/:industryKey` · `/me`（含 `?tab=watchlist` `?tab=tags` `?tab=tags&name=`）· `/search`（含 `?q=`，**顶栏全局工具、非 redirect**）；另加 `/market/heatmap` 一条 S1.5 前置占位，不计入归属表）。占位页必须**保留 query 参数与路径参数**（redirect 不吞参数），不能把带参访问收敛成一个无参空页。
3. **交付原子性**：某个目标页及其 redirect **必须在同一个 commit 里一起落地**；**不允许**「先加 redirect、目标页留到以后」。
4. 占位页只承诺「可达 + 不 404 + 规划中提示」，**不承诺数据**；模块真正的页面落地时替换占位实现即可，路由形状不变。

> 范围口径说明：**7 条保留**（`/market`、`/market/industry/:level1Code`、`/market/industry/:level1Code/:level2Code`、`/`、`/login`，**加上 Ruling 30 退回的 `/market/hot-sectors/:category` 与 `/market/concept/:boardCode`**）**今天已存在且有的有真数据**，本来就不 404，故不需要占位；**8 条归属目标**（7 条服务于 redirect、1 条 `/search` 服务于顶栏工具）今天不存在，才是占位范围；`/market/heatmap` 另行占位（S1.5 前置）。

---

## 附：本文的边界

| 不做什么 | 归属 |
|---|---|
| 不写前端代码、不改 `MainLayout.tsx` / `router/index.tsx` | Task 5（前端导航骨架） |
| 不定义徽标计数语义 | `docs/design/metric-dictionary.md` §4.1（Task 4） |
| 不定义各模块页面内部交互 | 各模块自己的 spec |
| 不新增 / 删减子模块 | plan §3.3 是转录源 |
| 不定义写侧模块的权限 scope | 模块落地时按 ADR 0008 做法逐个补 |
