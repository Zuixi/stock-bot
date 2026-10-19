# 2026-10-16 当日盘后（T+0）纳入数据面期望集

```
status:      completed
scope:       收盘后（≥17:30）把「当日」纳入对账期望集与派生指标收敛目标；当日不齐自动回落 T-1；hero 新鲜度改锚外部日历并改用日历计数（去掉自指）；T+0 值以读时推导的 provisional 如实披露
touches:     backend/app/services/reconciliation_service.py, backend/app/services/market_day_service.py, backend/app/services/derived_metrics_service.py, backend/app/services/market_sentiment_composite_service.py, backend/app/services/limit_up_service.py, backend/app/scheduler/jobs.py, backend/app/scheduler/runner.py, backend/app/schemas/limit_up.py, backend/app/api/v1/market_data.py, backend/app/config.py, backend/tests/**, frontend/src/features/sentiment/**, frontend/src/pages/sentiment/**, docs/**
updated:     2026-10-16
next-action: -（P0.5–P10 全部落地；真库验收见 §十）
```

## 一、为什么开这个任务

2026-10-16（周五）22:30 用户报："已经 16 号了，`/sentiment` 的 hero 还显示 15 号，为什么不会自动刷新"。实测根因三条（已核过代码与真库）：

| 事实 | 证据 |
|---|---|
| 库里**根本没有** 10-16 的行情 | `daily_quotes` `max(trade_date)=2026-10-15`；无 10-16 任何一行 |
| 综合指数最新行 = 10-15 | `industry_metrics` `market/market_sentiment_composite` 最大 `period=2026-10-15`（`value=27.546`，9/11 条腿，`effective_weight=0.84`） |
| 后端自称"不滞后" | `GET /market/sentiment/composite` → `as_of=2026-10-15`、**`expected_as_of=2026-10-15`**、`lag_trading_days=0`、`stale=false` |
| 当日从未进过期望集 | scheduler 日志：`RECONCILE all domains ok (expected_latest=2026-10-15)` → `converge done: target=2026-10-15` |

1. **期望集硬编码「截止昨日」**：`reconciliation_service.expected_trade_dates` 里 `end = today - timedelta(days=1)`（[:137](../backend/app/services/reconciliation_service.py#L137)），注释写明"当日不属于期望集"（[:132](../backend/app/services/reconciliation_service.py#L132)）。于是当日行情永不入 `daily_quotes`，下游派生指标的收敛目标（`expected_latest`）也永远是 T-1。
2. **新鲜度判据自指**：hero 的 `expected_as_of` 取自 `market_day_service.resolve_latest_complete_day`，而它的候选日就是 `max(daily_quotes.trade_date)`（[market_day_service.py:132](../backend/app/services/market_day_service.py#L132)）—— 与滞后的那条链同源。它只能发现"某天有行但不完整"，**发现不了"今天整天没采"**，所以必然报 `stale=false`。
3. 前端不轮询（`refetchOnWindowFocus:false` + 页内 `staleTime: 5min`，[sentiment/index.tsx:30](../frontend/src/pages/sentiment/index.tsx#L30)）：**即使轮询也一样** —— 后端那行数据本身不会变。

**这是已登记的遗留，不是新发现**：

- [data-sync-self-healing.md:98](./2026-09-17-data-sync-self-healing.md)：「若未来要提升当日新鲜度，**另立任务：≥17:30 尝试当日 + 行数守门 + 不达标自动回落**，不混入本方案」；同文 :148 列为待办第 3 项。
- [derived-metrics-converge.md:77-78](./2026-10-16-derived-metrics-converge.md)：「**T-1 不是"今天"**……要当日新鲜度是另一件事」。

本计划就是那个"另立任务"，且**直接复用** self-healing 已定的三条设计（≥17:30 / 行数守门 / 自动回落）。

## 二、术语澄清：`腿`（leg）

- **`腿` 是真实的行业行话**，源自英文 **leg**：多腿组合/价差策略的单个组成部分（pairs trade 的两条腿、期权多腿组合的 leg risk、跨期价差的近月/远月腿）。中文量化圈确实说"多腿组合 / 单腿 / 三条腿"。
- 但在**因子/多指标合成**语境（本处语境）里，更常见的说法是"**成分指标 / 分项 / 分量**"；英文对应 constituent / component。这里 11 条腿 = 11 个日频指标各自先转成 0–100 分位、再按权重合成一个 0–100 综合分。
- 结论：`leg` 留在**代码与 `extra.participants`** 里没问题（已有契约），但**用户界面**的"本日由 9/11 条腿合成"对投研工作台的读者偏内部行话 → 改文案（§P6b，**独立提交**）。

## 三、决策

### 3.1 双层职责解耦（避免"死锁"与"自指复活"）

把"底座过守门"直接塞进一个判据会导致两个致命缺陷：① 对账拉取前 DB 根本没有今天的行情，期望集会因"底座未过守门"退回昨天，于是今日行情**永远不拉**（鸡生蛋死锁）；② hero 若也看"底座过守门"，今天整天没采时就会退回昨天，`stale` 又变回 `false`（自指 bug 复活）。

因此拆成两层：

```
1. 理论期望日 calendar_expected_latest_day(calendar, now) [纯函数 / 零 IO / 不探库]
   now >= T0_CUTOFF 且 今日是交易日        → 今日
   否则                                    → 日历上今日之前最后一个交易日

2. 收敛目标日 reconcile_expected_latest(db, expected) [对账 apply 循环之后实探 DB]
   从 expected[-1] 往前找，最新一个过 is_day_complete 三元组门禁的交易日
   （不达标逐日回退；一个都不过 ⇒ None）
```

| 消费者 | 依赖的判据 | 语义与目的 |
|---|---|---|
| 对账期望集 `end` | `calendar_expected_latest_day` | 告诉对账器"按日历与时钟现在应该拉到哪天"（解开鸡生蛋死锁） |
| hero `expected_as_of`（新鲜度） | `calendar_expected_latest_day` | 告诉读者"理论上现在应该出到哪天"；DB 没出全即如实报 `stale=true`（去掉自指） |
| 派生收敛目标 `expected_latest` | `reconcile_expected_latest` | 保证收敛器只在底座**真正齐备**的日子上做数学计算（守门回落） |

**关键**：`calendar_expected_latest_day` 只回答"日历与时钟说该到哪天"，**不回答"数据到没到"**。三层语义（期望 / 齐备 / 实际）必须分开，混在一起就会出现上面两个缺陷。

### 3.2 P0 owner 裁定：**A**（2026-10-16）

T+0 当天，**融资融券两条腿是 T+1 数据，制度性缺失**（实测 10-15 的 `margin_financing_balance` / `margin_financing_buy_ratio` 行存在但 `value=NULL` / `missing_reason=source_empty`）。所以：

- T+0 的 composite 权重低于满权重（10-15 实测 9/11 条腿、`effective_weight=0.84`）；
- 融资融券数据披露后，收敛窗口（10 交易日）会 `ON CONFLICT DO UPDATE` **重算并改写当日的值** —— 即"今天看到的数字以后会变"。

**裁定 A（采用）**：hero 直接发布 T+0 值 + `provisional` 披露。备选 B（主数字仍只发各腿齐备日、T+0 作副读数）与 C（只推 9 个非融资融券指标）未采用。

**裁定含义（三条实施约束）**：① `provisional` 逐字透传、不在前端归并（**三态**，见 3.3）；② 改写事实必须主动披露，不得让读者以为数字是终值；③ `caliber_version` 不做版本分裂 —— provisional 是**同口径的时序事实**（腿还没到），不是新口径，不升版、不新增 `metric_key`。

### 3.3 `provisional` 的语义修正：读时三态推导，不写库

初版设计打算在写面带一个布尔标记，**放弃**，改用读时推导：

- **避免"缺键 = 不是临时值"的撒谎**：历史行没有该键，`default False` 会把"不知道"说成"是终值"，违反本仓 `stale` / `insufficientHistory` 一路沿用的**三态纪律**；
- **不新增写面契约**：不改 `extra` 形状、不动冻结字段；
- **自动收敛**：融资融券腿到位后 composite 被重算（9→11 条腿），`provisional` 自然翻成 `false`。

派生规则（读面，`composite_hero_payload`）：

```
provisional =
    None   该行 extra 无 participants（判不出 —— 不假装是终值）
    True   该行确实缺了「已知披露滞后」的腿（registry 声明的 late_arrival 集合）
          且该行仍在可改写窗口内（近 N 个交易日）
    False  其余（含：窗口外的历史行 —— 早已不可改写，视为终值）
```

- "已知披露滞后"必须来自 **registry 声明**（如给成分加 `late_arrival` 标记），不得按权重阈值猜 —— 否则 QVIX 那种上游滞后会被误标成 provisional；
- 窗口外的行不再改写（ADR 0016 的窗口语义），故不算 provisional；
- 文案改为「**待融资融券数据披露后重算**」，**不写"次日"** —— 实测 10-15 的融资融券到 10-16 21:32 仍是 `source_empty`，承诺具体时点会直接失实。

### 3.4 发布闸门的余量风险（P0.5 必须先实测）

### 3.4 发布闸门的余量：**已实测，裁定维持 0.80**

发布闸门 `COMPOSITE_PUBLISH_MIN_WEIGHT = 0.80`，最重的单条腿权重 0.12（QVIX，[industry_registry.py:2166-2173](../backend/app/services/industry_registry.py#L2166-L2173) 注释）。10-15 实测 0.84，缺的融资融券两条腿合计 0.16。**从 0.84 再少任何一条腿就会掉到 0.72 < 0.80，当日值被闸门挡成 `NULL`** —— 所以"T+0 到底发不发得出值"完全取决于 QVIX 这类上游在盘后是否到位。

**实测（2026-10-17 00:2x CST，距周五 10-16 收盘约 9.4 小时；只读探测上游，未写库）**：

| 来源 | 目标日 2026-10-16 | 结论 |
|---|---|---|
| TuShare `daily` | 5567 行，含当日 | ✅ 可得 |
| TuShare `daily_basic`（by_date） | 5567 行，含当日 | ✅ 可得 |
| TuShare `stk_limit` | 5655 行，含当日 | ✅ 可得 |
| TuShare `fut_daily` | 1076 行，含当日 | ✅ 可得（三条基差腿） |
| TuShare `index_daily` 000300.SH | 1 行，含当日 | ✅ 可得（换手率腿） |
| TuShare `margin` | **0 行** | ❌ 不可得（T+1 披露；10-13/14/15 均已可得，各 3 行） |
| akshare QVIX（静态 CSV） | `max_date=20261016` | ✅ 可得 |

- **QVIX 风险未发生**：CSV `Last-Modified: Fri, 16 Oct 2026 08:53:30 GMT` = **10-16 16:53 CST**，早于 17:30 cutoff 37 分钟 ⇒ 17:45 收敛时当日 QVIX 已就位。（QVIX 历史上确实会停更数日；那时当日值会因 0.72 < 0.80 被闸门挡下、hero 回落 T-1 并如实报 `stale=true` —— 这是设计好的诚实降级，不是死锁。）
- **唯一的制度性缺失是融资融券**（两条腿合计 0.16，T+1 披露）⇒ T+0 的合成值 = **9/11 条腿、权重 0.84 ≥ 0.80，会被发布**。

**裁定：走菜单 ①（维持 0.80 闸门不变），方案 A 可行**，不需要任何口径例外。

> ⚠️ cutoff=17:30 与 QVIX 的 16:53 更新只差 **37 分钟**，是单项最紧的余量。故 P10 仍要在交易日 17:00/17:30/18:00 连续观测 ≥3 日校准 cutoff 与重试 tick；若 QVIX 常态晚于 17:30，则依赖 18:30 重试 tick（见 §四.11 的前提）。

## 四、从现有实现里读出的硬约束

1. **cutoff 必须 ≥ 17:30，且晚于 16:50 的限价 job**：完整性判据 `is_day_complete` 要求 `limits_present`（[market_day_service.py:47-58](../backend/app/services/market_day_service.py#L47-L58)）。17:30 同时满足"晚于 16:50 限价入库"与"≤ 17:45 全量对账"，使**既有的 17:45 兜底对账自动成为 T+0 收敛点**，不必新开 cron。
2. **读面不准外呼 TuShare**（[market_sentiment_composite_service.py:873-877](../backend/app/services/market_sentiment_composite_service.py#L873-L877) 的既有纪律）：交易日历必须缓存，判据在本地纯计算。
3. **回落锚绝不能退回 `max(daily_quotes)`**：那正是要修的自指 bug。判据算不出时返回 `None`（前端已有"无法判定"档），不得退回旧锚。
4. ~~**`lag_trading_days` 继续用 `trading_days_between`**~~ —— **推翻**。该函数数的是 `daily_quotes` 里 `(after, upto]` 的 DISTINCT 交易日（[:849-861](../backend/app/services/market_sentiment_composite_service.py#L849-L861)）。在"今天整天没采"这个正是要修的场景里，`daily_quotes` 没有今天 ⇒ `count = 0` ⇒ `lag=0`、`stale=false`，**自指 bug 换个位置复活**，最关键的回归测试会直接红。**lag 必须改由缓存的交易日历计数**（日历中落在 `(as_of, expected_as_of]` 的天数）。
5. **日历缓存必须带"覆盖到哪天"**：只存交易日列表无法区分"今天休市"与"缓存是昨天写的、没覆盖今天"，会把昨天当期望日而**悄悄报 `stale=false`**。缓存存 `{days, covered_through}`；`covered_through < today` ⇒ 返回 `None`（无法判定），**不许猜**。
6. **16:30 / 16:45 两个分域 job 保持在 cutoff 之前**：继续做 T-1 的活，不引入 partial 风险（9/16 事故的教训）。
7. **守门必须复用三元组门禁**：[数据源 BP #41](../docs/references/best-practices/data-source.md) 的教训"行数够不等于数据可用"（某日 5485 行齐全但 `pct_chg` 只有 1 行非空）。复用 [market_day_service.py:47](../backend/app/services/market_day_service.py#L47) 的 `is_day_complete`（行数比例 ≥ 0.9 + `pct_chg` 非空率 ≥ 0.99 + `limits_present`），仓库内不搞两套完整性标准（[BP #33](../docs/references/best-practices/data-source.md)）。
   - ⚠️ **注意阈值分叉**：对账重拉用的 `COMPLETENESS_RATIO = 0.8`（[reconciliation_service.py:51](../backend/app/services/reconciliation_service.py#L51)），而 `is_day_complete` 用 0.9。行数落在 80%~90% 的日子会被对账判为"已 OK、无需重拉"却又永远过不了守门 ⇒ **当天卡在 T-1 且永不自愈**。P3 必须把"过不了守门"的日重新并入重拉队列，否则形成死锁。
8. **顺序由代码保证，不靠时间差**：[jobs.py](../backend/app/scheduler/jobs.py) 里 `reconcile_market_data_job` 的 docstring 明确写了这一点（收敛挂在对账之后）。本计划**不得**用"把 job 挪到 17:55 避开对账执行期"这类时钟间隙排序（实测空对账仅 17 秒，但 T+0 要拉 5,560 行行情 + daily_basic + 限价，TuShare 节流下可能是几分钟，无论怎么挪都不可靠）。
9. **`latest_quote_date` "永远不是今天"是被依赖的隐含不变量**：[limit_up_service.py:341](../backend/app/services/limit_up_service.py#L341) 用它论证某分支是死代码。T+0 打破该不变量后，相关注释与分支必须一并复核。
10. **cutoff 判据不得依赖 "今天是交易日" 之外的猜测**：`trade_cal` 不可用时 `expected_trade_dates` 会降级成工作日启发式（节假日会多试空日，ingest 空结果无害），但**降级结果不得进缓存**（否则把节假日误判固化 7 天）。

11. **追平判据看"行存在"而非"值存在"，会让重试 tick 对迟到腿失效（P0.5 实测确认）**：`_latest_period` 取每个 key 的 `max(period)`（[derived_metrics_service.py:227-248](../backend/app/services/derived_metrics_service.py#L227-L248)），`plan_leg_window` 在 `latest >= target` 且 `always_rescan=False` 时返回 `None`（零外呼，[:114-115](../backend/app/services/derived_metrics_service.py#L114-L115)）；而**只有 volatility 腿声明了 `always_rescan`**（[:212](../backend/app/services/derived_metrics_service.py#L212)）。
    - **实测证据**：上游在 10-17 00:2x 已有 10-15 的融资融券（3 行），而库里 10-15 的两条腿仍是 `value=NULL` / `source_empty`（10-16 01:37 写入后再没被碰过）—— 因为 10-16 21:32 那轮 `target=10-15`、activity 腿的 `max(period)` 已 = 10-15（**NULL 行也算"有行"**）⇒ 判为已追平 ⇒ **零外呼**。
    - **后果**：融资融券在"目标日推进"之前永远不会被回填 ⇒ 计划里的 18:30/20:00 重试 tick 对"当天迟到的 margin"**无效**（tick 重跑同一 target，判据仍是"已追平"）。
    - **修法（P4）**：给 registry 里声明为迟到到达的 key 加**值级追平判据** —— 目标日该 key 的值为 NULL 且仍在"迟到窗口"（近 N 个交易日）内时，仍重扫窗口。**不能**无条件改成值级（会让永久缺数的日子每天重拉）。

## 五、阶段

### P0 owner 裁定 —— ✅ 已裁定 **A**
- [x] 选定 **A**（T+0 直接发布 + `provisional` 披露）；结论随 P9 写进 **ADR 0020**（⚠️ 编号分叉：本计划起草时 0019 尚空，实际落地时已被并行的 `0019-official-first-source-layer.md` 占用，故顺延为 0020）

### P0.5 实测与盘点（**阻塞 P1**）—— ✅ 已完成，结论见 §3.4
- [x] **逐腿实测 T+0 可得性**（read-only 探测上游）：2026-10-17 00:2x 对 7 个来源做完一轮。**单点结论：除融资融券（T+1）外全部可得 ⇒ 9/11、0.84、发布**
  - [ ] **待补（不阻塞 P1）**：交易日 17:00/17:30/18:00/18:30/19:00 的逐时刻曲线，需下一交易日（10-19）；用于校准 cutoff 与重试 tick 时刻
- [x] **闸门口径裁定**：菜单 ①（维持 0.80 不变）
- [x] **盘点裸 `max(trade_date)` 消费者** —— 结论：**"必须改"清单为空**
  - 读者侧已全面守门：`resolve_latest_complete_day` 已被 20+ 处采用（[ranking_board_service.py:884](../backend/app/services/ranking_board_service.py#L884)、heatmap_service、market_service 7 处、concept_service、sw_drilldown_service、limit_up_service、derived_metrics_service、composite hero）
  - [ranking_board_service.py:221](../backend/app/services/ranking_board_service.py#L221) / [:288](../backend/app/services/ranking_board_service.py#L288) 的 `max(trade_date)` 严格 `< :as_of`，且 `as_of` 来自守门判据 ⇒ **安全**
  - [market_service.py:1781](../backend/app/services/market_service.py#L1781) 是**个股** `LATERAL` 取该股最新一行 ⇒ **有意为之**（个股详情就该显示当日价），非全市场聚合，不因半截日失真
  - [market_data_service.py:2126](../backend/app/services/market_data_service.py#L2126)（限价 ingest 窗口上界）与 [market_activity_service.py:1287](../backend/app/services/market_activity_service.py#L1287)（活跃度腿窗口）是**写侧** ⇒ T+0 后自动覆盖当日，正是补当日限价所需，**有益**
  - [limit_up_repo.py:427](../backend/app/repositories/limit_up_repo.py#L427)（`market_sentiment_daily` 的 max）是派生表 ⇒ 应随收敛推进到当日，**有意为之**
  - [market_data_repo.py:289](../backend/app/repositories/market_data_repo.py#L289)/[:583](../backend/app/repositories/market_data_repo.py#L583)/[:631](../backend/app/repositories/market_data_repo.py#L631)、[index_repo.py:39](../backend/app/repositories/index_repo.py#L39)、[quote_repo.py:64](../backend/app/repositories/quote_repo.py#L64) 均非 `daily_quotes` 或带显式区间 ⇒ **不受影响**
  - ⇒ **P3 不再需要连带改读者**（原计划的担心未成立），范围缩小
- [x] **复核 `latest_quote_date` 不变量**：[limit_up_service.py:341](../backend/app/services/limit_up_service.py#L341) 的"死代码"说法只是**注释**，代码里已无 `target == 今日` 分支（唯一 `_today_sh()` 用途是 intraday 模式的取数守卫 [:138](../backend/app/services/limit_up_service.py#L138)）⇒ **无行为风险**，只需 P2 顺手改文案
  - **未采用的菜单项**（留档）：② 给 late_arrival 腿在临时行上单独口径（属口径变更）；③ 多条腿常态迟到 ⇒ 退回 §3.2 的 B/C 方案；④ 当日全腿都拿不到 ⇒ 推迟本任务

### P1 判据纯函数 + 交易日历缓存 —— ✅ 已完成
- [x] `market_day_service` 加 `T0_CUTOFF = time(17, 30)` 与纯函数 `calendar_expected_latest_day(calendar, *, covered_through, now) -> date | None`：
  - [x] **时区规整**（[数据源 BP #27](../docs/references/best-practices/data-source.md)）：`_now_sh()` 对 naive 按上海本地解释、aware 转上海时区，杜绝 naive/aware 比较抛 `TypeError`；
  - [x] **覆盖度守卫**：`covered_through is None or < today` ⇒ `None`（不许猜）；
  - [x] **零 IO、无隐含时钟**：单测直接喂 `now`；并用签名断言（`params == {calendar, covered_through, now}`）钉死"判据里不得出现数据齐备性参数"
- [x] 交易日历走 Redis（[架构 BP #15](../docs/references/best-practices/architecture.md)）：key `market:day:trade_calendar`，值 `{"days": [...], "covered_through": "..."}`，TTL 7 天 —— `calendar_cache_payload` / `parse_calendar_payload` / `write_calendar_cache`
  - [x] **显式字符串序列化**：写 `isoformat()`、读 `date.fromisoformat()`，畸形载荷当缓存未命中（不抛）
  - [x] **降级不写缓存**：`write_calendar_cache` 的 docstring 明确"只在日历非降级时调用"（调用点见 P2）
- [x] `resolve_expected_day(cache, *, now=None)` 编排：命中 → 本地算；未命中 / cache=None / 覆盖不到 / 开关关闭 ⇒ `(None, reason)`，**不外呼、不退回 `daily_quotes`**
- [x] env 开关 `settings.t0_after_close_enabled`（`app/config.py`）一键退回 T-1 语义
- [x] 单测 22 条（边界：cutoff 前 1 分/正点/后 1 分、周末、覆盖度不足、空日历、全在未来、naive vs aware、载荷往返与畸形、编排四类 reason、结构性"不读库"）
- [x] **变异验证**：`>= T0_CUTOFF` → `>` 与 `covered_through < today` → `<=` 两处变异都让测试转红，随后按 sha256 校验还原（证明断言真能失败）
- [x] **仍待 P2**：把 `write_calendar_cache` 接到对账上（需同时把 `trade_cal` 取数上界放宽到 `today`，否则 `covered_through` 覆盖不到今天）—— 故 P1 本身是**纯新增、零行为变更**，可独立合并/回滚

### P2 期望集纳入当日 —— ✅ 已完成
- [x] `expected_trade_dates`：`end = calendar_expected_latest_day(cal, covered_through, now)`；`now` 作为**显式可注入参数**（不得默认读真实时钟，否则既有固定日期用例会变成时间相关而 flaky）；`degraded` 启发式同步遵守 cutoff；
- [x] **翻转既有断言** `test_expected_dates_never_include_today`（[:59](../backend/tests/test_reconciliation_service.py#L59)）—— 它钉的正是要改的契约，改名 + 断言双态（cutoff 前不含 / cutoff 后含）；
- [x] 更新"截止昨日"的表述：[reconciliation_service.py:3](../backend/app/services/reconciliation_service.py#L3) 模块头、[:132](../backend/app/services/reconciliation_service.py#L132) 注释、[jobs.py:182](../backend/app/scheduler/jobs.py#L182) / [:501-502](../backend/app/scheduler/jobs.py#L501-L502)、[derived_metrics_service.py:19](../backend/app/services/derived_metrics_service.py#L19) / [:820](../backend/app/services/derived_metrics_service.py#L820)。

### P3 守门回落 `expected_latest` —— ✅ 已完成
- [x] `reconcile_market_data`：在**底座 apply 循环 + commit 之后**，从 `expected[-1]` 往前找，按 `is_day_complete` 三元组守门（复用 `market_day_service` 的单日统计，可把私有 `_day_stats` 提为公开 helper，**不另抄一份判据**），最新过守门的日赋给 `result["expected_latest"]`；
- [x] 不达标逐日回退，并暴露事实：`expected_latest_source: "gated" | "fallback" | "none"`；
- [x] **修死锁（§四.7）**：把"行数 ≥ 0.8 但过不了 `is_day_complete`（<0.9 / `pct_chg` 空 / 缺限价）"的日**重新并入重拉队列**，否则该日永远卡在 T-1 且不自愈；
- [x] ~~跑 P0.5 清单里"必须改"的裸 `max(trade_date)` 消费者~~ —— P0.5 盘点结论为**空清单**，无需连带改读者
- [x] **`sentiment` 域的 `sent_todo` 天然跟着走**（已按 `base_complete` 过滤），确认当日温度计行会在 17:45 那一轮被写入。

### P4 收敛 target + `provisional` 读时三态 —— ✅ 已完成
- [x] `_converge_derived_metrics` 继续吃对账的 `expected_latest`（**不新增第二处推算**），随 P3 自动获得守门与回落；
- [x] registry 给"已知披露滞后"的成分加声明（如 `late_arrival`，融资融券两条腿），**不写库**、不动权重与口径；
- [x] **值级追平判据（§四.11，重试 tick 生效的前提）**：对声明为 `late_arrival` 的 key，目标日该 key 值为 NULL 且仍在迟到窗口内 ⇒ 仍重扫窗口；**不从宽到全部 key**
- [x] `composite_hero_payload` + `SentimentCompositeOut` 新增 `provisional: bool | None`（**三态**，规则见 §3.3；`None` = 判不出，不得归并成 `False`）；
- [x] 契约冻结性检查：`COMPOSITE_HISTORY_WINDOW` / 发布闸门 / `caliber_version` / 权重与归一化规则一律不动。

### P5 hero 新鲜度改锚外部日历 —— ✅ 已完成
- [x] `get_composite_hero` 的 `expected_as_of` 改读 `resolve_expected_day(cache)`；
- [x] **`lag_trading_days` 改用日历计数**（§四.4）：以缓存日历中 `(as_of, expected_as_of]` 的天数计；日历不可用 ⇒ `lag=None`；
- [x] 三态语义不变：判不出 → `expected_as_of=lag=stale=None`（"无法判定"，不得降级成 `false`）；
- [x] 护栏用例：**"当日整天没采"必须报 `stale=true, lag=1`**（今天的 bug 直接钉成回归测试）；
- [x] `data-freshness` 窗口行为定案：[reconciliation_service.py:229](../backend/app/services/reconciliation_service.py#L229) 注明该端点恒以 `apply=False` 调对账；期望集改动后 17:30~17:45 之间当日就是"缺口"，**要么**给它一个 `pending`（宽限）语义、**要么**在文档里写清它此时会报缺口，二选一落地。

### P6 编排：代码串联 + 盘后重试 —— ✅ 已完成
- [x] **facts / sentiment 作为 `reconcile_market_data_job` 的后置步骤串在代码里**（与 `_converge_derived_metrics` 同范式），**不靠挪 cron 时刻**（§四.8）。`limit_up_stock_daily` 在 `INSPECT_DOMAINS` 里不会被对账自愈，故必须在收敛后显式调用一次（当日）；
- [x] **加 cutoff 后的重试 tick**（幂等）：现状 16:30/16:45 在 cutoff 前、17:45 之后下一次是**次日 10:00**，中间约 16 小时都会报 `stale`。加 1~2 个 tick（如 18:30 / 20:00）调用同一个 job；无缺口时零外呼，成本可忽略（符合[架构 BP #16](../docs/references/best-practices/architecture.md) 的电平触发）；
  - ⚠️ **前提**：tick 对"当天迟到的融资融券"要真正起作用，必须先落地 **§四.11 的值级追平判据**（P4），否则 tick 会因"行已存在"判为已追平而零外呼；
- [x] 16:30/16:45/16:50 三个 T-1 job 保持不动；
- [x] `sentiment_daily`（17:15）与 `limit_up_stock_facts`（17:20）改为由对账尾部驱动；cron 时刻保留与否由 P0.5 实测（上游最早可得时刻）定，**不作为正确性依赖**。

### P6b 文案（**独立提交**，与数据链解耦） —— ✅ 已完成
- [x] UI 的"N/11 条腿"改为"**成分指标**"；同步 `moduleTable.ts` / spec 措辞与断言；代码 `legs` 字段名与 `extra.participants` 契约**不动**；
- [x] 独立提交的理由：数据链出问题要回滚时不应连带回滚文案，review 面也不该被放大。

### P7 前端：联动刷新与体验闭环 —— ✅ 已完成
- [x] 页面三个 query 同属 `["sentiment", ...]` 前缀（[index.tsx:73-91](../frontend/src/pages/sentiment/index.tsx#L73-L91)）。`asOf` 变化时必须让 metrics / trends 一起更新（`invalidateQueries({queryKey: ["sentiment"]})` 或三者共用同一 `queryOptions`），否则会出现**hero 是今天、各模块是昨天**；
- [x] 开启 `refetchOnWindowFocus: true`（页面级覆盖全局 `false`），用户切屏回来即拉最新；
- [x] **只在 `stale === true` 时**做低频探测（约 5 分钟），追平即停；**不要因 `provisional === true` 轮询** —— provisional 会持续到融资融券披露（实测可超一天），会空转一整天；
- [x] `CompositeHero` / 页头 `stateSentence` 加 provisional 文案：明示"缺融资融券（T+1 披露），**待披露后重算**"，**不写"次日"**；`stale=true` 与"无法判定"两档文案保持。

### P8 测试 —— ✅ 已完成
- [x] `test_market_day_service`：`calendar_expected_latest_day` 边界（naive vs aware、cutoff 前 1 分/正点/后 1 分、周末、节假日、日历空、**`covered_through` 不含今天 ⇒ `None`**）；
- [x] `test_reconciliation_service`：期望集 cutoff 双态、节假日不含当日、降级路径遵守 cutoff；
- [x] `test_reconciliation_service` / `test_reconcile_jobs`：三元组守门回落（行数满但 `pct_chg` 空或缺限价 ⇒ 回退 T-1）、**0.8~0.9 行数区间必须被重拉**（§四.7 死锁回归）、Redis ISO 存取与覆盖度守卫；
- [x] `test_derived_metrics_converge` / `test_derived_metrics_job`：target=当日 / 回落两条路径；
- [x] `test_market_sentiment_composite_api`：**`stale=true, lag=1`（今天没采）**、`provisional` 三态、判不出 ⇒ `None`；
- [x] 前端：`npx tsc --noEmit` + hero 文案断言。

### P9 文档与门禁 —— ✅ 已完成
- [x] **ADR 0020**（双层解耦 + P0 裁定 + P0.5 实测结论与闸门口径 + 为什么不用 `max(daily_quotes)` 当锚 + 为什么 lag 必须日历计数 + 为什么 cutoff=17:30）；落地前先 `ls docs/decisions/` 复核编号未被并行分支占用（`doc_gate` 会校验编号连续性）
- [x] 增补 `docs/specs/S1-market-sentiment.md`（S1.13 freshness 段）、`docs/design/metric-dictionary.md`（provisional 语义与三态）；
- [x] `docs/Changelog.md` 一句话；`plans/index.md` 状态回填；
- [x] `bash scripts/self_review.sh --full`（含 `doc_gate.sh`）；动了基准对象才跑 `bash scripts/bench.sh`。

### P10 真库验收（判据见 §六）—— ✅ 已完成（实证见 §十）
- [x] 交易日 17:30 后触发：`expected_latest` = 当日、hero `as_of` = 当日、`lag_trading_days=0`；
- [x] 构造"当日上游没出全"：回退 T-1 且前端报 `stale=true, lag=1`；
- [x] 连续观测 ≥3 个交易日，确认 T+0 值实际发布率与 P0.5 预测一致（若不符，回到 P0.5 的裁定菜单）。

## 六、验收判据（可核对的事实）

| # | 判据 | 核对方式 |
|---|---|---|
| 1 | 交易日 cutoff 后，当日进入期望集 | 日志 `RECONCILE ... (expected_latest=<当日>)` |
| 2 | hero `as_of` 追上当日 | `GET /api/v1/market/sentiment/composite` → `as_of=<当日>`、`stale=false` |
| 3 | 当日数据不全时**如实报滞后**（且 lag 由日历计数得出） | 构造"当日无行情" → `stale=true`、`lag_trading_days=1`、`expected_as_of=<当日>` |
| 4 | 判不出时**不假装新鲜** | 清 Redis 日历键（或 `covered_through` 过期）→ `stale=null` |
| 5 | 16:30/16:45 不引入 partial | 该两轮日志 `expected_latest` 仍是前一交易日 |
| 6 | `provisional` 三态正确 | payload `provisional` ∈ {true,false,null}；缺 `participants` 的旧行 ⇒ `null`（不是 `false`） |
| 7 | 0.8~0.9 行数区间不自锁 | 构造 85% 行数的日 ⇒ 该日被重拉（而非卡在 T-1） |
| 8 | 页面内部一致 | hero 的 `asOf` 变化后，metrics / trends 同批更新（不出现 hero=今天、模块=昨天） |
| 9 | 既有门禁不回归 | `self_review.sh --full` 绿；`test_market_sentiment_composite*` 全绿 |
| 10 | 文案已换「成分指标」 | hero 断言 + `moduleTable.ts` 无"条腿"字样（P6b） |

## 七、风险

- **上游未出全 → partial 行**：本计划正围绕它设计（三元组守门 + 回落 + 重拉 + 读者侧 `is_day_complete`）。**cutoff 不得早于实测可得时刻**，否则复现 9/16 事故。
- **闸门余量只有 0.04（§3.4）**：再少一条腿（如 QVIX）当日值即被挡成 `NULL`，方案退化成"天天报滞后"。P0.5 必须实测，不得凭假设开工 —— 这是本计划**最大的未知**。
- **裸 `max(trade_date)` 消费者**：当日行写入 `daily_quotes` 后会读到可能不完整的当日（[数据源 BP #42](../docs/references/best-practices/data-source.md) 的事故形态）。P0.5 盘点后"必须改"的必须在 P3 同批改完。
- **阈值分叉自锁（§四.7）**：对账 0.8 与守门 0.9 不一致会造成"永不重拉"的静默卡死，P3 必须修。
- **TuShare 额度**：每交易日多一次当日全量拉取（~5,560 行），量级可忽略；不引入历史回补。重试 tick 无缺口时零外呼。
- **T+0 值事后被改写**：无法用"不算"消除，只能披露（P4 `provisional`）。这是本方案**唯一**的产品级代价。
- **节假日误报**：判据必须走 `trade_cal`；日历不可得时宁可报"无法判定"，不用 weekday 猜测当权威。
- **Redis 冷启动**：stack 起来后到启动对账（+2min）之间 hero 报"无法判定"（诚实降级，优于撒谎）；若 Redis 被单独清空，最长等到下一个对账 tick。
- **上游日历降级**：`degraded=True` 时不写缓存。
- **页面内部不一致**：composite 已到当日而温度计停在 T-1 —— P6 的代码串联必须与 P2/P3 同批交付。

## 八、不做的事

- **不新增表**、不加迁移：全部复用 `industry_metrics` / `daily_quotes` / Redis 日历键。
- **不改任何口径**（除 P0.5 若裁定走闸门例外，须单独进 ADR）：发布闸门 0.80、`COMPOSITE_HISTORY_WINDOW=1260`、`caliber_version`、权重与归一化规则一律不动。
- **不做盘中（T+0 盘中）**：只做**盘后**。盘中的实时读面已有独立轨道（东财涨停池 `intraday_sentiment_poll` / `market_sentiment_intraday`），本计划不碰。
- **不靠时钟间隙排序 job**：顺序一律由代码串联保证（§四.8）。
- **不加高频无脑轮询**：只在 `stale` 时低频探测，追平即停。
- **不回头改写历史行**：收敛窗口仍只有 10 个交易日（与 ADR 0016 一致）。

## 九、修订记录（两轮 review 的吸收）

| 修订 | 触发 | 结论 |
|---|---|---|
| 判据拆两层 | 第一轮 review | 理论期望（日历/时钟）与齐备守门（探库）分离，解死锁与自指 |
| 守门改三元组 | 第一轮 review | 复用 `is_day_complete`，弃纯行数阈值（[BP #41](../docs/references/best-practices/data-source.md)） |
| 日历 ISO 序列化 / 时区规整 | 第一轮 review | 显式 `isoformat` 往返 + `Asia/Shanghai` 强制规整 |
| **lag 改日历计数** | 第二轮 review | `trading_days_between` 在"今天没采"时返回 0 ⇒ 自指复活，必须改 |
| **日历缓存带 `covered_through`** | 第二轮 review | 区分"休市"与"未覆盖"，后者返回 `None` |
| **新增 P0.5 实测** | 第二轮 review | 闸门余量仅 0.04，QVIX 迟到即发不出值；先实测再定口径 |
| **新增裸 `max(trade_date)` 盘点** | 第二轮 review | 当日行入 `daily_quotes` 后，未走守门的读者会读到半截日（[BP #42](../docs/references/best-practices/data-source.md)） |
| **job 排序改代码串联** | 第二轮 review | 弃"挪到 17:55 避开执行期"，改为对账尾部串联（§四.8） |
| **加盘后重试 tick** | 第二轮 review | 17:45 是当天唯一机会，之后要到次日 10:00 |
| **前端三 query 联动、只在 stale 探测** | 第二轮 review | 防 hero/模块错位；provisional 可持续 >1 天，不能作为轮询条件 |
| **`provisional` 改读时三态** | 第二轮 review | 避免"缺键 = 终值"的撒谎与新增写面契约 |
| **文案去掉"次日"** | 第二轮 review | 实测融资融券 >1 天仍未披露，承诺时点会失实 |
| **P6b 拆独立提交** | 第二轮 review | 文案与数据链解耦，回滚面与 review 面各自可控 |
| 补 `data-freshness` 窗口定案 | 第二轮 review | 该端点 `apply=False`，cutoff 后到对账前会报缺口 |
| 补阈值分叉死锁修复 | 第二轮 review | 对账 0.8 与守门 0.9 不一致会造成永不重拉 |
| **ADR 编号 0019 → 0020** | 落地前复核 | 0019 已被并行的 `official-first-source-layer` 占用（本仓已有"ADR 编号分叉"先例） |
| **闸门余量风险解除** | P0.5 实测（10-17 00:2x） | QVIX CSV `Last-Modified` = 10-16 16:53 CST（早于 cutoff 37 分钟）⇒ 当日可得；唯一缺失是 T+1 的融资融券 ⇒ **9/11、0.84、发布**，维持 0.80 闸门 |
| **裸 `max(trade_date)` 清单为空** | P0.5 盘点 | 读者侧 20+ 处已走 `resolve_latest_complete_day`；其余为写侧窗口或非 `daily_quotes` ⇒ P3 范围缩小 |
| **`latest_quote_date` 不变量无行为风险** | P0.5 复核 | [limit_up_service.py:341] 只是注释，代码里已无 `target == 今日` 分支 ⇒ 仅需改文案 |
| **新增 §四.11 值级追平判据** | P0.5 实测发现 | 追平看"行存在"⇒ 10-15 的 margin 上游已有却因"已追平"零外呼而留 NULL；不修则 18:30/20:00 重试 tick 对当天迟到腿 **无效** |

## 十、真库验收实证（2026-10-17 11:12–11:30 CST，真实 docker 栈）

部署方式：`docker compose -p stock-bot -f docker-compose.yml up -d --no-deps api scheduler worker frontend`
（**必须带 `-p stock-bot`** —— 线上栈的 compose project 是 `stock-bot`；且**必须用 `-f docker-compose.yml`
排除 `docker-compose.override.yml`** —— 后者把 `postgres_data` 改指 `stock-bot_postgres_data`，而真实数据在
`stock_bot_wt_p7_postgres_data`。构建/上线全程 `--no-deps`，postgres 未被触碰（实测 `Up 14 hours` 不变）。

| 判据 | 实测 |
|---|---|
| 期望日判据生效（周六启动对账） | `RECONCILE all domains ok (expected_latest=2026-10-16)`；`daily_quotes` 2026-10-16 = **5567 行**（此前 0 行） |
| 收敛追上当日 | `Derived metrics converge done: status=ok target=2026-10-16 alerts=[]` |
| hero `as_of` 追上最新交易日 | `as_of=2026-10-16`、`expected_as_of=2026-10-16`、`lag_trading_days=0`、`stale=false` |
| 合成值发布 | `value=32.2336`、`leg_count=11/11`、`effective_weight=1.0`、`provisional=false` |
| 两口径分开可读 | `/market/data-freshness` → `calendar_expected_latest=2026-10-16`、`expected_latest=2026-10-16`、`expected_latest_source=gated`、四域 `ok` |
| 日历缓存 | Redis `market:day:trade_calendar` 含 13 个交易日（2026-09-22…2026-10-16）、`covered_through=2026-10-17`、TTL≈7 天 |
| 派生四腿追平 | `market_metrics.legs` 四条腿 `latest` 全 = 2026-10-16、`lag=0` |
| 重试 tick 注册 | 日志 `Added job "Reconcile retry 18:30" / "Reconcile retry 20:00"` |
| 单元/集成门禁 | `pytest` **1489 passed / 46 skipped**；4 个失败**全部**来自并行工作流（`insider_net_change_daily` 未接入任何腿，见该 key 是他人未提交改动），与本任务无关 |

**顺带证实的"临时值会被改写"（ADR 0020 §6 的产品级代价）**：本次收敛把 10-15 从
`9/11 条腿、权重 0.84、值 27.546` 改写为 **`11/11、权重 1.0、值 24.2180`** —— 因为周五的融资融券
在周六上午已披露到位。这正是 `provisional=true → false` 的真实路径（盘后当期缺腿 ⇒ 披露后重算）。

**已知遗留（不阻塞）**：
- 交易日 17:00/17:30/18:00 的逐时刻可得性曲线仍待下一交易日（10-19）观测（P0.5 的嵌套项），用于校准 cutoff 与重试 tick 时刻；当前 cutoff=17:30 由"QVIX CSV 16:53 更新 + 限价 16:50 入库"两条实测支撑。
- `job:failures` 里留有一条 `market_metrics_refresh`（11:12:30）—— 那是**错误镜像**（并行工作流的旧代码，静态 registry 表不一致）当轮的失败；11:22 换成正确镜像后重跑成功，该记录未手工作废（保持告警诚实）。
- Playwright e2e 未在本机跑（需 dev server + 登录态）；前端只做了 `tsc --noEmit` 与产物文案核验（镜像内 `assets/index-*.js` 含「成分指标」、无「条腿」）。
