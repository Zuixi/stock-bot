# Plan: 公开行情台首页（Public Market Homepage）

> **定位（2026-09-11 拍板）**：首页**以免登录行情为主**——打开即是一台可用的行情台（指数、榜单、板块、资金、日历、资讯），
> 营销叙事收窄为页面尾部的一段收口。登录只解锁**个性化**（自选、标签、提醒），不解锁**数据可见性**。
> 设计参考：[附录 A](#附录-a-设计参考实测数据)（2026-09-11 chrome-devtools 实测，**内部参考，不得进对外文案**）。
>
> **任务级详细计划**：[2026-09-11-public-market-homepage-tasks.md](./2026-09-11-public-market-homepage-tasks.md)
> （覆盖 Phase 0–3 的逐步 TDD 任务；Phase 4/5 待 Phase 0 spike 结论后另立详细计划）。
>
> 关联文档：[docs/design/data-source.md](../docs/design/data-source.md)、
> [docs/architecture/authentication-and-gateway.md](../docs/architecture/authentication-and-gateway.md)、
> [plans/industry-research-workbench.md](./industry-research-workbench.md)

---

## 0. 前置事实核实（2026-09-11 实测，load-bearing）

### 0.1 「免登录」已经基本成立 —— 这不是鉴权改造项目

| 断言 | 证据 |
|---|---|
| 网关对无 cookie 请求**匿名放行** | `forward-auth/app/main.py`：`if not session_id: return Response(status_code=200)`，auth-service 零调用 |
| 行情页面**本就无守卫** | `frontend/src/app/router/index.tsx`：仅 `/watchlist`、`/tags`、`/tags/:tagName` 包 `RequireAuth`；`/market`、`/research`、`/stock/:symbol`、`/index/:tsCode` 全部公开 |
| 行情端点**本就公开** | `backend/app/api/v1/`：exchanges / stocks / financials / market / market-data / clusters / industries(GET) 均未挂 `CurrentUserDep` |

**结论**：不需要新的网关或鉴权改造。真正的工作量在数据面（0.3）与防护面（0.5）。

### 0.2 在库可直接用的数据（零新增采集）

| 能力 | 端点 | 服务端缓存 | 数据新鲜度 |
|---|---|---|---|
| 大盘指数 | `GET /market/indices` | ✅ Redis 300s | 日频 |
| 全球+A股指数卡片 | `GET /market/global-indices` | ✅ 60s | **实时**（东财快照）+ EOD 兜底 |
| 涨跌分布（11 档） | `GET /market/distribution` | ✅ Redis 300s | 随日线 |
| 行业板块涨跌 | `GET /market/sectors` | ✅ Redis 300s | 日频（**CSRC 口径**，见风险 ①） |
| 板块资金流榜 | `GET /market/sector-moneyflow` | — | 盘中 5 分钟轮询（东财） |
| 大盘资金流 / 北向 | `GET /market/market-moneyflow`、`/northbound` | — | 盘中 / 日频（北向语义待核实，风险 ②） |
| 事件（龙虎榜/大宗/解禁/回购） | `dragon-tiger`、`block-trades`、`share-floats`、`repurchases` | — | 日频（盘后） |
| 公告流 | `GET /market/announcements` | — | 巨潮，8–22 点每 10 分钟 |
| 申万分类树 / 成分 | `GET /market/sw-industry/tree`、`/sw-industry/{l1}/stocks` | — | 静态 |

### 0.3 三个真实缺口（主要成本）

**缺口 A：榜单无高性能路径，且 `pct_chg` 被丢弃**

- 唯一能排序的 `GET /api/v1/exchanges/stocks/enriched?sort_by=`，白名单只支持 `changePercent / latestPrice / turnover(成交额) / marketCap / pe`，**不支持换手率、成交量**。
- 排序路径**不是数据库排序**：`stock_service.py` `list_stocks_enriched` 的 `if params.sort_by:` 分支执行「拉全市场 10k 行 → LATERAL enrich → **Python sort** → 分页」，约 +70ms/2300 只。
- **该路径完全不碰缓存**：函数收了 `cache: CacheClient` 但排序分支里一次 `get/set` 都没有——见 0.5。
- **关键发现**：TuShare `daily` 原生返回 `pct_chg` 与 `pre_close`，但 `DailyQuote` 模型无这两列、`tushare_ingest.py` 入库映射丢弃 → 涨跌幅只能查询时 LATERAL 现算，**无索引可支撑 `ORDER BY pct_chg`**。修复成本极低（把已在手的数据存下来）。

**缺口 B：日历全部无数据源**（财报/分红/IPO/宏观无结构化数据；`fetch_new_share` 客户端方法存在但零调用；交易日历无持久化表，`_is_workday() = weekday()<5` 节假日空跑——**既存 bug**）

**缺口 C：无财经新闻源**（只有巨潮公告；`data-source.md` 无新闻源调研章节）

### 0.4 设计面缺口（对比度实测）

| 场景 | 现值 | 对比度 | 判定 |
|---|---|---|---|
| 浅色 · 涨 | `#f5222d` on `#ffffff` | **4.08:1** | 低于 AA 4.5:1 |
| 浅色 · 跌 | `#22c55e` on `#ffffff` | **2.28:1** | **严重不足**（<3:1） |
| 暗色 · 涨/跌 | `#f23645` / `#089981` on `#131722` | 4.59 / 5.01 | 达标 |
| 参考 | `#cc2f3c` / `#06806b` on `#ffffff` | 5.20 / 4.87 | 参照系取 600 档 |

### 0.5 匿名流量防护现状（评审补充，2026-09-11 复核）

| 端点 | 服务端缓存 | 匿名可承受 |
|---|---|---|
| `/market/indices`、`/distribution`、`/sectors`、`/capital-flow`、`/hot-boards` | ✅ `CacheDep` 注入 + Redis 300s（`market_service.py` `_MARKET_CACHE_TTL`） | ✅ |
| `/market/global-indices` | ✅ 60s | ✅ |
| **`/exchanges/stocks/enriched?sort_by=`（Phase 1 临时榜单路径）** | ❌ **排序分支无任何缓存** | ❌ 每次请求全市场 LATERAL + Python sort |
| 网关限流 | `api-ratelimit: average 100 / burst 50 / 1s`（每 IP） | `burst < average` 疑似笔误，需复核 |
| **整站 SEO** | `sec-headers` 给所有响应（含 frontend 路由）打 `X-Robots-Tag: noindex, nofollow, nosnippet, noarchive` | **当前整站拒绝收录**——SEO 目标与网关现状矛盾，需决策 |

---

## 设计原则

1. **公开优先**：数据可见性不设登录墙；登录只绑定"属于我"的东西。任何"注册后可见"的行情数据都是设计错误。
2. **一套组件服务两个入口**：首页行情台与 `/market` 页**必须共用** `features/market/` 组件与 `shared/ui/` 原语，禁止平行实现。
3. **复用既有管道**：client 方法 / service / QUEUES+Worker / APScheduler / Alembic 链 / `CacheClient` / React Query 模式一律复用。
4. **口径诚实**：个股是 T+1 日频，**不假装实时**。所有榜单/日历标注 `as_of`；指数层可实时。
5. **后端驱动模块**：首页区块结构、列定义、Tab 由 API payload 描述；前端组件不内置资产类别知识。
6. **未验证的外部源 = mock + 开关 + TODO**（沿用仓库既有约定）。
7. **失败可降级**：每区块独立降级，单源故障不白屏（沿用 `MarketPulse` 模式）。
8. **性能防护前置**：任何进入公开页面的端点必须有服务端缓存或索引路径，客户端 `staleTime` 不算防护。

---

## Architectural decisions（持久决策）

### D1. 首页形态：**免登录行情为主体，营销收尾**（2026-09-11 拍板）

`/` 保留公开独立布局（不套 `MainLayout`），区块序列——**行情占主体，营销压缩为一段**：

```
LandingNav（增加行情区块锚点：脉搏/榜单/板块/日历/资讯）
├─ 1. CompactHero        薄首屏：一句话 + 主 CTA + 市场状态徽章（不占首屏主体）
├─ ── 行情台主体（全部公开、免登录）──
│  2. MarketPulse         指数条 + 涨跌分布 + 涨跌家数（首屏主角）
│  3. RankingMatrix       涨幅 / 跌幅 / 成交额 / 换手率
│  4. SectorFlow          申万行业涨跌（Phase 3 前临时 CSRC）+ 板块资金流 Top
│  5. MoneyAndSentiment   北向（语义待 Phase 0）+ 大盘资金流
│  6. MarketCalendar      财报披露 / 分红 / 新股 /（宏观视 spike）
│  7. MarketNews          公告快讯（+新闻视 spike）
├─ ── 营销收尾（压缩为一段）──
│  8. ProductShowcase + ValueProps（合并为一屏）
│  9. AccountPerks        "公开 vs 登录"能力对照
│  10. BottomCTA
└─ LandingFooter（含数据来源署名）
```

### D2. 公开边界契约（写死，不准漂移）

| 能力 | 匿名 | 登录 |
|---|---|---|
| 指数 / 榜单 / 板块 / 资金流 / 日历 / 资讯 | ✅ | ✅ |
| 行业与个股详情、K 线、财务、估值 | ✅ | ✅ |
| 自选（watchlist）/ 标签（tags） | ❌ → `/login?returnTo=` | ✅ |
| 任务触发、研究数据写入 | ❌ | 需 `tasks:trigger` / `research:manage` |

在 router 为公开路由加注释锚定本契约；e2e 锁「未登录全链路零 401」。

### D3. 榜单走「存储列 + 复合索引」，不做预计算快照表

`pct_chg` 是 TuShare 原生字段，落库成本 ≈ 0；预计算快照有口径漂移风险且违背 DRY。
榜单类型 MVP = `gainers / losers / amount / turnover_rate / volume`（全部有存储列 + 索引可走）；
**amplitude（振幅）不做**——`(high-low)/pre_close` 是表达式，索引撑不住，YAGNI。

### D4. 最新交易日统一解析

`market_service.get_latest_trade_date(db, cache)`（`SELECT max(trade_date) FROM daily_quotes`，Redis 300s），
所有榜单/日历/分布共用；响应带 `as_of` 与 `is_latest_trading_day`。
`is_latest_trading_day` Phase 2 用「最近工作日」启发式（**显式 TODO**），Phase 4 接 `trade_calendar` 后替换实现（接口不变）。

### D5. 日历数据模型统一（Phase 4）

```sql
market_calendar_events(id, event_type, event_date DATE, symbol NULL,
  title TEXT, payload JSONB, source TEXT, event_key TEXT,
  UNIQUE(event_type, event_date, event_key), INDEX(event_type, event_date))
```

### D6. 行业口径：申万为准，CSRC 仅作临时桥

产品身份是「按申万分类统计」（AGENTS.md 第一句）。首页 SectorFlow 在 Phase 3 落地前
临时用 `/market/sectors`（CSRC 口径）并**在 UI 上标注口径**；Phase 3 新增
`GET /market/sw-industry/performance`（`sw_industry_members` L3 成分 → parent 两跳上卷 L1 → join `daily_quotes`）后切换。

### D7. 涨跌色按 WCAG AA 重取（提前到 Phase 1，独立缺陷）

- 浅色：涨 `#f5222d` → **`#c62828`**（5.62:1）；跌 `#22c55e` → **`#0a7d5f`**（5.11:1）
- 暗色：**保持** `#f23645` / `#089981`（4.59 / 5.01 已达标）
- 同步改 `theme.ts` 的 `THEME_COLORS` 与 `app/styles/theme.css`；加设计令牌校验脚本（对比度 + 两处一致性）锁住

### D8. `pct_chg` 历史回填必须走权威源

- **按 `trade_date` 重拉 `fetch_daily`**（约 250 次调用/年 · 3 年 ≈ 750 次，需计入 TuShare 积分配额），取原生 `pct_chg`/`pre_close` 幂等 UPDATE。
- **禁止用 `LAG(close)` 库内现算**：除权除息日的 `pct_chg` 必须基于调整后前收盘，naive 现算在除权日是错的。

### D9. 分支基线与 CI

基于 **`feature/landing-market`**（base 为 `feature/p7-auth-gateway`）。
⚠️ stacked PR 不触发 CI（`pull_request.branches:[main]`）：需手动 `gh workflow run` 或等栈底合并后 rebase。
⚠️ 新增 Alembic 迁移后必须 `alembic heads` 查多 head（并联 PR 已踩过，需 merge 迁移）。

---

## Phase 总览与裁剪

| Phase | 内容 | 状态 |
|---|---|---|
| **0** | 数据源 spike + 数据语义核实 + noindex/SEO 决策 | 详细任务已排（见 tasks 文档） |
| **1** | 首页骨架：行情为主体 + 共享原语 + 防护补丁 + e2e | 详细任务已排 |
| **2** | 榜单数据面：`pct_chg` 落库 + 索引 + rankings API | 详细任务已排 |
| **3** | 申万行业行情聚合（D6） | 详细任务已排 |
| **4** | 日历（trade_calendar 修空跑 + 各事件源） | **待 Phase 0 结论后另立详细计划** |
| **5** | 资讯（公告流完善 + 新闻源视结论） | **待 Phase 0 结论后另立详细计划** |
| **6** | 节奏刻度、限流复核、文档与门禁收口 | 并入 Phase 4/5 的收尾计划 |

各 Phase 的 What to build / Acceptance criteria 以 **tasks 文档**为准（含逐步代码与测试）；
本文件保留为总纲与决策记录。

---

## 风险与未决项

| # | 风险 | 处置 |
|---|---|---|
| ① | 首页板块口径 CSRC ≠ 产品身份申万 | D6：Phase 3 申万聚合落地，CSRC 仅临时 + UI 标注 |
| ② | 北向 `net_amount` 披露口径 2024 年后变化，近年可能断流 | Phase 0 一条 SQL 核实近 30 天；断流则模块换「成交总额」口径或撤 |
| ③ | 沪深300/上证50/北证50 不在 `global_index_daily` job 的 `GLOBAL_INDICES`，DB 有数据后不会补齐 | Phase 0 核实 `/market/indices` 是否返回沪深300；缺则修 job |
| ④ | `daily_quotes` 分区状态未确认（注释称外部分区） | Phase 2 迁移前 SQL 实测；未分区→普通复合索引 |
| ⑤ | 日历/新闻数据源未验证（Web 核实被拦） | Phase 0 spike；不可用则 Phase 4/5 裁剪 |
| ⑥ | Tier 2 API 基准**从未实施**（`scripts/` 仅 Tier 1 的 bench.sh） | Phase 2 验收用 `EXPLAIN` 索引断言测试替代，不引用不存在的门禁 |
| ⑦ | 整站 `X-Robots-Tag: noindex`，SEO 目标与之矛盾；且公开再分发行情数据有条款约束 | Phase 0 决策：默认维持 noindex；若要 SEO 需显式解除并限定范围 + 页脚数据署名 |
| ⑧ | stacked PR 不跑 CI；Alembic 多 head | D9 |
| ⑨ | 首页与 `/market` 双份实现 | 原则 2 + 复用审计 + e2e |
| ⑩ | 个股 T+1，无盘中实时 | 原则 4：`as_of` 标注；实时个股源不在本计划 |

---

## 附录 A: 设计参考实测数据

来源：2026-09-11 chrome-devtools 实测 TradingView 首页。**内部设计参考，不得出现在对外文案/PR 描述。**

### A.1 页面骨架

- 页面全高 **18,946px**；暗色营销首屏 → 产品展示 → 浅色数据区（圆角叠压）→ 页脚（981px，105 链接）
- 资产巨块序列 + **模块模板 × 资产类别矩阵**：每类资产用相同子模块序列（横滑三卡 → 内容卡 → 榜单 → 日历 → 新闻）+ 常驻胶囊分段切换器
- 导航三级 mega menu（一级 5 组；二级顶部 featured 促销位 + 分组标签 + `›` 三级）

### A.2 设计令牌

- `:root` **248 个 CSS 变量**；**具名调色板**（`--color-cold-gray-50…950` / `ripe-red` / `iguana-green` / `tan-orange` / `banana-yellow` / `tv-blue #2962ff` / `deep-blue` / `minty-green`），每族 50→900 + a100→a900
- 涨跌色取 **600 档**保对比度：跌 `#cc2f3c`（5.20:1）、涨 `#06806b`（4.87:1）
- 字体系统栈 + 正文 14px；营销 H2 64px/600/`-2.56px` 负字距；数据区 H2 36px / H3 28px
- **节奏系统** `--v-rhythm-spacing-1..5` 四档响应式（如 spacing-1 = 80/120/160/200px）；断点 19 个
- 主题 = `<html data-theme>` **单属性切换**，无第二套暗色样式表（与本仓库 `theme-context.tsx` 一致）

### A.3 可迁移手法

1. 数据行唯一版式：左（logo + 名称 + 灰底 ticker 胶囊）｜右（价格大字 + 涨跌小字，右对齐 `tabular-nums`），单位小字降级（`977.99 usd`）
2. 卡片固定三段式：标题行 → 内容 → `See all X ›`
3. 锚文本短、链接描述完整 + URL 语义化（SEO/无障碍双赢；前提是有可索引页面，见风险 ⑦）
4. 横滑卡列右缘 48px 圆形箭头；占比条多色分段配图例
5. 缺失显示占位而非 0；每区块独立降级
6. 未知项：mega menu 二级 hover 惰性渲染未进 a11y 树（仅完整展开 Products 一组）；暗色为手动改写 `data-theme` 验证
