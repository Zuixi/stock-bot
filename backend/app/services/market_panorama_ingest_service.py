"""A股全景三张日表的写入口（回补脚本与日终 job 共用同一套 SQL）。

为什么要 service 而不是把 SQL 留在 `scripts/`：日终 job 与回补脚本必须用**同一口径**；
两份 SQL 迟早会漂移（尤其在桶边界这种"看起来只是字符串"的地方）。这里只做一件事：
给定日期范围，算广度/11 桶/行业并 upsert。

- 广度口径与 `market_service._DISTRIBUTION_RANGES` 逐字一致：上涨 `>=1%`、平/微涨
  `0~1%`、下跌 `<0`；
- 行业归属用**当前**申万快照（C 类），写 `attribution='current_sw_snapshot'`；
- 缺限价时 `limit_up_count/limit_down_count` 落 NULL（缺失 ≠ 0）。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

BUCKETS: tuple[str, ...] = (
    "跌停",
    ">-7%",
    "-5~-7%",
    "-3~-5%",
    "-1~-3%",
    "0~-1%",
    "0~1%",
    "1~3%",
    "3~5%",
    ">5%",
    "涨停",
)

_BUCKET_PREDICATES: dict[str, str] = {
    "跌停": "q.pct_chg <= -9.5",
    ">-7%": "q.pct_chg > -9.5 AND q.pct_chg < -7",
    "-5~-7%": "q.pct_chg >= -7 AND q.pct_chg < -5",
    "-3~-5%": "q.pct_chg >= -5 AND q.pct_chg < -3",
    "-1~-3%": "q.pct_chg >= -3 AND q.pct_chg < -1",
    "0~-1%": "q.pct_chg >= -1 AND q.pct_chg < 0",
    "0~1%": "q.pct_chg >= 0 AND q.pct_chg < 1",
    "1~3%": "q.pct_chg >= 1 AND q.pct_chg < 3",
    "3~5%": "q.pct_chg >= 3 AND q.pct_chg < 5",
    ">5%": "q.pct_chg >= 5 AND q.pct_chg < 9.5",
    "涨停": "q.pct_chg >= 9.5",
}

_BREADTH_SQL = text(
    f"""
    SELECT q.trade_date,
           count(*) FILTER (WHERE q.pct_chg >= 1) AS up_count,
           count(*) FILTER (WHERE q.pct_chg < 0) AS down_count,
           count(*) FILTER (WHERE q.pct_chg >= 0 AND q.pct_chg < 1) AS flat_count,
           CASE WHEN count(l.stock_id) = 0 THEN NULL
                ELSE count(*) FILTER (
                    WHERE l.up_limit IS NOT NULL AND q.close >= l.up_limit - 0.005
                ) END AS limit_up_count,
           CASE WHEN count(l.stock_id) = 0 THEN NULL
                ELSE count(*) FILTER (
                    WHERE l.down_limit IS NOT NULL AND q.close <= l.down_limit + 0.005
                ) END AS limit_down_count,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY q.pct_chg) AS median_pct,
           avg(q.pct_chg) AS mean_pct,
           sum(q.amount) AS amount,
           {
        ", ".join(
            f"count(*) FILTER (WHERE {_BUCKET_PREDICATES[b]}) AS b{i}"
            for i, b in enumerate(BUCKETS)
        )
    }
    FROM daily_quotes q
    LEFT JOIN stock_price_limits l
           ON l.trade_date = q.trade_date AND l.stock_id = q.stock_id
    WHERE q.trade_date BETWEEN :start AND :end
      AND q.pct_chg IS NOT NULL
    GROUP BY q.trade_date
    ORDER BY q.trade_date
    """
)

_INDUSTRY_SQL = text(
    """
    WITH stock_l1 AS (
        SELECT m.symbol,
               min(c1.industry_code) AS code,
               min(c1.industry_name) AS name
        FROM sw_industry_members m
        JOIN sw_industry_classes c3
          ON c3.industry_code = m.industry_code AND c3.level = 3
        JOIN sw_industry_classes c2
          ON c2.industry_code = c3.parent_code AND c2.level = 2
        JOIN sw_industry_classes c1
          ON c1.industry_code = c2.parent_code AND c1.level = 1
        GROUP BY m.symbol
    )
    SELECT q.trade_date,
           sl.code AS industry_code,
           sl.name AS industry_name,
           avg(q.pct_chg) AS pct_chg,
           count(*) FILTER (WHERE q.pct_chg >= 1) AS up_count,
           count(*) FILTER (WHERE q.pct_chg < 0) AS down_count,
           count(*) FILTER (WHERE q.pct_chg >= 0 AND q.pct_chg < 1) AS flat_count,
           CASE WHEN count(l.stock_id) = 0 THEN NULL
                ELSE count(*) FILTER (
                    WHERE l.up_limit IS NOT NULL AND q.close >= l.up_limit - 0.005
                ) END AS limit_up_count,
           sum(q.amount) AS amount,
           CASE WHEN count(*) = 0 THEN NULL
                ELSE count(*) FILTER (WHERE q.pct_chg >= 1)::float / count(*)
           END AS breadth
    FROM daily_quotes q
    JOIN stocks s ON s.id = q.stock_id
    JOIN stock_l1 sl ON sl.symbol = s.symbol
    LEFT JOIN stock_price_limits l
           ON l.trade_date = q.trade_date AND l.stock_id = q.stock_id
    WHERE q.trade_date BETWEEN :start AND :end
      AND q.pct_chg IS NOT NULL
    GROUP BY q.trade_date, sl.code, sl.name
    ORDER BY q.trade_date, sl.code
    """
)

_UPSERT_BREADTH = text(
    """
    INSERT INTO market_breadth_daily
        (trade_date, up_count, down_count, flat_count, limit_up_count, limit_down_count,
         median_pct, mean_pct, amount, source)
    VALUES
        (:trade_date, :up_count, :down_count, :flat_count, :limit_up_count, :limit_down_count,
         :median_pct, :mean_pct, :amount, :source)
    ON CONFLICT ON CONSTRAINT uq_market_breadth_daily_date DO UPDATE SET
        up_count = EXCLUDED.up_count,
        down_count = EXCLUDED.down_count,
        flat_count = EXCLUDED.flat_count,
        limit_up_count = EXCLUDED.limit_up_count,
        limit_down_count = EXCLUDED.limit_down_count,
        median_pct = EXCLUDED.median_pct,
        mean_pct = EXCLUDED.mean_pct,
        amount = EXCLUDED.amount,
        source = EXCLUDED.source,
        observed_at = now()
    """
)

_UPSERT_BUCKET = text(
    """
    INSERT INTO market_breadth_bucket_daily (trade_date, bucket, count)
    VALUES (:trade_date, :bucket, :count)
    ON CONFLICT ON CONSTRAINT uq_market_breadth_bucket_daily_date_bucket DO UPDATE SET
        count = EXCLUDED.count
    """
)

_UPSERT_INDUSTRY = text(
    """
    INSERT INTO market_industry_daily
        (trade_date, industry_code, industry_name, pct_chg, up_count, down_count, flat_count,
         limit_up_count, amount, breadth, attribution, source)
    VALUES
        (:trade_date, :industry_code, :industry_name, :pct_chg, :up_count, :down_count,
         :flat_count, :limit_up_count, :amount, :breadth, 'current_sw_snapshot', :source)
    ON CONFLICT ON CONSTRAINT uq_market_industry_daily_date_code DO UPDATE SET
        industry_name = EXCLUDED.industry_name,
        pct_chg = EXCLUDED.pct_chg,
        up_count = EXCLUDED.up_count,
        down_count = EXCLUDED.down_count,
        flat_count = EXCLUDED.flat_count,
        limit_up_count = EXCLUDED.limit_up_count,
        amount = EXCLUDED.amount,
        breadth = EXCLUDED.breadth,
        attribution = 'current_sw_snapshot',
        source = EXCLUDED.source,
        observed_at = now()
    """
)


async def compute_breadth_rows(db: AsyncSession, start: date, end: date) -> list[Any]:
    return list((await db.execute(_BREADTH_SQL, {"start": start, "end": end})).mappings().all())


async def compute_industry_rows(db: AsyncSession, start: date, end: date) -> list[Any]:
    return list((await db.execute(_INDUSTRY_SQL, {"start": start, "end": end})).mappings().all())


async def upsert_rows(
    db: AsyncSession,
    breadth_rows: list[Any],
    industry_rows: list[Any],
    *,
    source: str,
) -> None:
    for row in breadth_rows:
        data = dict(row)
        await db.execute(
            _UPSERT_BREADTH,
            {
                **{
                    k: data.get(k)
                    for k in (
                        "trade_date",
                        "up_count",
                        "down_count",
                        "flat_count",
                        "limit_up_count",
                        "limit_down_count",
                        "median_pct",
                        "mean_pct",
                        "amount",
                    )
                },
                "source": source,
            },
        )
        for i, bucket in enumerate(BUCKETS):
            await db.execute(
                _UPSERT_BUCKET,
                {
                    "trade_date": data["trade_date"],
                    "bucket": bucket,
                    "count": int(data[f"b{i}"] or 0),
                },
            )
    for row in industry_rows:
        await db.execute(_UPSERT_INDUSTRY, {**dict(row), "source": source})


async def refresh_day(
    db: AsyncSession, day: date, *, source: str = "local_calc"
) -> tuple[int, int]:
    """算并 upsert 一天（日终 job 用）；返回 (广度行数, 行业行数)。"""
    breadth_rows = await compute_breadth_rows(db, day, day)
    industry_rows = await compute_industry_rows(db, day, day)
    await upsert_rows(db, breadth_rows, industry_rows, source=source)
    return len(breadth_rows), len(industry_rows)
