#!/usr/bin/env python3
"""回补 §S1.7 **陆股通持仓（季度）**（`hk_hold` 逐交易所取）到 `industry_metrics`。

**为什么按季度、为什么按交易所分开取**：
- 该数据**季度披露**（2024-08-19 起；规则 = 季末后第 5 个北向交易日发布）
  ⇒ 一行 = 一只股票 × 一个季末；
- `hk_hold` 单次 **4200 行上限**（实测不带 `exchange` 时 SH 2089 + SZ 1097 + HK 1014 = 4200，
  即 **SZ 被截断**；分开取 SH 2089 + SZ 2320 = 4409 才完整）⇒ 必须逐交易所取并断言未撞上限。

用法（cwd = `backend/`，走 `DATABASE_URL` + `TUSHARE_TOKEN`）::

    # 默认：最近 4 个季末
    uv run python scripts/backfill_northbound_holding.py

    # 指定季末（必须是 3/31·6/30·9/30·12/31）
    uv run python scripts/backfill_northbound_holding.py --date 2026-09-30

    # 只对账（不联网、不写库）
    uv run python scripts/backfill_northbound_holding.py --coverage-only

退出码：0 = 成功；1 = 参数非法 / 整轮一行都没落库。

**幂等**：唯一键 + `ON CONFLICT DO UPDATE` ⇒ 重跑同一季末新增行数 = 0。

⚠️ **PIT**：`published_at` = 季末后第 5 个交易日 21:00（上海时区），交易日集合取自库内
`daily_quotes`；若行情底座还没到第 5 个交易日 ⇒ **响亮失败**，不得猜一个日期（ADR 0011）。

⚠️ **口径**：CCASS participants **汇总**（名义持有人）——**不得**称"外资"（Ruling 19）。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import northbound_holding_service as svc  # noqa: E402

DEFAULT_QUARTERS = 4


def _parse_day(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:  # pragma: no cover
        raise argparse.ArgumentTypeError(f"日期须为 YYYY-MM-DD：{raw!r}") from exc


async def _run(args: argparse.Namespace) -> int:
    from app.core.database import async_session_factory  # noqa: PLC0415

    if args.coverage_only:
        async with async_session_factory() as db:
            cov = await svc.coverage(db)
        print(f"northbound_holding_quarterly: rows={cov['rows']} 逐季={cov['per_quarter']}")
        if cov["rows"] == 0:
            print("✘ 覆盖率对账失败：一行都没有", file=sys.stderr)
            return 1
        print("✔ 覆盖率对账通过")
        return 0

    if args.date is not None:
        targets = [args.date]
    else:
        targets = svc.recent_quarter_ends(date.today(), args.quarters)
    if args.quarters <= 0:
        print("✘ --quarters 须 > 0", file=sys.stderr)
        return 1

    print(f"回补 northbound_holding_quarterly：{len(targets)} 个季末 {targets[0]}…{targets[-1]}")
    total = 0
    for index, quarter in enumerate(targets, start=1):
        async with async_session_factory() as db:
            result = await svc.ingest_quarter(db, quarter)
            await db.commit()
        total += int(result.get("rows", 0))
        print(f"  [{index}/{len(targets)}] {quarter} → {result}")
        if index < len(targets) and args.sleep:
            await asyncio.sleep(args.sleep)

    print(f"合计写入（含幂等更新）：{total}")
    if total == 0:
        print(
            "✘ 整轮一行都没落库 —— 参数错 / 上游无权限 / 行情底座不足，三者必须查清",
            file=sys.stderr,
        )
        return 1
    async with async_session_factory() as db:
        cov = await svc.coverage(db)
    print(f"northbound_holding_quarterly: rows={cov['rows']} 逐季={cov['per_quarter']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--date", type=_parse_day, default=None, help="单个季末 YYYY-MM-DD")
    parser.add_argument("--quarters", type=int, default=DEFAULT_QUARTERS)
    parser.add_argument("--sleep", type=float, default=0.5)
    parser.add_argument("--coverage-only", action="store_true")
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":
    sys.exit(main())
