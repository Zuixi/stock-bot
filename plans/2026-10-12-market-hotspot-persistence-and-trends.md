---
status: active
scope: 市场热点/情绪/资金数据的日级持久化边界与统计追踪——个股级涨停事实、东财实时源日终捕获、覆盖率只读巡检、情绪与板块趋势端点
touches: backend/app/models/market_data.py, backend/app/migrations/versions/, backend/app/repositories/market_data_repo.py, backend/app/repositories/limit_up_repo.py, backend/app/services/market_data_service.py, backend/app/services/limit_up_service.py, backend/app/services/reconciliation_service.py, backend/app/core/providers/eastmoney_client.py, backend/app/scheduler/jobs.py, backend/app/scheduler/runner.py, backend/app/workers/market_data_worker.py, backend/app/schemas/task.py, backend/app/schemas/market_data.py, backend/app/api/v1/market_data.py, backend/scripts/, backend/tests/, frontend/src/shared/api/, frontend/src/features/market/, docs/decisions/, docs/features.md, docs/Changelog.md
updated: 2026-10-12
next-action: Task 7 完成（前端 mapper + 情绪分位 chip / 板块连续性 chip + 复用 TrendSpark；新 e2e 6 条红→绿、既有五个 market e2e 38 passed 零回归、tsc/build 绿；Ruling 23 跳过可选 Step 4）；下一步 Task 8（文档收口）
---

# 市场热点数据持久化与趋势追踪 实施计划（v2）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让"每天的热点"成为可查询、可统计、可复现的事实——情绪指标（涨停/炸板/连板/晋级率）与热点标的（个股级涨停事实）按交易日沉淀，东财实时源（板块资金流/热门板块榜/盘中涨停池明细）在收盘后当天落库不再丢失，并在此之上提供分位/连续性/龙头更替等统计追踪。

**Why now（用户反馈 + 真库实测，2026-10-12）:** 行情原始事实存得很深，但"派生层"存得又浅又有洞，而"不可回补的实时源"基本没存：

| 表 | 行数 | 天数 / 交易日 | 覆盖 | 判定 |
|---|---|---|---|---|
| `daily_quotes` | 4,427,962 | 829（2023-05-09→2026-10-09） | — | B 类事实底座 |
| `stock_price_limits` | 1,395,289 | 254（2025-09-16→） | **100%** | 涨停判定权威口径 |
| `daily_basic_indicators` | 2,010,361 | 369 | — | 事实底座 |
| `market_sentiment_daily` | 21 | 21（2026-09-03→） | **100%** | 无洞，但只有 3 周历史 |
| `market_moneyflow_daily` | 139 | 139 | **100%** | 可用 |
| `dragon_tiger_entries` / `block_trades` | 1,780 / 2,879 | 27 / 27 | **100%** | 无洞（历史回补另议，见 T5） |
| `share_floats` / `stock_repurchases` | 25,031 / 878 | 18 / 30 | — | 无洞 |
| `sector_moneyflow_snapshots` | 5,583 | 13 / 20 | **65%** | 三维度齐全（concept 2580/industry 2600/region 403 行）；缺 15 日，**其中历史缺口不可回补**（东财无历史接口） |
| `market_sentiment_intraday` | 277 | 6 / 11 | **54.5%** | 只存 4 个计数，**个股明细被丢弃**（`market_data_repo.py:517-540`） |
| `northbound_daily` | 41 | — | — | 上游 `moneyflow_hsgt` 停更，标注不改源 |
| `industry_signals` / `_events` / `_evaluations` | 24 / **0** / **0** | — | — | 信号框架只有定义（侧分支在建，本计划不碰） |

**Architecture:** 三层分工——
1. **事实层（日级，落库）**：新增 `market_limit_up_stock_daily`（个股级涨停/炸板/连板事实 + 东财 EOD 增强，两口径并列）；`sector_moneyflow_snapshots` 补 2 列承载热门板块榜。
2. **派生层（可回算，落库仅为口径冻结 + 查询性能）**：`market_sentiment_daily` 由 21 天回补到限价覆盖的**安全区间 239 天**。
3. **统计层（读时计算，不物化）**：趋势/分位/连续性端点，读时 SQL over 事实层 + Redis 缓存；**不新建指标框架**。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Alembic + APScheduler（`Asia/Shanghai`）+ RabbitMQ Worker + Redis + PostgreSQL；前端 React 18 + antd 5 + TanStack Query + ECharts；Playwright e2e。**零新增第三方依赖**。

**Spec:** 本文件即 spec；口径依据 [`docs/design/market-data-trust-and-realtime.md`](../docs/design/market-data-trust-and-realtime.md)（D1 本地自算为主干 / Web 仅装饰层）、[`docs/design/limit-up-sentiment.md`](../docs/design/limit-up-sentiment.md)、[ADR 0004](../docs/decisions/0004-current-snapshot-not-backfilled.md)（由 Task 3 的 ADR 0009 划清边界）。

## v2 修订记录（ponytail review 的 7 项更正 + 4 项拍板）

| # | 类别 | v1 的问题 | v2 的处置 |
|---|---|---|---|
| E1 | 错误 | 把 6 个新域塞进 `reconciliation_service.DOMAINS` —— 但补漏循环硬编码 3 个域（`reconciliation_service.py:228`）+ `sentiment` 特例（`:264`），新域会"看着被纳入、永不补漏"（假绿） | 拆成**可自愈域**（有 `_refetch_*`）与**只读巡检域**；覆盖率只做只读巡检，**不碰 apply 循环**；**不新建 service 文件**，扩既有返回结构 |
| E2 | 错误 | 回补起点未算窗口 → 前 15 个交易日的 `streak`/晋级率被窗口起点截平（`window_trade_days(10)=10+6=16`，`limit_up_service.py:35/42`，窗口取自 `daily_quotes` 而限价自 2025-09-16 才有） | 加**窗口完备性守卫**（窗口内任一日缺限价即 skip）；安全起点实测 **2025-10-15**，可回补 **239 天**（不是 254） |
| E3 | 错误 | 只写"复用 `fetch_day_breadth`" —— 它只回**计数**，`_WINDOW_SQL` 的 `cand` 也不含炸板票 → `is_zb` 行根本落不出来 | 新增 `fetch_day_broken_board_stocks`（同一谓词改成选行） |
| E4 | 错误 | "hot-boards 完全不落库" 表述错误：三维度行**已在** `sector_moneyflow_snapshots`（同源同形） | T2 从"新建落库链路"降为"**端点改读表 + 1 处 fields 扩参 + EOD/当日补写**" |
| E5 | 错误 | 计划自相矛盾：不做清单禁止"用当前申万分类回算历史"，T6 又要做 | 删该不做项；ADR 0009 明确：ADR 0004 管"快照型分类数据本身"，**不禁止**"用行情事实做聚合 + 归属按当前快照并显式标注"；双向引用 |
| E6 | 错误 | 行集只有"涨停 ∪ 炸板" → 断板日/停牌日都表现为"无行"，**踩「缺失 ≠ 0」** | 行集 = 窗口 when `as_of` 的候选行（含"昨日涨停今日未板"）∪ 炸板行；`missing_days` 显式落列；不变式改为 `count(is_lu) == zt_count` |
| E7 | 错误 | v1 说"对近日缺失日补抓板块快照" —— 东财实时板块资金流**没有历史接口**，昨天的数据无法补 | 明确：**历史缺口不可补**，只保证"从今日起不再丢 + 当日重跑补写"；验收目标从"65%→100%"改为"**上线日起 100%**" |
| R1 | 冗余 | T2 要加 5 列（`amount/flat_count/rank/captured_at/is_final`） | 只加 **`amount` + `flat_count`**（前端 `HotBoardItem` 契约消费，`market.ts:30-52`、`HotSectors.tsx:77`）；`rank` 前端不消费且可推导；`captured_at` 与 `updated_at` 重复；`is_final` 可推导 |
| R2 | 冗余 | T1 存 `em_streak`/`board_name` | 删（无消费方）；要对照时再加列 |
| R3 | 冗余 | 3 个回补脚本 | 合并为 **1 个** `backfill_market_history.py --domain` |
| R4 | 冗余 | "盘中明细 EOD" 独立成 Task | 折进 T1（同一张表的第二条写路径） |
| R5 | 冗余 | 龙虎榜/大宗/解禁/回购回补进了关键路径 | 降级为独立的 **T5（可选）**；数据面 27/27 无洞 |
| R6 | 冗余 | T6 给 5+ 指标做分位；新建 `/market/trends` 页 | 指标收到 **3 个**；前端先做**卡片 chip + sparkline**，独立视图走 `/market?tab=trends`（零新路由） |
| 拍板① | 决策 | `pct_chg` 全量 3 年 | **365 天**（脚本已支持 `--start-date/--end-date`）。实测 365 天覆盖 **241** 个限价交易日 ≥ 安全起点后的 **239** 天 → 够用；且**它不是回补的前置**（`persist_snapshot` 的闸门是 `is_partial`=4000 行下限，与 `pct_chg` 无关）→ 降为 T5 独立项 |
| 拍板② | 决策 | 申万归属历史口径待定 | **接受"按当前申万分类回溯 + 显式标注"**（`attribution` + `available_from` 必填，UI 固定文案"按当前分类回溯，非当时成分"） |
| 拍板③ | 决策 | hot-boards 是否改读表 | **改读当日快照表**（盘中新度 ≤5 分钟 + Redis 60s 读缓存） |
| 拍板④ | 决策 | 盘中 5 分钟轨迹是否保留 | **只落日终一份**（不做 360 万行/年的轨迹） |

## 数据分类判据（本计划的立论基础）

| 类 | 定义 | 处置 | 例 |
|---|---|---|---|
| **A · 不可回补** | 上游只给"当下"，无历史接口 | **必须当天落库**，否则永久丢失；**不得承诺补历史** | 东财板块资金流/板块榜（`eastmoney_client.py:236` 无日期参数）、盘中涨停池个股明细（封板时间/封单/炸板次数） |
| **B · 可回算且无前视偏差** | 只用**当日**点内事实（行情 + 限价） | 落库（口径冻结 + 性能），**允许回补**（需窗口完备守卫） | 情绪 KPI（`_BREADTH_SQL`）、个股 `is_lu/touched/streak/N天M板`（`_WINDOW_SQL` 判定部分） |
| **C · 依赖当前分类/成分快照** | 上游只给"今天谁属于谁" | 可用于**行情事实的聚合与回溯展示**，但归属必须**字段级标注**且不得声称时点成分；禁止用当前快照回算"成分本身" | 申万归属（`sw_industry_members` 无生效日期）；例外：概念成分有追加式差分表（`concept_member_changes`，2026-09-20 起）可给时点口径 |

> 硬约束：所有落库行必须带 `source`/`as_of`(或 `trade_date`)/`observed_at`；回补行与当日实时行必须可区分。禁止把"事后算的"与"当天看到的"混成一行（[`best-practices/metrics-rule-engine.md`](../docs/references/best-practices/metrics-rule-engine.md) 的前视偏差教训）。
> 口径冻结：行的**判定**（`is_lu/is_zb/streak`）恒以交易所限价为准；东财只填它独有的列（`seal_*`/`break_count`），**永不覆写**判定列。

## Global Constraints

- 分层铁律：`app/api/v1` → `app/services` → `app/repositories` → `app/models`；scheduler/worker 只调 service；**双轨制**（APScheduler 与 RabbitMQ Worker 共用同一 service 方法，不得各自实现抓取）；本计划不需要新队列
- **读端点不得写库**（唯一例外仍是 `get_snapshot` 的东财盘中增强）
- 价格 `Numeric(12,4)`，金额 `Numeric(20,4)`，**禁止 `Float`** 承载价格/金额
- 时间一律 `ZoneInfo("Asia/Shanghai")`；`CronTrigger` 显式 `timezone="Asia/Shanghai"`
- 迁移：`uv run alembic heads` 单 head；模型与 DDL 同 PR
- 幂等：落库一律 `ON CONFLICT`（键含改变语义的维度）；回补脚本可中断续跑、重跑收敛
- 缺失语义：缺失渲染 `--`（绝不 `?? 0`）；样本不足返回 `insufficient_history`，不给假分位
- **不改既有端点语义/响应字段**（本计划对 `/market/hot-boards` 的改动是"数据来源从实时改为当日快照 + 回落实时"，字段集不变）
- 门禁：每 Task 收尾 `git status --porcelain` + `bash scripts/slop_scan.sh` + `bash scripts/self_review.sh`（后端加 `--full`）；改端点同步 `docs/features.md`；不触 Tier-1 纯计算热点则不需要 `scripts/bench.sh`
- 每个「实测 N」必须用真库/真接口重测并**就地回填本文件**（本地栈 `127.0.0.1:5433`）

---

## Task 1: 个股级涨停事实表 + 东财 EOD 增强

**为什么：** 用户要的"市场情绪标的持续追踪"现在没有可统计对象——`market_sentiment_daily` 24 个字段全是聚合，个股只剩 `max_streak_symbol` 一个代码；而盘中涨停池明细每 5 分钟抓一次被丢弃（`market_data_repo.py:517-540`：只写 `len(rows)` 与 `max(streak)`，`dt_count/zb_count` 硬编码 0）。

**Files:**
- Modify: `backend/app/models/market_data.py`（+`MarketLimitUpStockDaily`）
- Create: `backend/app/migrations/versions/<rev>_add_market_limit_up_stock_daily.py`
- Modify: `backend/app/repositories/limit_up_repo.py`（+`fetch_day_broken_board_stocks`、+`upsert_limit_up_stock_daily`、+`list_limit_up_stock_daily`、+`limit_up_stock_daily_missing_dates`）
- Modify: `backend/app/repositories/market_data_repo.py`（+`enrich_limit_up_stock_daily_from_pool`：只更新东财拥有的列）
- Modify: `backend/app/services/limit_up_service.py`（+`persist_stock_facts(db, as_of, *, fact_source)`、+`enrich_stock_facts_from_pool(db, as_of, pool_rows)`）
- Modify: `backend/app/services/intraday_sentiment_service.py`（盘中池 → 结构化 enriched 行，供 enrichment 复用）
- Modify: `backend/app/scheduler/jobs.py`（+`limit_up_stock_facts_job` 交易日 17:20：**facts → commit → best-effort enrichment**）
- Modify: `backend/app/scheduler/runner.py`、`backend/app/schemas/task.py`（`MarketDataJobType` +1：`limit_up_stock_facts`，支持可选 `trade_date` 做 retro）、`backend/app/workers/market_data_worker.py`（同队列新分支）
- Test: `backend/tests/test_limit_up_stock_daily.py`（行构造 + 口径不变式 + enrichment 不改写判定列）

**表结构**（唯一键 `(trade_date, stock_id)`；行集 ≈ 涨停 ∪ 炸板 ∪ 昨日涨停 ≈ 200–450 行/日）：

| 列 | 类型 | 语义 |
|---|---|---|
| `trade_date` / `stock_id` / `symbol` | `Date` / `int` / `String(16)` | 唯一键 + 溯源（对齐 `stock_price_limits` 约定） |
| `is_lu` / `touched` / `is_zb` | `bool` | 收盘涨停 / 触板 / 炸板（`touched AND NOT is_lu`），**判定恒来自交易所限价** |
| `prev_is_lu` | `bool` | 前一日是否涨停（"昨日涨停今日表现"队列由此可查，无需回看窗口） |
| `streak` / `days_span` / `boards_in_window` / `missing_days` | `int` | 复用 `limit_up_calculator.n_day_m_board` / `missing_days`（与梯队同源，禁止另写） |
| `seal_time` / `seal_fund` / `break_count` | `String(8)` / `Numeric(20,4)` / `int`，可空 | **仅东财可得**；历史回补恒 NULL，**绝不填 0** |
| `fact_source` | `String(16)` | `local_calc` \| `backfill` |
| `enrich_source` | `String(16)`，可空 | `eastmoney_eod` \| `NULL` |
| `observed_at` | `TIMESTAMPTZ` | 写入时刻（区分"当天看到的"与"事后回补的"） |

**Interfaces:**
- `async def persist_stock_facts(db, as_of: date, *, fact_source: str = "local_calc") -> dict[str, int]`
  - 数据来源：`fetch_limit_up_window(as_of, as_of_prev, window_start)` 的**当日行**（含 `as_of_prev` 候选 → `prev_is_lu` 与断板票天然在内）+ `fetch_day_broken_board_stocks(db, as_of)` 的炸板行（`_WINDOW_SQL` 的 `cand` 不含炸板票）
  - `as_of_prev` / `window_start` 由 `list_recent_trade_dates(db, as_of, window_trade_days(lookback))` 给出（与 `get_snapshot` 同参）
- `async def fetch_day_broken_board_stocks(db, as_of) -> list[dict]` —— 谓词与 `_BREADTH_SQL` 的 `zb_count` **逐字一致**（`high >= up_limit - 0.005 AND close < up_limit - 0.005`），返回 `stock_id/symbol/name`
- `async def enrich_stock_facts_from_pool(db, as_of, pool_rows) -> int` —— 只 `SET seal_time/seal_fund/break_count/enrich_source`，**不碰** `is_lu/touched/is_zb/streak/*_span`

**时序裁决（Ruling 10，实施中实测发现，取代 v2 初稿的 15:05 job）：** 15:05 时当日 facts 行**还不存在**（`stock_price_limits` 16:50 才入库、facts 17:20 才建行），UPDATE-only 的 enrichment 必然命中 0 行 → A 类封板明细当天就丢。故：**取消 15:05 job**，把 enrichment 接在 17:20 的 facts commit **之后**，并且：
- enrichment **best-effort**：外呼抛错/空池 → `logger.warning` + 既有 `_alert_job_failure`，**不得**回滚 facts、不得让 job 整体失败（B 类事实优先于 A 类装饰）
- `enriched_rows == 0` 也要告警（空池与"无行可更"分别记 reason）——A 类静默丢失必须可见
- worker job 支持可选 `trade_date`，对指定日跑 facts + enrichment（东财涨停池端点有近 ~20 交易日历史）→ 17:20 那次外呼失败时的**同一代码路径**兜底，不新增 job 类型

**测试策略（必须有能在默认门禁失败的测试）：**
- 行构造：涨停 / 炸板 / 昨日涨停今日未板 / 停牌缺行 四态；缺失值不落 0
- **口径不变式**：`count(*) FILTER (WHERE is_lu) WHERE trade_date=d` **等于** `market_sentiment_daily.zt_count`（同日）；炸板行数等于 `_BREADTH_SQL` 的 `zb_count`
- enrichment：单元测试断言 SQL 的 `SET` 子句不含判定列（防回归）
- repo：沿用 `build_window_sql_for_explain` 的同源思路，不复制 SQL 文本做计划守卫

**验收（真库实测，数字回填本文件）：** 跑 `persist_stock_facts(2026-09-08)` → `is_lu=75`、`is_zb=39`、总行数=当日候选∪炸板；`uv run alembic heads` 单 head；enrichment 跑完后 `is_lu` 计数**零变化**；空池/外呼失败时 `job:failures` 有记录且 facts 已提交（不回滚）。

- [ ] Step 1: 模型 + 迁移 + 唯一键
- [ ] Step 2: 失败测试（四态行构造 + 口径不变式 + enrichment 不改判定列）
- [ ] Step 3: `fetch_day_broken_board_stocks` + repo 四函数 + service 两函数
- [ ] Step 4: scheduler/worker/schema 接线（17:20 facts→enrichment；worker 支持 `trade_date` retro；双轨同方法）
- [ ] Step 5: 真库实测并回填数字

---

## Task 2: 板块快照——`/market/hot-boards` 改读当日表 + 补 2 列 + 当日 EOD/补写

**为什么：** 端点从不读表（`market_service.py:653-660` Redis + 实时抓取）→ 数据不可历史查询；表缺 `amount`/`flat_count` 两列 → 读表会让前端契约（`HotBoardItem`）缺字段。**注意：A 类，历史缺口不可补。**

**Files:**
- Create: 迁移 `<rev>_extend_sector_moneyflow_snapshot`（+`amount Numeric(20,4)`、+`flat_count Integer`，均为 NULL 允许）
- Modify: `backend/app/models/market_data.py`（`SectorMoneyflowSnapshot` +2 列）
- Modify: `backend/app/core/providers/eastmoney_client.py`（`fetch_sector_moneyflow` 的 `fields` 加 `f6,f106` 并映射 `amount`/`flat_count` —— **一次请求拿全**，不再发第二次 `clist`）
- Modify: `backend/app/repositories/market_data_repo.py`（`upsert_sector_moneyflow` 写入 2 新列；`list_sector_moneyflow` 保留单日；+`list_sector_moneyflow_range(dimension, start, end)` 供 T6）
- Modify: `backend/app/services/market_data_service.py`（`ingest_sector_moneyflow` 写新列；`get_hot_boards` 增"读表优先"分支：当日表行 → 无行回落实时抓取并沿用既有 `source` 判别）
- Modify: `backend/app/scheduler/jobs.py` + `runner.py`（+`board_snapshot_eod_job` 交易日 15:05：收盘最终态写入；+`board_snapshot_rewrite_job` 交易日 15:35：**当日**缺行才重写，跨日不承诺）
- Modify: `backend/app/schemas/task.py` + `workers/market_data_worker.py`（+`board_snapshot_eod`）
- Test: `backend/tests/test_board_snapshot.py`（读表优先/回落两分支；读表行字段集 ⊇ 前端契约；当日缺行不得返回空表）

**口径裁定：** `/market/hot-boards` 的 items 由表行按 `pct_change DESC, board_code ASC`（确定性 tiebreak）排序取 Top-N —— 与上游 `fid=f3` 语义一致，故**不存 `rank`**；`as_of`/`as_of_quality` 语义与 `/market/sector-moneyflow` 统一（同一次抓取、同一行）。

**行集与新鲜度裁决（Ruling 11，实施中实测发现）：** 上游 `clist` **`pz` 上限 100 且忽略更大值**（实测 `pz=1000` 仍只回 100 行：industry `total=496` / concept `506` / region `31`），而 5 分钟轮询按 `fid=f62`（主力净流入）取 Top-100 → 当日表行是"每次轮询 Top-100 的并集、旧行永不刷新"（实测 industry 当日 229 个 code，其中 84 行停在 09:25），且**该行集不是 hot-boards 的 `f3`（涨幅）榜全集**。故：
- 新增 **`capture_kind`** 列（`String(16)`，非空，默认 `'poll'`；取值 `poll` | `eod_full`），行级记录"这一行来自盘中轮询还是收盘全量"
- **15:05 EOD 改为分页全量抓取**（复用既有 `_paged_clist` 模式；industry 5 页 + concept 6 页 + region 1 页 ≈ 12 次请求/日），写 `capture_kind='eod_full'` → 当日**所有板块**在收盘时被刷新一次，"并集+陈旧行"问题自然消解（无需 DELETE 剪枝）
- **读表闸门**：仅当该 `(day, dimension)` 存在 `eod_full` 行（当日已全量捕获）才走表；否则走既有实时路径（盘中本来就更新）。交易日盘中不走表 = 不引入"用 Top-100 子集冒充 f3 全量榜"的错误
- **15:35 重写 job 的守卫从"当日无任何行"改为"当日无 `eod_full` 行"** → 只落了盘中轮询、错过 15:05 的日子也能补上收盘态（修正 Ruling 11 同时发现的第二个缺口）
- `list_sector_moneyflow_range(dimension, start, end, capture_kind=None)` 供 T6 只取完整日
- 延迟项（记入账本，交 final review）：`_is_workday()` 是纯 weekday 判断 → 节假日工作日的行会带节假日日期（既有约定，非本任务引入）

**验收（真库实测，数字回填本文件）：** `/market/hot-boards?category=industry` 读表分支与实时分支**同 code 行资金口径一致**；`amount`/`flat_count` 非空率 ≥99%（上游为空的板块允许 NULL）；EOD 全量抓取的行数 ≈ 上游 `total`（industry 496 / concept 506 / region 31）；**明确记录**：历史 15 个缺失交易日不可补，覆盖率目标 = 上线日起 100%。

- [ ] Step 1: 迁移 `amount`/`flat_count`/`capture_kind` + 模型
- [ ] Step 2: `fetch_sector_moneyflow` fields 扩参（含"不新增第二次请求"的断言测试）+ 分页全量 helper
- [ ] Step 3: 失败测试（读表闸门 `eod_full` / 回落 / 字段集 ⊇ 契约 / 缺行不返空 / 分页覆盖 total）
- [ ] Step 4: `get_hot_boards` 读表分支 + 15:05 全量 EOD + 15:35 补写（守卫=`eod_full` 缺失）+ worker 分支
- [ ] Step 5: 真库实测并回填数字（含 EOD 全量行数 vs 上游 total）

---

## Task 3: 覆盖率只读巡检 + ADR 0009

**为什么：** `DOMAINS` 只 4 个域（`reconciliation_service.py:34`）→ 65%/54.5% 这类洞当天不告警。**但补漏循环硬编码 3 域**（`:228`），不能靠"加域"解决。

**Files:**
- Create: `docs/decisions/0009-market-derived-fact-persistence.md`（+`docs/decisions/index.md` 登记）
- Modify: `backend/app/services/reconciliation_service.py`（新增**只读**覆盖率聚合：`coverage_report(db, *, window_days=30)`；**不修改** 3 域补漏循环与 `sentiment` 特例；`DOMAINS` 的语义在 docstring 里显式分节为"可自愈域 / 只读巡检域"）
- Modify: `backend/app/schemas/market_data.py`（`DataFreshnessOut` +`coverage: list[...]`）
- Modify: `backend/app/api/v1/market_data.py`（`/data-freshness` 组装；只读路径沿用 `apply=False`）
- Modify: `docs/features.md`（数据新鲜度一行补覆盖率语义）
- Test: `backend/tests/test_market_coverage.py`（纯函数 `compute_coverage(trading_days, have_days)`：分母为 0、未来脏行、缺日列表；巡检域不得进入补漏路径的断言）

**Interfaces:**
- `def compute_coverage(trading_days: list[date], have_days: set[date], complete_days: set[date] | None = None, *, window: tuple[date, date] | None = None) -> dict` —— `{have_days, complete_days, trading_days, cover_pct, missing_days, extra_days}`；`trading_days` 为空 → `cover_pct=None`（不产生 0/0）；返回的 `have_days` 与分母同口径（**窗口内交易日**的行数），窗口外/非交易日的脏行进 `extra_days`
- `async def coverage_report(db, *, window_days: int = 30) -> list[dict]` —— 巡检域：`sector_moneyflow` / `sentiment_daily`（**不叫 `sentiment`**：那是可自愈域的名，重名会让"巡检域 vs 补漏域"的边界糊掉；同一张表 `market_sentiment_daily`）/ `sentiment_intraday` / `limit_up_stock_daily` / `dragon_tiger` / `block_trade` / `share_float` / `repurchase`；分母 = **交易日**（`daily_quotes` ∪ `stock_price_limits`，见 `TRADING_DAY_SOURCES`；**不用自然日**），窗口 = 最大(域首行, 日历末日前 N 自然日) → 日历末日。并集是评审 Important #1 的口径修正：**任何单源都可能缺日**（该修正落地时真库 `daily_quotes` 恰好是次源超集、并集没多出日子，但这一天可能再来），单源当分母会把行情自己的洞算成巡检域的洞；并集相对主源多出的每一天都会把 `degraded_trading_calendar` 置 true
- 缺表/缺列（迁移未应用）→ 该域 `missing_table`/`missing_column`，其余域照常渲染（SAVEPOINT 隔离，不 500）；**并集相对主源多出的交易日 → `degraded_trading_calendar=true`**（判据是「并集 != 主源」，不是「主源表不存在」：主源在库但缺日、由次源补上时分母口径已经变了，不能标 ok）
- **测量窗口末端锚定交易日历末日**（不是各域自己的最后一行）：夹到域末日会让整域停更的域分母随之缩小（极端退化到单日）→ `cover_pct=100`/`missing_days=[]`，对「这个域整体停更」完全盲；`window_days` 语义 = 以日历末日前推 N 自然日的测量窗
- 缺行 → 复用既有 `job_alert_service`（不新增机制）；**覆盖率本身不写登记表**（只读端点每次请求都调，会刷出假告警），缺口经 `cover_pct`/`missing_days` 可见

**ADR 0009 必须写清三件事：** ① A/B/C 三条判据；② 与 ADR 0004 的边界（ADR 0004 管"快照型分类数据本身不得回算"，**不禁止**用行情事实做聚合 + 按当前分类归属并显式标注 = 拍板②）；③ 被否决方案（全读时计算 / 全落库含榜单 / 5 分钟全池明细）。外加一条：**产地决定可信度**——`sector_moneyflow` 的"完整日"= 有 `capture_kind='eod_full'` 行的日子（Ruling 11/12），`poll` 行只是盘中子集。

**Ruling 13（覆盖率必须区分"有行"与"完整"）：** T2 之后 `sector_moneyflow` 的行数不再等于数据完整——盘中轮询写的是 inflow-Top-100 子集。故 `coverage` 每域同时返回两个数：`have_days`（有任何行）与 `complete_days`（`sector_moneyflow` 取有 `eod_full` 行的日子；其余域 = `have_days`），`cover_pct` 基于 `complete_days`，`have_days` 原样带回（供与本文档 Why-now 表对拍）。历史 13 天只有 poll 行 → `complete_days=0`，`have_days` 仍应复现 ~65%。

**验收（真库实测，2026-10-12 12:20 +0800，默认 `window_days=30`，评审修复轮 1 的新锚定基准；数字回填本行）：**

| 域 | row_count | have_days | complete_days | trading_days | cover_pct | 缺日 | 区间 |
|---|---|---|---|---|---|---|---|
| `sector_moneyflow` | 4232 | **9** | **0** | 17 | **0.0** | 8 | 2026-09-04..10-12 |
| `sentiment_daily` | 17 | 17 | 17 | 17 | 100.0 | 0 | 2026-09-03..10-09 |
| `sentiment_intraday` | 1 | 1 | 1 | **11** | 9.09 | 10 | 2026-09-17..09-17 |
| `limit_up_stock_daily` | 0 | 0 | 0 | 17 | 0.0 | 17 | —（T1 真库行已被 e2e 清理） |
| `dragon_tiger` | 1077 | 16 | 16 | **17** | **94.12** | 1（10-09） | 2026-08-25..10-08 |
| `block_trade` | 1561 | 16 | 16 | **17** | **94.12** | 1（10-09） | 2026-08-25..10-08 |
| `share_float` | 24562 | 12 | 12 | 17 | 70.59 | 5 | 2026-08-28..10-09 |
| `repurchase` | 458 | 16 | 16 | 17 | 94.12 | 1（09-09） | 2026-08-28..10-09 |

- **Important #2 的可见收益就在表里**：`dragon_tiger`/`block_trade` 的分母从 16 → **17**，`cover_pct` 从虚高的 100.0 → **94.12**，并报出真实缺日 **2026-10-09**（旧口径把窗口末端夹到域末日 `10-08`，这个洞被夹掉了）。`repurchase` 也报出 `09-09`。
- 窗口 = 最大(域首行, 日历末日前 30 自然日) → **日历末日**；分母只随日历变、不随任何单个域停更而缩短。
- **`complete_days = 0` 复现**：`sector_moneyflow` 全表 `capture_kind='eod_full'` 行数为 **0**（全是 `poll`）→ 完整日自 T2 上线起累积。这就是 Ruling 13 要防的"有行冒充完整"。
- **~65% 形状复现**：`window_days=3650`（不限窗口）时 `sector_moneyflow` 为 **12/20 = 60%**（本文档 Why-now 表口径为 13/20 = 65%，差 1 天是实测时点不同 + 板块表仍在被 5 分钟轮询写入）；`complete_days` 仍为 0。
- **`sentiment_intraday` 54.5% 的分母复现**：`trading_days = 11` 与 Why-now 表的 6/11 同形；`have_days=1` 是因为 `tests/test_intraday_snapshot_table.py` 的迁移 down/up 用例**会 DROP+重建 `market_sentiment_intraday`**（全量 suite 每跑一次清一次历史，只留 scheduler 重建后的行）——按 controller caveat，这是"最后一次全量 suite 之后"的真库状态，不是覆盖率实现的问题。
- **`degraded_trading_calendar` 真库为 false（正确）**：实测 `daily_quotes` 是次源的**超集**（829 vs 254，次源相对主源多出 **0** 天）→ 并集没有多出日子，不该标降级；该标记的正例由桩用例 `test_coverage_report_degraded_when_union_fills_primary_gap` 钉住，真库反例由 `@pytest.mark.e2e` 的 `test_coverage_report_calendar_not_degraded_on_real_db` 钉住（**不用真库状态代替契约**）。
- **"整域停更"的构造证据**：`test_stale_domain_old_vs_new_anchor_evidence` 用同一份桩数据并排断言旧口径（分母夹到域末日 → `cover_pct=100`/无缺日）与新口径（分母 3 个交易日 → `33.33`/缺 2 天）。
- 缺表/缺列：单测断言 `missing_table` / `missing_column` + 其余域照常渲染（打桩触发，不真删表）；`bash scripts/doc_gate.sh` 绿（16 项）。

- [x] Step 1: 纯函数 + 单测（分母 0 / 脏行 / 缺日）
- [x] Step 2: `coverage_report` + `DataFreshnessOut.coverage` + 端点
- [x] Step 3: ADR 0009 + index 登记
- [x] Step 4: 真库实测并回填数字 + `docs/features.md` 同步

---

## Task 4: 历史回补（一个脚本，两个域）

**为什么：** `market_sentiment_daily` 只有 21 天；个股事实从 0 开始。可回补区间受**窗口完备性**限制：限价自 2025-09-16 起，安全起点 = 第 16 个交易日 = **2025-10-15**，共 **239 天**。

**Files:**
- Create: `backend/scripts/backfill_market_history.py`（`--domain sentiment|limit_up_stocks`、`--from`/`--to`/`--limit`/`--dry-run`）
- Modify: `backend/app/services/limit_up_service.py`（`persist_snapshot` 增 `fact_source`/`enrich` 参数；新增 **`window_limits_complete(db, as_of, lookback) -> bool`** 守卫；`get_snapshot` 增 `enrich` 开关）
- Modify: `backend/app/repositories/limit_up_repo.py`（Ruling 18 守卫：`upsert_sentiment_daily`/`upsert_limit_up_stock_daily` 增 `backfill_only`，`DO UPDATE ... WHERE <已存储的 source/fact_source> = 'backfill'`；`BACKFILL_SOURCE` 单一字面量）
- Test: `backend/tests/test_backfill_market_history.py`（区间生成只取交易日；窗口不完备 → skip 且原因可辨；`--dry-run` 不写库；重跑收敛；Ruling 18 默认门禁 SQL 守卫 + 真库哨兵日语义）

**实现要点：**
- `sentiment` 域逐日 `persist_snapshot(db, cache=None, as_of=d, fact_source="backfill", enrich=False)`（`limit_up_service.py:377`，已支持任意 `as_of` 且幂等）→ **零新逻辑**；`cache=None` 规避 Redis JSON 回读把 `as_of` 变 str 的既有坑；`enrich=False` 让历史回补不对东财外呼 239 次
- 跳过判据 = 实时路径判据（`is_partial`/`price_limits_missing`/…）**加上** `window_limits_complete`（E2）；逐日记录纳入/跳过原因，输出汇总表
- **升序**执行（`promo_*`/`broken_rate` 依赖前一日 streak）
- `limit_up_stocks` 域调用 T1 的 `persist_stock_facts(..., fact_source="backfill")`（**不**调 `persist_and_enrich_stock_facts`，历史日不增强）
- `pct_chg` **不是**本 Task 的前置（`persist_snapshot` 的闸门是 4000 行下限，与 `pct_chg` 无关）

**验收（真库实测 2026-10-12，数字已回填）：**
- `market_sentiment_daily` 天数 21 → **239**（`2025-10-15 → 2026-10-09`）。**原因分布**：**218 天 `ok`**（真写入/更新）+ **21 天 `kept_realtime`**（该日已有 `local_calc` 实时行、Ruling 18 守卫拒改，`upserted=0`；修复轮 2 前被误报成 `ok`）；从限价首日起算的只读预演 `--from 2025-09-16 --dry-run` = 254 天里 **15 天 `window_limits_incomplete`**（2025-09-16…2025-10-14，正好是限价窗口未完备的前 15 个交易日）→ 239 天，与 E2 推算逐日吻合
- `market_limit_up_stock_daily` 行数 **40,894**（239 天，日均 ≈171；`fact_source` 100% `backfill`；`seal_time`/`seal_fund`/`break_count`/`enrich_source` 非空计数 **0**，全部 NULL、绝无 0 填充）；耗时 54.8s；2026-09-08：**178 行 / is_lu=75 / is_zb=39 / prev_is_lu=95**
- 黄金日 2026-09-08 的既有行**字节不变**：21 行 `local_calc` 的聚合 md5 `0c8e6ffe4e41e736faf8ac17043f8982` 回补前后相同，黄金行 md5 `a5e745db72f1548a2c742dedef7553f0` 不变（Ruling 18 守卫，默认门禁 + 真库哨兵日双向验证）；连跑两次行数不变（sentiment 239 / facts 40,894），且 facts 内容 md5（除 `id`/`observed_at`）`96c5cc73e19a46d8e66871b1372cc58e` 在**全表重建**与**单日重建**两种路径下逐字节一致（含一次"整表被 pytest 迁移循环重建后重跑"）
- **修复轮 2（评审 Important #1/#2，已执行）**：① upsert 的**实际受影响行数**透到调用方（`persist_snapshot`/`persist_stock_facts` 返回 `upserted` + `kept_realtime`），脚本新增原因码 `kept_realtime` 与独立计数列 `skipped_kept_realtime`，`written_days` 只数真写入的天；真库重跑分布 sentiment **218 ok / 21 kept_realtime**、limit_up_stocks **239 ok**；② E2 单测替身改为真的读传入参数（spy 断言收到整段窗口），变异回退成 `missing_price_limit_dates(db, [as_of])` → 2 例变红（含 E2 本体）后逐字还原
- **修复轮 1（controller 裁决①，已执行）**：4 个与重算不一致的 `local_calc` 日（2026-09-10/11/14/15）已按模型契约「纯派生缓存可重建」**显式实时重算**（`persist_snapshot(db, cache=None, as_of=d)`，**不带**回补守卫、`source` 保持 `local_calc`，共 1.26s）：zt 39→40 / 39→40 / 55→57 / 32→32，zb 26 / 25 / 38→39 / 24→25。根因 = **写入时输入未全**（差值恰等于 2026-09-18 08:31/08:34 才落库的行情/限价中今日判为 zt/zb 的行数），`_BREADTH_SQL` 谓词自 2026-09-14 起逐字未变 → 非口径变化。**新增全量不变式 e2e 门禁** `tests/test_backfill_market_history.py::test_sentiment_daily_matches_fact_counts_for_every_day`（每日 `count(is_lu)==zt_count`、`count(is_zb)==zb_count`；零行日报 `missing_fact_rows` 不静默跳过；已做破坏验证）→ 实测 **239 天 0 不一致 / 0 缺行**

- [x] Step 1: 失败测试（区间/守卫/skip 原因/幂等）—— RED 22 failed / 2 passed（stub）→ GREEN 24 passed
- [x] Step 2: 脚本 + `window_limits_complete` + `persist_snapshot(fact_source=)` + Ruling 18 守卫
- [x] Step 3: 真库回补（记录耗时、纳入/跳过分布）
- [x] Step 4: 黄金日一致性 + 计数回填本文件

---

## Task 5（可选，独立不阻塞）: 数据面历史窗口

**为什么降级：** 数据面四表 **27/27 天无洞**；与"情绪标的/资金持续变化"弱相关 → 不进关键路径。

**Files:**
- 复用现有 worker 分支带参数（`market_data_service.py:783/811/923/934/476`），**不写新脚本**；需要的话 `--domain` 并入 T4 脚本
- Test: 逐日循环的日期生成 + 空结果不写空行 + 幂等

**Two 子项（各自独立，可只做其一）：**
- 5a `pct_chg` **365 天**修复（拍板①）：`uv run python scripts/backfill_pct_chg.py --start-date 2025-10-12 --end-date 2026-10-12`（≈241 次 TuShare 调用）；**验收（2026-10-12 实测）**：窗口内 **241/241** 个限价交易日过 `pct_chg≥0.99`（回填前 **17/241**，全部落在 `2026-09-09` 及以后）；**254 天全量口径 17 → 241**（超出 ≥235 目标）；另 **13** 个限价交易日（`2025-09-16 … 2025-10-11`）在 owner 批准的 365 天窗口**之外**，保持未回填、本任务不承诺
- 5b 龙虎榜/大宗/解禁/回购按上游窗口回补；验收：逐表"可回溯到的最早日期"**实测决定**（不预设）——**本轮明确不做**（controller 裁定：四表 27/27 天无洞，属可选子项，另立任务）

- [x] Step 1（5a）: `--limit 5` 冒烟（5 日 / 4.0s）→ 全窗口回填（`2025-10-12 → 2026-10-12`，241 个交易日、1,322,196 行更新、**0** 失败/0 空回、2 分 45 秒、约 241 次 TuShare `daily` 调用）→ 回填统计数字见上方验收行；复跑 5 个历史日幂等收敛（非空数不变）
- [x] Step 2（5b）: 本轮跳过（controller 范围裁定），未消耗 TuShare 配额

---

## Task 6: 统计端点（读时计算，不物化）

**Files:**
- Modify: `backend/app/api/v1/market_data.py`、`backend/app/schemas/limit_up.py`（+trends out）、`backend/app/schemas/market_data.py`（+sector trends out）
- Modify: `backend/app/services/limit_up_service.py`（+`get_sentiment_trends`）、`backend/app/services/market_data_service.py`（+`get_sector_trends`）
- Modify: `backend/app/repositories/limit_up_repo.py`（窗口读 `market_sentiment_daily` + 分位）、`backend/app/repositories/market_data_repo.py`（`list_sector_moneyflow_range`）
- Test: `backend/tests/test_sentiment_trends.py`、`backend/tests/test_sector_trends.py`（分位只用**有效正样本**、样本不足 → `insufficient_history`、端点值 0/100 边界、缺日 ≠ 0 流入）

**6a `GET /api/v1/market/sentiment/trends?days=250`：**
```
{ as_of, as_of_quality, window: {days, from, to, samples},
  series: [{trade_date, zt_count, dt_count, zb_count, broken_rate, promo_1to2, max_streak}],
  percentile: { zt_count, broken_rate, promo_1to2 },        # 仅 3 个（拍板：R6）
  streak: { zt_above_median_days },
  insufficient_history: bool }
```
`samples < 60` → `insufficient_history=true` 且 `percentile=null`。

**6b `GET /api/v1/market/sector-trends?dimension=&days=60`：**
- `available_from`（A 类数据自启用日起）**必填**；`attribution="current_sw_snapshot"`（申万）或 `"point_in_time"`（概念，走 `concept_member_changes`）**必填**
- 指标：连续上榜天数（按当日 `main_net_inflow` Top-N）、连续净流入/流出天数、龙头更替次数、板块涨停家数序列（读 T1 事实 + 归属）
- 缺日语义：**不做插值**，`missing_days` 显式返回；缺日不得计成"0 流入"

**验收（真库实测 2026-10-12，最后一次全量 pytest 之后；数字回填本行）：**
- **6a `days=250`** → `window.samples` = **239**（`2025-10-15 → 2026-10-09`，≥230 目标达成，依赖 T4）、`insufficient_history=false`，三个指标 `sample_count` 全 **239**：`zt_count` p0/p50/p100 = **32/76/226**、`broken_rate` **0.0763/0.2921/0.681**、`promo_1to2` **0.0/0.1591/0.4762**（`promo_1to2` p0=0.0 = "0 是可达的正经样本值"的端点证据，不是缺失）。`as_of=2026-10-09`、`as_of_quality=complete`、`streak.zt_above_median_days=119`
- **6a `days=30`** → `samples=30` → `insufficient_history=true`、`percentile=null`（59/60 边界由单测逐字钉住）
- **6b 当前真库如实**：`capture_kind='eod_full'` 行数 = **0**（T2 的 15:05 EOD job 上线前）→ 三个维度 `available_from=null`、`window.samples=0`、`boards=[]`，**且窗口内 20 个交易日全部列进 `missing_days`**（`2026-09-04 … 2026-10-09`；用 poll 行冒充完整日或把缺日算成 0 流入都会让这组数字变得"好看而错"）
- **6b 完整日闸门的实测证据（`@e2e`，自建自清哨兵行）**：往"窗口内最早交易日"写一行 `board_code='BK9999'` 的 `eod_full` 后 → `available_from=as_of=2026-09-04`、`window.samples=1`、`missing_days` 由 20 → **19**、`leader_change_count=0`（单一完整日没有"更替"）、`attribution="current_sw_snapshot"`；该行的 `main_net_inflow/pct_change/up_count/down_count` 与 `list_sector_moneyflow` 读同一行的值**逐字相等**。**注意**：哨兵日必须落在"窗口内"，写在窗口外的早期日期（如 1999-01-04）会因窗口下界被 `daily_quotes` 起点夹住而从序列中消失——这不是 bug，是"窗口=两端都得有数据"的口径（单测已覆盖）
- **代理不可用的显式声明**：`concept` 的 `attribution="point_in_time"`、`lu_count_source="point_in_time_membership"`；`industry`/`region` 没有"东财板块码 → 个股"的同源映射（`concept_members` 只覆盖 concept）→ `lu_count_source="unavailable"`、序列 `lu_count=null`，**不**用名字相近的申万码冒充
- `docs/features.md` 增两行（情绪趋势与历史分位 / 板块趋势）；`docs/Changelog.md` 一句；全量 **803 passed / 67 deselected / 0 failed**

- [x] Step 1: 纯函数 + 单测（分位/样本/边界/缺日）
- [x] Step 2: repo 窗口查询 + 两个 service + 两端点 + schema
- [x] Step 3: 真库实测并回填数字
- [x] Step 4: `docs/features.md` 同步

---

## Task 7: 前端——历史分位/连续性 chip + sparkline

**Files:**
- Modify: `frontend/src/shared/api/limitUp.ts`（+trends mapper，snake→camel 只在这一处）、`frontend/src/shared/api/marketData.ts`（+sector trends）
- Modify: `frontend/src/features/market/components/SentimentThermometer.tsx`（分位 chip + 复用 `TrendSpark`）、`SectorMoneyflowCard.tsx`（连续性 chip）
- Modify（可选，后置）: 独立趋势视图走 `/market?tab=trends`（**零新路由、零新目录**；Tab 已进 URL）
- Test: `frontend/e2e/marketTrends.spec.ts`（mock：分位 chip 渲染；`insufficient_history` → 显示"样本不足"而非分位；`available_from` 文案上屏；缺日 `--`）

**约束：** 缺失渲染 `--`；分位必须带样本区间与"按当前分类回溯"标注；不新增图表依赖。

**验收：** `npx tsc --noEmit` + `npm run build` + 新 e2e 绿；现有 market e2e（`limitUpSentiment`/`marketTrust`/`marketDataFace`/`marketPolling`/`darkmode`）**零回归**。

**交付实测（2026-10-12，Ruling 23：只做 Step 1–3，跳过可选 Step 4）：**
- **Step 1（mapper）**：snake→camel 只在 `frontend/src/shared/api/limitUp.ts`（`fetchSentimentTrends`：window/series/percentile/streak/insufficientHistory，`percentile=null` 原样保留）与 `marketData.ts`（`fetchSectorTrends`：attributionNote/availableFrom/luCountSource/missingDays）各一处；`window.from` / `percentile` 可空、`broken_rate`/`promo_1to2` 的 NULL **不映射成 0**
- **Step 2（chip）**：`SentimentThermometer` 新增 `trends` prop → 分位区块（每个 chip 带**该字段**的样本窗口「近 239/231/228 交易日分位」+ 样本区间 `from → to`；`insufficient_history=true` ⇒ 只渲染「样本不足」，**无任何分位数字**；字段全 NULL ⇒ 「无有效样本」），趋势线优先复用**同一个** `TrendSpark` 画 trends 长窗口序列（端点失败回落 30 天日历序列，零新增图表依赖）；`SectorMoneyflowCard` 新增连续性 chip 条（`attributionNote` + `自 available_from 起累积` + 连续上榜/净流入-流出/末完整日 `--`/`当日涨停 N 家`，`luCountSource=unavailable` 时不渲染家数；`missing_days` 按缺日披露「不计 0 流入」）
- **Step 3（e2e）**：新增 `frontend/e2e/marketTrends.spec.ts` 6 条（全 `page.route` mock）——RED **5 failed / 1 passed**（组件不存在）→ GREEN **6 passed**；含「insufficient 分支不得出现任何分位梯」的**变异验证**（把 `P50 90` 塞进样本不足文案 → 转红 → 还原转绿）。既有五个 market spec **38 passed / 0 failed**（零回归）；`npx tsc --noEmit` + `npm run build` 绿
- **活链路缺口（如实披露）**：本机 `backend_api` 容器镜像为 **2026-09-29** 构建（早于 Task 6），`/market/sentiment/trends` 与 `/market/sector-trends` 经 dev 代理仍 **404**（`limit-up-ladder`/`sector-moneyflow` 200）⇒ 两处 chip 的**真库渲染未验证**，mock 载荷逐字段对齐 Task 6 的 Pydantic schema（`SentimentTrendsOut` / `SectorTrendsOut`）；活链路验证需 `docker compose build api` 后重跑

- [x] Step 1: api mapper + 类型
- [x] Step 2: 卡片 chip/sparkline
- [x] Step 3: e2e（含样本不足与缺日分支）
- [ ] Step 4（可选）: `/market?tab=trends` 视图

---

## Task 8: 文档收口

- Modify: `docs/features.md`（新表/新端点语义）、`docs/architecture/database-architecture.md`（新表 + 扩列）、`docs/Changelog.md`（逐 Task 一句）、本文件（勾选 + 回填实测数字 + `status: completed`）、`plans/index.md`（状态）、`docs/references/best-practices/*.md`（**仅当**出现无法机械化的教训）

- [ ] Step 1: 逐 Task Changelog 齐全
- [ ] Step 2: 表名/端点名交叉引用一致（`doc_gate`）
- [ ] Step 3: `bash scripts/self_review.sh --full` 绿 + `bash scripts/doc_gate.sh` 绿
- [ ] Step 4: 回填全部「实测」占位并翻状态

---

## 明确不做（YAGNI / 已否决）

| 不做 | 原因 |
|---|---|
| 把 `rankings`/`distribution`/`sectors`/`capital-flow`/`sw-performance` 落库 | 底表 `daily_quotes` 已在库，按日回算即可；只补窗口参数 |
| 5 分钟级全池明细全落 | ≈48 tick × 100–300 行 × 250 天 ≈ 360 万行/年且冗余；只落 EOD 一份 + 保留现有分时聚合（拍板④） |
| 汇总表存 `rank`/`captured_at`/`is_final` | 前端不消费 / 与 `updated_at` 重复 / 可推导 |
| 承诺回补板块快照的历史缺口 | A 类：东财无历史接口，**昨天的板块资金流永远拿不回来**；只保证上线日起 100% |
| 把新巡检域塞进对账 `apply` 补漏循环 | 补漏循环硬编码 3 域；新域没有 `_refetch_*`，塞进去就是假绿 |
| 用当前申万分类回算"**成分本身**"的历史 | ADR 0004：前视 + 幸存者偏差。**允许**用行情事实聚合 + 标注 `attribution`（拍板②） |
| 回补北向资金 | 上游 `moneyflow_hsgt` 停更；只标注 `source_status=discontinued` |
| 新建指标/信号框架；把 `market_sentiment_intraday` 写进收盘序列 | `industry_metrics` registry 与侧分支 signal verification 已存在；盘中口径不得污染收盘权威序列 |

## 风险与回滚

| 风险 | 缓解 |
|---|---|
| 回补写入"假完整"的脏日 | 窗口完备守卫 + 逐日纳入/跳过原因；`fact_source`/`observed_at` 可区分；按 `(trade_date, stock_id)` 删除重跑（幂等） |
| EOD enrichment 覆写判定列 | 列级互斥 + 单测断言 `SET` 子句不含判定列 |
| `/market/hot-boards` 改读表导致响应退化 | 字段集 ⊇ 前端契约的测试；无当日行 → 回落实时抓取（不返空表）；**不改字段名** |
| 回补耗时/配额 | `--from/--to/--limit` 可中断续跑；分年/分域执行并记录调用次数 |
| 分位被当成投资信号 | 文案固定"历史分位（近 N 交易日）"+ 样本区间；不做买卖措辞 |

**回滚：** 全部为新增表/新增列/新增 Job/新增只读端点 → 停 Job + 路由下线 + `alembic downgrade`（新表 DROP、新列 DROP）；`market_sentiment_daily` 的回补行可按日期区间精确删除，不影响实时路径。

## 验收总清单

- [x] ADR 0009 落地并登记（`docs/decisions/0009-market-derived-fact-persistence.md` + index）；`doc_gate` 全绿（16 项）
- [x] `/market/data-freshness` 输出覆盖率（`coverage`），如实呈现旧形状：sector_moneyflow `have_days` 复现 ~60-65% / `complete_days=0`（Ruling 13）、`sentiment_intraday.trading_days=11`（54.5% 的分母）
- [ ] `market_limit_up_stock_daily` 上线，`count(is_lu)` 与 `market_sentiment_daily.zt_count` 逐日一致（不变式测试在库）
- [ ] EOD enrichment 落库且判定列零改写
- [ ] `/market/hot-boards` 读表分支与实时分支同 code 口径一致；`amount`/`flat_count` 非空率 ≥99%
- [ ] `market_sentiment_daily` 21 → ≥235 天，黄金日 2026-09-08 字节不变
- [ ] 个股事实 239 天回补完成（行数与抽样一致性实测回填）
- [x] 两个 trends 端点上线，样本不足返回 `insufficient_history`，`attribution`/`available_from` 必填（真库实测见 Task 6 验收行）
- [ ] 前端 chip + e2e 绿；`tsc`/`build` 绿；现有 market e2e 零回归
- [ ] `bash scripts/self_review.sh --full` 绿；`docs/Changelog.md` 逐 Task 有记录
