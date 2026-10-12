---
status: draft
scope: 市场热点/情绪/资金数据的日级持久化边界与统计追踪——个股级涨停事实、东财实时源日终捕获、覆盖率对账门禁、情绪与板块趋势端点
touches: backend/app/models/market_data.py, backend/app/migrations/versions/, backend/app/repositories/market_data_repo.py, backend/app/repositories/limit_up_repo.py, backend/app/services/market_data_service.py, backend/app/services/limit_up_service.py, backend/app/services/reconciliation_service.py, backend/app/scheduler/jobs.py, backend/app/scheduler/runner.py, backend/app/workers/market_data_worker.py, backend/app/schemas/task.py, backend/app/schemas/market_data.py, backend/app/api/v1/market_data.py, backend/scripts/, backend/tests/, frontend/src/shared/api/, frontend/src/features/market/, docs/decisions/, docs/features.md, docs/Changelog.md
updated: 2026-10-12
next-action: 待 owner 评审；批准后从 Phase 0 Task 0.1（ADR 0009 固化落库/回补边界）开始
---

# 市场热点数据持久化与趋势追踪 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让"每天的热点"成为可查询、可统计、可复现的事实——情绪指标（涨停/炸板/连板/晋级率）与热点标的（个股级涨停事实）按交易日沉淀，东财实时源（板块资金流/热门板块榜/盘中涨停池明细）在收盘后当天落库不再丢失，并在此之上提供分位/连续性/龙头更替等统计追踪视图。

**Why now（用户反馈 + 真库实测，2026-10-12）:** 行情原始事实存得很深（`daily_quotes` 829 个交易日、`stock_price_limits` 254 个交易日 100% 覆盖），但"派生层"存得又浅又有洞，而"不可回补的实时源"基本没存：

| 表 | 行数 | 天数 / 交易日 | 覆盖 | 判定 |
|---|---|---|---|---|
| `daily_quotes` | 4,427,962 | 829（2023-05-09→2026-10-09） | — | A/B 类事实底座 |
| `stock_price_limits` | 1,395,289 | 254（2025-09-16→） | **100%** | 涨停判定权威口径 |
| `daily_basic_indicators` | 2,010,361 | 369 | — | 事实底座 |
| `market_sentiment_daily` | 21 | 21（2026-09-03→） | **100%** | 无洞，但**只有 3 周历史** |
| `market_moneyflow_daily` | 139 | 139 | **100%** | 可用 |
| `dragon_tiger_entries` / `block_trades` | 1,780 / 2,879 | 27 / 27 | **100%** | 只有 ~1 个月历史 |
| `share_floats` / `stock_repurchases` | 25,031 / 878 | 18 / 30 | — | 只有 ~1 个月历史 |
| `sector_moneyflow_snapshots` | 5,583 | 13 / 20 | **65%** | 缺 15 个交易日（含 2026-10-08） |
| `market_sentiment_intraday` | 277 | 6 / 11 | **54.5%** | 且只存 4 个计数，个股明细被丢弃 |
| `northbound_daily` | 41 | — | — | 上游 `moneyflow_hsgt` 已停更，标注不改源 |
| `industry_signals` / `_events` / `_evaluations` | 24 / **0** / **0** | — | — | 信号框架只有定义、无数据（侧分支在建） |

**完全不落库**：`/market/hot-boards`（东财实时榜，Redis 60s，key 带 `today`：`market_service.py:653`）；`rankings`/`distribution`/`sectors`/`capital-flow`/`sw-performance`（读时 SQL over `daily_quotes`——**这类不需要落库**，见「不做清单」）。

**Architecture:** 三层分工——
1. **事实层（日级，落库）**：新增 `market_limit_up_stock_daily`（个股级涨停/炸板/连板事实，含本地口径与东财 EOD 增强两套字段）；`sector_moneyflow_snapshots` 扩列承载板块快照（不再新建近似表）。
2. **派生层（可回算，落库仅为口径冻结与查询性能）**：`market_sentiment_daily` 由 21 天回补到 `stock_price_limits` 覆盖的 254 天。
3. **统计层（读时计算，不物化）**：趋势/分位/连续性端点，读时 SQL over 事实层 + Redis 缓存；**不新建指标框架**（等确有多指标注册需求再复用 `industry_metrics` 的 registry/rollup 范式）。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Alembic + APScheduler（`Asia/Shanghai`）+ RabbitMQ Worker + Redis + PostgreSQL；前端 React 18 + antd 5 + TanStack Query + ECharts；Playwright e2e。**零新增第三方依赖**。

**Spec:** 本文件即 spec（含 2026-10-12 真库实测数字）；口径依据 `docs/design/market-data-trust-and-realtime.md`（D1 本地自算为主干 / Web 仅装饰层）、`docs/design/limit-up-sentiment.md`、[ADR 0004](../docs/decisions/0004-current-snapshot-not-backfilled.md)。

## 数据分类判据（本计划的立论基础）

| 类 | 定义 | 处置 | 例 |
|---|---|---|---|
| **A · 不可回补** | 上游只给"当下"，无历史接口 | **必须当天落库**，否则永久丢失 | 东财板块资金流（`fetch_sector_moneyflow(dimension)` 无日期参数，`eastmoney_client.py:236`）、热门板块榜、盘中涨停池个股明细（封板时间/封单/炸板次数） |
| **B · 可回算且无前视偏差** | 只用**当日**点内事实（行情 + 限价） | 落库（口径冻结 + 性能），**允许回补** | 情绪 KPI（`_BREADTH_SQL`）、个股 `is_lu/touched/streak/N天M板`（`_WINDOW_SQL`） |
| **C · 依赖当前分类/成分快照** | 上游只给"今天谁属于谁" | **禁止用于历史回算**（ADR 0004：前视/幸存者偏差），只向前累积或读时标注 | 申万 L1/L3 归属（`sw_industry_members` 无生效日期）、指数成分；**例外**：概念成分已有追加式差分表（`concept_member_changes`，2026-09-20 起），可按时点重建 |

> 硬约束：所有落库行必须带 `source` + `as_of`（或 `trade_date`）+ `observed_at`；回补行与当日实时行必须可区分（`source` 或 `is_backfill`）。禁止把"事后算的"与"当天看到的"混成一行——这条来自 [`best-practices/metrics-rule-engine.md`](../docs/references/best-practices/metrics-rule-engine.md) 的前视偏差教训。

## Global Constraints

- 分层铁律：`app/api/v1` → `app/services` → `app/repositories` → `app/models`；scheduler/worker 只调 service；**双轨制**（APScheduler 与 RabbitMQ Worker 共用同一 service 方法，不得各自实现抓取）；新队列必须注册 `app/core/mq.py` 的 `QUEUES`（本计划不需要新队列）
- **读端点不得写库**（唯一例外仍是 `get_snapshot` 的东财盘中增强，既有契约）；补数一律走 scheduler/worker/CLI
- 价格列一律 `Numeric(12,4)` 或更宽（金额 `Numeric(20,4)`），**禁止 `Float`** 承载价格/金额
- 时间判断一律 `ZoneInfo("Asia/Shanghai")`；`CronTrigger` 必须显式 `timezone="Asia/Shanghai"`
- 迁移：`uv run alembic heads` 必须只有 1 个 head；模型与 DDL 同 PR 落地
- 幂等：所有落库走 `ON CONFLICT`（键带上改变语义的维度）；回补脚本必须可中断续跑、重跑收敛
- 缺失值语义：缺失渲染 `--`（绝不 `?? 0`）；分位/比值在样本不足时返回 `insufficient_history`，**不返回假分位**
- 门禁：每个 Task 收尾 `git status --porcelain` + `bash scripts/slop_scan.sh` + `bash scripts/self_review.sh`（后端改动加 `--full`）；新增/改端点必须同步 `docs/features.md`；不触 Tier-1 纯计算热点则不需要 `bash scripts/bench.sh`（若改了 `limit_up_calculator`/rollup 等热点则必须跑）
- 真库验证优先：计划里每个「实测 N」都必须用真库/真接口重测并就地更新（本地栈 `127.0.0.1:5433`，`uv run python -c` 或 `psql`）

---

## Phase 0 · 度量与决策先行（把"洞"变可见，先写边界再动手）

### Task 0.1: ADR 0009「市场热点数据的落库与回补边界」

**Files:**
- Create: `docs/decisions/0009-market-derived-fact-persistence.md`
- Modify: `docs/decisions/index.md`（新增一行）

**内容要点（三条判据 A/B/C 的正式化）：**
- 决策：**A 类必须当天落库**；**B 类落库但不作为真相来源**（真相是 `daily_quotes` + `stock_price_limits`），回补必须标注来源与判据日；**C 类禁止参与历史回算**（沿用 ADR 0004，并明确"行情派生事实不属于快照型分类数据"，避免被误读为一律禁止回补）
- 记录被否决的方案：① 全部读时计算（A 类信息永久丢失）② 全部落库含榜单类（多一份会漂移的副本）③ 5 分钟级全池明细全落（≈360 万行/年且冗余）
- 与 ADR 0004 的关系：**不 supersede**，而是划清适用边界，双向引用

**验收：** `bash scripts/doc_gate.sh` 通过（ADR 只增不改 + index 登记）；ADR 头部 `status: accepted`、`date: 2026-10-12`。

- [ ] Step 1: 写 ADR 正文（背景 / 决策 / 影响 / 备选与否决原因）
- [ ] Step 2: `docs/decisions/index.md` 登记 0009
- [ ] Step 3: `bash scripts/doc_gate.sh` 绿

---

### Task 0.2: 覆盖率进入对账与 `/market/data-freshness`

**背景（实测）：** `reconciliation_service.py:34` 的 `DOMAINS` 只有 4 个域（`daily_quotes`/`daily_basic`/`price_limits`/`sentiment`）→ `sector_moneyflow_snapshots` 65%、`market_sentiment_intraday` 54.5% 这种洞**当天不会告警**，`data-freshness` 端点也照不到。

**Files:**
- Modify: `backend/app/services/reconciliation_service.py`（`DOMAINS` + `_ROW_MODEL` + 每个域的"当日应有行"判据）
- Modify: `backend/app/services/market_data_service.py` 或新增 `market_coverage_service.py`（覆盖率计算：每表近 N 交易日 `have/trading_days`）
- Modify: `backend/app/schemas/market_data.py`（`DataFreshnessOut` 增加 `coverage[]`）
- Modify: `backend/app/api/v1/market_data.py`（`/data-freshness` 组装覆盖率）
- Test: `backend/tests/test_market_coverage.py`（纯单测：覆盖率算法 + 缺失日判定；含"分母为 0 不产生 0/0"与"未来脏行不计入"）

**Interfaces:**
- `DOMAINS` 扩展为：`daily_quotes, daily_basic, price_limits, sentiment, sector_moneyflow, sentiment_intraday, limit_up_stock_daily, dragon_tiger, block_trade, share_float, repurchase`
- `async def coverage_report(db, *, window_days: int = 30) -> list[dict]` → `[{table, first_day, last_day, have_days, trading_days, cover_pct, missing_days: [...]}]`（分母 = `daily_quotes` 的交易日，不用自然日）
- 缺失当日/近窗口行 → 复用既有 `job_alert_service` 告警通道（不新增机制）

**测试策略：** 覆盖率是纯计算（给定交易日集合与各表已有日集合）→ 抽成纯函数 `compute_coverage(trading_days, have_days) -> dict` 做无 DB 单测；真库行为放 Task 0.2 的实测步骤。

**验收（实测数字必须回填本文件）：**
- 真库调用 `/market/data-freshness` → `coverage` 里 `sector_moneyflow` 的 `cover_pct ≈ 65`、`sentiment_intraday ≈ 54.5`，与本文档「Why now」表一致（不一致就说明算错）
- 故意删一天行（临时库或事务回滚）→ 告警出现、`missing_days` 列出该日

- [ ] Step 1: 纯函数 + 单测（先失败）
- [ ] Step 2: `DOMAINS`/`_ROW_MODEL` 扩展 + 每域判据
- [ ] Step 3: `DataFreshnessOut.coverage` + 端点组装
- [ ] Step 4: 真库实测并回填数字
- [ ] Step 5: `docs/features.md` 数据新鲜度一行补充覆盖率语义

---

## Phase 1 · 停止失血（A 类：今天不存，永远没有）

### Task 1.1: 新表 `market_limit_up_stock_daily` + 日终写入

**为什么是这张表：** 用户要的"市场情绪标的持续追踪"（谁连续几天是龙头、在几板位置、何时炸板）现在没有可统计对象——`market_sentiment_daily` 24 个字段全是聚合，个股只剩 `max_streak_symbol` 一个代码；而涨停池**个股明细每 5 分钟抓一次被丢弃**（`market_data_repo.py:502-540`：只写 `len(rows)` 与 `max(streak)`，`dt_count/zb_count` 硬编码 0）。

**Files:**
- Modify: `backend/app/models/market_data.py`（+`MarketLimitUpStockDaily`）
- Create: `backend/app/migrations/versions/<rev>_add_market_limit_up_stock_daily.py`
- Modify: `backend/app/repositories/limit_up_repo.py`（`upsert_limit_up_stock_daily` / `list_limit_up_stock_daily` / `limit_up_stock_daily_missing_dates`）
- Modify: `backend/app/repositories/market_data_repo.py`（`enrich_limit_up_stock_daily_from_pool`：只更新东财拥有的列）
- Modify: `backend/app/services/limit_up_service.py`（+`persist_stock_facts(db, as_of)`）
- Modify: `backend/app/services/intraday_sentiment_service.py`（盘中池明细 → 结构化行，供 enrichment 复用）
- Modify: `backend/app/scheduler/jobs.py`（+`limit_up_stock_facts_job`，交易日 17:20，晚于 16:50 限价补漏、紧随 17:15 情绪聚合）
- Modify: `backend/app/scheduler/runner.py`、`backend/app/schemas/task.py`（`MarketDataJobType` +`"limit_up_stock_facts"`）、`backend/app/workers/market_data_worker.py`（同队列新分支）
- Test: `backend/tests/test_limit_up_stock_daily.py`（纯函数/行构造 + mock repo）、`backend/tests/test_limit_up_repo.py`（追加计划守卫：新查询走既有索引，不引入逐股 probe）

**表结构（粒度 = 每股每交易日一行，日行数约 150–450）：**

| 列 | 类型 | 语义 |
|---|---|---|
| `trade_date` | `Date` | 交易日（唯一键之一） |
| `stock_id` / `symbol` | `int` / `String(16)` | 业务键 + 溯源（对齐 `stock_price_limits` 的 stock_id 约定） |
| `is_lu` / `touched` / `is_zb` | `bool` | 收盘涨停 / 盘中触板 / 炸板（`touched AND NOT is_lu`）——**口径来自交易所限价** |
| `streak` / `days_span` / `boards_in_window` / `missing_days` | `int` | 复用 `limit_up_calculator.n_day_m_board` / `missing_days`（与梯队同源，禁止另写一套） |
| `seal_time` / `seal_fund` / `break_count` | `String(8)` / `Numeric(20,4)` / `int`，可空 | **仅东财可得**（历史留 NULL，绝不填 0） |
| `board_name` / `em_streak` | `String` / `int`，可空 | 东财口径对照字段（不覆盖 `is_lu/streak`，两口径差异本身是统计素材） |
| `fact_source` | `String(16)` | `local_calc` \| `backfill` |
| `enrich_source` | `String(16)`，可空 | `eastmoney_eod` \| `NULL`（NULL = 该日无东财增强，如历史回补） |
| `observed_at` | `TIMESTAMPTZ` | 写入时刻（区分"当天看到的"与"事后回补的"） |

**唯一键：** `(trade_date, stock_id)`。

**Interfaces:**
- `async def persist_stock_facts(db, as_of: date, *, fact_source: str = "local_calc") -> dict[str, int]`
  - 复用 `limit_up_repo.fetch_limit_up_window`（`as_of_prev` + `window_start` 由 `list_recent_trade_dates` 给出，与 `get_snapshot` 同参）+ `fetch_day_breadth`；只写"当日涨停 ∪ 当日炸板"的行
- `async def enrich_from_pool(db, as_of: date, pool_rows: list[dict]) -> int` —— 只 `SET seal_time/seal_fund/break_count/board_name/em_streak/enrich_source`，**不碰** `is_lu/streak`
- CLI：`backend/scripts/backfill_limit_up_stock_daily.py --from --to`（见 Task 2.2）

**测试策略：**
- 纯单测：行构造（涨停/炸板/停牌缺行三态）、`is_zb = touched AND NOT is_lu`、缺失值不落 0
- 口径回归：黄金日 2026-09-08 → `is_lu` 行数 **=75**、炸板行数 **=39**（与 `market_sentiment_daily` 同源断言，防止两处口径漂移）
- **不变式（必须写成测试）**：`SELECT count(*) FROM market_limit_up_stock_daily WHERE trade_date=d AND is_lu` **必须等于** `market_sentiment_daily.zt_count`（同一天）——把"两套口径"钉死
- repo：计划守卫（沿用 `build_window_sql_for_explain` 的同源思路，禁止复制 SQL 文本）

**验收（实测回填）：** 真库跑 `persist_stock_facts(2026-09-08)` → 行数、`is_lu` 计数、`zb` 计数三项与 `market_sentiment_daily` 当日行一致；`uv run alembic heads` 单 head。

- [ ] Step 1: 模型 + 迁移 + 唯一键
- [ ] Step 2: 失败测试（行构造 + 口径不变式）
- [ ] Step 3: repo 三函数 + service `persist_stock_facts`
- [ ] Step 4: scheduler/worker/schema 接线（双轨同方法）
- [ ] Step 5: 真库实测并回填数字

---

### Task 1.2: 东财盘中池明细 EOD 落库（不再丢弃）

**背景：** `upsert_intraday_snapshot` 拿到 `rows` 只取 `len` 与 `max_streak`（`market_data_repo.py:517-540`），个股/封板时间/封单/炸板次数全部丢弃。

**Files:**
- Modify: `backend/app/scheduler/jobs.py`（`intraday_sentiment_poll_job` 保持 5 分钟聚合不变；**新增**收盘后 `intraday_pool_eod_job`，交易日 15:05 抓一次池 → 调 Task 1.1 的 `enrich_from_pool`）
- Modify: `backend/app/scheduler/runner.py`（+CronTrigger 15:05）
- Test: `backend/tests/test_intraday_pool_eod.py`（mock client：池为空 / 池含停牌 / 池含与本地口径不一致的票 → 断言只写 enrichment 列，`is_lu/streak` 不被改写）

**口径裁定（写进代码注释与 ADR）：** 行以**本地自算**为准；东财只填它独有的列 + 对照列。两口径差异（如东财`lbc` vs 本地 `streak`）保留为数据，不强行对齐——**对齐会把两套口径的差异藏起来**。

**验收：** 真库 + 真接口（交易时段外可用上一交易日池）跑一次 → 当日行 `enrich_source='eastmoney_eod'`，且 `is_lu` 计数与运行前完全一致（零改写）。

- [ ] Step 1: 失败测试（enrichment 只动自己的列）
- [ ] Step 2: `enrich_from_pool` + EOD job + 注册
- [ ] Step 3: 真库实测（记录池行数与 enrichment 覆盖行数）

---

### Task 1.3: 板块快照 EOD 最终态 + 热门板块复用同表落库

**背景：** `/market/hot-boards` 完全不落库（Redis 60s，`market_service.py:653`）；`sector_moneyflow_snapshots` 只有盘中 5 分钟轮询的覆盖式写入（`ON CONFLICT DO UPDATE`，`market_data_repo.py:69-83`）→ 当天只留"最后一拍"，且 13/20 天有洞（含 2026-10-08）。上游 `fetch_sector_moneyflow(dimension)` / `fetch_board_list(category)` 同源同形（同一 `clist` 接口、同一 `fs` 映射、`_map_board_row` 同一字段集）→ **不新建近似表**。

**Files:**
- Create: 迁移 `<rev>_extend_sector_moneyflow_snapshot`（+`amount Numeric(20,4)`、+`flat_count int`、+`rank int`、+`captured_at TIMESTAMPTZ`、+`is_final bool`）
- Modify: `backend/app/models/market_data.py`（`SectorMoneyflowSnapshot` 扩列）
- Modify: `backend/app/services/market_data_service.py`（`ingest_sector_moneyflow(db, *, is_final=False)` 写入新列；新增 `ingest_board_snapshot_eod(db)`；`get_hot_boards` 增"读表优先"分支：读当日表行 → 无当日行回落实时抓取并标 `source`）
- Modify: `backend/app/repositories/market_data_repo.py`（`list_sector_moneyflow` 增窗口查询 `list_sector_moneyflow_range(dimension, start, end)` → 供 Task 3.2 用）
- Modify: `backend/app/scheduler/jobs.py` + `runner.py`（当日 15:05 `board_snapshot_eod_job`；收盘后补漏 17:50 `board_snapshot_reconcile_job`：对近 5 个交易日缺失日补抓）
- Modify: `backend/app/schemas/task.py` + `workers/market_data_worker.py`（+`board_snapshot_eod`、+`board_snapshot_backfill{trade_date}`）
- Test: `backend/tests/test_board_snapshot.py`（读表优先/回落实时两条分支；缺当日行不返回空表而是回落；`rank` 由上游顺序写入而非事后拍脑袋排序）

**口径裁定：**
- `hot-boards` 端点语义从"实时抓取"改为"当日快照读表（Redis 60s 读缓存）"——盘中仍是 5 分钟新度，收盘后成为**可历史查询的当日事实**；`as_of_quality` 沿用当日口径标注
- 补漏只补"有 `daily_quotes` 交易日但无快照行"的日期，且**只对当日可得的日期生效**（东财无历史接口，超过当天无法补——这正是 A 类的定义，写进 ADR）

**验收（实测回填）：**
- 真库 `/market/hot-boards?category=industry` → 当日表行数与上游 Top-100 一致；`/market/sector-moneyflow` 与 `hot-boards` 的同一 `board_code` 资金口径一致（同一次抓取）
- 覆盖率从 65% 起：执行 `board_snapshot_backfill` 后，近 5 个交易日的缺失日补齐（历史更早的洞**无法**补，明确记录）

- [ ] Step 1: 迁移扩列 + 模型
- [ ] Step 2: 失败测试（读表优先 / 回落 / rank 来源）
- [ ] Step 3: ingest 扩参 + EOD/补漏 job + worker 分支
- [ ] Step 4: `/market/hot-boards` 读表分支 + `as_of` 标注统一
- [ ] Step 5: 真库实测并回填数字

---

## Phase 2 · 回补可回算的历史（B 类）

### Task 2.0: 历史 `pct_chg` 回填（前置依赖，复用既有脚本）

**为什么是前置：** `market_day_service.is_day_complete` 的判据是 `rows ≥ 0.9×universe AND pct_chg 非空率 ≥ 0.99 AND 该日有限价`。真库实测（254 个限价交易日）：**行数全部达标，但过 `pct_chg ≥ 0.99` 的只有 17/254 天**——`daily_quotes.pct_chg` 在 2026-09 之前非空率 ≈ 0.5%，2026-10 才 100%。不回填它，任何"完整日"判据都会把历史判成 `partial`（也影响分布/榜单的 `as_of_quality`）。

**Files:**
- 复用（不改）：`backend/scripts/backfill_pct_chg.py`（已有：`--years 3` / `--limit 5` 冒烟 / 幂等 / 可中断续跑；源码注释已写明禁止用 `LAG(close)` 替代）
- Modify: `backend/app/services/reconciliation_service.py`（如需把 `pct_chg` 非空率纳入缺口判据，与 Task 0.2 合并）
- Test: 复用脚本 `--limit 5` 冒烟 + 真库前后非空率对比

**执行注意：** ~3 年 ≈ **750 次 TuShare 调用**（脚本注释给出）→ 按配额分批、避开盘中；先 `--limit 5` 验证，再按年滚动。

**验收（实测回填）：** 回填后重跑 `complete_days` 统计（本文档 Why-now 的 SQL）→ 254 天中 `pct_chg≥0.99` 通过数从 **17 → ≥240**；`/market/distribution` 对历史日的 `as_of_quality` 抽样由 `partial` 转 `complete`。

- [ ] Step 1: `--limit 5` 冒烟 + 记录配对差异
- [ ] Step 2: 按年分批全量回填（运维步骤，记录调用次数与耗时）
- [ ] Step 3: 重跑完整性统计并回填本文件数字

---

### Task 2.1: 情绪 KPI 日史回补（21 → ≥240 天）

**Files:**
- Create: `backend/scripts/backfill_sentiment_history.py`（`--from`/`--to`/`--dry-run`）
- Modify: `backend/app/services/limit_up_service.py`（`persist_snapshot` 增加 `fact_source`/回补语义参数，写 `source='local_calc'` 与 `observed_at`；**跳过策略不变**）
- Test: `backend/tests/test_backfill_sentiment.py`（纯函数：日期区间生成只取交易日、跳过判据日的三态返回；mock repo 断言 `upsert` 调用序列）

**实现要点（复用，不重写）：**
- 逐日调用既有 `persist_snapshot(db, cache=None, as_of=d)`（`limit_up_service.py:377`，已支持任意 `as_of` 且幂等）
- **升序**回补（`promo_*`/`broken_rate` 依赖前一日 streak）；`cache=None` 规避 Redis JSON 回读把 `as_of` 变 str 的既有坑（源码注释有记录）
- 跳过判据沿用实时路径（`is_partial` / `price_limits_missing` / `no_limit_up_rows`…），但**回补模式改用 Task 0.2 的覆盖判据**：只要求"该日行数达标 + 该日有限价"，不把 `pct_chg` 当门槛（涨停判定根本不用它，`_BREADTH_SQL` 只用 close/high/up_limit/down_limit）——这一点必须写进脚本 docstring，否则会与 Task 2.0 的语义混淆

**验收（实测回填）：**
- `SELECT count(DISTINCT trade_date) FROM market_sentiment_daily` → **≥240**（254 限价交易日减去真降级日；差值必须逐日列出原因）
- 黄金日 2026-09-08 重算结果与既有行字节一致（75/1/39/0.3421/…）——**回补不得改写已有正确行**

- [ ] Step 1: 失败测试（区间/跳过/幂等）
- [ ] Step 2: 脚本 + `persist_snapshot` 回补语义
- [ ] Step 3: 真库回补（记录实际写入天数与跳过原因分布）
- [ ] Step 4: 黄金日一致性 + 计数回填本文件

---

### Task 2.2: 个股级涨停事实回补（Task 1.1 表的 254 天）

**Files:**
- Create: `backend/scripts/backfill_limit_up_stock_daily.py`（`--from`/`--to`/`--dry-run`/`--limit`）
- Modify: `backend/app/services/limit_up_service.py`（`persist_stock_facts(..., fact_source="backfill")`）

**实现要点：** 每个 `as_of` 一次 `fetch_limit_up_window` + 一次广度/炸板查询 → upsert；历史日 `seal_*/break_count` 一律 NULL（东财无历史，A 类定义）；`fact_source='backfill'`、`observed_at=now()` 与当日实时行区分。

**性能预算（实测回填）：** 单窗查询实测 ~48ms（`limit_up_repo.py:104` 注释记录的 16 交易日窗口）；254 天预计数分钟量级（含炸板扫描），实测数字回填本行。

**验收（实测回填）：**
- 行数 ≈ Σ(每日涨停 ∪ 炸板)，抽样 5 个交易日的 `is_lu` 计数与 `market_sentiment_daily.zt_count` 一致（Task 1.1 的不变式在历史区间同样成立）
- 2026-09-08：`is_lu=75`、`is_zb=39`
- 幂等：连跑两次行数不变

- [ ] Step 1: 脚本 + `--limit` 冒烟
- [ ] Step 2: 真库回补 254 天（记录耗时）
- [ ] Step 3: 抽样一致性 + 计数回填本文件

---

### Task 2.3: 数据面历史窗口回补（~1 个月 → 上游允许的上限）

**Files:**
- Modify: `backend/app/services/market_data_service.py`（`ingest_share_floats(days)` / `ingest_repurchases(days)` 支持大窗口；`ingest_market_moneyflow_daily(days)` 窗口上限）
- Create: `backend/scripts/backfill_market_data_face.py`（`--domain dragon_tiger|block_trade|share_float|repurchase|market_moneyflow --from --to`）
- Modify: `backend/app/schemas/task.py` + `workers/market_data_worker.py`（复用既有 job 类型带参数，不新增类型）
- Test: `backend/tests/test_market_data_backfill.py`（逐日循环的日期生成 + 空结果不写空行 + 幂等）

**可回补性（已核源码）：** `dragon_tiger`/`block_trades` 有 `trade_date` 参数（`market_data_service.py:783/811`）；`share_floats`/`repurchases` 有 `days` 窗口（`:923/934`）；`market_moneyflow` 的东财 `fflow/daykline` 有 `days`（`:476`）。**北向不做**（上游停更，只标注）。

**验收（实测回填）：** 各表 `count(distinct trade_date)` 目标与实测值逐项写回本文件（TuShare `top_list`/`block_trade` 的实际可回溯窗口以实测为准——**实测决定目标，不预设**）。

- [ ] Step 1: 窗口参数 + 脚本
- [ ] Step 2: 逐域真库回补（记录每域可回溯到的最早日期）
- [ ] Step 3: 覆盖表更新 + 数字回填

---

## Phase 3 · 统计与追踪（读时计算，不物化）

### Task 3.1: `GET /api/v1/market/sentiment/trends`

**Files:**
- Modify: `backend/app/api/v1/market_data.py`、`backend/app/schemas/limit_up.py`、`backend/app/services/limit_up_service.py`（+`get_trends(db, cache, days)`）、`backend/app/repositories/limit_up_repo.py`（窗口查询 + 分位）
- Test: `backend/tests/test_sentiment_trends.py`（纯函数：分位算法只用**有效样本**、样本不足返回 `insufficient_history`、`pct_rank` 端点值 0/100 边界）

**契约（响应形状）：**
```
{ as_of, as_of_quality, window: {days, from, to, samples},
  series: [{trade_date, zt_count, dt_count, zb_count, broken_rate, promo_1to2, promo_2to3, max_streak}],
  percentile: { zt_count: 82.5, broken_rate: 31.0, ... },      # 当日值在窗口内的分位（0-100）
  streak: { zt_above_median_days: 3, ... },                     # 连续同向天数
  insufficient_history: false }
```
- **只基于有效正样本**：比值类字段为 NULL 的日子不计入该字段分布（沿用 best-practice：负值/缺失不得进入分位样本）
- 窗口默认 250 交易日、上限 500；`samples < 60` → `insufficient_history=true` 且 `percentile` 为 `null`（**不给假分位**）

**验收：** 真库 `days=250` → `samples ≥ 240`（依赖 Task 2.1/2.0 完成）；当日 `zt_count` 分位与手算一致（抽查 3 天）；样本数 30 时 `insufficient_history=true`。

- [ ] Step 1: 纯函数 + 单测（分位/样本/边界）
- [ ] Step 2: repo 窗口查询 + service + 端点 + schema
- [ ] Step 3: 真库实测并回填数字
- [ ] Step 4: `docs/features.md` 增行

---

### Task 3.2: `GET /api/v1/market/sector-trends`

**Files:**
- Modify: `backend/app/api/v1/market_data.py`、`backend/app/schemas/market_data.py`、`backend/app/services/market_data_service.py`（+`get_sector_trends`）、`backend/app/repositories/market_data_repo.py`（`list_sector_moneyflow_range`）
- Test: `backend/tests/test_sector_trends.py`（纯函数：连续上榜/连续净流入天数、龙头更替次数、缺日不把"缺"当"0 流入"）

**契约要点：**
- 参数：`dimension=industry|concept|region`、`days≤120`（受限于 A 类数据**从启用日起累积**，响应必须回 `available_from` 让 UI 如实标注）
- 指标：板块**连续上榜天数**（按当日 `main_net_inflow` Top-N）、**连续净流入/流出天数**、**龙头更替次数**、板块涨停家数序列（读 Task 2.2 的个股事实 + 申万/概念归属）
- **归属口径**：申万归属是 C 类快照 → 响应里只提供"按**当前**分类回溯"的口径并标注（`attribution="current_sw_snapshot"`）；概念归属走差分表可给点时点口径（`attribution="point_in_time"`，2026-09-20 起）。**两种口径必须字段级可区分。**

**验收：** 真库 `dimension=industry&days=60` → `available_from` = 启用日、序列无洞（缺日显式列出）；同一板块的资金序列与 `/market/sector-moneyflow` 当日值一致。

- [ ] Step 1: 纯函数 + 单测（连续性/缺日语义）
- [ ] Step 2: repo 窗口查询 + service + 端点 + schema
- [ ] Step 3: 真库实测并回填数字
- [ ] Step 4: `docs/features.md` 增行

---

### Task 3.3（显式延后，不在本计划内）: 信号与前向验证

`industry_signal_events`/`_evaluations` 在 main 上是 **0 行**，验证框架在侧分支 `feat/industry-signal-verification`（未合入）。**本计划不造第二套框架**：等该分支合入 main 后，再评估把"情绪冰点/沸点""板块资金三日连续净流入""龙头断板"等规则接入同一套 signal/evaluation 机制（另立计划）。

---

## Phase 4 · 呈现与收口

### Task 4.1: 前端——历史分位、sparkline 与趋势视图

**Files:**
- Modify: `frontend/src/shared/api/limitUp.ts`（+trends mapper，snake→camel 只在这一处）、`frontend/src/shared/api/marketData.ts`（+sector trends）
- Modify: `frontend/src/features/market/components/SentimentThermometer.tsx`（分位 chip + 复用 `TrendSpark`）、`SectorMoneyflowCard.tsx`（板块连续性 chip）
- Create: `frontend/src/pages/market-trends/`（情绪/资金趋势页，路由 `/market/trends`，复用 ECharts 封装与 `SectionCard`）
- Modify: `frontend/src/app/router/index.tsx`、`frontend/src/pages/market/index.tsx`（入口链接）
- Test: `frontend/e2e/marketTrends.spec.ts`（mock 端点：分位 chip 渲染、`insufficient_history` 时不显示分位而显示"样本不足"、`available_from` 文案上屏、缺日渲染 `--`）

**约束：** 缺失值渲染 `--`；分位/样本区间与"自 X 起累积"必须显式标注（延续既有 as_of/口径徽标语言）；不引入新的图表依赖。

**验收：** `npx tsc --noEmit` + `npm run build` + 新 e2e 用例通过；现有 market 相关 e2e 不回归（`limitUpSentiment`/`marketTrust`/`marketDataFace`/`marketPolling`/`darkmode`）。

- [ ] Step 1: api mapper + 类型
- [ ] Step 2: 卡片分位/连续性 chip
- [ ] Step 3: 趋势页 + 路由 + 入口
- [ ] Step 4: e2e（含样本不足与缺日分支）

---

### Task 4.2: 文档收口

**Files:** `docs/features.md`（新端点 + 新表语义一行）、`docs/Changelog.md`（每个 Task 一句话）、`docs/architecture/database-architecture.md`（新表/扩列）、本文件（勾选 + 回填实测数字 + `status: completed`）、`plans/index.md`（状态更新）、`docs/references/best-practices/*.md`（仅当出现**无法机械化**的教训）

- [ ] Step 1: 逐 Task 的 Changelog 条目确认齐全
- [ ] Step 2: `docs/features.md` / `database-architecture.md` 交叉引用一致（表名、端点名、`metric_key` 无漂移）
- [ ] Step 3: `bash scripts/self_review.sh --full` 绿 + `bash scripts/doc_gate.sh` 绿
- [ ] Step 4: 回填全部「实测」占位并翻状态

---

## 明确不做（YAGNI / 已否决）

| 不做 | 原因 |
|---|---|
| 把 `rankings`/`distribution`/`sectors`/`capital-flow`/`sw-performance` 落库 | 底表 `daily_quotes` 已在库，按日回算即可；落库只多一份会漂移的副本。**只补窗口参数** |
| 5 分钟级**全池**明细全落 | ≈48 tick × 100–300 行 × 250 天 ≈ 360 万行/年且大量冗余；只落 EOD 一份 + 保留现有分时聚合 |
| 保留板块快照的盘中轨迹（改唯一键加 `captured_at`） | 需要新的保留策略与清理任务；先按"当日最终态"落地，有真实需求再另立计划 |
| 用当前申万分类回算历史板块归属 | ADR 0004：前视 + 幸存者偏差，不可检测 |
| 回补北向资金 | 上游 `moneyflow_hsgt` 停更；只标注 `source_status=discontinued` |
| 新建指标/信号框架 | `industry_metrics` registry/rollup 与侧分支 signal verification 已存在；先用读时 SQL，需求明确后再复用 |
| 把情绪 `mode=intraday` 写入 `market_sentiment_daily` | 既有契约：盘中口径不污染收盘权威序列（`docs/design/market-data-trust-and-realtime.md:95`） |

## 风险与回滚

| 风险 | 缓解 |
|---|---|
| 回补把历史"假完整"（脏日/降级日写入） | 回补判据独立于实时判据并**逐日列出纳入/跳过原因**；`source`/`observed_at` 可区分；发现错误按 `(trade_date, stock_id)` 删除重跑（幂等） |
| 东财接口字段/数量漂移（A 类不可补） | 落库时存 `source` + 原始 `rank`/`amount`；对账 Task 0.2 每天比对"应有行 vs 实有行" |
| 新增日终 job 与既有盘中 job 写同一表产生顺序竞争 | 两条写路径列级互斥（EOD 只写 enrichment 列）；测试锁定"不覆写 `is_lu/streak`" |
| 回补窗口过长导致 TuShare 配额/时长 | 脚本支持 `--from/--to/--limit` 可中断续跑；分年/分域执行并记录配额消耗 |
| 前端分位被当成"投资信号" | 文案固定为"历史分位（近 N 交易日）"并强制带样本区间；不做买卖建议措辞 |

**回滚：** 全部为新增表/新增列/新增 Job/新增端点——回滚 = 停用 Job + 路由下线 + `alembic downgrade`（新表 DROP、新列 DROP）；`market_sentiment_daily` 回补行可按 `source`/日期区间精确删除，不影响实时路径。

## 验收总清单（本计划完成时逐项填实测数字）

- [ ] ADR 0009 落地并在 `decisions/index.md` 登记；`doc_gate` 16+ 项绿
- [ ] `/market/data-freshness` 输出覆盖率；`sector_moneyflow` 65% / `intraday` 54.5% 被**如实**呈现（先复现旧数字，再验证补齐后变化）
- [ ] `market_limit_up_stock_daily` 上线且 `is_lu` 计数与 `market_sentiment_daily.zt_count` 逐日一致（不变式测试在库）
- [ ] 东财 EOD enrichment 落库（`enrich_source='eastmoney_eod'` 行数 > 0，且 `is_lu` 零改写）
- [ ] `pct_chg` 全量回填后 `complete_days` 由 17/254 → ≥240/254
- [ ] `market_sentiment_daily` 天数由 21 → ≥240，黄金日 2026-09-08 字节不变
- [ ] 数据面 4 表历史窗口按实测上限回补完成（逐表数字回填）
- [ ] `/market/sentiment/trends`、`/market/sector-trends` 上线，样本不足返回 `insufficient_history`
- [ ] 前端趋势页 + 分位 chip + e2e 绿；`tsc`/`build` 绿
- [ ] `bash scripts/self_review.sh --full` 绿；`docs/Changelog.md` 逐 Task 有记录
