#!/usr/bin/env python3
"""回补 spec §S1.12（情绪 · 投资者行为）到 `industry_metrics`（`industry_key='market'`）。

这是**运维入口**，不是读路：读路是 S1.2 的 `GET /api/v1/metrics/{latest,series}`。
本脚本**不新建表、不新建读路、不新增迁移**。

用法（cwd = `backend/`，走 `DATABASE_URL`；本仓惯例：回补指向 **dev 库 `stock_bot`**）::

    # 默认：回补最近 60 个月的月频腿 + 全量周度腿（上游实测起点 2023-01）
    .venv/bin/python scripts/backfill_investor_behavior.py

    # 指定月上界
    .venv/bin/python scripts/backfill_investor_behavior.py --to 2026-09-01

    # **离线复跑**（不触网）：吃探针 `--json-out` 的产物还原月频腿
    .venv/bin/python scripts/backfill_investor_behavior.py \\
        --offline-months /tmp/s112_fund_new_found.json

    # 只打印覆盖率对账（不写库、不联网）
    .venv/bin/python scripts/backfill_investor_behavior.py --coverage-only

退出码：0 = 成功且 PIT 审计通过；1 = 参数非法 / **一行都没落库** /
覆盖率对账发现"有行但值为 NULL"却**没有原因码** / 本模块的 key 有行缺
`published_at` 或 `caliber_version`（Ruling 59 的 PIT 审计 —— 这两列可空，
漏带会静默落 NULL）。

**落库周期（Ruling 59）**：两个指标各自锚在**自身输入**的可得周期上 ——
`new_fund_share` 锚在"有基金成立的那些月"、`public_fund_equity_position` 锚在
"乐咕乐股给出观测的那些日"（实测周度）。**不**拿 `daily_quotes` 当日历底座：
月/周频指标的分母不是交易日，拿行情日历当分母会把每个非交易日算成"缺数"。

**幂等**：唯一键 + `ON CONFLICT DO UPDATE`，重跑同一区间新增行数 = 0。

**不落的项**（这是 spec 要求的，不是漏做）：
- 新增散户开户数 —— Step-0 实机判**不可得**（最新月 2023-08、无权威第二源）⇒
  **不注册 key**、UI 无此项（本脚本也无从落它）。
- ETF 份额变化 —— **复用 §S1.7**（`etf_share_change` 已由 `backfill_capital_flows.py` 落）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import async_session_factory  # noqa: E402
from app.services import investor_behavior_service as svc  # noqa: E402
from app.services import market_activity_service as activity  # noqa: E402


def _parse_day(raw: str | None) -> date | None:
    return date.fromisoformat(raw) if raw else None


async def _run(args: argparse.Namespace) -> int:
    coverage_lo = args.coverage_from
    coverage_hi = args.coverage_to
    offline = svc.load_offline_months(args.offline_months) if args.offline_months else None

    async with async_session_factory() as db:
        if not args.coverage_only:
            stats = await svc.backfill_investor_behavior(
                db,
                month_hi=args.month_to,
                lookback_months=args.lookback_months,
                offline_months=offline,
            )
            print(json.dumps(stats.as_dict(), ensure_ascii=False, indent=2, default=str))
            if stats.rows_upserted == 0:
                print("✘ 区间内一行都没落库 —— 没有可回补的底座", file=sys.stderr)
                return 1
            coverage_lo, coverage_hi = stats.period_lo, stats.period_hi

        coverage = await svc.investor_behavior_coverage(
            db, period_lo=coverage_lo, period_hi=coverage_hi
        )
        print("\n=== 覆盖率对账（covered = 有值 / null = 有行但值为 NULL / pending = 无行）===")
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
                f"{cov.rows:>6}{cov.covered_periods:>9}{cov.null_periods:>7}  {note}"
            )
            if cov.null_periods and not cov.missing_reasons:
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
        ours = set(svc.ALL_METRIC_KEYS)
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
        # 整个 `market` scope 的完整性：只**告警**（别的批次窗口外的欠账不归本脚本管，
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
        "--to",
        dest="month_to",
        type=_parse_day,
        default=None,
        help="月上界（任意一天；会被折到该月 1 日），默认今天",
    )
    parser.add_argument(
        "--lookback-months",
        type=int,
        default=60,
        help="未给 --to 时回看的月数（默认 60；上游实测完整起点 2023-01）",
    )
    parser.add_argument(
        "--offline-months",
        default=None,
        help="探针 --json-out 的月度记录文件（给了就**不触网**，月频腿离线还原）",
    )
    parser.add_argument(
        "--coverage-only", action="store_true", help="只打印覆盖率对账，不写库不联网"
    )
    parser.add_argument("--coverage-from", type=_parse_day, default=None)
    parser.add_argument("--coverage-to", type=_parse_day, default=None)
    args = parser.parse_args()
    if args.lookback_months <= 0:
        print("✘ --lookback-months 须 > 0", file=sys.stderr)
        return 1
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":
    sys.exit(main())
