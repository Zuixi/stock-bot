"""CNINFO (巨潮资讯) WebAPI client.

Implements the following CNINFO WebAPI endpoints:

Stock quotes (sysapi)
  - p_sysapi1015 : 个股日行情（单股单日）

Index APIs  (api/index/)  ← docs/references/cninfo/指数API/
  - p_index2905 : 交易所指数日行情
  - p_index2911 : 交易所指数基本信息
  - p_swindex   : 申万指数行情

Authentication
--------------
All endpoints require an ``mcode`` header.  ``mcode`` is the current Unix
timestamp **in seconds** (10 digits) encoded with the ``missjson`` custom
Base64 algorithm that is embedded in the CNINFO frontend JS.

Paid endpoints additionally require a ``token`` (or ``access_token``) form /
query parameter.  Set the ``CNINFO_TOKEN`` environment variable or pass
``token=`` to ``CnInfoClient`` to enable paid access.

See: https://webapi.cninfo.com.cn/#/apiDoc
API docs: docs/references/cninfo/指数API/
Backend docs: backend/docs/cninfo_api.md
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import date, timedelta
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_CNINFO_SYSAPI_URL = "http://webapi.cninfo.com.cn/api/sysapi"
_CNINFO_INDEX_URL = "http://webapi.cninfo.com.cn/api/index"
_MCODE_KEY = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
_DEFAULT_HEADERS = {
    "Referer": "http://webapi.cninfo.com.cn/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}
_REQUEST_DELAY_SECONDS = 0.25

# Index code → frontend exchange label
_INDEX_EXCHANGE_MAP: dict[str, str] = {
    "000001": "Shanghai_Stocks",
    "000016": "Shanghai_Stocks",
    "000300": "Shanghai_Stocks",
    "000905": "Shanghai_Stocks",
    "399001": "Shenzen_Stocks",
    "399006": "Shenzen_Stocks",
    "899050": "Beijing_Stocks",
}
# Human-readable names used as fallback when INDEXNAME is absent
_INDEX_NAME_FALLBACK: dict[str, str] = {
    "000001": "上证指数",
    "000016": "上证50",
    "000300": "沪深300",
    "000905": "中证500",
    "399001": "深证成指",
    "399006": "创业板指",
    "899050": "北证50",
}


# ---------------------------------------------------------------------------
# mcode generation
# ---------------------------------------------------------------------------

def _generate_mcode() -> str:
    """Pure-Python equivalent of the JS ``missjson(String(Math.floor(Date.now()/1000)))`` call.

    JS uses ``Date.getTime() / 1000`` (ms → s).
    Python's ``time.time()`` already returns seconds — no division needed.

    The algorithm encodes the decimal digits of the Unix-second timestamp
    using a custom 6-bit Base64 table, processing three characters at a time.
    """
    ts = str(int(time.time()))
    data = [ord(c) for c in ts]
    output: list[str] = []
    i = 0
    length = len(data)
    while i < length:
        c1 = data[i]; i += 1
        c2 = data[i] if i < length else None; i += 1
        c3 = data[i] if i < length else None; i += 1

        e1 = c1 >> 2
        e2 = ((c1 & 3) << 4) | ((c2 >> 4) if c2 is not None else 0)
        if c2 is None:
            e3 = e4 = 64
        elif c3 is None:
            e3 = (c2 & 15) << 2
            e4 = 64
        else:
            e3 = ((c2 & 15) << 2) | (c3 >> 6)
            e4 = c3 & 63

        output.extend([_MCODE_KEY[e1], _MCODE_KEY[e2], _MCODE_KEY[e3], _MCODE_KEY[e4]])

    return "".join(output)


# ---------------------------------------------------------------------------
# Response parsers
# ---------------------------------------------------------------------------

def _safe_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_int(v: Any) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _parse_stock_quote_record(record: dict[str, Any]) -> dict[str, Any]:
    """Normalise a raw ``p_sysapi1015`` record to snake_case keys."""
    raw_date = str(record.get("TDATE", "")).strip()
    trade_date: date | None = None
    if len(raw_date) == 8:
        try:
            trade_date = date(int(raw_date[:4]), int(raw_date[4:6]), int(raw_date[6:8]))
        except ValueError:
            pass
    return {
        "symbol": str(record.get("ZQDM", "")).strip(),
        "name": str(record.get("ZQJC", "")).strip(),
        "trade_date": trade_date,
        "open": _safe_float(record.get("OPRICE")),
        "high": _safe_float(record.get("HPRICE")),
        "low": _safe_float(record.get("LPRICE")),
        "close": _safe_float(record.get("CPRICE")),
        "volume": _safe_int(record.get("CJSL")),
        "amount": _safe_float(record.get("CJJE")),
        "source": "cninfo:p_sysapi1015",
    }


def _parse_index_record(record: dict[str, Any]) -> dict[str, Any]:
    """Normalise a raw ``p_index2905`` / ``p_swindex`` record.

    Field mapping (from docs/references/cninfo/指数API/):
      TRADEDATE → trade_date
      INDEXCODE → code
      INDEXNAME → name
      F001V     → exchange (raw, e.g. "上交所")
      F003N     → open
      F004N     → high
      F005N     → low
      F006N     → close  (最近指数)
      F007N     → prev_close (昨日收市指数)
      F008N     → volume
      F009N     → trades
      F010N     → amount
    """
    raw_date = str(record.get("TRADEDATE", "")).strip()
    trade_date: date | None = None
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            from datetime import datetime  # noqa: PLC0415
            trade_date = datetime.strptime(raw_date, fmt).date()
            break
        except ValueError:
            pass

    code = str(record.get("INDEXCODE", "")).strip()
    close = _safe_float(record.get("F006N"))
    prev_close = _safe_float(record.get("F007N"))

    change: float | None = None
    change_pct: float | None = None
    if close is not None and prev_close is not None and prev_close != 0:
        change = round(close - prev_close, 4)
        change_pct = round((close - prev_close) / prev_close * 100, 4)

    raw_exchange = str(record.get("F001V", "")).strip()
    exchange = _INDEX_EXCHANGE_MAP.get(code, _map_exchange_name(raw_exchange))

    return {
        "code": code,
        "name": str(record.get("INDEXNAME", _INDEX_NAME_FALLBACK.get(code, code))).strip(),
        "trade_date": trade_date,
        "open": _safe_float(record.get("F003N")),
        "high": _safe_float(record.get("F004N")),
        "low": _safe_float(record.get("F005N")),
        "close": close,
        "prev_close": prev_close,
        "change": change,
        "changePercent": change_pct,
        "volume": _safe_int(record.get("F008N")),
        "trades": _safe_int(record.get("F009N")),
        "amount": _safe_float(record.get("F010N")),
        "exchange": exchange,
        "source": "cninfo:p_index2905",
    }


def _map_exchange_name(raw: str) -> str:
    """Map CNINFO exchange label to internal canonical name."""
    mapping = {
        "上交所": "Shanghai_Stocks",
        "深交所": "Shenzen_Stocks",
        "北交所": "Beijing_Stocks",
        "上海证券交易所": "Shanghai_Stocks",
        "深圳证券交易所": "Shenzen_Stocks",
        "北京证券交易所": "Beijing_Stocks",
    }
    return mapping.get(raw, raw)


# ---------------------------------------------------------------------------
# CnInfoClient
# ---------------------------------------------------------------------------

class CnInfoClient:
    """Async HTTP client for the CNINFO WebAPI.

    Endpoints implemented
    ---------------------
    Stock quotes (``/api/sysapi/``):
      - ``p_sysapi1015`` — 个股日行情（requires CNINFO_TOKEN since 2024+）

    Index APIs (``/api/index/``, ref: docs/references/cninfo/指数API/):
      - ``p_index2905`` — 交易所指数日行情
      - ``p_index2911`` — 交易所指数基本信息
      - ``p_swindex``   — 申万指数行情

    Authentication
    --------------
    All endpoints need ``mcode`` header (auto-generated).
    Paid endpoints additionally need ``token`` / ``access_token``.
    Set ``CNINFO_TOKEN`` env var or pass ``token=`` to this class.

    Usage::

        async with CnInfoClient() as client:
            indices = await client.get_index_daily(edate=date.today())
            sw = await client.get_sw_index(edate=date.today())
    """

    def __init__(
        self,
        request_delay: float = _REQUEST_DELAY_SECONDS,
        timeout: float = 15.0,
        token: str | None = None,
    ) -> None:
        import os  # noqa: PLC0415

        self._request_delay = request_delay
        self._timeout = timeout
        self._token: str = token or os.environ.get("CNINFO_TOKEN", "")
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "CnInfoClient":
        await self._ensure_client()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    def _headers(self) -> dict[str, str]:
        return {**_DEFAULT_HEADERS, "mcode": _generate_mcode()}

    def _auth_params(self) -> dict[str, str]:
        """Return token param dict if a token is configured."""
        return {"access_token": self._token} if self._token else {}

    async def _get_index(
        self, endpoint: str, params: dict[str, str]
    ) -> list[dict[str, Any]]:
        """Generic GET helper for /api/index/* endpoints."""
        client = await self._ensure_client()
        url = f"{_CNINFO_INDEX_URL}/{endpoint}"
        all_params = {**params, **self._auth_params(), "format": "json"}
        try:
            response = await client.get(url, params=all_params, headers=self._headers())
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "cninfo %s HTTP error status=%s", endpoint, exc.response.status_code
            )
            return []
        except Exception as exc:
            logger.warning("cninfo %s request failed: %s", endpoint, exc)
            return []

        result_code = str(body.get("resultcode", ""))
        if result_code != "200":
            msg = body.get("resultmsg", "")
            if "token" in str(msg).lower() or result_code in ("401", "451"):
                logger.warning(
                    "cninfo %s auth error (code=%s): %s. "
                    "Set CNINFO_TOKEN env var. Register at https://webapi.cninfo.com.cn",
                    endpoint, result_code, msg,
                )
            else:
                logger.warning("cninfo %s error code=%s msg=%s", endpoint, result_code, msg)
            return []

        records = body.get("records") or []
        return [_parse_index_record(r) for r in records]

    # ------------------------------------------------------------------
    # Index APIs — p_index2905 / p_index2911 / p_swindex
    # ------------------------------------------------------------------

    async def get_index_daily(
        self,
        edate: date,
        scode: str | None = None,
        sdate: date | None = None,
        market: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch exchange index daily quotes via ``p_index2905``.

        Ref: docs/references/cninfo/指数API/交易所指数日行情API.md

        Parameters
        ----------
        edate:
            End date (required). Format: YYYY-MM-DD or YYYYMMDD.
        scode:
            Single index code (e.g. ``"000300"``).  Leave empty to get all
            indices for that date.
        sdate:
            Start date for a range query.
        market:
            ``"上交所"`` or ``"深交所"``.  Leave empty for all markets.
        """
        params: dict[str, str] = {"edate": edate.strftime("%Y-%m-%d")}
        if scode:
            params["scode"] = scode
        if sdate:
            params["sdate"] = sdate.strftime("%Y-%m-%d")
        if market:
            params["market"] = market
        records = await self._get_index("p_index2905", params)
        # Tag source correctly
        for r in records:
            r["source"] = "cninfo:p_index2905"
        return records

    async def get_sw_index(
        self,
        edate: date,
        scode: str | None = None,
        sdate: date | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch Shenwan (申万) industry index quotes via ``p_swindex``.

        Ref: docs/references/cninfo/指数API/申万指数行情.md

        Parameters
        ----------
        edate:
            End date (required).
        scode:
            Single SW index code.  Leave empty to get all SW indices for
            that date.
        sdate:
            Start date for a range query.
        """
        params: dict[str, str] = {"edate": edate.strftime("%Y-%m-%d")}
        if scode:
            params["scode"] = scode
        if sdate:
            params["sdate"] = sdate.strftime("%Y-%m-%d")
        records = await self._get_index("p_swindex", params)
        for r in records:
            r["source"] = "cninfo:p_swindex"
        return records

    async def get_index_info(self, scode: str | None = None) -> list[dict[str, Any]]:
        """Fetch exchange index basic info via ``p_index2911``.

        Ref: docs/references/cninfo/指数API/交易所指数基本信息.md
        """
        params: dict[str, str] = {}
        if scode:
            params["scode"] = scode
        client = await self._ensure_client()
        url = f"{_CNINFO_INDEX_URL}/p_index2911"
        all_params = {**params, **self._auth_params(), "format": "json"}
        try:
            response = await client.get(url, params=all_params, headers=self._headers())
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            logger.warning("cninfo p_index2911 request failed: %s", exc)
            return []
        records = body.get("records") or []
        return list(records)

    # ------------------------------------------------------------------
    # Stock quote API — p_sysapi1015
    # ------------------------------------------------------------------

    async def get_daily_quote(
        self, symbol: str, trade_date: date
    ) -> dict[str, Any] | None:
        """Fetch a single stock's OHLCV for one trading day via ``p_sysapi1015``.

        Returns a normalised dict or ``None`` if the date is a non-trading day,
        the request fails, or token auth is required but not configured.
        """
        client = await self._ensure_client()
        tdate = trade_date.strftime("%Y%m%d")
        url = f"{_CNINFO_SYSAPI_URL}/p_sysapi1015"
        form_data: dict[str, str] = {"tdate": tdate, "scode": symbol}
        if self._token:
            form_data["token"] = self._token
        try:
            response = await client.post(
                url, data=form_data, headers=self._headers()
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "cninfo p_sysapi1015 HTTP error symbol=%s date=%s status=%s",
                symbol, tdate, exc.response.status_code,
            )
            return None
        except Exception as exc:
            logger.warning(
                "cninfo p_sysapi1015 request failed symbol=%s date=%s: %s",
                symbol, tdate, exc,
            )
            return None

        result_code = str(body.get("resultcode", ""))
        if result_code != "200":
            msg = body.get("resultmsg", "")
            if "token" in str(msg).lower() or result_code in ("401", "451"):
                logger.warning(
                    "cninfo p_sysapi1015 auth error (code=%s): %s. "
                    "Set CNINFO_TOKEN env var. Register at https://webapi.cninfo.com.cn",
                    result_code, msg,
                )
            else:
                logger.warning(
                    "cninfo p_sysapi1015 non-200 symbol=%s date=%s code=%s msg=%s",
                    symbol, tdate, result_code, msg,
                )
            return None

        records = body.get("records") or []
        if not records:
            return None
        return _parse_stock_quote_record(records[0])

    async def get_daily_quotes_range(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> list[dict[str, Any]]:
        """Fetch OHLCV for every calendar day in [start, end] via ``p_sysapi1015``.

        Non-trading days silently return no data.
        Inserts ``_request_delay`` between requests to respect rate limits.
        """
        results: list[dict[str, Any]] = []
        current = start
        while current <= end:
            record = await self.get_daily_quote(symbol, current)
            if record is not None and record.get("trade_date") is not None:
                results.append(record)
            if current < end:
                await asyncio.sleep(self._request_delay)
            current += timedelta(days=1)
        return results


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_default_client: CnInfoClient | None = None


def get_cninfo_client() -> CnInfoClient:
    """Return the module-level singleton ``CnInfoClient`` (lazy init).

    For testing, replace ``cninfo_client._default_client`` with a mock.
    """
    global _default_client
    if _default_client is None:
        _default_client = CnInfoClient()
    return _default_client
