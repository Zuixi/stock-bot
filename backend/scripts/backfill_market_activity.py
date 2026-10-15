#!/usr/bin/env python3
"""回补 spec §S1.9（交易活跃度与杠杆资金）+ §S1.11（估值与风险溢价）到 `industry_metrics`。

这是**运维入口**，不是读路：读路是 S1.2 的 `GET /api/v1/metrics/{latest,series}`
（`industry_key='market'`）。本脚本**不新建表、不新建读路**。

用法（cwd = `backend/`，走 `DATABASE_URL`）::

    # 默认：回补最近 1900 个日历日（≈5.2 年 ≥ spec §S1.11 的「≥5 年」判据）
    DATABASE_URL=postgresql+asyncpg://... .venv/bin/python scripts/backfill_market_activity.py

    # 指定区间
    .venv/bin/python scripts/backfill_market_activity.py --from 2023-05-09 --to 2026-10-09

    # 只打印覆盖率对账（不写库、不联网）
    .venv/bin/python scripts/backfill_market_activity.py --coverage-only

    # 只落 ERP 的 PE 底座（沪深300 整体法 PE），不算 ERP
    .venv/bin/python scripts/backfill_market_activity.py --pe-only --from ... --to ...

退出码：0 = 成功且 PIT 审计通过；1 = 参数非法 / **整个区间一行都没落库** /
覆盖率对账发现真缺口 / **`industry_key='market'` 有行缺 `published_at` 或
`caliber_version`**（Ruling 59 的 PIT 审计 —— 这两列可空，漏带会静默落 NULL）。
注意：**早于行情底座、但 PE 与 10Y 都有的区间是正常成功**（只有 ERP/10Y 有行），
不再因为 `quote_days == 0` 判失败（Ruling 59 连带修正）。

**落库天（Ruling 59）**：行情类 11 个指标锚在 `daily_quotes` 交易日；
`cn_treasury_yield_10y` / `erp_10y` 锚在**自身输入的可得性交集**（中债曲线日 ∩
沪深300 PE 日），故后两者的行数**多于** `daily_quotes` 的交易日数 ——
默认窗口下 `erp_10y` 为 1259 天（2021-07-27 起，≈5.2 年 ≥ spec §S1.11 的「≥5 年」），
其余指标为 829 天（2023-05-09 起）。

**幂等**：唯一键 `(industry_key, stock_id, metric_key, source, freq, period)` +
`ON CONFLICT DO UPDATE`，同一区间重跑新增行数 = 0（ADR 0014 的 B 类可回补）。
自建换手率有**口径感知**的增量缓存：已按当前 `caliber_version` 落值的 (key, day)
不重算、也不重写（重写会把好值覆盖成 NULL），故中断后可重跑续补。

**联网**：默认会真的调 TuShare（`margin` / `index_dailybasic` / `index_weight`）与
AKShare（`bond_china_yield`）。`--coverage-only` 不联网。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import cast

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

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.database import async_session_factory  # noqa: E402
from app.services import market_activity_service as svc  # noqa: E402


def _parse_day(raw: str | None) -> date | None:
    return date.fromisoformat(raw) if raw else None


async def _latest_quote_day(db: AsyncSession) -> date | None:
    """`daily_quotes` 的最近交易日 —— 回补窗口锚在**数据**上，不锚 `date.today()`。"""
    value = (await db.execute(text("SELECT max(trade_date) FROM daily_quotes"))).scalar_one()
    return cast("date | None", value)


async def _run(args: argparse.Namespace) -> int:
    coverage_lo = args.coverage_from
    coverage_hi = args.coverage_to
    async with async_session_factory() as db:
        if not args.coverage_only:
            day_hi = args.day_to or await _latest_quote_day(db)
            if day_hi is None:
                print("✘ daily_quotes 为空 —— 没有可回补的底座", file=sys.stderr)
                return 1
            day_lo = args.day_from or (day_hi - timedelta(days=args.lookback))
            if args.pe_only:
                rows = await svc.ingest_erp_pe(db, day_lo, day_hi)
                await db.commit()
                print(json.dumps({"pe_rows_upserted": rows}, ensure_ascii=False, indent=2))
            else:
                stats = await _upsert(db, day_lo, day_hi)
                print(json.dumps(stats.as_dict(), ensure_ascii=False, indent=2))
                # **判据是"有没有落库"，不是"有没有行情日"**（Ruling 59 的连带修正）：
                # `erp_10y` / `cn_treasury_yield_10y` 锚在自己的输入上，早于行情底座的
                # 区间（如 2021-07-27..2022-12-31）照样有 ERP 可落 —— 那种区间
                # `quote_days == 0` 却是**正常成功**，旧的判据会把它误报成失败。
                if stats.rows_upserted == 0:
                    print(
                        "✘ 区间内既无 daily_quotes 交易日、也没有任何指标落库 —— "
                        "没有可回补的底座（行情类锚在 daily_quotes；ERP 类锚在 PE ∩ 10Y）",
                        file=sys.stderr,
                    )
                    return 1
            coverage_lo, coverage_hi = day_lo, day_hi

        coverage = await svc.metric_coverage(db, day_lo=coverage_lo, day_hi=coverage_hi)
        print(
            "\n=== 覆盖率对账（covered = 有值 / null = 有行但值为 NULL / missing = 交易日无行）==="
        )
        header = (
            f"{'metric_key':<28}{'from':<12}{'to':<12}"
            f"{'rows':>6}{'covered':>9}{'null':>7}{'missing':>9}  reasons"
        )
        print(header)
        print("-" * len(header))
        for cov in coverage:
            print(
                f"{cov.metric_key:<28}"
                f"{cov.period_from.isoformat() if cov.period_from else '—':<12}"
                f"{cov.period_to.isoformat() if cov.period_to else '—':<12}"
                f"{cov.rows:>6}{cov.covered_days:>9}{cov.null_days:>7}"
                f"{len(cov.missing_days):>9}  "
                f"{json.dumps(cov.missing_reasons, ensure_ascii=False)}"
            )

        # ── PIT / 口径落库审计（Ruling 59：把"报告里的 PIT 100%"机械化）────────
        # **硬判据 = 本次写过的窗口**（`[coverage_lo, coverage_hi]`）：这两列可空，
        # 漏带会静默落 NULL —— 本任务曾有 12036 行全部 NULL 而无人报错。判据限定在窗口内，
        # 是为了让"分年分段修复历史欠账"不被窗口外的旧行挡住（那是上一段的欠账，不是本段的）。
        window_lo = coverage_lo if coverage_lo is not None else date(1900, 1, 1)
        window_hi = coverage_hi if coverage_hi is not None else date(2999, 12, 31)
        audit = await svc.pit_audit(db, window_lo, window_hi)
        if not audit:
            print("\n✘ PIT 审计：窗口内一行都没有 —— 回补没有落库", file=sys.stderr)
            return 1
        print(
            f"\n=== PIT / 口径审计（窗口 {window_lo} … {window_hi}）==="
            "  count(*) / count(published_at) / count(caliber_version)"
        )
        ahead = (
            f"{'metric_key':<30}{'rows':>7}{'published_at':>15}{'caliber_version':>17}  calibers"
        )
        print(ahead)
        print("-" * len(ahead))
        missing: list[str] = []
        for row in audit:
            print(
                f"{row.metric_key:<30}{row.rows:>7}{row.with_published_at:>15}"
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
        # 整个 `market` scope 的完整性：只**告警**（窗口外的欠账由别的窗口/回补负责，
        # 但必须看得见 —— 这正是"报告声称 100%、库里却是 0"要防的形态）。
        whole = await svc.pit_audit(db)
        whole_missing = [
            f"{r.metric_key}(pub {r.with_published_at}/{r.rows})" for r in whole if not r.ok
        ]
        if whole_missing:
            print(
                "\n⚠ PIT 告警：`industry_key='market'` **窗口之外**仍有缺 PIT/口径的行 —— "
                + "; ".join(whole_missing)
                + "\n   （对本模块的 key 请回补覆盖这些 period 的区间）",
                file=sys.stderr,
            )
    return 0


async def _upsert(db: AsyncSession, day_lo: date, day_hi: date) -> svc.BackfillStats:
    """先落 PE 底座再算区间（`erp_10y` 的分子必须先存在）。"""
    await svc.ingest_erp_pe(db, day_lo, day_hi)
    stats = await svc.upsert_metric_range(db, day_lo, day_hi)
    await db.commit()
    return stats


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
        default=1900,
        help="未给 --from 时回看的日历日数（默认 1900 ≈ 5.2 年 ≥ spec §S1.11 的 5 年）",
    )
    parser.add_argument(
        "--coverage-only", action="store_true", help="只打印覆盖率对账，不写库不联网"
    )
    parser.add_argument("--pe-only", action="store_true", help="只回补 ERP 的 PE 底座")
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
