#!/usr/bin/env python3
"""自选体检的**合规词表**（唯一真相）+ 扫描函数。

为什么词表在代码里而不是在文档里：spec §S8.5 只写"禁用词 ≥20 个，清单在
``backend/scripts/triage_compliance.py``"。文档写一份、代码写一份必然漂移，最后
没人知道哪份是真的；所以词表只此一份，文档只保留"≥20 的下限"这句话。

三条口径：

1. **判断词一个都不许出现在体检文案里**——探测器 `headline`、页面文案、API 常量、
   e2e 断言四个面一起扫（扫描面见 `check_triage_compliance.py` 的 `TARGETS`）；
2. 命中按**逐字子串**判定（中文没有词边界）：所以"建议关注"必然同时命中"建议关注"
   这个词本身。"宁可误伤，不许漏"是本脚本的取向——误伤的修法是改文案，不是删词；
3. 词表**只增不删**：删词 = 悄悄放宽合规边界，需要 owner 决定，也会让所有历史的
   "0 命中"结论失去意义。

唯一的例外机制是**行级豁免标记** `compliance-allow`：某行必须逐字列出禁用词时
（例如 e2e 的 `FORBIDDEN` 断言清单本身），在该行写理由。脚本会把豁免行数打印出来，
让评审看得见——豁免不是"静默通过"，而是"显式声明这几行必须包含禁用词"。

单独运行（打印词表与每条的解释）::

    cd backend && UV_CACHE_DIR=.uv-cache uv run python scripts/triage_compliance.py

门禁运行（命中即非零退出）::

    cd backend && UV_CACHE_DIR=.uv-cache uv run python scripts/check_triage_compliance.py

退出码（`triage_compliance.py` 直接运行时）：0 = 词表自检通过。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

#: spec §S8.1/§S8.5 的"不构成投资建议"下限：**≥20 个判断词**（只增不删）。
#:
#: 分类（写在这里是给评审看的，扫描时一视同仁）：
#: - 交易指令：买入 / 卖出 / 加仓 / 减仓 / 建仓 / 清仓 / 抄底 / 上车 / 跟上
#: - 收益承诺：必涨 / 稳赚 / 保本 / 翻倍 / 满仓
#: - 评价与推荐：推荐 / 建议关注 / 看好 / 机会 / 布局
#: - 价格目标：目标价 / 目标位 / 止盈
#: - 标的标签：牛股 / 黑马
BANNED_WORDS: tuple[str, ...] = (
    "买入",
    "卖出",
    "推荐",
    "建议关注",
    "看好",
    "目标价",
    "必涨",
    "稳赚",
    "抄底",
    "翻倍",
    "满仓",
    "保本",
    "上车",
    "跟上",
    "牛股",
    "黑马",
    "止盈",
    "目标位",
    "机会",
    "布局",
    "加仓",
    "减仓",
    "建仓",
    "清仓",
)

#: 行级豁免标记：出现在某一行即跳过该行的命中（豁免行数会被打印出来，评审可见）。
ALLOW_MARKER = "compliance-allow"

#: spec §S8.5 的下限。词表被误删到 20 个以下时**导入期就炸**，
#: 否则"0 命中"会悄悄变成一句空话（扫的词变少了，当然不命中）。
MIN_WORDS = 20


def _assert_wordlist() -> None:
    """导入期断言：词表 ≥ ``MIN_WORDS`` 且无重复、无空串。"""
    if len(BANNED_WORDS) < MIN_WORDS:
        raise RuntimeError(
            f"禁用词表不得少于 {MIN_WORDS} 个（spec §S8.5），当前 {len(BANNED_WORDS)}"
        )
    if len(set(BANNED_WORDS)) != len(BANNED_WORDS):
        raise RuntimeError(f"禁用词表有重复项：{BANNED_WORDS}")
    if any(not word.strip() for word in BANNED_WORDS):
        raise RuntimeError("禁用词表含空串（会命中每一行）")


_assert_wordlist()


@dataclass(frozen=True)
class Hit:
    """一处命中：文件 + 行号 + 词 + 该行原文（截断到 120 字符便于打印）。"""

    path: str
    line_no: int
    word: str
    line: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line_no}: 命中禁用词「{self.word}」\n    {self.line[:120]}"


@dataclass(frozen=True)
class ScanReport:
    """一次扫描的结果（``hits`` 为空才算合规）。"""

    files: int
    hits: tuple[Hit, ...]
    #: 含豁免标记而被跳过的行数（>0 时打印出来，评审必须看一眼）
    allowed_lines: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.hits


def scan_text(text: str, *, path: str = "<text>") -> tuple[list[Hit], list[str]]:
    """扫一段文本；返回 ``(命中列表, 含豁免标记的行)``。

    逐字子串匹配（中文无词边界），一行里命中多个词就算多条。
    """
    hits: list[Hit] = []
    allowed: list[str] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if ALLOW_MARKER in line:
            allowed.append(f"{path}:{line_no}")
            continue
        for word in BANNED_WORDS:
            if word in line:
                hits.append(Hit(path=path, line_no=line_no, word=word, line=line.strip()))
    return hits, allowed


def scan_paths(root: Path, targets: tuple[str, ...]) -> ScanReport:
    """扫 ``root`` 下的若干个**文件或目录**（目录递归 ``*.ts/.tsx/.css/.py``）。

    目标不存在视为**失败条件**（由调用方判定）：路径改名后扫描会静默变成"0 命中"，
    那是最危险的一种绿——所以这里把缺失目标记进 ``missing``，交给调用方非零退出。
    """
    files: list[Path] = []
    for target in targets:
        path = root / target
        if path.is_dir():
            files.extend(
                sorted(
                    p
                    for p in path.rglob("*")
                    if p.is_file() and p.suffix in {".ts", ".tsx", ".css", ".py"}
                )
            )
        elif path.is_file():
            files.append(path)
    hits: list[Hit] = []
    allowed: list[str] = []
    scanned = 0
    for path in files:
        text = path.read_text(encoding="utf-8")
        scanned += 1
        file_hits, file_allowed = scan_text(text, path=str(path.relative_to(root)))
        hits.extend(file_hits)
        allowed.extend(file_allowed)
    return ScanReport(files=scanned, hits=tuple(hits), allowed_lines=tuple(allowed))


def missing_targets(root: Path, targets: tuple[str, ...]) -> list[str]:
    """扫描面里不存在的目标（改名/删文件后必须显式失败，不许"扫 0 个文件"当通过）。"""
    return [target for target in targets if not (root / target).exists()]


if __name__ == "__main__":  # pragma: no cover - 手工检查脚本
    print(f"禁用词 {len(BANNED_WORDS)} 个（下限 {MIN_WORDS}，spec §S8.5）：")
    for word in BANNED_WORDS:
        print(f"  - {word}")
    print()
    print("扫描面与退出码见 check_triage_compliance.py：")
    print("  cd backend && UV_CACHE_DIR=.uv-cache uv run python scripts/check_triage_compliance.py")
    sys.exit(0)
