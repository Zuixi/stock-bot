#!/usr/bin/env python3
"""扫「自选体检」的文案面，命中禁用词即以非零码退出（spec §S8.5 的机械门禁）。

扫描面（`TARGETS`，改路径必须同步这里；目标不存在 = **失败**，不许"扫 0 个文件"当通过）：

- ``frontend/src/features/triage`` —— 体检组件与样式（全目录递归，新增文件自动纳入）
- ``frontend/src/pages/me/index.tsx`` —— Tab 标题与页面文案
- ``frontend/e2e/watchlistTriage.spec.ts`` —— 断言里的 fixture 文案（假数据也会被截图/演示）
- ``backend/app/api/v1/watchlist_triage.py`` —— 固定 DISCLAIMER 与错误提示
- ``backend/app/services/triage_detectors.py`` —— 探测器 `headline` / `fact label`
  （用户真正读到的那句话）

**为什么连后端一起扫**：`headline` 是后端拼的，前端原样上屏；只扫前端等于只守住了半条边。

唯一例外是行级豁免标记 ``compliance-allow``（见 `triage_compliance.py`）：某行必须逐字
列出禁用词时（e2e 的 `FORBIDDEN` 断言清单），在该行写理由。豁免行数会被打印。

用法::

    cd backend && UV_CACHE_DIR=.uv-cache uv run python scripts/check_triage_compliance.py

退出码：0 = 0 命中；1 = 有命中 / 扫描目标缺失。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 直接从同目录 import：脚本用 `uv run python scripts/xxx.py` 跑，cwd = backend/
sys.path.insert(0, str(Path(__file__).resolve().parent))

from triage_compliance import (  # noqa: E402
    BANNED_WORDS,
    MIN_WORDS,
    missing_targets,
    scan_paths,
)

#: 仓库根（本文件在 ``<repo>/backend/scripts/`` 下）
REPO_ROOT = Path(__file__).resolve().parents[2]

#: 扫描面：体检文案会出现的全部文件/目录（见模块 docstring 的逐条理由）
TARGETS: tuple[str, ...] = (
    "frontend/src/features/triage",
    "frontend/src/features/journal",
    "frontend/src/shared/api/journal.ts",
    "frontend/src/pages/me/index.tsx",
    "frontend/e2e/watchlistTriage.spec.ts",
    "frontend/e2e/decisionJournal.spec.ts",
    "backend/app/api/v1/journal.py",
    "backend/app/services/journal_service.py",
    "backend/app/api/v1/watchlist_triage.py",
    "backend/app/services/triage_detectors.py",
)


def main() -> int:
    print(
        f"禁用词表 {len(BANNED_WORDS)} 个（下限 {MIN_WORDS}，spec §S8.5）· "
        f"扫描 {len(TARGETS)} 个目标"
    )

    missing = missing_targets(REPO_ROOT, TARGETS)
    if missing:
        # 路径改名/删文件后若静默跳过，门禁会变成"扫了 0 个文件 → 0 命中 → 通过"，
        # 那是最危险的一种绿。故缺失目标是**硬失败**。
        print("扫描目标不存在（路径改名请同步本脚本 TARGETS）：", file=sys.stderr)
        for target in missing:
            print(f"  - {target}", file=sys.stderr)
        return 1

    report = scan_paths(REPO_ROOT, TARGETS)
    print(f"实际扫描 {report.files} 个文件")

    if report.allowed_lines:
        # 豁免不是静默通过：列出来让评审看见"这几行必须包含禁用词"。
        print(f"行级豁免 {len(report.allowed_lines)} 处（{report.allowed_lines}）")

    if not report.ok:
        print(f"命中禁用词 {len(report.hits)} 处：", file=sys.stderr)
        for hit in report.hits:
            print(f"  {hit}", file=sys.stderr)
        print(
            "\n修法：改文案（陈述事实，不做判断），而不是删词表或加豁免标记。"
            "体检页只说「发生了什么 / 数据截至 / 点哪复核」。",
            file=sys.stderr,
        )
        return 1

    print("0 命中：体检文案无判断词")
    return 0


if __name__ == "__main__":
    sys.exit(main())
