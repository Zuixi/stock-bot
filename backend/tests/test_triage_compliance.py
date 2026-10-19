"""合规门禁（spec §S8.5）的**回归测试**：词表下限、扫描面齐全、体检文案 0 命中。

为什么给一个"脚本"配测试：脚本只有被人想起来才会跑，而"0 命中"是合规结论——一旦哪天
文案里混进判断词、或扫描目标被改名导致"扫 0 个文件也算通过"，没人会发现。把脚本本身
接进 pytest 之后，`uv run pytest` 就是那道门禁（`scripts/check_triage_compliance.py` 仍可
单独跑，输出更适合人读）。
"""

from __future__ import annotations

from scripts import check_triage_compliance as chk
from scripts import triage_compliance as tc

#: spec §S8.5 明写的 20 个词（词表可以更多，但一个都不许少）
REQUIRED_WORDS = {
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
}


def test_banned_words_cover_spec_minimum() -> None:
    """词表 ≥20 且覆盖 spec 逐字列出的 20 个（放宽合规边界必须显式改这里 + 过评审）。"""
    assert len(tc.BANNED_WORDS) >= tc.MIN_WORDS == 20
    assert REQUIRED_WORDS <= set(tc.BANNED_WORDS), f"少了：{REQUIRED_WORDS - set(tc.BANNED_WORDS)}"
    assert len(set(tc.BANNED_WORDS)) == len(tc.BANNED_WORDS), "词表有重复项"


def test_scan_targets_all_exist() -> None:
    """扫描面必须全部存在：目标改名/删除会让扫描静默变成"0 个文件 → 通过"。"""
    assert tc.missing_targets(chk.REPO_ROOT, chk.TARGETS) == []


def test_triage_copy_has_zero_banned_words() -> None:
    """体检的全部文案面（前端组件/页面/e2e fixture/API 常量/探测器 headline）0 命中。"""
    report = tc.scan_paths(chk.REPO_ROOT, chk.TARGETS)
    assert report.files >= len(chk.TARGETS) - 1, f"只扫到 {report.files} 个文件，扫描面可能失效"
    assert report.ok, "命中禁用词：\n" + "\n".join(str(hit) for hit in report.hits)


def test_scanner_detects_hits_and_honours_allow_marker() -> None:
    """扫描器自身的行为：命中要报、豁免行要跳过（否则"0 命中"可能来自扫描器失效）。"""
    hits, allowed = tc.scan_text("这只必涨\n// compliance-allow: 断言清单\n今天你的自选都平静")
    assert [hit.word for hit in hits] == ["必涨"]
    assert [hit.line_no for hit in hits] == [1]
    assert allowed == ["<text>:2"]
