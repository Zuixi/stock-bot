#!/usr/bin/env python3
"""核对 docs/design/metric-dictionary.md §4.2 的"真实命名样例"表 ↔ industry_registry.

口径字典 §4.2 规则 3 的要求是"单位与频率随 ``metric_key`` 在 registry 固定"，
故字典里那张表**必须是 registry 的逐字转录**。本脚本做机械核对（防止手抄漂移）：

    文档表 (metric_key, name, unit, freq)  ==  MetricDef(key, name, unit, freq)

三个方向都查：

1. **正向**：文档样例表的每一行（name / unit / freq）与 registry 逐字比对
2. **反查**：**已注册**的 key 若被错移进 5 列的"新增提案"表 → 失败
   （提案表里的 key 尚未注册，正向查不到它，必须靠这条兜住）
3. **覆盖面**：打印样例表覆盖了多少已注册 key（只报告，不作为失败条件——
   样例表是"示例"不是"全集"）

任一条不满足即以非 0 退出（可直接当门禁）。

用法（cwd 任意：脚本自己把 ``backend/`` 插进 ``sys.path``）::

    uv run --project backend python backend/scripts/check_metric_dictionary.py

退出码：0 = 全绿；1 = 有 MISMATCH / 已注册键混入提案表 / 解析不到样例表。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BACKEND_ROOT = _REPO_ROOT / "backend"
sys.path.insert(0, str(_BACKEND_ROOT))  # cwd 无关：让 `import app.*` 恒可用

DOC = _REPO_ROOT / "docs/design/metric-dictionary.md"

# "（**空串** = 无量纲比值）" 是文档对 registry `unit=""` 的显式写法（见文档表下注）
EMPTY_UNIT_DOC = "（**空串** = 无量纲比值）"

KEY_RE = re.compile(r"^[a-z][a-z0-9_.]*$")


def _norm_unit(raw: str) -> str:
    return "" if raw == EMPTY_UNIT_DOC else raw


def _section(text: str) -> str:
    """截取 §4.2 正文（到 §4.3 之前）。"""
    lines: list[str] = []
    in_section = False
    for line in text.splitlines():
        if line.startswith("## §4.2"):
            in_section = True
            continue
        if in_section and line.startswith("## §4.3"):
            break
        if in_section:
            lines.append(line)
    return "\n".join(lines)


def _table_rows(text: str, n_cells: int) -> list[list[str]]:
    """取 §4.2 正文里指定列数的表格数据行（排除表头行 ``metric_key``）。"""
    rows: list[list[str]] = []
    for line in text.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != n_cells:
            continue
        key = cells[0].strip("`")
        if not KEY_RE.match(key) or key == "metric_key":
            continue
        rows.append(cells)
    return rows


def doc_sample_rows(text: str) -> list[tuple[str, str, str, str]]:
    """§4.2「真实命名样例」的 4 列样例行（metric_key / name / unit / freq）。"""
    return [(c[0].strip("`"), c[1], _norm_unit(c[2]), c[3]) for c in _table_rows(text, 4)]


def doc_proposal_keys(text: str) -> set[str]:
    """§4.2「新增提案」的 5 列表里的 key 集合（这些应当**未注册**）。"""
    return {c[0].strip("`") for c in _table_rows(text, 5)}


def main() -> int:
    from app.services.industry_registry import INDUSTRIES  # noqa: PLC0415

    registry = {m.key: (m.name, m.unit, m.freq) for ind in INDUSTRIES.values() for m in ind.metrics}

    text = DOC.read_text(encoding="utf-8")
    section = _section(text)
    rows = doc_sample_rows(section)
    if not rows:
        print("✘ 未在 §4.2 解析到样例表（标题或表头格式变了？）")
        return 1

    bad = 0
    print(
        f"{'metric_key':<28}{'doc name':<14}{'doc unit':<12}{'doc freq':<10}"
        f"{'reg unit':<12}{'reg freq':<10}result"
    )
    print("-" * 104)
    for key, doc_name, doc_unit, doc_freq in rows:
        if key not in registry:
            print(
                f"{key:<28}{doc_name:<14}{doc_unit:<12}{doc_freq:<10}"
                f"{'—':<12}{'—':<10}MISSING IN REGISTRY"
            )
            bad += 1
            continue
        reg_name, reg_unit, reg_freq = registry[key]
        diffs = []
        if doc_unit != reg_unit:
            diffs.append(f"unit {doc_unit!r}!={reg_unit!r}")
        if doc_freq != reg_freq:
            diffs.append(f"freq {doc_freq!r}!={reg_freq!r}")
        if doc_name != reg_name:
            diffs.append(f"name {doc_name!r}!={reg_name!r}")
        if diffs:
            bad += 1
        print(
            f"{key:<28}{doc_name:<14}{doc_unit:<12}{doc_freq:<10}"
            f"{reg_unit or '(空)':<12}{reg_freq:<10}"
            f"{'OK' if not diffs else 'MISMATCH: ' + '; '.join(diffs)}"
        )

    # 反查：已注册的 key 不得出现在"新增提案"表里（否则是把既有 key 当提案，或反之）
    misplaced = sorted(doc_proposal_keys(section) & registry.keys())
    print()
    if misplaced:
        for key in misplaced:
            print(f"✘ {key!r} 已在 registry 注册，却被列进 §4.2「新增提案」表")
        bad += len(misplaced)
    else:
        print("✔ 反查：§4.2「新增提案」表中无已注册 key")

    covered = [k for k, *_ in rows if k in registry]
    print(
        f"✔ 覆盖面：样例表含 {len(covered)} 个已注册 key / registry 共 {len(registry)} 个"
        f"（样例表是示例，不要求全集）"
    )
    print("-" * 104)
    print(f"核对 {len(rows)} 行，MISMATCH {bad} 行 → {'✔ 全绿' if bad == 0 else '✘ 有错'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
