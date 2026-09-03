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
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_EM_UT = "fa5fd1943c7b386f172d6893dbfba10b"
_SNAPSHOT_BASE = "https://push2delay.eastmoney.com"
# push2 主站在本环境已开始拒连（host/容器均 000 disconnect，2026-09-03 下午实测）；
# clist 端点在 push2delay 同构可用（字段/数据一致），改走 delay 域保住盘中轮询可用性。
_CLIST_BASE = "https://push2delay.eastmoney.com"
_MIN_INTERVAL = 0.3


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
        if dimension not in ("industry", "concept"):
            raise ValueError(f"dimension must be industry|concept, got {dimension}")
        fs = "m:90+t:2+f:!50" if dimension == "industry" else "m:90+t:3+f:!50"
        data = await self._get_json(
            _CLIST_BASE,
            "/api/qt/clist/get",
            {
                "pn": 1, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                "fid": "f62", "fs": fs,
                "fields": "f12,f14,f3,f62,f66,f72,f104,f105,f184",
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
            }
            for d in diff
        ]


_client: EastmoneyClient | None = None


def get_eastmoney_client() -> EastmoneyClient:
    global _client  # noqa: PLW0603
    if _client is None:
        _client = EastmoneyClient()
    return _client
