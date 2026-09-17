# 市场数据可信度与实时化改造 — 设计（spec）

> 状态：**已批准，实施中**（2026-09-17）。计划见 [plans/2026-09-17-market-sentiment-overhaul.md](../../plans/2026-09-17-market-sentiment-overhaul.md)。
> 上游设计参考：[limit-up-sentiment.md](./limit-up-sentiment.md)（连板梯队与市场情绪口径）、[data-source.md](./data-source.md)（数据源调研）。
> 承接：[plans/2026-09-17-data-sync-self-healing.md](../../plans/2026-09-17-data-sync-self-healing.md)（对账式自愈数据面）——本设计修掉它留下的两个判据盲区。

## 1. 背景：两条实测证据链

**证据链 A（读侧失真）**：2026-09-17 `daily_quotes` 只有 1 行（`data_init` 单股覆盖任务的窗口上界取 `date.today()`，拉到当日未收盘数据入库）。因为所有"按日聚合"端点都取 `max(trade_date)` 作 as_of，这一天把整站打坏：

| 端点 | 实测返回 |
|---|---|
| `/market/limit-up-ladder`（缺省） | `as_of=09-17`、`degraded_reason=price_limits_missing`、0 档梯队 |
| `/market/limit-up-ladder?date=2026-09-16` | 5 档梯队、zt=91、无降级（数据其实齐全） |
| `/market/hot-boards?category=industry` | 1 行（1 只股） |
| `/market/hot-boards?category=concept` | `[]`（硬编码 return） |
| `/market/rankings` | 1 项，且 `is_latest_trading_day=True` |
| `/market/sectors`、`/market/capital-flow`、`/market/sw-industry/performance` | 各 1 项 |

**证据链 B（写侧静默故障）**：
- `share_float_daily_job` 每交易日 17:30 报 `asyncpg.exceptions.InterfaceError: the number of query arguments cannot exceed 32767`，APScheduler 仍打 "executed successfully"，`share_floats` 自 2026-09-04 起 13 天未更新。
- `daily_quotes.pct_chg` 在 09-14/09-15/09-16 只有 1 行非空（其余 NULL）；`reconciliation_service` 的完整性判据只看行数，**列内容坏了永远不会被重拉**。
- `sector_moneyflow_snapshots` 最新 09-08，而读路径 `list_sector_moneyflow(db, _today_sh(), ...)` 严格过滤"今天"→ 卡片永久空；同期东财 clist 实测能返回 100 行（含 `board_code`/主力净额/领涨股）。
- `northbound_daily` 最新 09-07，上游 TuShare `moneyflow_hsgt` 近 30 日窗口实测最新只到 2026-08-21（数据源事实停更），卡片仍画无标注的 stale 曲线。

**结论**：问题的根不是"任务没跑"，而是**判据只看行数、读侧只认最大日期、失败只写日志**。三者叠加让"坏数据"和"没数据"都长成"正常但空"。

## 2. 目标与非目标

**目标**
1. 任何按日聚合的读路径，**不再被单条脏行/缺列整体打坏**，且能自证"数据截至哪一天、质量如何"。
2. 交易时段能看到**盘中**情绪，且与**收盘**口径在文案与字段上明确区分。
3. 热门板块**可下钻、可比、有新鲜度**（真实板块码 + 成分股 + 成交额/主力净额）。
4. 任务失败**不再静默**：失败可被 `/market/data-freshness` 观测。

**非目标（YAGNI）**
- 不做分笔/Level-2、不做逐日明细表、不引入 WebSocket 推送、不做移动端专门适配。
- 不重构指数 K 线链路（`index_dailies` 与 `sse_index_snapshots` 现状保留）。
- 不为前端引入单元测试框架（前端验证 = `tsc` + Playwright e2e；见 §7）。
- 不删既有端点（改语义而不新增平行端点，避免两套口径）。

## 3. 设计决策（已拍板）

| # | 决策 | 理由 |
|---|---|---|
| D1 | **盘中口径以东财为主**（涨停池），本地 EOD 自算保留为**收盘权威口径**；两者并列、各自标注 | 东财涨停池实测已含 `streak/days/boards/seal_time/seal_fund/break_count/board_name`，是现成的盘中真值；本地自算是回填/校验与历史一致性所需。原设计文档的"不做 Web-first"（§211）在本条上作废 |
| D2 | **热门板块改用东财真实板块体系**（行业/概念/地域），带真实 `code` 与成分股 | 现 `csrc_desc`/`province` 不是板块，`code` 恒空导致下钻与高亮全部失效；申万 L3 卡保留但标注自己的口径，不混用 |
| D3 | **北向卡保留但标注"数据源已停更"**，数据版图同步降级为"规划中" | 上游事实停更，删除会丢历史可读性；标注后不再误导 |
| D4 | **失败可观测**：任务失败写 Redis 告警键并在 freshness 端点暴露 | APScheduler 的 "executed successfully" 只表示函数返回，不能作为成功信号 |
| D5 | **完整性判据升级为日级三元组**：行数 ≥ 0.9×universe **且** `pct_chg` 非空率 ≥ 0.99 **且** 该日 `stock_price_limits` 存在 | 行数够但列全 NULL 的日子（09-14/15）必须被判为不完整并触发重拉 |
| D6 | **列表型端点统一包成对象** `{as_of, as_of_quality, items}` | 现状 `distribution/sectors/capital-flow` 根本没有 as_of，无法在前端标注新鲜度 |
| D7 | **当日全市场派生数据只从一次快照加载**（Redis `market:day:rows:{day}`，TTL 300s），各分组在服务端从该快照计算 | 消灭 5 个端点各跑一遍 597ms 的同类 SQL |
| D8 | **读路径回落**：按"某张表今天有数据"过滤的读路径改为"该表最近可用日 + 标注" | 采集漏一天不该让卡片空一天 |

## 4. 架构与契约

### 4.1 日级完整性（新增 `backend/app/services/market_day_service.py`）

```python
MarketQuality = Literal["complete", "partial", "fallback"]

@dataclass(frozen=True)
class MarketDay:
    day: date
    quality: MarketQuality     # complete=当日即最新完整日；partial=当日不完整但无更早完整日；fallback=回落到更早完整日
    reason: str | None         # partial/fallback 时为 "latest_day_incomplete" 等
    rows: int                  # 该日 daily_quotes 行数
    universe: int              # stocks 表在市数量（阈值分母）
    pct_chg_ratio: float       # 该日 pct_chg 非空比例
    limits_present: bool       # 该日 stock_price_limits 是否有行

async def resolve_latest_complete_day(db, *, cache=None) -> MarketDay: ...
def is_day_complete(rows: int, universe: int, pct_chg_ratio: float, limits_present: bool) -> bool: ...
```

- 候选日 = `max(daily_quotes.trade_date)`；不完整时沿 `list_recent_trade_dates` 向前最多回看 5 个交易日，取第一个完整日（`quality="fallback"`）。
- 阈值常量：`MIN_ROW_RATIO = 0.9`、`MIN_PCT_CHG_RATIO = 0.99`、`FALLBACK_LOOKBACK_DAYS = 5`。
- 缓存键 `market:day:latest_complete`，TTL 60s（写路径无法精确失效，用短 TTL 兜底）。
- 全部 8 个读端点与 `limit_up_service` 的 `latest_quote_date` 取值改走它。**这是唯一一处"最新日"判据**。

### 4.2 失败可观测（`backend/app/services/job_alert_service.py`）

```python
async def record_job_failure(job_id: str, error: str) -> None      # Redis ZSET/HSET，TTL 7d
async def list_job_failures(limit: int = 20) -> list[dict]         # → /market/data-freshness.failed_jobs
```
- `scheduler/jobs.py` 里所有 `except Exception: logger.exception(...)` 的 job 统一追加 `await record_job_failure(<job_id>, str(exc))`（Redis 不可用时静默，不因告警二次失败）。
- `DataFreshnessOut` 新增 `failed_jobs: list[JobFailureOut]`。

### 4.3 盘中/收盘双口径（`limit_up_service`）

- 既有端点新增查询参数 `mode: Literal["close", "intraday"] = "close"`；默认不变（向后兼容）。
- `mode=intraday`：源 = 东财涨停池（当日），产出与本地口径**同构**的 snapshot（`echelons/kpis/sectors/yesterday` 字段名一致），并按东财池字段补齐 `seal_time/seal_fund/break_count`；板块归属用池内 `board_name`。
- snapshot 新增 `as_of_label: str`：收盘口径 = `"收盘"`；盘中 = `"盘中 HH:MM"`（取自采集时刻，上海时区）。
- 盘中口径**不写** `market_sentiment_daily`（收盘口径的历史序列不被盘中数据污染）。
- 新增表 `market_sentiment_intraday(id, trade_date, captured_at, zt_count, dt_count, zb_count, max_streak)`，`unique(trade_date, captured_at)`；交易日 09:30–15:00 每 5 分钟采集（`job_defaults` 已全局 `misfire_grace_time=None, coalesce=True`）。分时情绪曲线读该表。

### 4.4 板块数据面（东财体系）

- `GET /market/hot-boards?category={industry|concept|region}` **改实现**为东财三套板块，字段：`{id, code(BKxxxx), name, changePercent, amount, mainNetInflow, mainNetRatio, upCount, flatCount, downCount, leaders:[{symbol,name,changePercent}]}`；**去掉 `concept: return []`**。
- `GET /market/boards/{board_code}/stocks?limit=` 新增：成分股列表（东财 `fs=b:BKxxxx` clist），字段与 `StockTable` 所需一致。
- 卡片与列表页：真实排序维度（涨跌幅/成交额/主力净额/家数）、搜索、切分类保留选中、点条目开成分股抽屉。
- 申万 L3 卡（`SwL3LimitUpBoard`）保留，卡头显式标注"申万三级口径"。

### 4.5 前端刷新与标注

- 新增 `useMarketPolling()`（`src/features/market/hooks/`）：以 `marketStatus()`（交易日 + 09:30–15:00 上海时区）决定 `refetchInterval`——盘中 30s，其余关闭。
- 所有行情中心卡片接入；`staleTime` 与后端 TTL 对齐（快照类 300s、实时类 60s）。
- 统一 query key（同一端点只用一个 key 前缀），消除 `sectors`/`hot-boards` 的重复请求。
- 所有卡片显示 `as_of` + 口径徽标（实时/盘中/T-1/数据源停更）。

## 5. 数据修复（一次性，已授权）

1. 删除 2026-09-17 的脏行（`daily_quotes` 1 行，来源 `data_init` 覆盖任务）。
2. 回填 09-14 / 09-15 的 `daily_quotes.pct_chg`（重跑既有 `ingest_daily_quotes`，幂等）。
3. 建 `market_sentiment_intraday` 表（Alembic 迁移）。
每步先备份受影响行数，可回滚；执行写入 `docs/Changelog.md` 运维条目。

## 6. 验收标准（每个阶段都必须通过才进入下一阶段）

| 阶段 | 验收 |
|---|---|
| Phase 0 | 人为保留 09-17 脏行时：8 个端点全部返回 09-16 完整数据且带 `as_of_quality=fallback`；`resolve_latest_complete_day` 对"行数够但 pct_chg 全 NULL"的日子判为不完整；`share_float` 分片后重跑幂等；失败 job 出现在 `/market/data-freshness.failed_jobs` |
| Phase 1 | 同一端点本地实测 P95 < 50 ms（改读 `pct_chg` 列 + 单次快照）；同一页面同端点只发 1 次请求；`bash scripts/bench.sh` 绿 |
| Phase 2 | 盘中口径（用录制夹具，不依赖当时是否开市）梯队与东财池一致且 `as_of_label="盘中 HH:MM"`；收盘口径不变；盘中不写 `market_sentiment_daily`；分时曲线有点序列 |
| Phase 3 | 每张卡都有 as_of 与口径徽标；键盘可达（原生链接）；点板块 → 看到成分股；`/market` 暗色无白底残留；北向卡标注停更 |

**总体门禁（最终）**：`bash scripts/self_review.sh --full` + `bash scripts/bench.sh` + 后端 pytest（排除 e2e/bench）+ 前端 `tsc --noEmit`/`build` + **经 gateway 实跑 E2E**（`E2E_BASE_URL=http://localhost`，失败项与干净 main 基线对账）。

## 7. 测试策略

- **后端**：纯函数与判据用单测钉死（完整性三元组、盘中快照构造、SQL 契约、分片批量写、失败告警）；涉及外部接口的用固定夹具（录制的东财响应 JSON），**不在测试里打真实外网**。
- **前端**：`npx tsc --noEmit` + `check:design` + Playwright e2e（新增：盘中/收盘切换、下钻抽屉、as_of 徽标、键盘可达、暗色）。
- **E2E 运行方式**：worktree 的代码通过 `docker compose build api`（镜像名固定 `stock-bot-backend:local`）+ `docker cp` 前端 `dist/` 进运行中的 frontend 容器来"部署"，复用现有 compose 栈（不新建 compose project，避免端口冲突）。
- 每个任务 TDD：先写失败测试 → 跑失败 → 最小实现 → 跑通过 → 提交。

## 8. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 东财接口限流/字段变动 | 盘中口径全部包在 try/except 内，失败回落收盘口径并在 `as_of_label` 标注；夹具测试不依赖实时性 |
| 列表端点改对象形状破坏前端 | 前后端同任务内一起改并跑 e2e；`tsc` 兜底 |
| 完整性阈值误判（如极端缩量日） | 阈值集中为常量 + 单测覆盖边界；`quality` 只是标注，仍返回数据 |
| 建表迁移失败 | `migrate` 服务在 compose 中先于 api 运行（`service_completed_successfully`），迁移幂等 |
