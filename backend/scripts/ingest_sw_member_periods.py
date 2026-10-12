"""S1.4 · 回填 SCD2 申万成分归属（上游 TuShare ``index_member``）。

用法（backend/ 下）::

    .venv/bin/python scripts/ingest_sw_member_periods.py        # 全部三级（511 指数，~5 分钟）
    .venv/bin/python scripts/ingest_sw_member_periods.py --levels 1
    .venv/bin/python scripts/ingest_sw_member_periods.py --limit 20   # 冒烟（不抬高口径日）

**默认全部三级**（Fix round 1 · 评审 C1）：L1/L2 的成分与"其下 L3 的并集"**不等价**
（实测 24/31 个一级行业偏少：5211 vs 4950、农林牧渔 104 vs 80、钢铁 43 vs 28、
饲料 18 vs 11），因为上游有直接挂在 L1/L2 的成员。

幂等：重跑同一份上游数据 = 新增行 0（唯一键
``(industry_code, stock_code, effective_from, effective_to)``）；
``--limit`` 截断的运行 ``fully_ingested=False``，**不会**抬高读面的 ``scd2_as_of``。
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
        default="1,2,3",
        help="逗号分隔的层级（1/2/3）；默认 1,2,3（L1/L2 ≠ L3 并集，必须逐级灌）",
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

    print(f"[RESULT ] as_of={result.as_of} levels={result.levels} status={result.status}")
    print(
        f"[RESULT ] 请求指数={result.requested_indices} 成功={result.index_codes} "
        f"fully_ingested={result.fully_ingested}"
    )
    print(
        f"[RESULT ] 新增={result.rows_inserted} 更新={result.rows_updated} "
        f"（合计 upsert {result.rows_upserted}）"
    )
    for level, (idx, rows) in sorted(result.coverage_per_level.items()):
        print(f"[COVER  ] L{level}: 成功指数={idx} 区间行={rows}")
    print(
        f"[COVER  ] 上游 0 行响应 {len(result.zero_row_indices)} 个："
        f"{result.zero_row_indices[:8]}{'…' if len(result.zero_row_indices) > 8 else ''}"
    )
    print(f"[RESULT ] failed={len(result.failed)}")
    for code, err in list(result.failed.items())[:10]:
        print(f"[FAIL   ] {code}: {err}")
    print(
        f"[AS_OF  ] 最近一次**完整成功** ingest 的 as_of = {as_of}"
        f"（= 读面可还原能力的上界；limit/partial 运行不抬高它）"
    )
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
