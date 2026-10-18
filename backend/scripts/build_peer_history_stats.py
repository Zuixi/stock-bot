#!/usr/bin/env python3
"""生成「同类历史（peer history）」的**全市场同桶**统计（spec §S9.6，产物随仓库入库）。

**可复跑、只读**：只对真实 PG 发 ``SELECT``，产出写进
``app/services/peer_history_stats.json``。它不是缓存：桶里的"历史上长什么样"必须可评审、
可 diff（与 ``build_triage_rule_stats.py`` 同一条纪律）。

口径（spec §S9.6.1/§S9.6.2，改动前先看 spec 那一节）：

1. **桶**（3 维，45 个 + ``unknown`` 单列，**不合并**）：
   情绪分位 ``<20 / 20–40 / 40–60 / 60–80 / ≥80``（``industry_metrics`` market scope 的
   综合指数分位，与快照同口径）；板块当日涨跌 ``≤−2% / (−2%,2%) / ≥2%``（所属申万一级
   成分股等权平均）；距 MA20 ``<−5% / [−5%,5%] / >5%``；
2. **样本**：窗口内**每一条「股票 × 交易日」**（不是中位数再取中位数——那样一个桶最多只有
   240 个样本，永远过不了 30 的闸）；
3. **收益**：``close/pre_close`` 按交易日**连乘**（``sum(ln(1+r))`` 再 ``exp``），
   **交易日历对齐**：T 与 T+N 都必须是市场交易日，停牌无 T+N 行情则不计（绝不 ``lead``）；
   winsorize 到 ±50%（未复权极端值会把"超额"放大数倍）；
4. **超额** = 个股 T+N 收益 − **当日全市场中位 T+N 收益**（中位对中位，与
   ``triage_rule_stats`` 同口径；用均值会把「中位 − 均值」的右偏残差变成每个桶都固定负偏，
   那就不是信号了）；`med` = 桶内全部样本的中位数（%）；
5. **窗口**：``daily_quotes`` 最大交易日往前 ``WINDOW_MONTHS`` 个自然月；
6. **PIT**：产物只到 ``as_of``（= 窗口末日），请求时 ``as_of >= 快照日`` → 拒绝使用
   （``stale_guard``，spec §S9.6.3）。

**没有任何用户数据**：这个产物只由行情/行业/情绪三张表算出，与"别人的日志"无关
（spec §S9.7.9 的隐私底线：不做跨用户聚合）。

数据不足（窗口内样本为 0）→ 该桶不写 / 整个 ``buckets`` 为空，读面按"样本不足"显示，
**不给数字**。

用法::

    cd backend && UV_CACHE_DIR=.uv-cache uv run python scripts/build_peer_history_stats.py
"""

from __future__ import annotations

import asyncio
import calendar
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory, engine
from app.services import industry_registry as registry
from app.services import peer_history_service as ph
from app.services.market_sentiment_composite_service import composite_percentile

#: 统计窗口（月）——契约里的 ``window_months``
WINDOW_MONTHS = 12

#: 窗口前多取的自然日：覆盖 MA20（20 个交易日）与 T+20 的前视需求
LOOKBACK_NATURAL_DAYS = 130

#: 产出路径（``peer_history_service`` 读的就是它）
OUT_PATH = Path(__file__).resolve().parents[1] / "app" / "services" / "peer_history_stats.json"

#: 收益/超额统一换成百分数
PCT = 100.0
#: 中位数保留位数（与 ``triage_rule_stats.json`` 的 3 位一致）
DIGITS = 3
#: L2 最少样本（spec §S9.6.2：不足 30 不给数字）——产物里也写一份，读面用同一个数
MIN_SAMPLES = ph.MIN_MARKET_SAMPLE

BASIS = (
    "收益 = close/pre_close 按交易日连乘（T 与 T+N 均为市场交易日，停牌无 T+N 行情则不计），"
    "winsorize ±50%；超额 = 个股 T+N 收益 − 当日全市场中位 T+N 收益"
    "（中位对中位，与 triage_rule_stats 同口径）；"
    "med 为桶内全部「股票 × 交易日」样本的中位数（%）；桶 = 情绪分位 × 板块当日涨跌 × 距 MA20"
    "（spec §S9.6.1，边界 20/40/60/80、±2、±5；unknown 单列不合并）；"
    "n 为 T+5 样本数，t20_n 为 T+20 样本数"
)


def _minus_months(day: date, months: int) -> date:
    """``day`` 往前 ``months`` 个自然月（月末截断到目标月最后一天）。"""
    total = day.year * 12 + (day.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last))


def _ret_expr(horizon: int) -> str:
    """T+horizon 的收益表达式：缺失（停牌无 T+N 行）必须是 NULL。

    **不能**写成 ``least(greatest(exp(...) - 1, -0.5), 0.5)``：PostgreSQL 的 ``greatest``
    会忽略 NULL，缺失收益会被 winsorize **变成 -50%**——"把没数据当成最差数据"。
    """
    return (
        f"CASE WHEN f.c{horizon} IS NULL THEN NULL"
        f" ELSE least(greatest(exp(f.c{horizon} - f.c) - 1, -0.5), 0.5) * {PCT} END AS r{horizon}"
    )


def _bucket_case(column: str, bounds: tuple[float, float], labels: tuple[str, str, str]) -> str:
    """按 ``bounds`` 两段边界把 ``column`` 落进三桶；NULL → ``unknown``（spec §S9.6.1）。

    边界**含哪一侧**是契约的一部分（spec 逐字）：
    情绪 ``<20 / 20–40 / 40–60 / 60–80 / ≥80`` 由 :func:`_sentiment_case` 单独处理；
    这里只处理三段式（板块 `≤−2 / (−2,2) / ≥2`、MA20 `<−5 / [−5,5] / >5`）。
    """
    low, high = bounds
    return (
        f"CASE WHEN {column} IS NULL THEN 'unknown'"
        f" WHEN {column} <= {low} THEN '{labels[0]}'"
        f" WHEN {column} < {high} THEN '{labels[1]}'"
        f" ELSE '{labels[2]}' END"
    )


def _sentiment_case(column: str) -> str:
    """情绪分位五桶（spec §S9.6.1：``<20`` / ``20–40`` / ``40–60`` / ``60–80`` / ``≥80``）。

    ``column`` 是 SQL 里的表达式（脚本内一律传 ``j.senti_pct``，与快照同口径）。
    """
    b = ph.SENTIMENT_BOUNDS
    labels = ph.SENTIMENT_LABELS
    return (
        f"CASE WHEN {column} IS NULL THEN 'unknown'"
        f" WHEN {column} < {b[0]} THEN '{labels[0]}'"
        f" WHEN {column} < {b[1]} THEN '{labels[1]}'"
        f" WHEN {column} < {b[2]} THEN '{labels[2]}'"
        f" WHEN {column} < {b[3]} THEN '{labels[3]}'"
        f" ELSE '{labels[4]}' END"
    )


def _sql() -> str:
    """一条 SQL 算出"每个桶的样本数 + 两个视界的中位超额"。

    为什么一条大 SQL：``cum`` / ``fwd`` / ``feat`` 三块是 45 个桶共用的同一份窗口特征，
    分成 45 条等于把 130 万行窗口函数算 45 遍。
    """
    return f"""
    WITH cal_idx AS (
        SELECT trade_date, row_number() OVER (ORDER BY trade_date) AS idx
        FROM (SELECT DISTINCT trade_date FROM daily_quotes WHERE trade_date <= :as_of) c
    ),
    base AS (
        SELECT q.stock_id, q.trade_date, q.close, q.pre_close
        FROM daily_quotes q
        WHERE q.trade_date >= :lookback_start
          AND q.trade_date <= :as_of
          AND q.close > 0
          AND q.pre_close > 0
    ),
    cum AS (
        SELECT stock_id, trade_date,
               sum(ln(close / pre_close)) OVER (
                   PARTITION BY stock_id ORDER BY trade_date
               ) AS c
        FROM base
    ),
    feat AS (
        SELECT b.stock_id, b.trade_date, b.close, b.pre_close,
               count(*) OVER w21 AS ma_n,
               avg(b.close) OVER w21 AS ma20
        FROM base b
        WINDOW w21 AS (
            PARTITION BY b.stock_id ORDER BY b.trade_date
            ROWS BETWEEN {ph.MA_DAYS - 1} PRECEDING AND CURRENT ROW
        )
    ),
    fwd AS (
        SELECT a.stock_id, a.trade_date, a.c,
               b5.c AS c5, b20.c AS c20
        FROM cum a
        JOIN cal_idx c0 ON c0.trade_date = a.trade_date
        LEFT JOIN cal_idx f5 ON f5.idx = c0.idx + 5
        LEFT JOIN cal_idx f20 ON f20.idx = c0.idx + 20
        LEFT JOIN cum b5 ON b5.stock_id = a.stock_id AND b5.trade_date = f5.trade_date
        LEFT JOIN cum b20 ON b20.stock_id = a.stock_id AND b20.trade_date = f20.trade_date
        WHERE a.trade_date >= :window_start
    ),
    ret AS (
        SELECT stock_id, trade_date, {_ret_expr(5)}, {_ret_expr(20)}
        FROM fwd f
    ),
    sector_l1 AS (
        SELECT m.symbol, c1.industry_code AS l1_code
        FROM sw_industry_members m
        JOIN sw_industry_classes c3
          ON c3.industry_code = m.industry_code AND c3.level = 3
        JOIN sw_industry_classes c2 ON c2.industry_code = c3.parent_code
        JOIN sw_industry_classes c1 ON c1.industry_code = c2.parent_code
    ),
    stock_l1 AS (
        SELECT s.id AS stock_id, l1.l1_code
        FROM stocks s
        JOIN sector_l1 l1 ON l1.symbol = s.symbol
    ),
    sector_day AS (
        SELECT f.trade_date, sl.l1_code,
               avg((f.close / f.pre_close - 1) * {PCT}) AS pct
        FROM feat f
        JOIN stock_l1 sl ON sl.stock_id = f.stock_id
        WHERE f.trade_date >= :window_start
        GROUP BY f.trade_date, sl.l1_code
    ),
    senti AS (
        SELECT * FROM unnest(
            CAST(:senti_dates AS date[]),
            CAST(:senti_pcts AS double precision[])
        ) AS t(trade_date, pct)
    ),
    x AS (
        SELECT r.stock_id, r.trade_date, r.r5, r.r20,
               (f.close / NULLIF(f.ma20, 0) - 1) * {PCT} AS ma20_dist,
               sd.pct AS sector_pct
        FROM ret r
        JOIN feat f ON f.stock_id = r.stock_id AND f.trade_date = r.trade_date
        LEFT JOIN stock_l1 sl ON sl.stock_id = r.stock_id
        LEFT JOIN sector_day sd
               ON sd.trade_date = r.trade_date AND sd.l1_code = sl.l1_code
    ),
    bench AS (
        -- 全市场**当日中位**收益：与 triage_rule_stats 同口径（中位对中位，不用均值——
        -- 用均值会让「中位 − 均值」的右偏残差把每个桶都压成固定负值，那就不是信号了）
        SELECT trade_date,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY r5)  AS m5,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY r20) AS m20
        FROM x
        GROUP BY trade_date
    ),
    joined AS (
        SELECT x.stock_id, x.trade_date, se.pct AS senti_pct,
               x.sector_pct, x.ma20_dist,
               CASE WHEN x.r5 IS NULL THEN NULL ELSE x.r5 - b.m5 END AS e5,
               CASE WHEN x.r20 IS NULL THEN NULL ELSE x.r20 - b.m20 END AS e20
        FROM x
        LEFT JOIN bench b ON b.trade_date = x.trade_date
        LEFT JOIN senti se ON se.trade_date = x.trade_date
    ),
    bucketed AS (
        SELECT {_sentiment_case("j.senti_pct")} AS s_bucket,
               {_bucket_case("j.sector_pct", ph.SECTOR_BOUNDS, ph.SECTOR_LABELS)} AS sec_bucket,
               {_bucket_case("j.ma20_dist", ph.MA20_BOUNDS, ph.MA20_LABELS)} AS ma_bucket,
               j.e5, j.e20
        FROM joined j
    )
    SELECT s_bucket, sec_bucket, ma_bucket,
           count(*) FILTER (WHERE e5 IS NOT NULL)  AS n,
           count(*) FILTER (WHERE e20 IS NOT NULL) AS t20_n,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY e5)  AS med5,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY e20) AS med20
    FROM bucketed
    GROUP BY s_bucket, sec_bucket, ma_bucket
    ORDER BY s_bucket, sec_bucket, ma_bucket
    """


async def _sentiment_series(session: AsyncSession, as_of: date) -> dict[date, float | None]:
    """每个交易日一条情绪分位（与 ``build_snapshot`` 同口径：252 窗口 / ≥126 样本）。"""
    rows = (
        await session.execute(
            text(
                """
                SELECT period, value
                  FROM industry_metrics
                 WHERE industry_key = 'market'
                   AND stock_id = 0
                   AND metric_key = :metric_key
                   AND period <= :as_of
                   AND value IS NOT NULL
                 ORDER BY period
                """
            ),
            {"metric_key": registry.COMPOSITE_METRIC_KEY, "as_of": as_of},
        )
    ).all()
    out: dict[date, float | None] = {}
    values: list[float] = []
    for period, value in rows:
        values.append(float(value))
        _, percentile = composite_percentile(
            values,
            registry.COMPOSITE_PERCENTILE_WINDOW,
            min_samples=registry.COMPOSITE_PERCENTILE_MIN_SAMPLES,
        )
        out[period] = percentile
    return out


async def _read_as_of(session: AsyncSession) -> date | None:
    row = (await session.execute(text("SELECT max(trade_date) FROM daily_quotes"))).first()
    return None if row is None else row[0]


async def _build(session: AsyncSession) -> dict[str, Any]:
    as_of = await _read_as_of(session)
    if as_of is None:
        return {
            "version": ph.PEER_HISTORY_VERSION,
            "as_of": None,
            "window_months": WINDOW_MONTHS,
            "min_sample": MIN_SAMPLES,
            "basis": BASIS,
            "buckets": {},
            "note": "daily_quotes 为空：无法生成同类历史统计",
        }
    window_start = _minus_months(as_of, WINDOW_MONTHS)
    lookback_start = window_start - timedelta(days=LOOKBACK_NATURAL_DAYS)
    senti = await _sentiment_series(session, as_of)
    senti_dates = sorted(senti)
    rows = (
        (
            await session.execute(
                text(_sql()),
                {
                    "as_of": as_of,
                    "window_start": window_start,
                    "lookback_start": lookback_start,
                    "senti_dates": senti_dates,
                    "senti_pcts": [senti[day] for day in senti_dates],
                },
            )
        )
        .mappings()
        .all()
    )
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = ph.bucket_key(
            {
                "sentiment": row["s_bucket"],
                "sector": row["sec_bucket"],
                "ma20": row["ma_bucket"],
            }
        )
        buckets[key] = {
            "n": int(row["n"] or 0),
            "t20_n": int(row["t20_n"] or 0),
            "t5_excess_med": (None if row["med5"] is None else round(float(row["med5"]), DIGITS)),
            "t20_excess_med": (
                None if row["med20"] is None else round(float(row["med20"]), DIGITS)
            ),
        }
    return {
        "version": ph.PEER_HISTORY_VERSION,
        "as_of": as_of.isoformat(),
        "window_months": WINDOW_MONTHS,
        "min_sample": MIN_SAMPLES,
        "basis": BASIS,
        "buckets": {key: buckets[key] for key in sorted(buckets)},
    }


def write_document(doc: dict[str, Any], path: Path = OUT_PATH) -> Path:
    """写产物（**确定性**：键有序、固定缩进、结尾一个换行；两次运行逐字节一致）。"""
    path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


async def _amain() -> int:
    try:
        async with async_session_factory() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            doc = await _build(session)
    finally:
        await engine.dispose()
    path = write_document(doc)
    buckets = doc["buckets"]
    total = sum(int(b["n"] or 0) for b in buckets.values())
    print(f"同类历史：as_of={doc['as_of']} 桶={len(buckets)} 样本={total} → {path}")
    for key, bucket in buckets.items():
        print(
            f"  {key:<40} n={bucket['n']:>7}"
            f" t5={bucket['t5_excess_med']} t20={bucket['t20_excess_med']}"
        )
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())
