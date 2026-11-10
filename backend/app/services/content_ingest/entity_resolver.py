"""从文本解析 A 股实体（``entity_type='stock'``，``entity_id`` = 6 位代码）。

纯函数，无网络无数据库：名称 → 代码的映射由调用方传入（来自 ``stocks`` 表的
``symbol`` / ``name``）。输出可直接写入 ``content_entities``。

两条判据：
1. 代码优先：先按 A 股代码前缀正则收集，保证 ``西麦食品(002956)`` 这种带代码的
   文本一定命中，且顺序即文本顺序。
2. 名称最长优先：``{"平安": "000001", "平安银行": "000001"}`` 下，文本里的
   ``平安银行`` 只能由更长的那个名字消化，否则 ``平安`` 会吃进子串并产出伪实体；
   已被更长名字占用的 span 一律跳过。
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping

# 沪市主板 60 / 科创板 68 / 深市主板 00 / 创业板 30 / 新三板 43·83·87 / 北交所 92
_CODE_RE = re.compile(r"(?<!\d)((?:60|68|00|30|43|83|87|92)\d{4})(?!\d)")

Span = tuple[int, int]


def _overlaps(span: Span, taken: list[Span]) -> bool:
    start, end = span
    return any(start < t_end and t_start < end for t_start, t_end in taken)


def _iter_occurrences(text: str, name: str) -> Iterator[Span]:
    start = text.find(name)
    while start != -1:
        yield start, start + len(name)
        start = text.find(name, start + 1)


def resolve_stock_entities(
    text: str, name_to_symbol: Mapping[str, str]
) -> list[tuple[str, str]]:
    """Return deduplicated [("stock", symbol)] for A-share codes/names in text.

    ``text`` 为空或没有命中时返回 ``[]``。代码命中排在名称命中之前。
    """
    if not text:
        return []

    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    for match in _CODE_RE.finditer(text):
        symbol = match.group(1)
        if symbol not in seen:
            seen.add(symbol)
            found.append(("stock", symbol))

    taken: list[Span] = []
    for name in sorted(name_to_symbol, key=lambda n: (-len(n), n)):
        if len(name) < 2:
            continue
        for span in _iter_occurrences(text, name):
            if _overlaps(span, taken):
                continue
            taken.append(span)
            symbol = name_to_symbol[name]
            if symbol not in seen:
                seen.add(symbol)
                found.append(("stock", symbol))

    return found
