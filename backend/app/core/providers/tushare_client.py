"""TuShare Pro API client.

Wraps the synchronous ``tushare`` library with async helpers, retry logic,
and rate-limiting suitable for the 2000-credit tier (50 req/min).

API reference: docs/references/tushare/index.md

Usage::

    from app.core.providers.tushare_client import get_tushare_client

    client = get_tushare_client()
    df = await client.fetch_stock_basic("Shanghai_Stocks")
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pandas as pd

from app.core.providers.rate_limited import RateLimitedSyncProvider

logger = logging.getLogger(__name__)

EXCHANGE_TO_TUSHARE: dict[str, str] = {
    "Shanghai_Stocks": "SSE",
    "Shenzen_Stocks": "SZSE",
    "Beijing_Stocks": "BSE",
}

TUSHARE_TO_EXCHANGE: dict[str, str] = {v: k for k, v in EXCHANGE_TO_TUSHARE.items()}

_REQUEST_INTERVAL = 0.5  # seconds between requests (120 req/min, under 200/min limit)
_MAX_RETRIES = 3


class TuShareClient(RateLimitedSyncProvider):
    """Async-friendly wrapper around ``tushare.pro_api``.

    Throttling / retry / thread-offload live in :class:`RateLimitedSyncProvider`.
    """

    request_interval = _REQUEST_INTERVAL
    max_retries = _MAX_RETRIES

    def __init__(self, token: str, max_retries: int = _MAX_RETRIES) -> None:
        super().__init__()
        import tushare as ts

        if not token:
            logger.error("[TOKEN ERROR]: NEED VALID TOKEN.")
            raise ValueError("TuShare token is required. Set TUSHARE_TOKEN in .env")
        self._pro = ts.pro_api(token)
        self.max_retries = max(1, max_retries)

    # ------------------------------------------------------------------
    # Base-class hooks
    # ------------------------------------------------------------------

    def handle_error(self, api_name: str, exc: Exception) -> None:
        msg = str(exc)
        if "权限" in msg or "40203" in msg:
            raise RuntimeError(
                f"TuShare API '{api_name}' permission denied. "
                "Check your token credits at https://tushare.pro/document/1?doc_id=108"
            ) from exc

    def normalize_result(self, result: Any) -> Any:
        return pd.DataFrame() if result is None else result

    # ------------------------------------------------------------------
    # Low-level query with retry + rate-limiting
    # ------------------------------------------------------------------

    def _query_sync(self, api_name: str, fields: str = "", **kwargs: Any) -> pd.DataFrame:
        """Call a TuShare Pro API with retry and inter-request throttling."""
        if fields:
            return self.invoke_sync(
                api_name, lambda: self._pro.query(api_name, fields=fields, **kwargs)
            )
        return self.invoke_sync(api_name, lambda: self._pro.query(api_name, **kwargs))

    async def _query(self, api_name: str, fields: str = "", **kwargs: Any) -> pd.DataFrame:
        return await asyncio.to_thread(self._query_sync, api_name, fields=fields, **kwargs)

    # ------------------------------------------------------------------
    # Stock universe APIs
    # ------------------------------------------------------------------

    async def fetch_stock_basic(
        self,
        exchange: str,
        list_status: str = "L",
        fields: str = (
            "ts_code,symbol,name,area,industry,fullname,enname,cnspell,"
            "market,exchange,list_status,list_date,delist_date,is_hs,"
            "act_name,act_ent_type"
        ),
    ) -> pd.DataFrame:
        """Fetch basic stock info for an exchange.

        See: docs/references/tushare/stock_list.md
        """
        ts_exchange = EXCHANGE_TO_TUSHARE.get(exchange, exchange)
        return await self._query(
            "stock_basic",
            fields=fields,
            exchange=ts_exchange,
            list_status=list_status,
        )

    async def fetch_stock_company(
        self,
        exchange: str,
        fields: str = (
            "ts_code,com_name,com_id,exchange,chairman,manager,secretary,"
            "reg_capital,setup_date,province,city,introduction,website,"
            "email,employees,main_business,business_scope"
        ),
    ) -> pd.DataFrame:
        """Fetch listed-company details for an exchange.

        See: docs/references/tushare/company_basic_info.md
        """
        ts_exchange = EXCHANGE_TO_TUSHARE.get(exchange, exchange)
        return await self._query(
            "stock_company",
            fields=fields,
            exchange=ts_exchange,
        )

    async def fetch_new_share(
        self,
        start_date: str = "",
        end_date: str = "",
    ) -> pd.DataFrame:
        """Fetch IPO listing data.

        See: docs/references/tushare/IPO_stocks.md
        """
        kwargs: dict[str, str] = {}
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return await self._query("new_share", **kwargs)

    async def fetch_stk_managers(self, ts_code: str) -> pd.DataFrame:
        """Fetch company management info.

        See: docs/references/tushare/company_sharers.md
        """
        return await self._query("stk_managers", ts_code=ts_code)

    # ------------------------------------------------------------------
    # Trading data APIs
    # ------------------------------------------------------------------

    async def fetch_daily(
        self,
        trade_date: str = "",
        ts_code: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> pd.DataFrame:
        """Fetch daily OHLCV quotes.

        Prefer ``trade_date`` for batch-fetching an entire market's daily data
        (more efficient than looping over ts_code).

        See: docs/references/tushare/reference.md
        """
        kwargs: dict[str, str] = {}
        if trade_date:
            kwargs["trade_date"] = trade_date
        if ts_code:
            kwargs["ts_code"] = ts_code
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return await self._query("daily", **kwargs)

    async def fetch_trade_cal(
        self,
        exchange: str = "SSE",
        start_date: str = "",
        end_date: str = "",
        is_open: str = "1",
    ) -> pd.DataFrame:
        """Fetch trading calendar.

        See: docs/references/tushare/reference.md
        """
        kwargs: dict[str, str] = {"exchange": exchange, "is_open": is_open}
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return await self._query("trade_cal", fields="cal_date", **kwargs)

    # ------------------------------------------------------------------
    # Index data APIs
    # ------------------------------------------------------------------

    async def fetch_index_daily(
        self,
        ts_code: str = "",
        trade_date: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> pd.DataFrame:
        """Fetch index daily OHLCV (e.g. 000001.SH for 上证指数).

        Use ``trade_date`` for a snapshot of all indices on one day, or
        ``ts_code`` + date range for one index's history.
        """
        kwargs: dict[str, str] = {}
        if ts_code:
            kwargs["ts_code"] = ts_code
        if trade_date:
            kwargs["trade_date"] = trade_date
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return await self._query("index_daily", **kwargs)

    # ------------------------------------------------------------------
    # Daily metrics API
    # ------------------------------------------------------------------

    async def fetch_daily_basic(
        self,
        ts_code: str = "",
        trade_date: str = "",
        start_date: str = "",
        end_date: str = "",
        fields: str = (
            "ts_code,trade_date,close,turnover_rate,volume_ratio,"
            "pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,"
            "total_share,float_share,free_share,total_mv,circ_mv"
        ),
    ) -> pd.DataFrame:
        """Fetch daily fundamental indicators (PE/PB/market cap etc.).

        See: docs/references/tushare/每日指标.md
        """
        kwargs: dict[str, str] = {}
        if ts_code:
            kwargs["ts_code"] = ts_code
        if trade_date:
            kwargs["trade_date"] = trade_date
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return await self._query("daily_basic", fields=fields, **kwargs)

    # ------------------------------------------------------------------
    # Shenwan industry classification APIs
    # ------------------------------------------------------------------

    async def fetch_index_classify(
        self,
        level: str = "",
        src: str = "SW",
    ) -> pd.DataFrame:
        """Fetch Shenwan industry classification tree.

        ``level``: L1 / L2 / L3.  ``src``: SW (申万).
        Returns: index_code, industry_name, level, etc.
        """
        kwargs: dict[str, str] = {"src": src}
        if level:
            kwargs["level"] = level
        return await self._query("index_classify", **kwargs)

    async def fetch_index_member(
        self,
        index_code: str = "",
        ts_code: str = "",
    ) -> pd.DataFrame:
        """Fetch constituent stocks of an index (Shenwan or exchange index).

        Either ``index_code`` (e.g. '850531.SI') or ``ts_code`` can be used.
        """
        kwargs: dict[str, str] = {}
        if index_code:
            kwargs["index_code"] = index_code
        if ts_code:
            kwargs["ts_code"] = ts_code
        return await self._query("index_member", **kwargs)

    async def fetch_stk_limit(
        self,
        trade_date: str = "",
        ts_code: str = "",
    ) -> pd.DataFrame:
        """Fetch daily price limit info (涨跌停价格).

        See: docs/references/tushare/每日涨停价格.md
        """
        kwargs: dict[str, str] = {}
        if trade_date:
            kwargs["trade_date"] = trade_date
        if ts_code:
            kwargs["ts_code"] = ts_code
        return await self._query("stk_limit", **kwargs)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_default_client: TuShareClient | None = None


def get_tushare_client() -> TuShareClient:
    """Return the module-level singleton ``TuShareClient`` (lazy init)."""
    global _default_client
    if _default_client is None:
        from app.config import settings

        _default_client = TuShareClient(settings.tushare_token)
    return _default_client
