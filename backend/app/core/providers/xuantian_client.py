# backend/app/core/providers/xuantian_client.py
"""玄田数据（中国养猪网）产能数据客户端 — xt.yangzhu.vip 直连.

2026-09-29 实机验证：POST getmapdata?ptype=7 返回 2009→now 产能四指标
（能繁母猪存栏/猪肉产量/生猪存栏/生猪出栏）。数据本体为统计局口径（年度/季度
绝对数），月度行为"季度基数+农业农村部环比推算"。零新增依赖（httpx）。

Usage::

    from app.core.providers.xuantian_client import get_xuantian_client
    rows = await get_xuantian_client().fetch_capacity()
"""

from __future__ import annotations

import calendar
import logging
import re
from datetime import date

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://xt.yangzhu.vip/data/getmapdata"
_HEADERS = {
    "Referer": "https://zhujia.zhuwang.com.cn/",
    "Origin": "https://zhujia.zhuwang.com.cn",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}
_TIMEOUT = 15.0

# 列序固定：[周期, 能繁母猪存栏, 猪肉产量, 生猪存栏, 生猪出栏]（实机验证）
CAPACITY_COLUMNS = ("sow_inventory", "pork_output", "hog_inventory", "hog_slaughter_quarterly")

_YEAR_RE = re.compile(r"^(\d{4})$")
_QUARTER_RE = re.compile(r"^(\d{4})年([一二三四1-4])季度")
_MONTH_RE = re.compile(r"^(\d{4})年(\d{1,2})月")
_QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}


def _parse_period(raw: str) -> tuple[date, str]:
    """周期串 → (period 截止日, freq)；无法解析抛 ValueError."""
    s = raw.strip()
    if m := _YEAR_RE.match(s):
        return date(int(m.group(1)), 12, 31), "yearly"
    if m := _QUARTER_RE.match(s):
        q = m.group(2)
        qn = {"一": 1, "二": 2, "三": 3, "四": 4}.get(q, q)
        month, day = _QUARTER_END[int(qn)]
        return date(int(m.group(1)), month, day), "quarterly"
    if m := _MONTH_RE.match(s):
        y, mo = int(m.group(1)), int(m.group(2))
        return date(y, mo, calendar.monthrange(y, mo)[1]), "monthly"
    raise ValueError(f"unparseable period: {raw!r}")


def parse_capacity_rows(data: list) -> list[dict]:
    """原始 5 列行 → [{period, freq, values:{metric_key: 非零值}}]；整行无效则丢弃."""
    out: list[dict] = []
    for row in data:
        try:
            period, freq = _parse_period(str(row[0]))
        except ValueError:
            logger.warning("xuantian unparseable period %r (row skipped)", row[0])
            continue
        values: dict[str, float] = {}
        for i, key in enumerate(CAPACITY_COLUMNS, start=1):
            v = float(row[i])
            if v > 0:  # 0 = 哨兵（月度行未覆盖列），不是观测
                values[key] = v
        if values:
            out.append(
                {"period": period, "freq": freq, "values": values, "raw_period": str(row[0])}
            )
    return out


class XuantianClient:
    """httpx 直连（UA + 15s 超时）；任何失败 log warning 返回 None（不抛穿，CAAA 同约定）."""

    async def fetch_capacity(self) -> list[dict] | None:
        try:
            async with httpx.AsyncClient(headers=_HEADERS, timeout=_TIMEOUT) as client:
                resp = await client.post(_BASE, params={"ptype": "7", "areano": "-1"})
                resp.raise_for_status()
                payload = resp.json()
            if payload.get("code") != 200:
                logger.warning("xuantian non-200 code %s (skipped)", payload.get("code"))
                return None
            return parse_capacity_rows(payload["data"])
        except Exception as exc:
            logger.warning("xuantian capacity fetch failed (skipped): %s", exc)
            return None


_default_client: XuantianClient | None = None


def get_xuantian_client() -> XuantianClient:
    """Return the module-level singleton ``XuantianClient`` (lazy init)."""
    global _default_client
    if _default_client is None:
        _default_client = XuantianClient()
    return _default_client
