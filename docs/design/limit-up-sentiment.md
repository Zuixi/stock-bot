# 连板梯队与市场情绪（limit-up-sentiment.md）

> 设计日期：2026-09-14。实施计划见 [plans/2026-09-14-limit-up-sentiment.md](../../plans/2026-09-14-limit-up-sentiment.md)。
> 本文是那份计划的 spec：所有口径、字段名、降级约定以本文为准，改名时两处同步修改。
> 数据源实测记录见 [data-source.md](./data-source.md) §七。

## 一、要解决的问题

现有行情中心能回答"哪个板块涨得多"（申万一级聚合、板块资金流），但回答不了短线用户真正看的三个问题：

1. **连板梯队**：今天几板到几板、每档有哪些票（空间板是谁）。
2. **三级细分板块的最高板**：申万 L3（346 个小类）里哪个细分最强的票在几板。
3. **昨日涨停股今日表现**：赚钱效应、晋级率、炸板率——即"情绪温度计"。

## 二、核心决策：本地 K 线自算为主干，第三方 Web 为增强

**决策 1：限价基准必须用 TuShare `stk_limit`，禁止用"ST/代码前缀推比例"。**

实测（2026-09-08，基准日前收=2026-09-07）：

| 判定基准 | 涨停家数 | 与权威口径分歧 |
|---|---|---|
| `stk_limit.up_limit`（权威） | **75** | — |
| 名称 ST + 代码前缀启发式 | 83 | 10 只（9 只 ST 名称股真实限幅为 10%、1 只北交所取整差 1 分） |

分歧样例：`ST晨鸣` 当日 `up_limit=2.13`（前收 1.94 → 真实限幅 10%），启发式按 5% 给出 2.04 → 制造幽灵涨停。
`stocks.name` 是**当日快照**，不是历史名称，所以"名称含 ST ⇒ 5%"在历史回放时必然错。

**决策 2：`prev_close` 必须用 `stk_limit.pre_close`，不能用库内 `LAG(close)`。**

实测：把窗口前一日误写成 09-04（真实上一交易日 09-07）时涨停数从 75 变 142，**无任何报错**；除权日同样错。
`stk_limit` 提供交易所口径的 `pre_close`（实测 5,637/5,637 非空，但需显式传 `fields`；见下文），同时解决了 `daily_quotes.pre_close` 在生产库尚未迁移（当前 alembic=`b2f3c4d5e6a7`，落后 head）时不可用的问题。

两个实现硬条件（实测 2026-09-14）：①`pre_close` 在 TuShare 文档里是「默认显示 = N」字段，不传 `fields` 只回 `[trade_date, ts_code, up_limit, down_limit]`（5,637 行），落库整列 NULL 且不报错；②它的语义是「本交易日的前收」，算某日涨跌幅必须取**该日行**的 `pre_close`（取前一日行 = 静默多算一天，实测昨日涨停股今日均值 2.8225% → 13.9488%）。

**决策 3：自算路径必须先于 Web 路径落地，且 Web 失败不构成功能降级。**

理由：兜底路径若自身外呼，就不是兜底。本地自算的权威性来自"交易所口径限价 + 库内日线"，可复现、可回放、可离线。

**决策 4：不实现启发式兜底。**

限价缺失时返回 `items: [] + limits_present: false + degraded_reason`，**不**输出已知错误的比例推算值。修复路径是补数任务，不是猜。

## 三、口径定义

### 3.1 涨停/跌停/炸板

```python
is_lu   = close >= up_limit - 0.005          # 收盘封板
touched = high  >= up_limit - 0.005          # 盘中触及过涨停
is_ld   = close <= down_limit + 0.005
炸板     = touched and not is_lu
```

`0.005` 容差是**为了吸收浮点噪声而保留**；`up_limit`/`down_limit`/`pre_close` 一律以 `Numeric(12,4)` 落库，与 `daily_quotes.close` 同型，因此比较是精确的（实测 75/75 干净命中，无边界抖动）。

### 3.2 连板数 `streak`

对候选股窗口内的行做 gaps-and-islands（`is_lu` 相同的最长连续行段），段内序号即该日期的 `streak_upto`。**窗口查询必须回整段交易日**（不是只回 `as_of`/`as_of_prev`），否则 `N天M板`/`missing_days` 无数据可算。实现上 `grp = rn_all - rn_by_islu` 只保证**组内常量**、不保证**组间唯一**（不同 `is_lu` 的岛会撞号），所以 `streak_upto` 的 `PARTITION BY` 必须带 `is_lu`（`stock_id, is_lu, grp`）；实测 2026-08-18~09-08 窗口内有 4 行 `is_lu` 因此被虚高（600127 报 3 实为 1）。
`streak` = 该股在 `as_of` 的 `streak_upto`。

**停牌 policy（必须显式、必须有测试）**：policy A —— 停牌日**既不计入也不打断**连板（与东财 `lbc` 一致，也是用户拿来对照的口径）。
实测依据：`002274 华昌化工` 在 08-26~09-01 停牌，窗口内 12 个交易日只有 7 天有行情；按 policy A 其复牌后的连续涨停仍算同一梯队。
`missing_days = 市场交易日数 - 该股窗口内行数` 必须随标的下发，让 UI 能披露停牌股。

### 3.3 `N天M板`

- `boards_in_window` M = 最近 `lookback`（默认 10）个**市场交易日**内 `is_lu` 的行数
- `days_span` N = 最早那次 `is_lu` 所在日 → `as_of`（含）之间的**市场交易日**数

"3天3板"= 连续三日板；"5天3板"= 五日窗口内三板（断板后回封）。`streak` 与 `N天M板` 是两个字段，不互相派生。

### 3.4 晋级率（交集式，分母显性）

```
1进2 = |{昨日 streak_upto==1 且 is_lu} ∩ {今日 is_lu 且 streak_upto==2}| / |{昨日 streak_upto==1 且 is_lu}|
2进3 = 同构（level=2）
```

- 分子用**交集**而非"今日二板家数"，避免停牌/异常序列污染。
- 每条晋级率必须回传分母 `n`；`n < 5` 时标 `noisy: true`（1/1 = 100% 不是信号）。

### 3.5 板块最高板（申万 L3）

按 `sw_l3_code` 分组，取 `max(streak)`；龙头 = `streak DESC, amount DESC, symbol ASC`（**确定性 tiebreak**，否则"龙头"在两次相同请求间跳变）。
L3→L2→L1 走 `sw_industry_classes.parent_code` 两跳链（同 `market_service._SW_PERF_SQL` 已验证的链：L1=31 / L2=134 / L3=346）。
映射不到的标的进 `unclassified_count` 兜底桶，并回传 `sw_coverage`（实测 `sw_industry_members` 覆盖 4,262/5,513 只 = 77%）。

### 3.6 情绪温度计

| 字段 | 定义 |
|---|---|
| `zt_count` / `dt_count` / `zb_count` | 当日涨停 / 跌停 / 炸板家数（**全市场口径**，不是候选集合口径） |
| `broken_rate` | `zb_count / (zt_count + zb_count)` |
| `yzt_avg_pct` | 昨日涨停股今日 `(close - pre_close)/pre_close*100` 的均值（`pre_close` 取**今日行**） |
| `yzt_avg_open_premium` | 同上但用 `open`（集合竞价溢价） |
| `promo_1to2` / `promo_2to3` | §3.4，各带 `n` / `noisy` |
| `max_streak` | 当日最高板高度（空间板） |

**全市场口径是硬要求**：候选集合口径会把 09-08 的炸板数从 39 算成 12（~3 倍偏差），因为炸板的票不在"今日或昨日涨停"候选集里。

## 四、数据落地（2 张表）

### `stock_price_limits`（权威限价，交易所口径）

`id / trade_date / stock_id / ts_code / pre_close / up_limit / down_limit / created_at`，唯一键 `(trade_date, stock_id)`，价格列 `Numeric(12,4)`（`pre_close` 需显式传 `fields` 才返回，否则整列 NULL）。

- **用 `stock_id` 而非 `ts_code` 做键**：`stocks` 表没有 `ts_code` 列（只在 `detail` JSONB 里），用 JSONB 做 join 需函数索引且每次读都要过 `stocks`；落库时用既有 `_stock_to_ts_code(exchange, symbol)` 解析成 `stock_id`。
- 只落能映射到 A 股 `stocks` 的行（实测单日 5,637 行中 5,499 行可映射，其余为基金/B 股）。
- **不额外建索引**：唯一键 `(trade_date, stock_id)` 已服务日筛，窗口侧走 hash join（实测单日 hash 2.6ms / 317kB）。82k 行（20 交易日）规模下加第二个索引是纯写放大。

### `market_sentiment_daily`（情绪周期聚合，约 10 个数字/日）

`trade_date`（唯一）/ `zt_count` / `dt_count` / `zb_count` / `broken_rate` / `yzt_avg_pct` / `promo_1to2` / `promo_1to2_n` / `promo_2to3` / `promo_2to3_n` / `max_streak` / `max_streak_symbol` / `source`。

**纯派生缓存**：可随时从 `daily_quotes + stock_price_limits` 重建，绝不是真相来源。存在的唯一理由是跨月的情绪周期时序图重算昂贵。

**不建逐日明细表**：`daily_quotes + stock_price_limits` 本身就是历史；实测当日约 75 只候选的一次完整梯队计算只需一条 SQL（16 交易日整窗 2,411 行 / ~48ms，见 §六）。建明细表 = 复制真相 + 多一条会过期的写入链路。

## 五、API 契约

前缀 `/api/v1/market`。

```
GET /limit-up-ladder?date=YYYY-MM-DD&lookback=10
GET /sector-limit-up?date=&sw_l1=
GET /yesterday-limit-up?date=
GET /sentiment/calendar?days=30
```

公共头（**契约字段，不是 UI 装饰**）：

```json
{
  "as_of": "2026-09-08",
  "as_of_prev": "2026-09-07",
  "source": "local_calc",
  "limits_present": true,
  "is_partial": false,
  "sw_coverage": 0.9333,
  "lookback": 10,
  "degraded_reason": null
}
```

### `source` 由数据可用性决定，不由偏好决定

1. `stock_price_limits` 有该日 **且** `daily_quotes` 当日行数 ≥ `PARTIAL_QUOTE_FLOOR`(4000，正常 5,490) → `local_calc`，`is_partial=false`。
2. 否则 → `items: []` + `limits_present=false` + `degraded_reason`（**不猜**）。

Web 附加增强（可失败、不影响主路径）：`source` 当前只有 `local_calc`，Web 只供增强字段（封板/封单/炸板次数），不产出梯队/板块/KPI（东财 `hybk` ≠ 申万 L3）。取数按 **`as_of`**（**必须传 `date`**，不传返回 `rc=102/data=null`）；失败只 log，`seal_*` 保持 `null`。不做交易时段判断：`daily_quotes` 只回补到上一个工作日，`latest_quote_date` 永远不是今天。

> **可用窗口有限（2026-09-14 实测）**：该端点只服务最近约 20 个交易日，更早的日期返回 `rc=0 / pool=[]`（实测 20260825 有行，20260820、20260814、20260807、20260707 均 0 行）。因此对更早的 `as_of`，增强会静默返回空、`seal_*` 保持 `null`——这是**预期行为**（该端点不是历史源），不是故障；不要为此加重试或把空池当错误。响应里的 `qdate` 恒为当天，**不能**用来校验请求日期（实测 `date=20260908` 时 `qdate=20260914`，但 `pool` 内容与 20260908 完全一致）。

### 降级矩阵

| 字段 | `local_calc` | Web 增强（可失败） |
|---|---|---|
| `streak` / `N天M板` / 梯队 / 板块最高板 / 晋级率 / 情绪 KPI | ✅ | —（东财 `hybk` ≠ 申万 L3，不提供） |
| `seal_time` 首次封板时间 | `null` → UI `--` | ✅ |
| `seal_fund` 封单资金 | `null` → UI `--` | ✅ |
| `break_count` 炸板次数 | `null` → UI `--` | ✅ |

## 六、性能与 SQL 约定（实测）

**查询形状决定性能，不是索引**：

| 形状 | 计划 | 耗时 |
|---|---|---|
| 限价表驱动、逐股索引探测 | Nested Loop，5,499 次 probe | 620 ms |
| **候选 CTE 先收敛（75 只）再回查窗口** | `Bitmap Heap Scan daily_quotes` + `Index Scan idx_daily_quotes_stock_date` | **~48 ms**（16 交易日整窗 2,411 行） |

**候选集必须 `DISTINCT`**：`cand` 是"今日 ∪ 昨日"涨停并集，两日都涨停的股票会在 `cand` 里出现两次 → `JOIN cand` 扇出 → 实测涨停数 94（真值 75）、并凭空造出 8 连板。同一查询加 `DISTINCT` 后完全一致。
（与仓库既有"扇出校验"教训同源：聚合前先证 `count(*) == count(DISTINCT 键)`。）

**两条查询，不能合并**：候选窗口查询（梯队/晋级）与当日全市场广度查询（涨停/跌停/炸板家数）口径不同，合并即错。

**窗口不能截成两天**：候选窗口查询要回整段交易日。只回 `as_of`/`as_of_prev` 会让 4 连板显示 `days_span=1`、`boards_in_window≤2`，
并把每只票的 `missing_days` 算成「窗口天数 - 2」（16 窗口 → 14）。实测同一窗口：整窗 2,411 行 vs 只回两天 302 行。

（2026-09-14 复测：`uq_daily_quotes_stock_date` 与 `idx_daily_quotes_stock_date` 同列，规划器按 OID 任选其一，
断言具体索引名会误红；旧稿的「34 ms / 895 行」与自己的 SQL 对不上（该 SQL 只回两天），已作废。）

## 七、非目标

不做分笔/封单采集、不做逐日明细表、不做启发式比例推算、不做 Web-first。
