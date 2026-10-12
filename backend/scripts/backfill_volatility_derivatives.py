#!/usr/bin/env python3
"""回补 spec §S1.10（波动率与衍生品）的 11 个指标到 `industry_metrics`。

这是**运维入口**，不是读路：读路是 S1.2 的 `GET /api/v1/metrics/{latest,series}`
（`industry_key='market'`）。本脚本**不新建表、不新建读路**。

用法（cwd = `backend/`，走 `DATABASE_URL`）::

    # 默认：回补最近 6100 个日历日（≈16.7 年，宽于两个源的全部可得起点）
    DATABASE_URL=postgresql+asyncpg://... .venv/bin/python \
        scripts/backfill_volatility_derivatives.py

    # 指定区间
    .venv/bin/python scripts/backfill_volatility_derivatives.py --from 2021-01-01 --to 2026-10-12

    # 只打印覆盖率对账（不写库、不联网）
    .venv/bin/python scripts/backfill_volatility_derivatives.py --coverage-only

退出码：0 = 成功且 PIT 审计通过；1 = 参数非法 / **一行都没落库** /
覆盖率对账发现"连行都没有"的交易日 / **本模块的 key 有行缺 `published_at` 或
`caliber_version`**（Ruling 59 的 PIT 审计 —— 这两列可空，漏带会静默落 NULL）。

**落库天（Ruling 59）**：交易日全集 = TuShare `trade_cal`(SSE)；5 条 QVIX 从**该 ETF
期权上市日**起逐交易日落行、4 个基差与 2 条贴水率从**该品种期货首个交易日**起落行。
落库天**不依赖** `daily_quotes`（它只有 2023-05-09 起的 829 天，会把 QVIX 的 11.6 年
截成 3.4 年 —— 那正是 Ruling 59 要修的形态）。默认窗口刻意宽于源起点：窗口宽不会多落行
（锚在各源自己的起点上），窗口太窄才会截断序列。

**缺数可见**：上游缺的那一天落 `value = NULL` + `extra.missing_reason`
（`source_not_updated` / `futures_leg_missing` / `spot_index_missing`），**不落 0、
不取最近值、不插值**。实测 QVIX 的上游静态 CSV 比期货腿**晚 3 个交易日**（2026-10-13 时
仍停在 2026-09-30），这 3 天会以 NULL 行出现在库里 —— 这是**故意**的（源侧滞后必须看得见）。

**幂等**：唯一键 `(industry_key, stock_id, metric_key, source, freq, period)` +
`ON CONFLICT DO UPDATE`，同一区间重跑新增行数 = 0（ADR 0014 的 B 类可回补）。

**联网**：默认会真的调 AKShare（5 条 QVIX）与 TuShare（`trade_cal` / `fut_basic` /
`fut_daily` / `fut_mapping` / `index_daily`）。`--coverage-only` 不联网。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: 本 worktree 的 `backend/.env` 是 git-ignored（常不存在）；与既有 spike/回补脚本同规矩，
#: 回落到主 checkout 的 `backend/.env` 取 token，**不打印**其值。
_TOKEN_FALLBACKS = (
    Path(__file__).resolve().parents[1] / ".env",
    Path("/Users/wqz/Developer/stock-bot/backend/.env"),
)


def _ensure_tushare_token() -> None:
    """`TUSHARE_TOKEN` 未在环境里时，从候选 `.env` 读进环境变量（必须在 import `app.*`
    **之前**调用：`settings` 在 import 期读 `.env`）。"""
    if os.environ.get("TUSHARE_TOKEN"):
        return
    try:
        from dotenv import dotenv_values
    except ImportError:  # pragma: no cover
        return
    for path in _TOKEN_FALLBACKS:
        if path.exists():
            token = dotenv_values(path).get("TUSHARE_TOKEN") or ""
            if token:
                os.environ["TUSHARE_TOKEN"] = token
                print(f"[ENV ] TUSHARE_TOKEN loaded from {path} (value not printed)")
                return


_ensure_tushare_token()

from app.core.database import async_session_factory  # noqa: E402
from app.services import market_activity_service as market_svc  # noqa: E402
from app.services import volatility_derivatives_service as svc  # noqa: E402


def _parse_day(raw: str | None) -> date | None:
    return date.fromisoformat(raw) if raw else None


async def _run(args: argparse.Namespace) -> int:
    coverage_lo = args.coverage_from
    coverage_hi = args.coverage_to
    async with async_session_factory() as db:
        if not args.coverage_only:
            day_hi = args.day_to or await svc.latest_source_day()
            if day_hi is None:
                print("✘ 期货腿最近 30 天内没有交易日 —— 没有可回补的底座", file=sys.stderr)
                return 1
            day_lo = args.day_from or (day_hi - timedelta(days=args.lookback))
            if day_lo > day_hi:
                print(f"✘ --from {day_lo} 晚于 --to {day_hi}", file=sys.stderr)
                return 1
            stats = await svc.upsert_metric_range(db, day_lo, day_hi)
            await db.commit()
            print(json.dumps(stats.as_dict(), ensure_ascii=False, indent=2))
            if stats.rows_upserted == 0:
                print(
                    "✘ 区间内一行都没落库 —— 窗口与两个源的可得期不重叠"
                    "（QVIX CSV 自 2015-02-09；IF 自 2010-04-16 / IC·IH 自 2015-04-16 / "
                    "IM 自 2022-07-22）",
                    file=sys.stderr,
                )
                return 1
            if not stats.trade_days:
                print("✘ trade_cal 在该区间没有交易日（窗口写错了？）", file=sys.stderr)
                return 1
            coverage_lo, coverage_hi = day_lo, day_hi

        coverage = await svc.metric_coverage(db, day_lo=coverage_lo, day_hi=coverage_hi)
        print(
            "\n=== 覆盖率对账（covered = 有值 / null = 有行但值为 NULL / missing = 交易日无行）==="
        )
        header = (
            f"{'metric_key':<20}{'from':<12}{'to':<12}"
            f"{'rows':>6}{'covered':>9}{'null':>7}{'missing':>9}  reasons"
        )
        print(header)
        print("-" * len(header))
        for cov in coverage:
            print(
                f"{cov.metric_key:<20}"
                f"{cov.period_from.isoformat() if cov.period_from else '—':<12}"
                f"{cov.period_to.isoformat() if cov.period_to else '—':<12}"
                f"{cov.rows:>6}{cov.covered_days:>9}{cov.null_days:>7}"
                f"{len(cov.missing_days):>9}  "
                f"{json.dumps(cov.missing_reasons, ensure_ascii=False)}"
            )

        # 判据 1：每个 key 都要有行（某 key 全程无行 = 计算入口漏了它 / 上游全空）
        empty = [c.metric_key for c in coverage if c.rows == 0]
        if empty:
            print(f"\n✘ 以下指标一行都没有：{empty}", file=sys.stderr)
            return 1
        # 判据 2：**"连行都没有"的交易日**是本模块自己的缺陷信号（上游缺数会以 NULL 行
        # 出现并带 missing_reason，**不会**表现为 missing_days）。故 missing_days 非空
        # 即回补没跑完/写失败，必须响亮失败。
        holes = {c.metric_key: c.missing_days for c in coverage if c.missing_days}
        if holes:
            for key, days in holes.items():
                print(
                    f"✘ {key} 有 {len(days)} 个交易日**连行都没有**（前几个："
                    f"{[d.isoformat() for d in days[:5]]}）—— 回补没覆盖到这些日子",
                    file=sys.stderr,
                )
            return 1

        # ── PIT / 口径落库审计（Ruling 59：把"报告里的 PIT 100%"机械化）────────
        window_lo = coverage_lo if coverage_lo is not None else date(1900, 1, 1)
        window_hi = coverage_hi if coverage_hi is not None else date(2999, 12, 31)
        audit = [
            row
            for row in await market_svc.pit_audit(db, window_lo, window_hi)
            if row.metric_key in set(svc.INGEST_METRIC_KEYS)
        ]
        if not audit:
            print("\n✘ PIT 审计：窗口内本模块一行都没有 —— 回补没有落库", file=sys.stderr)
            return 1
        print(
            f"\n=== PIT / 口径审计（窗口 {window_lo} … {window_hi}）==="
            "  count(*) / count(published_at) / count(caliber_version)"
        )
        ahead = (
            f"{'metric_key':<22}{'rows':>7}{'published_at':>15}{'caliber_version':>17}  calibers"
        )
        print(ahead)
        print("-" * len(ahead))
        missing: list[str] = []
        for row in audit:
            print(
                f"{row.metric_key:<22}{row.rows:>7}{row.with_published_at:>15}"
                f"{row.with_caliber:>17}  {','.join(row.calibers) or '—'}"
            )
            if not row.ok:
                missing.append(f"{row.metric_key}(pub {row.with_published_at}/{row.rows})")
        if missing:
            print(
                "\n✘ PIT 审计不通过：窗口内有缺 published_at 或 caliber_version 的行 —— "
                + "; ".join(missing),
                file=sys.stderr,
            )
            return 1
    return 0


async def main_async(args: argparse.Namespace) -> int:
    return await _run(args)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--from", dest="day_from", type=_parse_day, default=None, help="起始日 YYYY-MM-DD"
    )
    parser.add_argument("--to", dest="day_to", type=_parse_day, default=None, help="结束日")
    parser.add_argument(
        "--lookback",
        type=int,
        default=6100,
        help=(
            "未给 --from 时回看的日历日数（默认 6100 ≈ 16.7 年，**刻意宽于**两个源的"
            "可得起点：窗口宽不会多落行，窗口太窄会把 QVIX 的 11.6 年截断）"
        ),
    )
    parser.add_argument(
        "--coverage-only", action="store_true", help="只打印覆盖率对账，不写库不联网"
    )
    parser.add_argument("--coverage-from", type=_parse_day, default=None)
    parser.add_argument("--coverage-to", type=_parse_day, default=None)
    args = parser.parse_args()
    if args.lookback <= 0:
        print("✘ --lookback 须 > 0", file=sys.stderr)
        return 1
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":
    sys.exit(main())
