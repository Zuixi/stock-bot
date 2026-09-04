"""Eastmoney push2 HTTP client (batch-only endpoints, throttled).

Verified endpoints (2026-09-03):
- ulist.np/get on push2delay (push2 proper returns empty in this environment)
- clist/get on push2delay for sector money flow
  (push2 began refusing connections mid-day 2026-09-03)
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_EM_UT = "fa5fd1943c7b386f172d6893dbfba10b"
_SNAPSHOT_BASE = "https://push2delay.eastmoney.com"
# push2 主站在本环境已开始拒连（host/容器均 000 disconnect，2026-09-03 下午实测）；
# clist 端点在 push2delay 同构可用（字段/数据一致），改走 delay 域保住盘中轮询可用性。
_CLIST_BASE = "https://push2delay.eastmoney.com"
_HIS_BASE = "https://push2his.eastmoney.com"
_HIS_UT = "b2884a393a59ad64002292a3e90d46a5"  # push2his 历史端点固定 ut
_MIN_INTERVAL = 0.3


def _kf(v: str) -> float | None:
    """fflow kline CSV 列 → float；空串/非法值 → None。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _num(v: Any) -> float | None:
    """东财 fltt=2 下无效值为字符串 '-'。"""
    if v is None or isinstance(v, str):
        return None
    return float(v)


class EastmoneyClient:
    """节流 + UA 的东财只读客户端；仅批量端点，杜绝逐股轮询。"""

    def __init__(self) -> None:
        self._last_call = 0.0
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(
            headers={"User-Agent": _UA, "Referer": "https://quote.eastmoney.com/"},
            timeout=httpx.Timeout(10.0),
        )

    async def _get_json(self, base: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            wait = _MIN_INTERVAL - (time.monotonic() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()
        # 长连接池偶发陈旧连接被服务端断开（RemoteProtocolError）——重试一次
        try:
            resp = await self._client.get(base + path, params=params)
        except httpx.TransportError:
            await asyncio.sleep(0.5)
            resp = await self._client.get(base + path, params=params)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        if data.get("rc") not in (0, None):
            raise RuntimeError(f"eastmoney rc={data.get('rc')} path={path}")
        return data

    async def fetch_index_snapshot(self, secids: list[str]) -> list[dict[str, Any]]:
        data = await self._get_json(
            _SNAPSHOT_BASE,
            "/api/qt/ulist.np/get",
            {
                "ut": _EM_UT, "fltt": 2, "invt": 2, "np": 1,
                "fields": "f2,f3,f4,f12,f13,f14",
                "secids": ",".join(secids),
            },
        )
        diff = (data.get("data") or {}).get("diff") or []
        return [
            {
                "code": d.get("f12"),
                "name": d.get("f14"),
                "price": _num(d.get("f2")),
                "pct_change": _num(d.get("f3")),
                "change": _num(d.get("f4")),
            }
            for d in diff
        ]

    async def fetch_sector_moneyflow(self, dimension: str) -> list[dict[str, Any]]:
        if dimension not in ("industry", "concept", "region"):
            raise ValueError(f"dimension must be industry|concept|region, got {dimension}")
        fs = {
            "industry": "m:90+t:2+f:!50",
            "concept": "m:90+t:3+f:!50",
            "region": "m:90+t:1+f:!50",
        }[dimension]
        data = await self._get_json(
            _CLIST_BASE,
            "/api/qt/clist/get",
            {
                "pn": 1, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                "fid": "f62", "fs": fs,
                "fields": "f12,f14,f3,f62,f66,f72,f104,f105,f128,f136,f140,f184",
            },
        )
        diff = (data.get("data") or {}).get("diff") or []
        return [
            {
                "board_code": str(d.get("f12")),
                "board_name": d.get("f14"),
                "pct_change": _num(d.get("f3")),
                "main_net_inflow": _num(d.get("f62")),
                "super_large_net": _num(d.get("f66")),
                "large_net": _num(d.get("f72")),
                "up_count": d.get("f104"),
                "down_count": d.get("f105"),
                "main_net_ratio": _num(d.get("f184")),
                # data.eastmoney.com/bkzj/ 排行页同款"主力净流入最大股"
                "lead_stock_name": d.get("f128"),
                "lead_stock_code": d.get("f140"),
                "lead_stock_pct": _num(d.get("f136")),
            }
            for d in diff
        ]

    async def fetch_market_moneyflow_today(self) -> dict[str, Any]:
        """沪深两市今日四档资金流合计 + 分市场主力（ulist，secids=1.000001,0.399001）。"""
        data = await self._get_json(
            _SNAPSHOT_BASE,
            "/api/qt/ulist.np/get",
            {
                "ut": _EM_UT, "fltt": 2, "invt": 2, "np": 1,
                "fields": "f12,f14,f62,f66,f72,f78,f84,f184",
                "secids": "1.000001,0.399001",
            },
        )
        diff = (data.get("data") or {}).get("diff") or []
        markets: list[dict[str, Any]] = []
        for d in diff:
            if _num(d.get("f62")) is None:
                continue
            markets.append({
                "code": d.get("f12"), "name": d.get("f14"),
                "main_net": _num(d.get("f62")), "super_large_net": _num(d.get("f66")),
                "large_net": _num(d.get("f72")), "mid_net": _num(d.get("f78")),
                "small_net": _num(d.get("f84")), "main_ratio": _num(d.get("f184")),
            })

        def _sum(field: str) -> float | None:
            if not markets:
                return None
            return round(sum(m[field] or 0.0 for m in markets), 2)

        return {
            "total": {
                "main_net": _sum("main_net"), "super_large_net": _sum("super_large_net"),
                "large_net": _sum("large_net"), "mid_net": _sum("mid_net"),
                "small_net": _sum("small_net"),
            },
            "markets": markets,
        }

    async def fetch_market_moneyflow_daily(self, days: int) -> list[dict[str, Any]]:
        """沪深两市大盘资金流历史日线（fflow/daykline，双 secid 服务端合成）。

        kline 行序：日期, 主力, 小单, 中单, 大单, 超大, 五档占比×5, 收盘, 涨跌幅, 成交额(亿), …
        恒等式：主力 = 大单 + 超大单（映射处校验，破式记 warning 并跳过该行）。
        """
        data = await self._get_json(
            _HIS_BASE,
            "/api/qt/stock/fflow/daykline/get",
            {
                "lmt": days, "klt": 101, "ut": _HIS_UT,
                "fields1": "f1,f2,f3,f7",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
                "secid": "1.000001", "secid2": "0.399001",
            },
        )
        klines = (data.get("data") or {}).get("klines") or []
        rows: list[dict[str, Any]] = []
        for line in klines:
            p = line.split(",")
            if len(p) < 14:
                continue
            # kline 行是 CSV 字符串（_num 只认已类型化的 JSON 值），须显式 float()
            main_net, large_net, super_net = _kf(p[1]), _kf(p[4]), _kf(p[5])
            if main_net is not None and large_net is not None and super_net is not None:
                if abs((large_net + super_net) - main_net) > 1.0:
                    logger.warning("fflow daykline identity broken, skip: %s", p[0])
                    continue
            amount = _kf(p[13])
            rows.append({
                "trade_date": datetime.strptime(p[0], "%Y-%m-%d").date(),
                "main_net": main_net, "small_net": _kf(p[2]), "mid_net": _kf(p[3]),
                "large_net": large_net, "super_large_net": super_net,
                "main_ratio": _kf(p[6]), "close": _kf(p[11]), "pct_change": _kf(p[12]),
                "amount": amount * 1e8 if amount is not None else None,  # 源为亿元
            })
        return rows


_client: EastmoneyClient | None = None


def get_eastmoney_client() -> EastmoneyClient:
    global _client  # noqa: PLW0603
    if _client is None:
        _client = EastmoneyClient()
    return _client
