---
status: active
scope: 市场热点/情绪/资金数据的日级持久化边界与统计追踪——个股级涨停事实、东财实时源日终捕获、覆盖率只读巡检、情绪与板块趋势端点
touches: backend/app/models/market_data.py, backend/app/migrations/versions/, backend/app/repositories/market_data_repo.py, backend/app/repositories/limit_up_repo.py, backend/app/services/market_data_service.py, backend/app/services/limit_up_service.py, backend/app/services/reconciliation_service.py, backend/app/core/providers/eastmoney_client.py, backend/app/scheduler/jobs.py, backend/app/scheduler/runner.py, backend/app/workers/market_data_worker.py, backend/app/schemas/task.py, backend/app/schemas/market_data.py, backend/app/api/v1/market_data.py, backend/scripts/, backend/tests/, frontend/src/shared/api/, frontend/src/features/market/, docs/decisions/, docs/features.md, docs/Changelog.md
updated: 2026-10-12
next-action: Task 1（market_limit_up_stock_daily + EOD enrichment）在 worktree 分支 feat/market-hotspot-persistence 实施中
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
- Modify: `backend/app/scheduler/jobs.py`（+`limit_up_stock_facts_job` 交易日 17:20；+`intraday_pool_eod_job` 交易日 15:05）
- Modify: `backend/app/scheduler/runner.py`、`backend/app/schemas/task.py`（`MarketDataJobType` +2）、`backend/app/workers/market_data_worker.py`（同队列新分支）
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

**测试策略（必须有能在默认门禁失败的测试）：**
- 行构造：涨停 / 炸板 / 昨日涨停今日未板 / 停牌缺行 四态；缺失值不落 0
- **口径不变式**：`count(*) FILTER (WHERE is_lu) WHERE trade_date=d` **等于** `market_sentiment_daily.zt_count`（同日）；炸板行数等于 `_BREADTH_SQL` 的 `zb_count`
- enrichment：单元测试断言 SQL 的 `SET` 子句不含判定列（防回归）
- repo：沿用 `build_window_sql_for_explain` 的同源思路，不复制 SQL 文本做计划守卫

**验收（真库实测，数字回填本文件）：** 跑 `persist_stock_facts(2026-09-08)` → `is_lu=75`、`is_zb=39`、总行数=当日候选∪炸板；`uv run alembic heads` 单 head；EOD enrichment 跑完后 `is_lu` 计数**零变化**。

- [ ] Step 1: 模型 + 迁移 + 唯一键
- [ ] Step 2: 失败测试（四态行构造 + 口径不变式 + enrichment 不改判定列）
- [ ] Step 3: `fetch_day_broken_board_stocks` + repo 四函数 + service 两函数
- [ ] Step 4: scheduler/worker/schema 接线（17:20 事实、15:05 EOD enrichment；双轨同方法）
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

**验收（真库实测，数字回填本文件）：** `/market/hot-boards?category=industry` 读表分支与实时分支**同 code 行资金口径一致**；`amount`/`flat_count` 非空率 ≥99%（上游为空的板块允许 NULL）；**明确记录**：历史 15 个缺失交易日不可补，覆盖率目标 = 上线日起 100%。

- [ ] Step 1: 迁移 2 列 + 模型
- [ ] Step 2: `fetch_sector_moneyflow` fields 扩参（含"不新增第二次请求"的断言测试）
- [ ] Step 3: 失败测试（读表优先 / 回落 / 字段集 ⊇ 契约 / 缺行不返空）
- [ ] Step 4: `get_hot_boards` 读表分支 + EOD/当日补写 job + worker 分支
- [ ] Step 5: 真库实测并回填数字

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
- `def compute_coverage(trading_days: list[date], have_days: set[date]) -> dict` —— `{have_days, trading_days, cover_pct, missing_days}`；`trading_days` 为空 → `cover_pct=None`（不产生 0/0）
- `async def coverage_report(db, *, window_days: int = 30) -> list[dict]` —— 巡检域：`sector_moneyflow` / `sentiment` / `sentiment_intraday` / `limit_up_stock_daily` / `dragon_tiger` / `block_trade` / `share_float` / `repurchase`；分母 = `daily_quotes` 交易日（**不用自然日**）
- 缺行 → 复用既有 `job_alert_service`（不新增机制）

**ADR 0009 必须写清三件事：** ① A/B/C 三条判据；② 与 ADR 0004 的边界（ADR 0004 管"快照型分类数据本身不得回算"，**不禁止**用行情事实做聚合 + 按当前分类归属并显式标注 = 拍板②）；③ 被否决方案（全读时计算 / 全落库含榜单 / 5 分钟全池明细）。

**验收（真库实测）：** `/market/data-freshness` 的 `coverage` 里 `sector_moneyflow.cover_pct ≈ 65`、`sentiment_intraday ≈ 54.5`（与本文档 Why-now 表一致）；`limit_up_stock_daily` 在 T1 之前不存在时**不得 500**（返回 `missing_table` 或跳过并标注）；`bash scripts/doc_gate.sh` 绿。

- [ ] Step 1: 纯函数 + 单测（分母 0 / 脏行 / 缺日）
- [ ] Step 2: `coverage_report` + `DataFreshnessOut.coverage` + 端点
- [ ] Step 3: ADR 0009 + index 登记
- [ ] Step 4: 真库实测并回填数字 + `docs/features.md` 同步

---

## Task 4: 历史回补（一个脚本，两个域）

**为什么：** `market_sentiment_daily` 只有 21 天；个股事实从 0 开始。可回补区间受**窗口完备性**限制：限价自 2025-09-16 起，安全起点 = 第 16 个交易日 = **2025-10-15**，共 **239 天**。

**Files:**
- Create: `backend/scripts/backfill_market_history.py`（`--domain sentiment|limit_up_stocks`、`--from`/`--to`/`--limit`/`--dry-run`）
- Modify: `backend/app/services/limit_up_service.py`（`persist_snapshot` 增 `fact_source` 参数；新增 **`window_limits_complete(db, as_of, lookback) -> bool`** 守卫）
- Test: `backend/tests/test_backfill_market_history.py`（区间生成只取交易日；窗口不完备 → skip 且原因可辨；`--dry-run` 不写库；重跑收敛）

**实现要点：**
- `sentiment` 域逐日 `persist_snapshot(db, cache=None, as_of=d)`（`limit_up_service.py:377`，已支持任意 `as_of` 且幂等）→ **零新逻辑**；`cache=None` 规避 Redis JSON 回读把 `as_of` 变 str 的既有坑
- 跳过判据 = 实时路径判据（`is_partial`/`price_limits_missing`/…）**加上** `window_limits_complete`（E2）；逐日记录纳入/跳过原因，输出汇总表
- **升序**执行（`promo_*`/`broken_rate` 依赖前一日 streak）
- `limit_up_stocks` 域调用 T1 的 `persist_stock_facts(..., fact_source="backfill")`
- `pct_chg` **不是**本 Task 的前置（`persist_snapshot` 的闸门是 4000 行下限，与 `pct_chg` 无关）

**验收（真库实测，数字回填本文件）：**
- `market_sentiment_daily` 天数 21 → **≥235**（239 天内允许个别真降级日，差值逐日列原因）
- `market_limit_up_stock_daily` 行数 ≈ Σ(候选∪炸板)（**实测值回填**）；2026-09-08：`is_lu=75`、`is_zb=39`
- 黄金日 2026-09-08 的既有行**字节不变**（回补不得改写已正确行）；连跑两次行数不变

- [ ] Step 1: 失败测试（区间/守卫/skip 原因/幂等）
- [ ] Step 2: 脚本 + `window_limits_complete` + `persist_snapshot(fact_source=)`
- [ ] Step 3: 真库回补（记录耗时、纳入/跳过分布）
- [ ] Step 4: 黄金日一致性 + 计数回填本文件

---

## Task 5（可选，独立不阻塞）: 数据面历史窗口

**为什么降级：** 数据面四表 **27/27 天无洞**；与"情绪标的/资金持续变化"弱相关 → 不进关键路径。

**Files:**
- 复用现有 worker 分支带参数（`market_data_service.py:783/811/923/934/476`），**不写新脚本**；需要的话 `--domain` 并入 T4 脚本
- Test: 逐日循环的日期生成 + 空结果不写空行 + 幂等

**Two 子项（各自独立，可只做其一）：**
- 5a `pct_chg` **365 天**修复（拍板①）：`uv run python scripts/backfill_pct_chg.py --start-date <today-365> --end-date <today>`（≈241 次 TuShare 调用）；验收：254 个限价交易日里过 `pct_chg≥0.99` 的日数 **17 → ≥235**
- 5b 龙虎榜/大宗/解禁/回购按上游窗口回补；验收：逐表"可回溯到的最早日期"**实测决定**（不预设）

- [ ] Step 1（5a）: `--limit 5` 冒烟 → 分年回填 → 回填统计数字
- [ ] Step 2（5b）: 逐域回补（若做）→ 记录最早可回溯日

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

**验收（真库实测）：** 6a `days=250` → `samples ≥ 230`（依赖 T4）；`days=30` → `insufficient_history=true`；6b `available_from` = 表内最早日，与 `/market/sector-moneyflow` 当日值一致；`docs/features.md` 增两行。

- [ ] Step 1: 纯函数 + 单测（分位/样本/边界/缺日）
- [ ] Step 2: repo 窗口查询 + 两个 service + 两端点 + schema
- [ ] Step 3: 真库实测并回填数字
- [ ] Step 4: `docs/features.md` 同步

---

## Task 7: 前端——历史分位/连续性 chip + sparkline

**Files:**
- Modify: `frontend/src/shared/api/limitUp.ts`（+trends mapper，snake→camel 只在这一处）、`frontend/src/shared/api/marketData.ts`（+sector trends）
- Modify: `frontend/src/features/market/components/SentimentThermometer.tsx`（分位 chip + 复用 `TrendSpark`）、`SectorMoneyflowCard.tsx`（连续性 chip）
- Modify（可选，后置）: 独立趋势视图走 `/market?tab=trends`（**零新路由、零新目录**；Tab 已进 URL）
- Test: `frontend/e2e/marketTrends.spec.ts`（mock：分位 chip 渲染；`insufficient_history` → 显示"样本不足"而非分位；`available_from` 文案上屏；缺日 `--`）

**约束：** 缺失渲染 `--`；分位必须带样本区间与"按当前分类回溯"标注；不新增图表依赖。

**验收：** `npx tsc --noEmit` + `npm run build` + 新 e2e 绿；现有 market e2e（`limitUpSentiment`/`marketTrust`/`marketDataFace`/`marketPolling`/`darkmode`）**零回归**。

- [ ] Step 1: api mapper + 类型
- [ ] Step 2: 卡片 chip/sparkline
- [ ] Step 3: e2e（含样本不足与缺日分支）
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

- [ ] ADR 0009 落地并登记；`doc_gate` 全绿
- [ ] `/market/data-freshness` 输出覆盖率，如实呈现 65% / 54.5%（先复现旧数字）
- [ ] `market_limit_up_stock_daily` 上线，`count(is_lu)` 与 `market_sentiment_daily.zt_count` 逐日一致（不变式测试在库）
- [ ] EOD enrichment 落库且判定列零改写
- [ ] `/market/hot-boards` 读表分支与实时分支同 code 口径一致；`amount`/`flat_count` 非空率 ≥99%
- [ ] `market_sentiment_daily` 21 → ≥235 天，黄金日 2026-09-08 字节不变
- [ ] 个股事实 239 天回补完成（行数与抽样一致性实测回填）
- [ ] 两个 trends 端点上线，样本不足返回 `insufficient_history`，`attribution`/`available_from` 必填
- [ ] 前端 chip + e2e 绿；`tsc`/`build` 绿；现有 market e2e 零回归
- [ ] `bash scripts/self_review.sh --full` 绿；`docs/Changelog.md` 逐 Task 有记录
