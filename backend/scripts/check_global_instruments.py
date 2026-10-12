"""S1.3 验收脚本：全球标的五要素非空 + IXIC≠NDX 串号回归（**只读，不写库**）。

为什么要一个脚本而不是只在 pytest 里断言：pytest 只能看到**登记表常量**与**假行情**，
拿不到"上游此刻真的给了两格不同的价"这一事实。本脚本做两件事：

1. **五要素非空**：遍历 `market_data_service.GLOBAL_INDICES`，断言
   `name / ts_code / session / timezone / currency` 逐条非空（时区还须是合法 IANA 名）；
2. **IXIC ≠ NDX**：走读路径拿到两条标的的最新值，直接打印并断言不相等。

用法::

    backend/.venv/bin/python scripts/check_global_instruments.py            # 只查登记表
    backend/.venv/bin/python scripts/check_global_instruments.py --live     # 追加一次真实行情取数

`--live` 会真发一次上游请求（东财 `push2delay` 或新浪 `hq.sinajs.cn`）。**本仓库的开发沙箱
里东财 `push2` 域被拒连**（见 `docs/references/best-practices.md` 数据源章节与
`eastmoney_client.py` 顶部备注），此时 IXIC 会回落到"库内最近一根日线"，脚本会把
`price_source` 一并打印出来——**看到 `eod`/`none` 不等于失败，`0` 才是失败**。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from zoneinfo import ZoneInfo

from app.services.market_data_service import GLOBAL_INDICES, get_global_index_cards

FIELDS = ("name", "ts_code", "session", "timezone", "currency")

#: 串号回归的两个主角（S1.3 行为判据）
PAIR = ("IXIC", "NDX")


def check_registry() -> int:
    print(f"== 五要素非空：{len(GLOBAL_INDICES)} 条标的 ==")
    failures = 0
    for g in GLOBAL_INDICES:
        missing = [f for f in FIELDS if not str(g.get(f) or "").strip()]
        if missing:
            failures += 1
            print(f"FAIL {g.get('ts_code')}: 缺 {missing}")
            continue
        try:
            ZoneInfo(g["timezone"])
        except Exception as exc:  # noqa: BLE001 - 报告原始原因即可
            failures += 1
            print(f"FAIL {g['ts_code']}: 时区不可解析 {g['timezone']!r} ({exc})")
            continue
        print(
            f"PASS {g['market']:2s} {g['ts_code']:10s} {g['name']:12s} | "
            f"{g['session']:46s} | {g['timezone']:18s} | {g['currency']:4s} | "
            f"src={g['source']}"
        )
    assert_market_counts()
    return failures


def assert_market_counts() -> None:
    from collections import Counter

    counts = Counter(g["market"] for g in GLOBAL_INDICES)
    print(f"-- 按市场分组：{dict(counts)}（美国 {counts['US']} / 香港 {counts['HK']}）")


async def check_live() -> int:
    cards = await get_global_index_cards(cache=None)
    by_code = {c["ts_code"]: c for c in cards}
    print(f"\n== IXIC ≠ NDX（真实取数，{len(cards)} 条卡片）==")
    for code in PAIR:
        c = by_code.get(code)
        if c is None:
            print(f"FAIL {code} 不在卡片里")
            return 1
        print(
            f"{code:6s} name={c['name']:12s} price={c['price']!r:>14} "
            f"pct={c['pct_change']!r:>8} price_source={c['price_source']}"
        )
    ixic, ndx = by_code[PAIR[0]]["price"], by_code[PAIR[1]]["price"]
    if ixic is None or ndx is None:
        print(f"FAIL 存在空价（IXIC={ixic!r}, NDX={ndx!r}）——空价不得当成 0，须查上游/库内日线")
        return 1
    if ixic == ndx:
        print(f"FAIL IXIC == NDX == {ixic}：串号或样本无效，须换样本日并说明")
        return 1
    print(f"PASS IXIC({ixic}) != NDX({ndx})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="S1.3 全球标的验收（只读）")
    parser.add_argument("--live", action="store_true", help="追加一次真实行情取数")
    args = parser.parse_args()

    failed = check_registry()
    if args.live:
        failed += asyncio.run(check_live())
    if failed:
        print(f"\n验收未通过：{failed} 项")
        return 1
    print("\n验收通过：五要素非空率 100%" + ("，IXIC ≠ NDX" if args.live else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
