"""spec §S1.8 情绪/市场宽度与动量 —— 6 个全市场日频指标的**计算 + 落库**。

## 这个模块是什么

把 S1.8 的 6 个 `metric_key` 算成**日频序列**并写进 `industry_metrics`
（`industry_key='market'`，`stock_id=0`）。读面**不在这里** —— 走 S1.2 已建好的
`GET /api/v1/metrics/latest` / `/series`（registry 驱动 + 四徽章 + Redis 缓存）。
本模块只负责"s1.8 的 6 个指标从哪来、怎么算、缺数怎么标"，**不新建平行读路**。

## 复用审计（brief「控制器已核实的现状」表，直接采信）

| S1.8 `metric_key` | 底座 | 本模块做什么 |
|---|---|---|
| `limit_up_down_ratio` | `market_sentiment_daily.zt_count` / `.dt_count` | 只做除法 |
| `limit_up_break_ratio` | `market_sentiment_daily.broken_rate` | 只做 ×100 归一 |
| `max_consecutive_limit_up` | `market_sentiment_daily.max_streak` | 只做投影 |
| `first_board_promotion_ratio` | `market_sentiment_daily.promo_1to2` | 只做 ×100 归一 + 分母守卫 |
| `advance_decline_ratio` | `daily_quotes` 全市场家数 | **与市场模块同源**（见下） |
| `above_ma20_ratio` | `daily_quotes` + 新算 20 日滚动均值 | 唯一的新算法 |

**涨停事实一律不重算**：4 个涨停类指标的全部数值来自 `market_sentiment_daily` 的
既有列，本模块一行都不碰 `stock_price_limits` / `market_limit_up_stock_daily`。

## 同源要求（spec §S1.8 验收：市场 `A股涨跌分布` ↔ 情绪 `涨跌家数` 同源同值）

市场模块的涨跌分布（`market_service.get_distribution`）**不是**一条独立 SQL：它读
`market_snapshot_service.load_day_rows(db, day)` 取回当日全市场行，再用
`_distribution_bucket` 把每行的 `pct_chg` 数进 11 个桶。本模块的涨跌家数**不另写
SQL、不另写分桶**，而是：

- 调用**同一个** `market_snapshot_service.load_day_rows`（同一取行语句、同一份 Redis
  payload、同一套 `ROW_KEYS` 规范化）；
- 调用**同一个** `market_snapshot_service.summarize_group`（全市场就是一个组），而
  `summarize_group` 正是 `up_count` / `down_count` / `flat_count` 的唯一定义处。

于是"市场分布的正数桶之和 == 情绪上涨家数、负数桶之和 == 情绪下跌家数"**按构造
成立**，不是靠两套算法事后对齐（`tests/test_market_sentiment_breadth.py` 有真库锁定
用例逐桶相加对拍）。**这是刻意的"只复用、不新造"**：本模块里没有第二个 `pct_chg`
分桶实现，也没有第二条读 `daily_quotes` 的取行语句。

> 为什么不是"情绪算完、市场去引用情绪 API"（S0.4 §4.5 的模块边界裁定原文）：
> 裁定的是**口径与计算的归宿**（情绪拥有 `advance_decline_ratio` 这个注册指标的定义
> 与落库，市场侧不得再定义一个平行口径）。落到代码不该为此把 `daily_quotes` 读两遍 ——
> 情绪与市场共用同一个已存在的 loader 就是这条裁定的最强实现（一份 SQL、一份缓存），
> 而"市场去调情绪 API"会把一个同步纯聚合变成一次跨模块 HTTP 调用。
> **差异已核**：市场 `get_distribution` 只改 `market.py`（本任务并行禁止触碰），
> 故本次不动它；同值性由两侧共同的 loader + 上表的逐桶对拍用例保证，并在
> `task-11-report.md` 里列为"留给控制器的对齐项"。

## 缺数不静默为 0（spec §S1.8 验收 + ADR 0014）

- **缺数一律 `value = NULL`**，`extra.missing_reason` 带原因码（`None` = 无缺口）。
  0 在"涨跌停家数"上是"今天只有 0 家涨停"这一**事实断言**，与"这天没数据"相反。
- **完全不写 0 行**：某交易日若 `daily_quotes` 无行，本模块**不落任何行**（不拿
  0 伪造一天"全市场 0 家上涨"）；该日在覆盖率对账里表现为 `missing_days` 的
  `no_quote_rows`。
- 对账可见性由 :func:`metric_coverage` 提供（逐指标 `covered_days` / `null_days` /
  `missing_days` + 原因分布），可由 `/market/data-freshness` 一类巡检接口消费。
  **本任务不新增独立告警 job**（brief 硬性约束 2 只要求"缺数可见 + 说明是否需要独立
  告警"）：`market_sentiment_daily` 的缺口已有既有的 KPI 巡检/对账面，S1.8 的 6 个
  指标是它的纯派生（同一批交易日的投影），再加一个 job 只会重复报同一件事。

## A/B/C 归类（ADR 0014）

**6 个指标全属 B 类**（只用**当日**点内事实，可回算且无前视偏差）：

- `advance_decline_ratio` / `market_advance_count` / `market_decline_count`：只用当日
  `daily_quotes.pct_chg`（+ 平盘=当日 pct_chg 为 0/NULL）→ 可整天重算、允许回补。
- `above_ma20_ratio`：当日收盘 + **截至当日的** 20 日窗口（`ROWS BETWEEN 19 PRECEDING
  AND CURRENT ROW`，窗口内不含未来行）→ 可回算、允许回补；带窗口完备守卫
  （`win = 20`，不足 20 个交易日的标的整只剔除，不进分子也不进分母）。
- `limit_up_down_ratio` / `limit_up_break_ratio` / `max_consecutive_limit_up` /
  `first_board_promotion_ratio`：只读当日 `market_sentiment_daily` 行，而该表本身被
  ADR 0014 归为 B 类（"情绪 KPI"）→ 随底座同为 B 类。

**没有 A 类指标**（本模块不消费东财涨停池的封板时间/封单/炸板次数等一次性盘中明细，
那些在 `market_limit_up_stock_daily` 由别的路径负责）。**没有 C 类指标**：涨跌家数按
`daily_quotes` 全市场计数，不依赖申万归属或任何"今天谁属于谁"的分类快照 ——
故本模块的指标**不需要** `attribution=current_sw_snapshot` 这类字段级归属标注。

## PIT 纪律（ADR 0011 / Ruling 34-35）

`published_at` 恒填该交易日的 **15:00 Asia/Shanghai**（A 股收盘时刻）——这是这些
数值"实际可得"的最早时点：全部输入（收盘价、涨停判定）在收盘那一刻才定型。
于是读面的 `as_of_source` 恒为 `published_at`（**不是**回退的 `period`），
S2.2 的规则/回测护栏不会因本模块的序列而报"未登记 PIT"。
`caliber_version` 恒填 `s1.8-v1`（口径变更时 bump 并留断点，ADR 0011 第 4 条）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as dtime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.industry_research import IndustryMetric
from app.models.quote import DailyQuote
from app.repositories import industry_metric_repo as metric_repo
from app.services.industry_registry import MARKET_CALIBER_VERSION, MARKET_SENTIMENT_SOURCE
from app.services.market_service import (
    _DISTRIBUTION_RANGES as DISTRIBUTION_RANGES,
)
from app.services.market_service import (
    _distribution_bucket as distribution_bucket,
)
from app.services.market_snapshot_service import load_day_rows, summarize_group

logger = logging.getLogger(__name__)

_CN_TZ = ZoneInfo("Asia/Shanghai")

#: `market_sentiment_daily` 的 `source` 白名单里"实时口径"那一支（`backfill` 是回补支）。
#: 本模块**只读**该表，两个来源都读（回补行也是真实可算的事实）。
SENTIMENT_DAILY_TABLE = "market_sentiment_daily"

#: 指标值的权威来源标签。与 `market_sentiment_daily.source` 同值域（`local_calc` /
#: `backfill`），这里登记的是 registry 的**源优先级表**用的名字：`local_calc` 是本地
#: 计算口径，`backfill` 只是同口径的历史回补，不是另一个源（同口径不得分两个 key，
#: 也不得在 registry 里占两格 —— 字典 §4.2 规则 5）。
#: 故落库时把底座的两个 source 值**统一投影**为 `local_calc`，`extra.base_source`
#: 保留底座的原始产地标记以留痕（ADR 0014「产地决定可信度」）。
METRIC_SOURCE = MARKET_SENTIMENT_SOURCE

#: 20 日均线的窗口长度（交易日，含当日）。改这个数 = 改口径 → 必须 bump `caliber_version`。
MA20_WINDOW = 20

#: A 股收盘时刻：`published_at` 的取值。
MARKET_CLOSE = dtime(15, 0)

#: S1.8 的 6 个核心 `metric_key`（顺序 = spec 表格顺序）。
S18_METRIC_KEYS: tuple[str, ...] = (
    "advance_decline_ratio",
    "limit_up_down_ratio",
    "above_ma20_ratio",
    "limit_up_break_ratio",
    "max_consecutive_limit_up",
    "first_board_promotion_ratio",
)

#: 另外 2 个**底层家数**指标。为什么它们也是一等 `metric_key`（而不是只塞进 `extra`）：
#: spec §S1.8 的行为判据是"市场模块的涨跌分布与情绪模块的涨跌家数同源、数值一致"——
#: 要让"涨跌家数"这个对象**可被独立查询与对拍**，它必须自己是注册指标；埋在 `extra`
#: 里会让"同值"只能靠读 ratio 反推（分母为 0 的日子还反推不出来）。
BREADTH_COUNT_KEYS: tuple[str, ...] = ("market_advance_count", "market_decline_count")

#: 本模块对外承诺落库的全部 `metric_key`（读面 registry 也登记这 8 个）。
ALL_METRIC_KEYS: tuple[str, ...] = (*S18_METRIC_KEYS, *BREADTH_COUNT_KEYS)

#: `extra.missing_reason` 取值（与 registry 的 `MARKET_METRIC_MISSING_REASONS` 同源语义）。
MISSING_NO_SENTIMENT_DAY = "no_sentiment_day"
MISSING_SOURCE_NULL = "source_null"
MISSING_NO_QUOTE_ROWS = "no_quote_rows"
MISSING_NO_MA20_WINDOW = "no_ma20_window"


def published_at_for(day: date) -> datetime:
    """交易日 → 该日 15:00（Asia/Shanghai）的 `published_at`（带时区，non-naive）。

    naive datetime 会让读面的 `astimezone` 按服务器本地时区解释（Ruling 35 的反面），
    故这里恒带 `Asia/Shanghai`。
    """
    return datetime.combine(day, MARKET_CLOSE, tzinfo=_CN_TZ)


@dataclass(frozen=True)
class MetricValue:
    """一个指标在一天的结果：值 + 缺口原因 + 可复核的分母/原始计数。"""

    metric_key: str
    value: float | None
    missing_reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    """比值；任一输入缺失或分母为 0 → ``None``（**不落 0、不落无穷**）。

    分母为 0 在"涨跌停比"上是"今天没有跌停"——比值数学上无定义，落 0 会被读成
    "涨跌停一样多"，落 `inf` 会污染分位数。缺数恒 `None` 是本模块的统一纪律。
    """
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _pct(fraction: float | None) -> float | None:
    """0–1 小数 → 百分数（`None` 透传）。库内 `broken_rate` / `promo_1to2` 是小数。"""
    return None if fraction is None else fraction * 100.0


def _day_args(day_lo: date, day_hi: date) -> dict[str, date]:
    """`text()` SQL 的日期实参。

    **为什么 SQL 里写 `CAST(:day_lo AS date)` 而这里传 `date` 对象**：asyncpg 不做
    隐式类型推断 —— 裸写 `BETWEEN :day_lo AND :day_hi` 并传 Python `date` 会报
    ``'str' object has no attribute 'toordinal'``（驱动按语句文本编码参数、又期望
    `date`）。SQL 侧显式 `CAST` 让 PostgreSQL 承担类型转换，Python 侧照常传 `date`，
    两侧都不依赖"驱动猜类型"。

    只给 **raw `text()`** 的语句用（:func:`ratio_above_ma20`）。走 SQLAlchemy Core
    `select()` 的语句（:func:`_quote_days` / :func:`metric_coverage`）**直接传
    `date` 对象**：ORM 表达式自带类型，再 `isoformat()` 会变成
    ``date >= varchar`` 而报错。
    """
    return {"day_lo": day_lo, "day_hi": day_hi}


async def ratio_above_ma20(
    db: AsyncSession,
    day_lo: date,
    day_hi: date,
) -> dict[date, tuple[int, int]]:
    """`[day_lo, day_hi]` 内每日 (具备完整 20 日窗口的标的数, 其中 close > MA20 的数)。

    **算法**（单条窗口函数 SQL，无 Python 端逐股循环）：

    1. 内层：把 `[day_lo, day_hi]` 的 `daily_quotes` 行按 `stock_id` 分区、`trade_date`
       升序开两个窗口 ——
       `avg(close) OVER (PARTITION BY stock_id ORDER BY trade_date ROWS BETWEEN 19
       PRECEDING AND CURRENT ROW)` 是该股"含当日、前 19 个交易日"的 20 日简单均值；
       `count(*) OVER (同一个窗口)` 是**实际参与均值**的行数。
    2. 窗口内**不含未来行**（`ROWS ... PRECEDING` 只回看）→ 无前视偏差，可回补（B 类）。
    3. 守卫：`win = 20`。不足 20 个交易日的标的（次新股、区间开头）整只剔除，
       **不进分子也不进分母**。理由：按字面口径"没有 MA20"的股票既非"在均线之上"
       也非"在均线之下"；把它算作 0 等于让新上市高峰期被误读成情绪冰点。
    4. `ma20 IS NOT NULL` 再排除"close 全为 NULL 的窗口"（avg 回 NULL）。
    5. 外层按日分组，分子判据是 `close > ma20`（**严格大于**：等于均线不计入）。

    **为什么按 `[day_lo, day_hi]` 一次算而不是逐日算**：窗口函数的输入必须包含
    `day_lo` 之前 19 个交易日的行，一次算整段只需一趟扫描；逐日算会把
    `BETWEEN day - N AND day` 的内层重复 N 遍（250 天 × 每条扫 ~250 天 = 平方级）。
    调用方传入的 `day_lo` 必须是**目标区间首日往前推足够多个交易日**的日期
    （见 :func:`upsert_metric_range`：多取 60 个日历日，> 19 个交易日）。

    返回 `{交易日: (eligible, above)}`；区间内没有任何具备完整窗口的标的的日子
    **不出现在字典里**（调用方据此落 `missing_reason='no_ma20_window'`）。
    """
    sql = text(
        f"""
        SELECT w.trade_date,
               count(*)                                        AS eligible,
               count(*) FILTER (WHERE w.close > w.ma20)        AS above
        FROM (
            SELECT stock_id,
                   trade_date,
                   close,
                   avg(close) OVER w AS ma20,
                   count(*)   OVER w AS win
            FROM daily_quotes
            WHERE trade_date BETWEEN CAST(:day_lo AS date) AND CAST(:day_hi AS date)
            WINDOW w AS (
                PARTITION BY stock_id ORDER BY trade_date
                ROWS BETWEEN {MA20_WINDOW - 1} PRECEDING AND CURRENT ROW
            )
        ) w
        WHERE w.win = {MA20_WINDOW} AND w.ma20 IS NOT NULL
        GROUP BY w.trade_date
        ORDER BY w.trade_date
        """
    )
    rows = (await db.execute(sql, _day_args(day_lo, day_hi))).all()
    return {row.trade_date: (int(row.eligible), int(row.above)) for row in rows}


async def _sentiment_rows_by_day(db: AsyncSession, day_lo: date, day_hi: date) -> dict[date, Any]:
    """`[day_lo, day_hi]` 内 `market_sentiment_daily` 逐日行（**直读全列**）。

    用 `text()` 直读而不经 ORM：本模块只消费该表，加一列就在 SQL 里加一个字段，
    不必为它维护一个投影 dataclass；与 `limit_up_repo` 的分工是"那边服务读 API"、
    "本模块服务派生落库"。
    """
    sql = text(
        f"""
        SELECT trade_date, zt_count, dt_count, zb_count, broken_rate,
               promo_1to2, promo_1to2_n, max_streak, max_streak_symbol, source
        FROM {SENTIMENT_DAILY_TABLE}
        WHERE trade_date BETWEEN CAST(:day_lo AS date) AND CAST(:day_hi AS date)
        ORDER BY trade_date
        """
    )
    rows = (await db.execute(sql, _day_args(day_lo, day_hi))).all()
    return {row.trade_date: row for row in rows}


def _sentiment_metrics(day: date, row: Any | None) -> list[MetricValue]:
    """4 个涨停类指标：**只读** `market_sentiment_daily` 既有列，不重算涨停事实。"""
    if row is None:
        return [
            MetricValue(key, None, MISSING_NO_SENTIMENT_DAY)
            for key in (
                "limit_up_down_ratio",
                "limit_up_break_ratio",
                "max_consecutive_limit_up",
                "first_board_promotion_ratio",
            )
        ]
    zt, dt = row.zt_count, row.dt_count
    zt_dt = _ratio(zt, dt)
    broken = _pct(row.broken_rate)
    promo_n = row.promo_1to2_n
    # 分母为 0 → 比值无定义（写入方目前恒写 NULL，这里仍显式守卫：未来改口径时
    # 一个"0/0 被写成 0.0"的行会被这条挡住，而不是静默流进分位样本）。
    promo = _pct(row.promo_1to2) if promo_n else None
    return [
        MetricValue(
            "limit_up_down_ratio",
            zt_dt,
            None if zt_dt is not None else MISSING_SOURCE_NULL,
            {"numerator": zt, "denominator": dt, "base_source": row.source},
        ),
        MetricValue(
            "limit_up_break_ratio",
            broken,
            None if broken is not None else MISSING_SOURCE_NULL,
            {"base_source": row.source},
        ),
        MetricValue(
            "max_consecutive_limit_up",
            float(row.max_streak),
            None,
            {"max_streak_symbol": row.max_streak_symbol, "base_source": row.source},
        ),
        MetricValue(
            "first_board_promotion_ratio",
            promo,
            None if promo is not None else MISSING_SOURCE_NULL,
            {
                "numerator": row.promo_1to2,
                "denominator": promo_n,
                "base_source": row.source,
            },
        ),
    ]


#: 市场模块 `A股涨跌分布` 的 11 个桶里，哪些算"涨"、哪些算"跌"、哪个是"平"。
#:
#: **这是与市场分布做到"同源同值"的关键，也是一处必须写明的口径差**：
#: 市场分布的 `0~1%` 桶的判据是 `0 <= pct_chg < 1`（含 **pct_chg == 0**，即平盘），
#: 而 `summarize_group` 的 `up_count` 判据是 `pct_chg > 0`（**不含**平盘）。实测
#: 2026-10-09：分布正数桶之和 3414 vs `summarize_group.up_count` 3297，差额恰为
#: `flat_count = 117`（pct_chg == 0 的平盘股）。
#:
#: 取哪一边？spec §S1.8 的行为判据是"市场模块的涨跌分布与情绪模块的涨跌家数
#: **同源**（同一 API），**数值一致**"，且 §S0.4 的边界裁定把口径与计算判给情绪。
#: 于是以**市场已发布的 11 桶标签**为准（对同一份行、同一天，`sum` 桶 == 本模块值，
#: 逐桶可复核），并把上面的差额用 `flat_in_positive_bucket` / `sign_basis` 暴露出来
#: —— 不是两个算法，而是**同一个算法的两种切法之差被显式记账**（差额 = 平盘家数，
#: 恒等式 `up_bucket_sum - up_sign_count == flat_in_positive_bucket` 有测试锁定）。
#: 市场侧若以后改口径（把平盘挪出 `0~1%`），本模块的账目会当场对不上，必须同步改。
DISTRIBUTION_UP_LABELS: tuple[str, ...] = ("0~1%", "1~3%", "3~5%", ">5%", "涨停")
DISTRIBUTION_DOWN_LABELS: tuple[str, ...] = ("跌停", ">-7%", "-5~-7%", "-3~-5%", "-1~-3%", "0~-1%")

#: 记账字段名：`0~1%` 桶里"实际是平盘（pct_chg == 0）"的家数。
FLAT_IN_POSITIVE_BUCKET = "flat_in_positive_bucket"


def distribution_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """一日全市场行 → 11 个分布的桶计数（**复用市场模块的唯一分桶函数**）。

    直接用 `market_service._DISTRIBUTION_RANGES` + `market_service._distribution_bucket`
    —— 同一个桶表、同一个判据函数。本模块**不复制**这段分桶逻辑；市场侧改判据，
    这里自动跟着改（若改了桶标签，下面的 `_assert_bucket_labels` 会当场报错）。
    """
    _assert_bucket_labels()
    counts = dict.fromkeys(DISTRIBUTION_RANGES, 0)
    for row in rows:
        counts[distribution_bucket(row.get("pct_chg"))] += 1
    return counts


def _assert_bucket_labels() -> None:
    """市场分布的桶标签集必须**恰好**等于本模块的涨+跌标签集（自检，防止静默漂移）。

    少一个桶/多一个桶都会让 `distribution_counts` 的家数对不上市场端点；这条断言把
    "市场模块改了桶标签但没同步 S1.8"从静默错数变成当场 `RuntimeError`。
    """
    declared = set(DISTRIBUTION_UP_LABELS) | set(DISTRIBUTION_DOWN_LABELS)
    actual = set(DISTRIBUTION_RANGES)
    if declared != actual:
        raise RuntimeError(
            "market distribution bucket labels changed; S1.8 涨跌家数的标签映射需要同步："
            f"only_in_s1_8={sorted(declared - actual)} only_in_market={sorted(actual - declared)}"
        )


def breadth_counts_from_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    """一日全市场行的涨/平/跌家数（**与市场分布逐桶对拍**）。

    两份家数都在这里，**都是同一份行集**，差别只有一处口径切法（见
    :data:`DISTRIBUTION_UP_LABELS` 的说明）：

    - `up_count` / `down_count` / `flat_count`：**市场分布的桶之和**
      （`up = ` 正数桶之和，`down = ` 负数桶之和，`flat = total - up - down`）——
      这是落进 `market_advance_count` / `market_decline_count` /
      `advance_decline_ratio` 的口径，保证 spec 的"与市场分布数值一致"逐桶可验。
    - `up_sign_count` / `down_sign_count`：`summarize_group` 的**符号口径**
      （`pct_chg > 0` / `pct_chg < 0`，平盘含 NULL），随 `extra` 一起留痕。

    恒等式（有测试锁定）：`up_count - up_sign_count == flat_in_positive_bucket`，
    `down_count == down_sign_count`（实测负数桶的边界与 `< 0` 完全重合）。
    """
    distribution = distribution_counts(rows)
    stats = summarize_group(rows)
    total = int(stats["total"])
    up = sum(distribution[label] for label in DISTRIBUTION_UP_LABELS)
    down = sum(distribution[label] for label in DISTRIBUTION_DOWN_LABELS)
    return {
        "total": total,
        "up_count": up,
        "down_count": down,
        "flat_count": total - up - down,
        "up_sign_count": int(stats["up_count"]),
        "down_sign_count": int(stats["down_count"]),
        FLAT_IN_POSITIVE_BUCKET: up - int(stats["up_count"]),
    }


def _breadth_metrics(counts: dict[str, int] | None) -> list[MetricValue]:
    """涨跌家数 + 涨跌比。`counts is None` = 该日 `daily_quotes` 无行（缺数而非 0）。"""
    if counts is None:
        return [
            MetricValue("market_advance_count", None, MISSING_NO_QUOTE_ROWS),
            MetricValue("market_decline_count", None, MISSING_NO_QUOTE_ROWS),
            MetricValue("advance_decline_ratio", None, MISSING_NO_QUOTE_ROWS),
        ]
    up, down = counts["up_count"], counts["down_count"]
    adr = _ratio(up, down)
    extra = {
        "total": counts["total"],
        "up_count": up,
        "down_count": down,
        "flat_count": counts["flat_count"],
        # 口径留痕：本值是"市场分布桶之和"，与符号口径的差 = 平盘被含进 0~1% 桶的家数
        "sign_basis": "market_distribution_buckets",
        "up_sign_count": counts["up_sign_count"],
        "down_sign_count": counts["down_sign_count"],
        FLAT_IN_POSITIVE_BUCKET: counts[FLAT_IN_POSITIVE_BUCKET],
    }
    return [
        MetricValue("market_advance_count", float(up), None, dict(extra)),
        MetricValue("market_decline_count", float(down), None, dict(extra)),
        MetricValue(
            "advance_decline_ratio",
            adr,
            None if adr is not None else MISSING_SOURCE_NULL,
            dict(extra),
        ),
    ]


def _ma20_metric(day: date, ma20: dict[date, tuple[int, int]]) -> MetricValue:
    """MA20 占比：缺失分两种原因（整日无窗口 vs 该日无行），值恒 `None`（不落 0）。"""
    if day not in ma20:
        return MetricValue(
            "above_ma20_ratio", None, MISSING_NO_MA20_WINDOW, {"window": MA20_WINDOW}
        )
    eligible, above = ma20[day]
    if eligible == 0:  # pragma: no cover — SQL 的 GROUP BY 不会产出 0 行的组，防御性
        return MetricValue(
            "above_ma20_ratio", None, MISSING_NO_MA20_WINDOW, {"window": MA20_WINDOW}
        )
    ratio = above / eligible * 100.0
    return MetricValue(
        "above_ma20_ratio",
        ratio,
        None,
        {"window": MA20_WINDOW, "eligible": eligible, "above": above},
    )


async def compute_day_metrics(
    db: AsyncSession,
    day: date,
    *,
    quote_rows: list[dict[str, Any]] | None = None,
    ma20: dict[date, tuple[int, int]] | None = None,
    sentiment_row: Any | None = None,
    sentiment_loaded: bool = False,
) -> dict[str, MetricValue]:
    """一天 8 个指标的结果（本模块的**唯一**计算入口，回补与增量共用）。

    四个可选入参是**给区间调用方的批量注入口**（回补 250 天时不该逐日重扫）：

    - `quote_rows`：该日全市场行（`None` → 本函数自己调 `load_day_rows` 取）。
      传 `[]` 与 `None` 语义**不同**：`[]` = "取过了，当天确实没有行" → 缺数。
    - `ma20`：`ratio_above_ma20` 的整段结果（`None` → 只算这一天）。
    - `sentiment_row` / `sentiment_loaded`：`sentiment_loaded=True` 且
      `sentiment_row is None` = "查过了，该日无底座行" → 4 个指标缺数。

    返回 `{metric_key: MetricValue}`，键集恒为 :data:`ALL_METRIC_KEYS`。
    """
    if sentiment_loaded:
        sentiment = _sentiment_metrics(day, sentiment_row)
    else:
        rows = await _sentiment_rows_by_day(db, day, day)
        sentiment = _sentiment_metrics(day, rows.get(day))

    if quote_rows is None:
        quote_rows = await load_day_rows(db, day)
    counts = breadth_counts_from_rows(quote_rows) if quote_rows else None
    breadth = _breadth_metrics(counts)

    if ma20 is None:
        ma20 = await ratio_above_ma20(db, day, day)
    ma20_metric = _ma20_metric(day, ma20)

    return {m.metric_key: m for m in (*breadth, ma20_metric, *sentiment)}


def _row(day: date, metric: MetricValue) -> dict[str, Any]:
    """`MetricValue` → `industry_metrics` 行（scope='market'，`stock_id=0`）。"""
    extra = dict(metric.extra)
    if metric.missing_reason is not None:
        extra["missing_reason"] = metric.missing_reason
    return {
        "industry_key": "market",
        "stock_id": 0,
        "metric_key": metric.metric_key,
        "source": METRIC_SOURCE,
        "source_tier": "derived",
        "freq": "daily",
        "period": day,
        "value": metric.value,
        "unit": _UNITS[metric.metric_key],
        # NULL 值的行**也要留 extra**：`missing_reason` 是"为什么没有值"的唯一证据
        # （ADR 0014「产地决定可信度」的同款要求：有行 ≠ 有值，必须能区分）。
        "extra": extra or None,
        "published_at": published_at_for(day),
        "caliber_version": MARKET_CALIBER_VERSION,
    }


#: 单位随 metric_key 固定在 registry（字典 §4.2 规则 3）；本表是 registry 的投影，
#: 由 `test_market_sentiment_breadth.py` 的用例机械比对，禁止在这里"顺手改单位"。
_UNITS: dict[str, str | None] = {
    "advance_decline_ratio": None,
    "limit_up_down_ratio": None,
    "above_ma20_ratio": "%",
    "limit_up_break_ratio": "%",
    "max_consecutive_limit_up": "板",
    "first_board_promotion_ratio": "%",
    "market_advance_count": "家",
    "market_decline_count": "家",
}


@dataclass(frozen=True)
class BackfillStats:
    """一次区间落库的可核对统计（回补脚本直接打印它）。"""

    day_lo: date
    day_hi: date
    quote_days: int
    rows_upserted: int
    days_written: int
    #: `daily_quotes` 有行但指标**一行都没落**的日子（取行异常；正常应为空）。
    no_quote_row_days: list[date]
    #: 落了行、但 `above_ma20_ratio` 判"无完整 20 日窗口"的日子（回补区间头部）。
    no_ma20_window_days: list[date]
    per_metric_null: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "day_lo": self.day_lo.isoformat(),
            "day_hi": self.day_hi.isoformat(),
            "quote_days": self.quote_days,
            "rows_upserted": self.rows_upserted,
            "days_written": self.days_written,
            "no_quote_row_days": [d.isoformat() for d in self.no_quote_row_days],
            "no_ma20_window_days": [d.isoformat() for d in self.no_ma20_window_days],
            "per_metric_null": self.per_metric_null,
        }


async def _quote_days(db: AsyncSession, day_lo: date, day_hi: date) -> list[date]:
    """区间内 `daily_quotes` 实际持有的交易日（升序）—— 落库天的**唯一**定义。

    不按日历推：节假日工作日的脏日期不该被当成交易日，也不该被当成"缺数日"。
    """
    stmt = (
        select(DailyQuote.trade_date)
        .where(
            DailyQuote.trade_date >= day_lo,
            DailyQuote.trade_date <= day_hi,
        )
        .group_by(DailyQuote.trade_date)
        .order_by(DailyQuote.trade_date)
    )
    return [r[0] for r in (await db.execute(stmt)).all()]


async def upsert_metric_range(
    db: AsyncSession,
    day_lo: date,
    day_hi: date,
    *,
    warmup_days: int = 60,
) -> BackfillStats:
    """把 `[day_lo, day_hi]` 的 8 个指标算出来并**幂等**写进 `industry_metrics`。

    - `warmup_days`：MA20 的窗口预热。区间**首日前**多取这么多日历日的
      `daily_quotes` 行喂给窗口函数，否则回补区间的头 20 个交易日会集体判"窗口不足"
      而整片落 `no_ma20_window`。60 个日历日 ≈ 40 个交易日 > 19 个交易日，留足余量；
      预热行**只用于算 MA20，不落库**（落库天由 :func:`_quote_days` 决定）。
    - 幂等：唯一键 `(industry_key, stock_id, metric_key, source, freq, period)` +
      `ON CONFLICT DO UPDATE`，重跑同一区间新增行数 = 0。
    - 调用方负责 `await db.commit()`（本层不替上层决定事务边界）。
    """
    days = await _quote_days(db, day_lo, day_hi)
    ma20_window_lo = day_lo - timedelta(days=warmup_days)
    ma20 = await ratio_above_ma20(db, ma20_window_lo, day_hi)
    sentiment = await _sentiment_rows_by_day(db, day_lo, day_hi)

    rows: list[dict[str, Any]] = []
    per_metric_null: dict[str, int] = {k: 0 for k in ALL_METRIC_KEYS}
    no_quote_row_days: list[date] = []
    no_ma20_window_days: list[date] = []
    for day in days:
        quote_rows = await load_day_rows(db, day)
        if not quote_rows:
            # 有 daily_quotes 行的日子不该取回空行集；真取回空的（缓存/语句异常）时
            # 本日涨跌类整片缺数，且**不落 0** —— 记进 no_quote_row_days 交给对账。
            no_quote_row_days.append(day)
            quote_rows = []
        counts = breadth_counts_from_rows(quote_rows) if quote_rows else None
        values = {
            **{m.metric_key: m for m in _breadth_metrics(counts)},
            "above_ma20_ratio": _ma20_metric(day, ma20),
            **{m.metric_key: m for m in _sentiment_metrics(day, sentiment.get(day))},
        }
        if values["above_ma20_ratio"].missing_reason is not None:
            no_ma20_window_days.append(day)
        for key in ALL_METRIC_KEYS:
            metric = values[key]
            if metric.value is None:
                per_metric_null[key] += 1
            rows.append(_row(day, metric))

    upserted = await metric_repo.upsert_metrics(db, rows)
    return BackfillStats(
        day_lo=day_lo,
        day_hi=day_hi,
        quote_days=len(days),
        rows_upserted=upserted,
        days_written=len(days),
        no_quote_row_days=no_quote_row_days,
        no_ma20_window_days=no_ma20_window_days,
        per_metric_null=per_metric_null,
    )


async def backfill_sentiment_metrics(
    db: AsyncSession,
    *,
    day_hi: date | None = None,
    lookback_days: int = 400,
    warmup_days: int = 60,
) -> BackfillStats:
    """回补 `[day_hi - lookback_days, day_hi]` 的 8 个指标（日频序列）并提交。

    窗口默认 400 个日历日 ≥ 1 年（spec §S1.8 验收："6 个指标有 ≥1 年日频序列"）。
    `day_hi=None` → 取 `daily_quotes` 的 `max(trade_date)`（**不是** `date.today()`：
    今天是休市日时按今天回补只会得到一片空）。

    **B 类回补是幂等重算**（ADR 0014）：只用当日点内事实，任何一天都可以被整天
    覆盖重写 —— 唯一键 upsert 保证重跑不新增行。本函数是"一次性历史回补"的入口
    （自带 `commit`）；增量场景请直接用 :func:`upsert_metric_range` 并自行管事务。
    """
    if day_hi is None:
        day_hi = (await db.execute(text("SELECT max(trade_date) FROM daily_quotes"))).scalar_one()
    day_lo = day_hi - timedelta(days=lookback_days)
    stats = await upsert_metric_range(db, day_lo, day_hi, warmup_days=warmup_days)
    await db.commit()
    return stats


@dataclass(frozen=True)
class MetricCoverage:
    """一个指标的覆盖率对账（缺数可见性的对外形状）。"""

    metric_key: str
    period_from: date | None
    period_to: date | None
    rows: int
    covered_days: int
    null_days: int
    missing_days: list[date]
    missing_reasons: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric_key": self.metric_key,
            "period_from": self.period_from.isoformat() if self.period_from else None,
            "period_to": self.period_to.isoformat() if self.period_to else None,
            "rows": self.rows,
            "covered_days": self.covered_days,
            "null_days": self.null_days,
            "missing_days": [d.isoformat() for d in self.missing_days],
            "missing_reasons": self.missing_reasons,
        }


async def metric_coverage(
    db: AsyncSession,
    *,
    day_lo: date | None = None,
    day_hi: date | None = None,
) -> list[MetricCoverage]:
    """逐指标的覆盖率：`covered_days`（有值）/ `null_days`（有行但值为 NULL）/
    `missing_days`（该交易日**无行**）/ `missing_reasons`（NULL 的原因分布）。

    "该交易日"的分母 = `daily_quotes` 实际持有的交易日（与落库天同一定义），
    故 `missing_days` 是"行情有、指标没有"的真缺口，**不含**尚未采集行情的日子。
    """
    stmt = (
        select(
            IndustryMetric.metric_key,
            IndustryMetric.period,
            IndustryMetric.value,
            IndustryMetric.extra["missing_reason"].astext.label("reason"),
        )
        .where(IndustryMetric.industry_key == "market", IndustryMetric.stock_id == 0)
        .order_by(IndustryMetric.metric_key, IndustryMetric.period)
    )
    if day_lo is not None:
        stmt = stmt.where(IndustryMetric.period >= day_lo)
    if day_hi is not None:
        stmt = stmt.where(IndustryMetric.period <= day_hi)
    rows = (await db.execute(stmt)).all()
    quote_day_list = await _quote_days(
        db,
        day_lo or date(1900, 1, 1),
        day_hi or date(2999, 12, 31),
    )
    quote_days = set(quote_day_list)

    by_metric: dict[str, list[Any]] = {k: [] for k in ALL_METRIC_KEYS}
    for row in rows:
        by_metric.setdefault(row.metric_key, []).append(row)

    out: list[MetricCoverage] = []
    for key in ALL_METRIC_KEYS:
        metric_rows = by_metric.get(key, [])
        periods = {r.period for r in metric_rows}
        covered = sum(1 for r in metric_rows if r.value is not None)
        nulls = len(metric_rows) - covered
        reasons: dict[str, int] = {}
        for r in metric_rows:
            if r.value is None and r.reason:
                reasons[r.reason] = reasons.get(r.reason, 0) + 1
        out.append(
            MetricCoverage(
                metric_key=key,
                period_from=min(periods) if periods else None,
                period_to=max(periods) if periods else None,
                rows=len(metric_rows),
                covered_days=covered,
                null_days=nulls,
                missing_days=sorted(quote_days - periods),
                missing_reasons=dict(sorted(reasons.items())),
            )
        )
    return out
