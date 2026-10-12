"""S1.4 · 回填 SCD2 申万成分归属（上游 TuShare ``index_member``）。

用法（backend/ 下）::

    .venv/bin/python scripts/ingest_sw_member_periods.py           # 只灌 L3（346 指数，约 3 分钟）
    .venv/bin/python scripts/ingest_sw_member_periods.py --levels 1,2,3
    .venv/bin/python scripts/ingest_sw_member_periods.py --limit 20  # 冒烟

幂等：重跑同一份上游数据 = 新增行 0（唯一键 ``(industry_code, stock_code, effective_from)``）。
结果逐行打印，便于手抄进 docs/design/data-source.md 的实机记录表。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.database import async_session_factory  # noqa: E402
from app.services.sw_member_scd2_service import (  # noqa: E402
    get_member_as_of,
    ingest_sw_member_periods,
    parse_sw_index_mapping,
)


async def main() -> int:
    parser = argparse.ArgumentParser(description="回填 SCD2 申万成分归属")
    parser.add_argument(
        "--levels",
        default="3",
        help="逗号分隔的层级（1/2/3）；默认 3（祖级成分可由 L3 上卷）",
    )
    parser.add_argument("--limit", type=int, default=None, help="只灌前 N 个指数（冒烟）")
    args = parser.parse_args()
    levels = tuple(int(x) for x in args.levels.split(",") if x.strip())

    refs = parse_sw_index_mapping()
    by_level: dict[int, int] = {}
    for ref in refs:
        by_level[ref.level] = by_level.get(ref.level, 0) + 1
    print(
        f"[MAPPING] {len(refs)} 指数对照（L1/L2/L3 = {by_level.get(1, 0)}"
        f"/{by_level.get(2, 0)}/{by_level.get(3, 0)}）"
    )
    print(f"[PLAN   ] levels={levels} limit={args.limit}")

    async with async_session_factory() as db:
        result = await ingest_sw_member_periods(db, levels=levels, limit=args.limit)
        as_of = await get_member_as_of(db)

    print(f"[OK     ] as_of={result.as_of} levels={result.levels}")
    print(f"[OK     ] index_codes={result.index_codes} rows_upserted={result.rows_upserted}")
    print(f"[OK     ] per_level_rows={result.per_level_rows}")
    print(f"[OK     ] failed={len(result.failed)}")
    for code, err in list(result.failed.items())[:10]:
        print(f"[FAIL   ] {code}: {err}")
    print(f"[AS_OF  ] 最近一次成功 ingest 的 as_of = {as_of}（= 读面可还原能力的上界）")
    return 0 if not result.failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
