# 数据定时同步根治方案：对账式自愈数据面（Reconciliation-based Self-healing Data Plane）

> 状态：**Phase 1+2 已实施并实机验收通过（2026-09-17）**，Phase 3 待按需排期；L6 已划掉（后续部署服务器）。
> 验收记录：删 9/15 四域数据 → 启动对账自动补回；首跑另发现并补齐 9/03-9/11 七天情绪历史盲区。
>
> 目标：行情/情绪数据面的完整性**不再依赖"定时任务准点跑"**。宿主睡眠、Docker Desktop Resource Saver、容器重启、任务异常，任何一种停摆之后，系统在恢复点自动收敛到完整状态；派生缓存随数据自动失效；数据新鲜度可观测。设计参考业内最佳实践（Kubernetes controller 的 reconcile loop / level-triggered 思想、数据工程 "expected vs actual" 对账模式）。

## 1. 背景与问题定性

三次同构事故（2026-09-10 / 09-11 / 09-14、09-15 / 09-16），每次都靠手动补数收场：

| 日期 | 事故形态 | 直接原因 |
|---|---|---|
| 9/10、9/11、9/14 | daily_quotes 缺交易日 | 回补链路覆盖不一致（partial 行挡住"存在即跳过"判据） |
| 9/15 | 盘后任务链整体未跑 | 宿主挂起 + `misfire_grace_time=1s` 全量静默丢弃 |
| 9/15、9/16 | 二次复现，两日全缺 | 同上（9/16 16:21 栈启动后宿主再次挂起，scheduler 日志零输出） |
| 9/17 | 手动补齐后发现趋势线仍只 1 天 | `market:limit-up:calendar:{days}` Redis 缓存固化旧结果集 |

**共同模式：正确性寄托在"调度触发"这个最脆弱的环节上。** 开发机宿主会睡、容器会被 Resource Saver 挂起——这不是异常，是这台机器的常态。方案必须把正确性从"触发层"挪到"对账层"。

## 2. 根因模型（为什么反复发生）

```
宿主挂起（常态，不可根除）
   └→ APScheduler 默认 misfire_grace_time=1s → 到点任务静默丢弃   【触发层脆弱】
        └→ 回补 job 只补 T-1 + "存在即跳过" + 无补漏窗口          【放大器 A】
             └→ 停摆一天 = 永久空洞，只有人发现才补               【无自愈】
                  └→ 手动补数后派生缓存固化旧结果                 【放大器 B】
```

三个独立缺陷叠加：触发不可靠（L1 治）、语义只补一天（L3 治）、无对账兜底与缓存失效（L2/L4 治）。

## 3. 设计原则

1. **Level-triggered，非 edge-triggered**：任务的语义是"让数据达到期望状态"，不是"在时刻 T 执行动作 X"。任何时刻重跑都收敛到同一结果。
2. **正确性不依赖准点**：cron 只负责"让数据尽快出现"（timeliness）；完整性由对账循环保证（correctness）。二者解耦。
3. **幂等可重放**：所有 ingest 保持 upsert / DO NOTHING，对账可任意重跑、可并发重跑（`max_instances=1` 防同任务自叠）。
4. **完整性以行数量级判定，不以存在性判定**："该日有行"≠"该日完整"（9/16 的 2 行 partial 死锁教训）。阈值 = `0.8 × stocks 表活跃数`（全市场 ≈5546 行）。
5. **派生层随底层数据失效**：落库动作与缓存失效在同一服务方法内完成，不依赖调用方记得清缓存。

## 4. 分层方案

### L1 调度器硬化（配置级，治"迟到即丢弃"）

`create_scheduler()` 统一注入 job_defaults：

```python
scheduler = AsyncIOScheduler(
    timezone="Asia/Shanghai",
    job_defaults={"misfire_grace_time": None, "coalesce": True, "max_instances": 1},
)
```

- `misfire_grace_time=None`：迟到也执行——宿主醒来后，停摆期间错过的每个 cron 各补跑一次（`coalesce=True` 合并堆积，APScheduler 3.x 默认已开，显式声明防升级漂移）。
- **例外**：盘中高频任务补跑无意义且会堆积（醒来一次补几十个快照），单独设 `misfire_grace_time=300`：`sse_trade_hours`、`sse_trade_close`、`sector_moneyflow_poll`、`announcements_poll`。job 体内已有交易时段守卫（`_is_workday` / `_in_trading_hours`），补跑安全。
- 盘后日频任务（quotes/basic/price_limits/sentiment/龙虎榜等）吃 `None`：晚几小时跑也正确（TuShare 数据已在）。

### L2 对账收敛器（核心新增，治"停摆留洞"）

新服务 `backend/app/services/reconciliation_service.py`，单一入口：

```python
async def reconcile_market_data(
    db, *, window_days: int = 10, apply: bool = True, only: set[str] | None = None
) -> dict[str, Any]:
    """对账并（默认）补齐窗口内缺口。apply=False 为只读巡检（供 freshness 端点复用）。"""
```

**期望态**：`expected_trade_dates(n)` —— TuShare `trade_cal` 取最近 N 个交易日（截止**昨日**，维持 T-1 语义）。trade_cal 结果按日缓存 Redis（TTL 24h）；API 失败降级为「工作日启发式」并记录 degraded。

**逐域对账矩阵**：

| 域 | 完整判据 | 补齐动作 | 复用 |
|---|---|---|---|
| daily_quotes | 每期望日行数 ≥ 阈值 | `ingest_daily_quotes(db, d)`（upsert 幂等，partial 会被拉全） | `TuShareIngestService` |
| daily_basic | 同上 | `ingest_daily_basic(db, d)` | 同上 |
| stock_price_limits | 期望日无行（既有判据已正确） | `ingest_stock_price_limits` 的补漏逻辑抽出为按日可调 | `limit_up_repo.missing_price_limit_dates` |
| market_sentiment_daily | quotes+limits 齐备的期望日缺行 | `persist_snapshot(db, None, as_of=d)` 逐日 | 既有 skip 语义防假低谷 |
| （Phase 3）dragon_tiger / block_trades 等 | 表内最新日 < 期望最新日 | 既有按日 ingest | 各自 service |

**缓存失效内聚**：`persist_snapshot` 成功 upsert 后，SCAN+DEL `market:limit-up:calendar:*`（TTL 900s 太长，窗口内补齐后旧集仍存活 15 分钟）。放在 service 内而非 job 内——worker 手动触发、scheduler 定时、未来的任何调用方都自动获得失效语义。

**触发点（三处，互为冗余）**：
1. **scheduler 启动后 +2min**（`next_run_time=now+120s` 的 one-shot job）：宿主醒来/栈重启后必然经过的收敛点。
2. **cron 每交易日 17:45**（正常任务链之后的兜底自检）+ 非交易日每日 10:00 一次（防节假日前后窗口漂移，`apply=True` 但期望集为空时零成本）。
3. **worker 手动**：`market_data.fetch` 队列新增 job type `reconcile`（`MarketDataJobType` Literal 扩一个值 + worker 分支），运维可随时触发。

**输出**：每域 `{expected_days, missing_days, refetched_days, status}`，一处结构化 log（`RECONCILE domain=quotes missing=[...] filled=[...]`），补齐非空时 WARNING 级醒目。

### L3 消灭"只补 T-1 + exists-skip"放大器（语义重写）

`_fetch_yesterday_daily_quotes` / `_fetch_yesterday_daily_basic` 删除（连同 `quote_repo/daily_basic_repo.trade_date_exists` 的 exists-skip 分支），16:30 / 16:45 两个 job 改为薄封装调 `reconcile_market_data(only={"daily_quotes"})` / `(only={"daily_basic"})`：

- 窗口内任何缺口（昨天的、上周的、行数不足的）都会被拉齐——单日停摆不再留洞。
- 行数阈值判据天然解开 9/16 的 partial 死锁：2 行 < 阈值 → 重拉，无需人工 DELETE。
- 集中一处窗口语义，杜绝"两条回补链路覆盖不一致"（9/14 事故形态）。

**数据时点语义（保持 T-1，不做当日）**：16:30 拉的是昨日全量。TuShare 当日数据 ~16:00 后才逐步出全，当日拉取曾实际产生 partial（9/16 启动 seeding 事故）。若未来要提升当日新鲜度，另立任务：≥17:30 尝试当日 + 行数守门 + 不达标自动回落，不混入本方案。

### L4 派生缓存失效（治放大器 B）

除 L2 内聚的 calendar 失效外，审计 `limit_up_service` 全部缓存键：

| 键 | TTL | 处置 |
|---|---|---|
| `market:limit-up:snapshot:{date}:{lookback}` | 300s | 保留——短 TTL 自愈，改动收益低 |
| `market:limit-up:calendar:{days}` | 900s | 失效内聚进 `persist_snapshot`（L2） |

原则：**写派生表的同一事务边界内失效对应读缓存**；新增派生缓存时 TTL 上限 = 其兜底对账周期。

### L5 数据新鲜度可观测（把"静默"变"可见"）

- 新端点 `GET /market/data-freshness`：调 `reconcile_market_data(apply=False)` 只读巡检，返回每域 `{domain, latest_in_db, latest_expected, missing_days, status: ok|stale|degraded}`。对账查询均为索引聚合，成本可忽略。
- scheduler 每次 reconcile 后 log 一行摘要（已有，规范化字段）。
- 前端（Phase 3，可选）：market 页脚或情绪卡 as_of 行旁挂新鲜度角标，stale 时显示"缺 N 个交易日"。先以端点 + 日志闭环，UI 不阻塞主线。

### L6 宿主环境（~~用户拍板项~~ **已划掉**，2026-09-17：后续部署服务器，开发机不做宿主改造；L1-L5 在服务器环境同样成立且更稳）

- Docker Desktop Settings → Resources → 关闭 **Resource Saver**；
- Windows 电源设置：插电时睡眠关闭（`powercfg /change standby-timeout-ac 0`）或开发时段用 keep-awake。

**~~定位明确：L1–L5 已让正确性不依赖此项。~~ 已决策不做（2026-09-17）：系统将部署到服务器，无宿主睡眠/Resource Saver 问题；L1-L5 的对账兜底在服务器上继续作为防御层保留。

## 5. 实施计划（tracer-bullet 分期）

### Phase 1 —— 调度硬化 + 缓存失效（半天，最小风险消除）

1. `runner.py`：`job_defaults` 注入 + 盘中四任务显式 `misfire_grace_time=300`（TDD：断言 job 配置，防回归）。
2. `limit_up_service.persist_snapshot`：upsert 成功后 SCAN+DEL calendar 键（测试：fake cache 断言 DEL 被调）。
3. 部署验证：`docker compose restart scheduler`，日志确认 18 任务注册 + 配置生效。

### Phase 2 —— 对账收敛器 + 语义重写（一天，根治主体）

1. `reconciliation_service.py`：`expected_trade_dates`（trade_cal + Redis 缓存 + 降级）、行数阈值判据、逐域对账（quotes/basic/price_limits/sentiment 四域先行）。
   - TDD：交易日计算（含节假日 mock）、阈值判定、partial 重拉语义、只读模式不写库。
2. 重写 16:30/16:45 job 为 reconcile 薄封装；删除 `_fetch_yesterday_*` 与 exists-skip。
3. 触发点三处：startup one-shot（+2min）、17:45 cron、worker `reconcile` job type。
4. `persist_snapshot` 已在 Phase 1 完成缓存内聚。
5. 端点 `GET /market/data-freshness`（复用只读巡检）。
6. 验收（实机，用户可复现）：
   - 删任一交易日 quotes → 触发 reconcile → 自动补回，calendar 趋势线天数随之增长；
   - `docker compose stop scheduler` 过 17:45 再 `start` → 启动对账自动补齐当晚数据。

### Phase 3 —— 扩域与可观测增强（可选，按需排期）

1. dragon_tiger / block_trades / share_floats / repurchases / northbound 纳入对账矩阵（各自已有按日 ingest，接入成本低）。
2. 前端新鲜度角标。
3. 当日数据尝试（≥17:30 + 行数守门 + 自动回落），提升 as_of 至当日。

## 6. 验证与门禁

- 单测/集测：`uv run pytest`（reconciliation_service 全覆盖 + runner 配置断言 + persist_snapshot 缓存失效）。
- lint/类型：`uv run --extra dev ruff check .`、`uv run --extra dev mypy app`。
- bench：不涉及 Tier 1 热点（规则引擎/rollup/mapper），无需跑基准。
- 实机验收脚本化：Phase 2 验收两步各留操作记录进 Changelog。

## 7. 风险与边界

| 风险 | 缓解 |
|---|---|
| `grace=None` 使停摆很久的实例醒来后任务风暴 | `coalesce=True` 合并为每 job 一次；job 体内交易时段守卫；对账本身限窗 10 日 |
| TuShare 限流（~0.5s/请求）撞上补 N 日 | 窗口补齐本就低频（停摆后一次）；逐日串行已天然限速；失败日下轮对账重试 |
| trade_cal 不可用 | Redis 缓存 24h + 工作日启发式降级 + degraded 标记（节假日会多试几个空日，ingest 空结果无害） |
| 行数阈值误判（大量停牌日） | 0.8 系数留 20% 余量；误判后果只是多拉一次（幂等），方向安全 |
| 对账与正常 job 并发写 | 全部 upsert/DO NOTHING 幂等；`max_instances=1` 限同 job；跨 job 写冲突由唯一键吸收 |
| partial 死锁复发 | 行数判据取代存在性判据，结构性消除 |

## 8. 交叉引用

- 事故时间线与根因取证：`docs/Changelog.md` 2026-09-16 / 2026-09-17 条目
- 既有沉淀：`docs/references/best-practices.md`（misfire_grace_time / 容器挂起 / 补漏窗口 / 缓存固化四条）
- 情绪链路设计：`docs/design/limit-up-sentiment.md`
- 部署运维：`docs/build.md`（scheduler/worker 容器）
