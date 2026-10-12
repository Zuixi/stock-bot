"""生成 ``backend/data/sw_index_mapping.csv``（S1.4 SCD2 ingest 的对照表）。

来源 = ``docs/references/sw/申万行业分类.md``（申万行业指数分类标准 2021 版）的表格。
为什么要生成一份 CSV 而不是运行时解析 md（实测踩过的坑）：

* 后端容器 ``COPY backend/`` 后**没有**仓库根的 ``docs/``，运行时读 md 在 Docker 里必失败；
* 生成物落在 ``backend/data/``（与既有 ``sw_seed.sql`` / ``sw_custom_tags_seed.sql`` 同处），
  随镜像一起发布，路径不依赖仓库布局。

用法：``.venv/bin/python scripts/gen_sw_index_mapping.py``
预期产物：511 行（L1 31 + L2 134 + L3 346），4 列。
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND_ROOT.parent
_SRC = _REPO_ROOT / "docs" / "references" / "sw" / "申万行业分类.md"
_DEST = _BACKEND_ROOT / "data" / "sw_index_mapping.csv"

_LEVEL_BY_CATEGORY = {"一级行业": 1, "二级行业": 2, "三级行业": 3}


def parse(src: Path = _SRC) -> list[tuple[str, str, int, str]]:
    rows: list[tuple[str, str, int, str]] = []
    for line in src.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 7:
            continue
        if not re.fullmatch(r"\d{6}", cells[0]) or not re.fullmatch(r"\d{6}", cells[1]):
            continue
        level = _LEVEL_BY_CATEGORY.get(cells[5])
        if level is None:
            continue
        name = cells[2] if level == 1 else (cells[3] if level == 2 else cells[4])
        rows.append((cells[0], f"{cells[1]}.SI", level, name))
    return rows


def main() -> int:
    rows = parse()
    counts = {lvl: sum(1 for r in rows if r[2] == lvl) for lvl in (1, 2, 3)}
    if counts != {1: 31, 2: 134, 3: 346}:
        print(f"[FAIL] 层级计数不符：{counts}（契约 31/134/346）", file=sys.stderr)
        return 1
    with _DEST.open("w", encoding="utf-8", newline="") as fh:
        fh.write("# 申万行业代码 → 上游 TuShare index_member 指数代码（S1.4 SCD2 ingest 用）\n")
        fh.write("# 来源：docs/references/sw/申万行业分类.md（申万行业指数分类标准 2021 版）\n")
        fh.write("# 由 backend/scripts/gen_sw_index_mapping.py 生成，勿手改；")
        fh.write("511 行 = L1 31 + L2 134 + L3 346\n")
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["industry_code", "index_code", "level", "industry_name"])
        writer.writerows(rows)
    print(f"[OK] wrote {_DEST} rows={len(rows)} counts={counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
