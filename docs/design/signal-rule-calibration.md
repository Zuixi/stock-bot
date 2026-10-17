# 信号规则标定报告（决策筛子 S8 · 2026-10-17）

> 目的：回答"这 5 条规则是怎么设计的、值不值得上线"。**结论是：5 条里 2 条被证伪、1 条近零、1 条数据不足、2 条有正向符号但未验证。**
> 复跑方式：本文件每条 SQL 都可在 `docker exec postgres psql` 上直接执行（cwd 任意，库 = `stock_bot`）。数据截至 2026-10-16。

## 1. 方法（为什么不是"拍脑袋"）

1. **数据面驱动**：先盘真实存在的字段，规则只能长在存在的字段上（`required_fields` 导入期核对）。
2. **从散户动作反推**：追强势 / 接回调 / 找便宜的好公司 / 怕踩雷 → 一个理由类型只留一条规则。
3. **一条规则一句人话**；说不出人话不上线。
4. **每条必须可证伪**（失效条件机器可判定）。

**标定口径（本报告新增的硬纪律，写进 spec §S8.2）**：

| 项 | 口径 | 为什么 |
|---|---|---|
| 收益 | **按 `close/pre_close` 连乘的 5 日复权收益** | `adj_factor` 实测**仅 1.3% 非空**（1154/88922）⇒ 不能用来排除权；`pre_close` 已含除权调整 |
| 窗口对齐 | **市场交易日历对齐**（T 与 T+5 都必须是市场交易日） | 用 `lead(close,5)` 是"个股 5 行"，停牌股会跨月，制造假收益 |
| 异常值 | winsorize 到 ±50% | 未复权/极端值会把"超额"放大数倍 |
| 统计量 | **按日超额中位数**（当日信号组中位收益 − 当日全市场中位收益），再跨日取中位 | 直接比池化收益会被 beta 与日期构成污染 |
| 样本 | 每日信号数 ≥ **10** 才算一天 | 小样本日的中位数是噪声 |
| 分位口径 | PE 用**滚动 120 日区间位置**（`daily_basic` 实测仅自 2025-04-01 起，做不了 3 年分位） | 数据不够就不能宣称"3 年分位" |

## 2. 标定过程中先查出 4 个数据面错误（都会让规则"假上线"）

| # | 发现 | 实测 |
|---|---|---|
| 1 | `daily_basic_indicators.turnover_rate_f` **全列 NULL** | 133,308 行里 **0** 个非空 ⇒ 换手条件必须用 `turnover_rate`（总量口径）或自算自由流通换手 |
| 2 | `market_limit_up_stock_daily` **只有 2 个交易日** | 2026-10-12/13，247 行 ⇒ 首板规则**不能用它**做样本外；改用 `stock_price_limits`（2025-09-16 起 259 个交易日、142 万行，本仓自称"连板判定唯一权威基准"） |
| 3 | **无个股融资余额表** | 库里只有市场级 `margin`（三所汇总）⇒ 规则 3 的"融资余额 5 日降幅"**结构性不可得**，必须换条件 |
| 4 | `daily_basic_indicators` 仅自 **2025-04-01** 起 | 无法做"PE 三年分位"；只能用滚动 120 日区间位置（并如实标注） |

## 3. 标定结果（730 日窗口，daily-excess 口径）

| 规则 | 有效天数 | **5 日超额中位数** | 日均命中 | 判定 |
|---|---|---|---|---|
| 1 放量新高（60 日新高 + 量 ≥1.8×20 日均额） | 236 | **−0.922%** | 143 | ❌ **证伪**（跑输基准） |
| 2 首板（`stock_price_limits` 派生） | 241 | **−0.275%** | 63 | ❌ **证伪** |
| 3 急跌未破位 + 缩量（5 日跌 ≥12% + 量 <0.8×均额） | 70 | **+1.004%** | 28 | ⚠️ 正向但 **in-sample**、样本日仅 70 |
| C 缩量新高（60 日新高 + 量 <0.8×均额） | 103 | **+1.031%** | 39 | ⚠️ 正向但 **in-sample**（且是在看到 1 失败后才试的 → 选择偏差） |
| 4 PE 低位（120 日区间位置 <20%） | 247 | **+0.123%** | **1,459** | ❌ **近零且命中量不可用** |
| 5 风险红线 | — | 不可标定 | — | ⏸ `announcements.equity_change` **仅 30 行** |

**关键否证**：第一次跑出的 +3.9% / +4.9% 超额是**假的** —— 根因是未复权 + 停牌跨期 + 小样本日（改口径后回落到 +1.0%）。**不按第 1 节的口径跑，就会把噪声当 alpha。**

## 4. 结论

1. **"放量突破"与"首板接力"这两条最直觉的散户规则，在本市场样本上是负 edge** ⇒ 按 spec 的 kill criterion，不得上线。
2. 有两条候选（缩量新高、急跌不破位+缩量）出现 **+1%/5 日** 的正向符号，但：in-sample、样本日少（70/103）、未做样本外切分、且是**多方案试错后的幸存者** ⇒ 只能作为**研究假设**，不能当产品卖点。
3. 全部规则的命中量（28–1459/日）都远超"每天 0–3 条"的产品容量 ⇒ 必须引入**排序因子**，而排序因子本身同样需要标定（否则等于换一个黑箱）。
4. **规则 5（风险红线）在做标定前不可上线**：原料只有 30 行公告。

## 5. 对产品的直接含义（owner 2026-10-17 已裁定：**A**）

"每日信号清单"依赖**预测性 edge**；标定显示**当前资产不足以支撑**。三条现实路线：

- **A. 转自选体检（✅ 已采纳，2026-10-17）**：不做预测，只回答"你手里的票今天发生了什么"（异动/放量/破位/公告/龙虎榜）。**不需要 edge**，工具型定位天然成立；冷启动差（要先有自选）。
- **B. 继续量化研究**：扩样本外切分、更多变体、行业基准、交易成本。这是**研究工作量**（周/月级），不是"加页面"。
- **C. 保留清单但去掉预测话术**：只做"符合你条件的票"筛选器（**不宣称值得看**），价值弱但成本最低。

## 6. 复跑 SQL（节选，完整口径见第 1 节）

```sql
-- 复权 5 日收益 + 日历对齐（所有规则共用）
WITH d AS (SELECT max(trade_date) mx FROM daily_quotes),
cal AS (SELECT trade_date, row_number() OVER (ORDER BY trade_date) AS idx FROM (SELECT DISTINCT trade_date FROM daily_quotes) t),
fwdcal AS (SELECT c.trade_date, c5.trade_date AS t5 FROM cal c JOIN cal c5 ON c5.idx = c.idx + 5),
q AS (SELECT stock_id, trade_date, close, amount,
             sum(ln(1 + close/pre_close - 1)) OVER (PARTITION BY stock_id ORDER BY trade_date) AS cum,
             max(close) OVER (PARTITION BY stock_id ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS hi60,
             avg(amount) OVER (PARTITION BY stock_id ORDER BY trade_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS amt20
      FROM daily_quotes, d WHERE trade_date > d.mx - 800 AND pre_close > 0 AND close > 0),
j AS (SELECT a.stock_id, a.trade_date, a.close, a.amount, a.hi60, a.amt20,
             least(greatest(exp(b.cum - a.cum) - 1, -0.5), 0.5) AS ret5,
             (a.close >= a.hi60 AND a.amount >= 1.8*a.amt20) AS s1,
             (a.close >= a.hi60 AND a.amt20 IS NOT NULL AND a.amount < 0.8*a.amt20) AS s2
      FROM q a JOIN fwdcal f ON f.trade_date = a.trade_date JOIN q b ON b.stock_id = a.stock_id AND b.trade_date = f.t5),
per_day AS (SELECT trade_date, percentile_cont(0.5) WITHIN GROUP (ORDER BY ret5) AS mkt,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY ret5) FILTER (WHERE s1) AS m1,
                   count(*) FILTER (WHERE s1) AS n1
            FROM j GROUP BY trade_date)
SELECT count(*) FILTER (WHERE n1>=10) AS days,
       round(100*percentile_cont(0.5) WITHIN GROUP (ORDER BY (m1-mkt))::numeric, 3) AS excess_med_pct,
       round(avg(n1), 1) AS hits_per_day
FROM per_day WHERE n1>=10;
```

> 首板：把 `s1` 换成 `is_lu AND NOT coalesce(prev_lu,false)`，`is_lu` 由 `stock_price_limits.up_limit` 派生（`close >= up_limit - 0.005`）。
> PE：`((pe_ttm - min120)/(max120 - min120)) < 0.2`，`min/max` 用 `ROWS BETWEEN 119 PRECEDING AND CURRENT ROW`。
