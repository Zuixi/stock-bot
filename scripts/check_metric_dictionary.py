#!/usr/bin/env python3
"""核对 docs/design/metric-dictionary.md §4.2 的"真实命名样例"表 ↔ industry_registry.

口径字典 §4.2 规则 3 的要求是"单位与频率随 ``metric_key`` 在 registry 固定"，
故字典里那张表**必须是 registry 的逐字转录**。本脚本做机械核对（防止手抄漂移）：

    文档表 (metric_key, unit, freq)  ==  MetricDef(key, unit, freq)

打印逐行对比表，任一行不一致则以非 0 退出（可直接用作门禁）。

用法（必须在 backend/ 下跑，否则 import app 失败）::

    cd backend && UV_CACHE_DIR=/tmp/uv-cache uv run python ../scripts/check_metric_dictionary.py

退出码：0 = 全绿；1 = 有 MISMATCH 或解析不到样例表。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DOC = Path(__file__).resolve().parent.parent / "docs/design/metric-dictionary.md"

# "（**空串** = 无量纲比值）" 是文档对 registry `unit=""` 的显式写法（见文档表下注）
EMPTY_UNIT_DOC = "（**空串** = 无量纲比值）"


def _norm_unit(raw: str) -> str:
    return "" if raw == EMPTY_UNIT_DOC else raw


KEY_RE = re.compile(r"^[a-z][a-z0-9_.]*$")


def doc_rows(text: str) -> list[tuple[str, str, str, str]]:
    """取 §4.2「真实命名样例」里 4 列的样例行（metric_key/name/unit/freq）。

    只认首格是**合法 key 形态**的行（全小写 + 下划线，兼容既有 ``company.*`` 带点键），
    且排除表头本身（``metric_key`` 恰好也符合 key 形态）。
    """
    rows: list[tuple[str, str, str, str]] = []
    in_section = False
    for line in text.splitlines():
        if line.startswith("## §4.2"):
            in_section = True
            continue
        if in_section and line.startswith("## §4.3"):
            break
        if not in_section or not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 4:
            continue
        key = cells[0].strip("`")
        if not KEY_RE.match(key) or key == "metric_key":
            continue  # 表头行或非样例行
        rows.append((key, cells[1], _norm_unit(cells[2]), cells[3]))
    return rows


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
    from app.services.industry_registry import INDUSTRIES  # noqa: PLC0415

    registry = {m.key: (m.unit, m.freq) for ind in INDUSTRIES.values() for m in ind.metrics}

    text = DOC.read_text(encoding="utf-8")
    rows = doc_rows(text)
    if not rows:
        print("✘ 未在 §4.2 解析到样例表（标题或表头格式变了？）")
        return 1

    print(
        f"{'metric_key':<28}{'doc unit':<26}{'reg unit':<12}"
        f"{'doc freq':<10}{'reg freq':<10}result"
    )
    print("-" * 100)
    bad = 0
    for key, doc_unit, doc_freq, *_ in [(r[0], r[2], r[3], r[1]) for r in rows]:
        if key not in registry:
            print(f"{key:<28}{doc_unit:<26}{'—':<12}{doc_freq:<10}{'—':<10}MISSING IN REGISTRY")
            bad += 1
            continue
        reg_unit, reg_freq = registry[key]
        ok = doc_unit == reg_unit and doc_freq == reg_freq
        if not ok:
            bad += 1
        print(
            f"{key:<28}{doc_unit:<26}{reg_unit or '(空)':<12}{doc_freq:<10}{reg_freq:<10}"
            f"{'OK' if ok else 'MISMATCH'}"
        )
    print("-" * 100)
    print(f"核对 {len(rows)} 行，MISMATCH {bad} 行 → {'✔ 全绿' if bad == 0 else '✘ 有错'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
