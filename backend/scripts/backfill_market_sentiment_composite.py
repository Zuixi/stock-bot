#!/usr/bin/env python3
"""回补 spec §S1.13「市场情绪综合指数」（`market_sentiment_composite`）到 `industry_metrics`.

这是**运维入口**，不是读路：读路是 S1.2 的 `GET /api/v1/metrics/{latest,series}`
（`industry_key='market'`）。本脚本**不新建表、不新建读路、不新增迁移、不联网** ——
综合指数只消费库里既有的成分事实（11 条日频腿）。

用法（cwd = `backend/`，走 `DATABASE_URL`）::

    # 默认：从成分的最早日全量重算（分位是滚动窗口，只写区间内的行、用全量历史）
    .venv/bin/python scripts/backfill_market_sentiment_composite.py

    # 只补最近 400 个日历日
    .venv/bin/python scripts/backfill_market_sentiment_composite.py --lookback 400

    # 指定区间
    .venv/bin/python scripts/backfill_market_sentiment_composite.py \
        --from 2015-01-01 --to 2016-12-31

    # 只打印覆盖率/PIT/参与区间对账（不写库）
    .venv/bin/python scripts/backfill_market_sentiment_composite.py --coverage-only

退出码：0 = 成功；1 = 区间内一行都没落库 / PIT 审计不过（有行缺 `published_at` 或
`caliber_version`）/ **存在 `published_at` 早于当天最晚成分的行**（前视）。

**方法（v1）**：成分各自滚动分位（窗口 252 行、最小样本 126，反向腿先取相反数）
→ 按 registry 的公开权重表加权求和 × 100；当日缺分位的成分**剔除并对剩余权重重新
归一化**（`drop_and_renormalise`），参与集与生效权重逐日落 `extra`。
权重/窗口/版本号的唯一事实源 = `industry_registry.COMPOSITE_*`，公开转录在
`docs/design/metric-dictionary.md` §4.2。

**幂等**：唯一键 `(industry_key, stock_id, metric_key, source, freq, period)` +
`ON CONFLICT DO UPDATE`，同一区间重跑新增行数 = 0（ADR 0014 的 B 类可回补）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.database import async_session_factory  # noqa: E402
from app.services import market_sentiment_composite_service as svc  # noqa: E402
from app.services.industry_registry import (  # noqa: E402
    COMPOSITE_CALIBER_VERSION,
    COMPOSITE_CONSTITUENTS,
    COMPOSITE_METRIC_KEY,
)

CONSTITUENT_KEYS = tuple(c.metric_key for c in COMPOSITE_CONSTITUENTS)


def _parse_day(raw: str | None) -> date | None:
    return date.fromisoformat(raw) if raw else None


async def _pit_audit(db: AsyncSession, *, day_lo: date | None, day_hi: date | None) -> int:
    """PIT 审计（Ruling 59）：count(*) / count(published_at) / count(caliber_version) +
    `published_at::date` 与 `period` 的关系 + **"不早于当天最晚成分"** 的机械核对。

    最后一条是前视守卫：合成值的 `published_at` 必须 == 当天**全部**成分行
    `published_at` 的最大值 —— 早于它就说明用了当天还没定型的成分（前视）。
    """
    where = ["industry_key = 'market'", "stock_id = 0", f"metric_key = '{COMPOSITE_METRIC_KEY}'"]
    if day_lo is not None:
        where.append("period >= :lo")
    if day_hi is not None:
        where.append("period <= :hi")
    params: dict[str, Any] = {"lo": day_lo, "hi": day_hi}
    clause = " AND ".join(where)
    keys_sql = ", ".join(f"'{k}'" for k in CONSTITUENT_KEYS)
    join_where = [
        "industry_key = 'market'",
        "stock_id = 0",
        f"metric_key IN ({keys_sql})",
    ]
    if day_lo is not None:
        join_where.append("period >= :lo")
    if day_hi is not None:
        join_where.append("period <= :hi")
    stmt = text(
        f"""
        WITH c AS (
            SELECT period, published_at, caliber_version FROM industry_metrics WHERE {clause}
        ), k AS (
            SELECT period, max(published_at) AS latest_pub, count(*) AS n_rows
            FROM industry_metrics WHERE {" AND ".join(join_where)} GROUP BY period
        )
        SELECT
            (SELECT count(*) FROM industry_metrics WHERE {clause}) AS rows,
            (SELECT count(published_at) FROM industry_metrics WHERE {clause}) AS with_pub,
            (SELECT count(caliber_version) FROM industry_metrics WHERE {clause}) AS with_cv,
            (SELECT array_agg(DISTINCT caliber_version) FROM industry_metrics
              WHERE {clause}) AS calibers,
            (SELECT count(*) FROM c WHERE published_at::date = period) AS pub_eq_period,
            (SELECT count(*) FROM c WHERE published_at::date < period) AS pub_before_period,
            (SELECT count(*) FROM c WHERE published_at::date > period) AS pub_after_period,
            (SELECT count(*) FROM c JOIN k ON c.period = k.period) AS joined,
            (SELECT count(*) FROM c JOIN k ON c.period = k.period
              WHERE c.published_at = k.latest_pub) AS pit_exact,
            (SELECT count(*) FROM c JOIN k ON c.period = k.period
              WHERE c.published_at < k.latest_pub) AS pit_too_early,
            (SELECT count(*) FROM c JOIN k ON c.period = k.period
              WHERE c.published_at > k.latest_pub) AS pit_too_late
        """
    )
    row = (await db.execute(stmt, params)).one()
    print("\n=== PIT / 口径审计（合成指数）===")
    print(f"metric_key = {COMPOSITE_METRIC_KEY}")
    print(f"caliber_version 期望             = {COMPOSITE_CALIBER_VERSION}")
    print(f"  count(*)                        = {row.rows}")
    print(f"  count(published_at)             = {row.with_pub}")
    print(f"  count(caliber_version)          = {row.with_cv}")
    print(f"  array_agg(DISTINCT caliber)     = {row.calibers}")
    print(f"  published_at::date =  period    = {row.pub_eq_period}")
    print(f"  published_at::date <  period    = {row.pub_before_period}  （前视，必须为 0）")
    print(f"  published_at::date >  period    = {row.pub_after_period}")
    print("=== 前视守卫：published_at == max(当天全部成分行的 published_at) ===")
    print(f"  可比天数(合成日 ∩ 成分日)        = {row.joined}")
    print(f"  published_at == 当天最晚成分     = {row.pit_exact}")
    print(f"  published_at <  当天最晚成分     = {row.pit_too_early}  （前视，必须为 0）")
    print(f"  published_at >  当天最晚成分     = {row.pit_too_late}")
    bad = (
        row.with_pub != row.rows
        or row.with_cv != row.rows
        or row.pub_before_period
        or row.pit_too_early
        or row.pit_too_late
    )
    if bad:
        print(
            "\n✘ PIT 审计不通过（缺 PIT/口径，或 published_at 与成分可得性不一致）",
            file=sys.stderr,
        )
        return 1
    print("\n✔ PIT 审计通过：无缺列、无前视，published_at 恰为当天最晚成分的可得时刻")
    return 0


async def _run(args: argparse.Namespace) -> int:
    async with async_session_factory() as db:
        day_lo: date | None = args.day_from
        day_hi: date | None = args.day_to
        if not args.coverage_only:
            if day_lo is None and args.lookback is not None:
                latest = (
                    await db.execute(
                        text(
                            "SELECT max(period) FROM industry_metrics WHERE industry_key='market' "
                            "AND stock_id=0 AND metric_key = ANY(:keys)"
                        ),
                        {"keys": list(CONSTITUENT_KEYS)},
                    )
                ).scalar_one()
                if latest is not None:
                    day_lo = cast("date", latest) - timedelta(days=args.lookback)
            stats = await svc.upsert_composite_range(db, day_lo=day_lo, day_hi=day_hi)
            await db.commit()
            print(json.dumps(stats.as_dict(), ensure_ascii=False, indent=2))
        coverage = await svc.composite_coverage(db, day_lo=day_lo, day_hi=day_hi)
        print("\n=== 覆盖率对账（分母 = 合成指数自己的落库行）===")
        print(json.dumps(coverage.as_dict(), ensure_ascii=False, indent=2))
        if coverage.rows == 0:
            print("✘ 合成指数一行都没有 —— 回补没有落库", file=sys.stderr)
            return 1
        return await _pit_audit(db, day_lo=day_lo, day_hi=day_hi)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="day_from", type=_parse_day, default=None)
    parser.add_argument("--to", dest="day_to", type=_parse_day, default=None)
    parser.add_argument(
        "--lookback",
        type=int,
        default=None,
        help="未给 --from 时回看的日历日数（默认 None = 从成分最早日起全量重算）",
    )
    parser.add_argument("--coverage-only", action="store_true", help="只对账，不写库")
    args = parser.parse_args()
    if args.lookback is not None and args.lookback <= 0:
        print("✘ --lookback 须 > 0", file=sys.stderr)
        return 1
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":
    sys.exit(main())
