#!/usr/bin/env python3
"""相对基线对比：读 pytest-benchmark JSON（--benchmark-json 产物），按 median（P50）判退化。

用法: python scripts/bench_compare.py <baseline.json> <current.json> [--threshold 0.12] [--allow-added]

- 任一基准 median 相对退化 > threshold → 打印明细并 exit 1（硬门禁）。
- 本次新增、基线没有的基准 → 默认 exit 1（本地 gate：新基准须随 PR 刷新基线）；
  传 --allow-added 时降级为提示不失败（CI A/B 对比用——base 侧本就没有新基准）。
- 基线有、本次没跑的基准 → 忽略（套件删减不应误报）。

只用标准库；阈值默认 0.12（Phase A wall-clock 宽容差抗 runner 抖动，
Phase B 切指令数测量后建议收紧到 0.05）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_medians(path: Path) -> dict[str, float]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {b["fullname"]: float(b["stats"]["median"]) for b in data["benchmarks"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("current", type=Path)
    parser.add_argument("--threshold", type=float, default=0.12)
    parser.add_argument(
        "--allow-added",
        action="store_true",
        help="新增基准不判失败（CI A/B 模式：base commit 上本来就没有新基准）",
    )
    args = parser.parse_args()

    base = load_medians(args.baseline)
    cur = load_medians(args.current)

    regressed: list[tuple[str, float, float, float]] = []
    added: list[str] = []
    print(f"{'基准':<70} {'基线(s)':>10} {'当前(s)':>10} {'退化':>8}")
    for name, c in sorted(cur.items()):
        b = base.get(name)
        if b is None:
            added.append(name)
            print(f"{name:<70} {'-':>10} {c:>10.6f} {'新增':>8}")
            continue
        delta = (c - b) / b if b > 0 else 0.0
        flag = "  ✘ REGRESSED" if delta > args.threshold else ""
        if delta > args.threshold:
            regressed.append((name, b, c, delta))
        print(f"{name:<70} {b:>10.6f} {c:>10.6f} {delta:>+7.1%}{flag}")

    if regressed:
        print(f"\n✘ {len(regressed)} 项基准退化超过 {args.threshold:.0%}（硬门禁）：")
        for name, b, c, delta in regressed:
            print(f"  ✘ {name}: {b:.6f}s → {c:.6f}s ({delta:+.1%})")
        return 1
    if added:
        if args.allow_added:
            print(f"\n⚠ {len(added)} 项基准为新增（--allow-added，不计失败）")
            print(f"\n✔ 存量基准全部在阈值 {args.threshold:.0%} 内")
            return 0
        print(f"\n✘ {len(added)} 项基准不在基线中 —— 新基准须随 PR 刷新基线：")
        for name in added:
            print(f"  • {name}")
        print("  修复：bash scripts/bench.sh --save-baseline 后随本 PR 提交 baseline.json")
        return 1
    print(f"\n✔ 全部基准在阈值 {args.threshold:.0%} 内")
    return 0


if __name__ == "__main__":
    sys.exit(main())
