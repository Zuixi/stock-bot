#!/usr/bin/env python3
"""回补 spec §S1.7（资金流四件套）到 `industry_metrics`（`industry_key='market'`）。

这是**运维入口**，不是读路：读路是 S1.2 的 `GET /api/v1/metrics/{latest,series}`。
本脚本**不新建表、不新建读路**。

用法（cwd = `backend/`，走 `DATABASE_URL`）::

    # 默认：回补最近 1400 个日历日（篮子份额实测起点 2023-01-03 ≈ 3.8 年 ≥ spec 的 1 年）
    DATABASE_URL=postgresql+asyncpg://... .venv/bin/python scripts/backfill_capital_flows.py

    # 指定区间
    .venv/bin/python scripts/backfill_capital_flows.py --from 2023-01-01 --to 2026-10-12

    # 只打印覆盖率对账（不写库、不联网）
    .venv/bin/python scripts/backfill_capital_flows.py --coverage-only

退出码：0 = 成功且 PIT 审计通过；1 = 参数非法 / **整个区间一行都没落库** /
覆盖率对账发现"有行但值为 NULL"却**没有原因码** / 本模块的 key 有行缺
`published_at` 或 `caliber_version`（Ruling 59 的 PIT 审计 —— 这两列可空，
漏带会静默落 NULL）。

**落库天（Ruling 59）**：两个指标各自锚在**自身输入**的可得日上 ——
`etf_share_change` 锚在 `fund_share` 有值的日、`southbound_net_buy` 锚在
`moneyflow_hsgt` 有行的日（= 港股通交易日，是 A 股交易日的**子集**）。
**不**拿 `daily_quotes` 当日历底座：港股休市日结构性没有沪深港通数据，
拿行情日历当分母会把它们算成"缺数"。

**幂等**：唯一键 + `ON CONFLICT DO UPDATE`，重跑同一区间新增行数 = 0。

**不落的 key**（本脚本**不会**给它们写任何行，这是 spec 要求的）：
`industrial_capital_net_change`（待接 S3.2）· `northbound_holding_quarterly`
（陆股通持仓（季度），只登记口径与滞后说明）。
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

#: 本 worktree 的 `backend/.env` 是 git-ignored（常不存在）；与既有 spike 同规矩，
#: 回落到主 checkout 的 `backend/.env` 取 token，**不打印**其值。
_TOKEN_FALLBACKS = (
    Path(__file__).resolve().parents[1] / ".env",
    Path("/Users/wqz/Developer/stock-bot/backend/.env"),
)


def _ensure_tushare_token() -> None:
    """`TUSHARE_TOKEN` 未在环境里时，从候选 `.env` 读进环境变量（`settings` 已在
    import 期读过 `.env`，故必须在 import `app.*` **之前**调用）。"""
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
from app.services import capital_flows_service as svc  # noqa: E402
from app.services import market_activity_service as activity  # noqa: E402


def _parse_day(raw: str | None) -> date | None:
    return date.fromisoformat(raw) if raw else None


async def _run(args: argparse.Namespace) -> int:
    coverage_lo = args.coverage_from
    coverage_hi = args.coverage_to
    async with async_session_factory() as db:
        if not args.coverage_only:
            day_hi = args.day_to or await svc.latest_etf_share_day()
            day_lo = args.day_from or (day_hi - timedelta(days=args.lookback))
            if day_lo > day_hi:
                print("✘ --from 晚于 --to", file=sys.stderr)
                return 1
            stats = await svc.upsert_capital_flows_range(db, day_lo, day_hi)
            await db.commit()
            print(json.dumps(stats.as_dict(), ensure_ascii=False, indent=2))
            if stats.rows_upserted == 0:
                print("✘ 区间内一行都没落库 —— 没有可回补的底座", file=sys.stderr)
                return 1
            coverage_lo, coverage_hi = day_lo, day_hi

        coverage = await svc.capital_flows_coverage(db, day_lo=coverage_lo, day_hi=coverage_hi)
        print(
            "\n=== 覆盖率对账（covered = 有值 / null = 有行但值为 NULL / pending = 无行且待接）==="
        )
        header = (
            f"{'metric_key':<32}{'from':<12}{'to':<12}"
            f"{'rows':>6}{'covered':>9}{'null':>7}  reasons / pending"
        )
        print(header)
        print("-" * len(header))
        unexplained: list[str] = []
        for cov in coverage:
            note = json.dumps(cov.missing_reasons, ensure_ascii=False)
            if cov.pending_reason:
                note = f"PENDING: {cov.pending_reason}"
            print(
                f"{cov.metric_key:<32}"
                f"{cov.period_from.isoformat() if cov.period_from else '—':<12}"
                f"{cov.period_to.isoformat() if cov.period_to else '—':<12}"
                f"{cov.rows:>6}{cov.covered_days:>9}{cov.null_days:>7}  {note}"
            )
            if cov.null_days and not cov.missing_reasons:
                # "有行但 NULL 且没有原因码" = 前端的 `value=null` 无从解释（Ruling 61 要防的形态）
                unexplained.append(cov.metric_key)
        if unexplained:
            print(
                "\n✘ 覆盖率对账不通过：有 NULL 行却没有 `extra.missing_reason` —— "
                + "; ".join(unexplained),
                file=sys.stderr,
            )
            return 1

        # ── PIT / 口径落库审计（本模块的 key 是**硬判据**）─────────────────
        window_lo = coverage_lo if coverage_lo is not None else date(1900, 1, 1)
        window_hi = coverage_hi if coverage_hi is not None else date(2999, 12, 31)
        audit = await activity.pit_audit(db, window_lo, window_hi)
        ours = {k for k in svc.ALL_METRIC_KEYS}
        mine = [r for r in audit if r.metric_key in ours]
        if not mine:
            print("\n✘ PIT 审计：窗口内本模块一行都没有 —— 回补没有落库", file=sys.stderr)
            return 1
        print(
            f"\n=== PIT / 口径审计（本模块的 key；窗口 {window_lo} … {window_hi}）==="
            "  count(*) / count(published_at) / count(caliber_version)"
        )
        ahead = (
            f"{'metric_key':<32}{'rows':>7}{'published_at':>15}{'caliber_version':>17}  calibers"
        )
        print(ahead)
        print("-" * len(ahead))
        bad: list[str] = []
        for row in mine:
            print(
                f"{row.metric_key:<32}{row.rows:>7}{row.with_published_at:>15}"
                f"{row.with_caliber:>17}  {','.join(row.calibers) or '—'}"
            )
            if not row.ok:
                bad.append(f"{row.metric_key}(pub {row.with_published_at}/{row.rows})")
        if bad:
            print(
                "\n✘ PIT 审计不通过：本模块有行缺 published_at 或 caliber_version —— "
                + "; ".join(bad),
                file=sys.stderr,
            )
            return 1
        # 整个 `market` scope 的完整性：只**告警**（别的批次的窗口外欠账不归本脚本管，
        # 但必须看得见）。
        whole_missing = [
            f"{r.metric_key}(pub {r.with_published_at}/{r.rows})" for r in audit if not r.ok
        ]
        if whole_missing:
            print(
                "\n⚠ PIT 告警：`industry_key='market'` 窗口内**其它批次**仍有缺 PIT/口径的行 —— "
                + "; ".join(whole_missing),
                file=sys.stderr,
            )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--from", dest="day_from", type=_parse_day, default=None, help="起始交易日 YYYY-MM-DD"
    )
    parser.add_argument(
        "--to", dest="day_to", type=_parse_day, default=None, help="结束交易日 YYYY-MM-DD"
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=1400,
        help="未给 --from 时回看的日历日数（默认 1400 ≈ 3.8 年 ≥ spec §S1.7 的 1 年）",
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
        return asyncio.run(_run(args))
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":
    sys.exit(main())
