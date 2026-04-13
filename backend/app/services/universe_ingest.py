"""Universe ingest helpers: exchange crawlers + AKShare/yfinance enrichment."""

from __future__ import annotations

import asyncio
import logging
import math
import random
import sys
import threading
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

EXCHANGE_TO_CANONICAL = {
    "sse": "Shanghai_Stocks",
    "szse": "Shenzen_Stocks",
    "bse": "Beijing_Stocks",
}

CANONICAL_TO_SUFFIX = {
    "Shanghai_Stocks": ".SS",
    "Shenzen_Stocks": ".SZ",
    "Beijing_Stocks": ".BJ",
}

_PATH_LOCK = threading.Lock()


def normalize_exchange(exchange: str) -> tuple[str, str]:
    key = exchange.strip().lower()
    if key in EXCHANGE_TO_CANONICAL:
        return key, EXCHANGE_TO_CANONICAL[key]
    for short, canonical in EXCHANGE_TO_CANONICAL.items():
        if canonical.lower() == key:
            return short, canonical
    raise ValueError(f"Unsupported exchange: {exchange}")


def parse_listing_date(value: str | date | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _to_builtin(value: Any) -> Any:
    """Convert numpy/pandas scalars to plain Python types."""
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (str, int, float, bool, dict, list)):
        if isinstance(value, dict):
            return {str(k): _to_builtin(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_to_builtin(v) for v in value]
        return value
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return str(value)
    return str(value)


def _infer_exchange_from_symbol(symbol: str) -> str | None:
    if symbol.startswith(("600", "601", "603", "605", "688", "689", "900")):
        return "Shanghai_Stocks"
    if symbol.startswith(("000", "001", "002", "003", "300", "301", "200")):
        return "Shenzen_Stocks"
    if symbol.startswith(("4", "8")):
        return "Beijing_Stocks"
    return None


def _extract_detail_common_fields(detail: dict[str, Any]) -> dict[str, Any]:
    normalized = {str(k).strip().lower(): v for k, v in detail.items() if k is not None}
    return {
        "full_name": (
            normalized.get("股票简称")
            or normalized.get("公司名称")
            or normalized.get("longname")
            or normalized.get("shortname")
        ),
        "province": normalized.get("所属地域") or normalized.get("province"),
        "csrc_desc": normalized.get("行业") or normalized.get("industry"),
        "status": normalized.get("上市状态") or normalized.get("status"),
    }


def merge_detail_into_record(record: dict[str, Any], detail: dict[str, Any] | None) -> dict[str, Any]:
    if not detail:
        return record
    detail = _trim_detail_payload(detail)
    merged = dict(record)
    summary = _extract_detail_common_fields(detail)
    for key in ("full_name", "province", "csrc_desc", "status"):
        if not merged.get(key) and summary.get(key):
            merged[key] = summary[key]
    merged["detail"] = detail
    return merged


def _trim_detail_payload(
    detail: dict[str, Any], max_items: int = 240, max_chars: int = 64_000
) -> dict[str, Any]:
    items = list(detail.items())
    if len(items) > max_items:
        detail = dict(items[:max_items]) | {"__truncated__": True}
    if len(str(detail)) <= max_chars:
        return detail
    return {
        "provider": detail.get("provider"),
        "symbol": detail.get("symbol"),
        "full_name": detail.get("longName") or detail.get("公司名称"),
        "industry": detail.get("industry") or detail.get("行业"),
        "__truncated__": True,
    }


def _ensure_project_root_in_path(project_root: Path) -> None:
    root_str = str(project_root)
    with _PATH_LOCK:
        if root_str not in sys.path:
            sys.path.insert(0, root_str)


class UniverseDataProvider:
    """Fetch stock list + stock details with anti-scraping controls."""

    def __init__(
        self,
        detail_sleep_range: tuple[float, float] = (0.08, 0.25),
        detail_retry: int = 3,
    ):
        self.detail_sleep_range = detail_sleep_range
        self.detail_retry = max(1, detail_retry)

    async def fetch_universe_records(
        self,
        exchange: str,
        stock_type: str | None = None,
        source: str = "auto",
    ) -> list[dict[str, Any]]:
        exchange_short, exchange_canonical = normalize_exchange(exchange)
        source_mode = source.lower()

        if source_mode not in {"auto", "crawler", "akshare"}:
            raise ValueError("source must be one of: auto, crawler, akshare")

        if source_mode in {"auto", "crawler"}:
            records = await self._fetch_via_crawler(exchange_short, stock_type)
            if records:
                return records
            if source_mode == "crawler":
                return []

        records = await self._fetch_via_akshare_universe(exchange_canonical)
        return records

    async def fetch_stock_detail(
        self,
        exchange: str,
        symbol: str,
        detail_source: str = "auto",
    ) -> dict[str, Any] | None:
        source_mode = detail_source.lower()
        if source_mode not in {"auto", "akshare", "yfinance"}:
            raise ValueError("detail_source must be one of: auto, akshare, yfinance")

        providers = (
            ["akshare", "yfinance"]
            if source_mode == "auto"
            else [source_mode]
        )

        for provider in providers:
            detail = await self._fetch_detail_with_retry(provider, exchange, symbol)
            if detail:
                return detail
        return None

    async def _fetch_detail_with_retry(
        self, provider: str, exchange: str, symbol: str
    ) -> dict[str, Any] | None:
        for attempt in range(1, self.detail_retry + 1):
            try:
                if provider == "akshare":
                    detail = await self._fetch_detail_akshare(symbol)
                else:
                    detail = await self._fetch_detail_yfinance(exchange, symbol)
                if detail:
                    return detail
            except Exception as exc:  # pragma: no cover - defensive network path
                logger.warning(
                    "detail fetch failed provider=%s symbol=%s attempt=%s/%s: %s",
                    provider,
                    symbol,
                    attempt,
                    self.detail_retry,
                    exc,
                )
            if attempt < self.detail_retry:
                await asyncio.sleep((2 ** (attempt - 1)) * 0.2 + random.uniform(0.05, 0.15))
        return None

    async def sleep_between_detail_requests(self) -> None:
        min_sleep, max_sleep = self.detail_sleep_range
        if min_sleep > max_sleep:
            min_sleep, max_sleep = max_sleep, min_sleep
        if max_sleep <= 0:
            return
        await asyncio.sleep(random.uniform(max(0.0, min_sleep), max_sleep))

    async def _fetch_via_crawler(
        self, exchange: str, stock_type: str | None
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._fetch_via_crawler_sync, exchange, stock_type)

    def _fetch_via_crawler_sync(
        self, exchange: str, stock_type: str | None
    ) -> list[dict[str, Any]]:
        project_root = Path(__file__).parents[3]
        _ensure_project_root_in_path(project_root)

        from src.config import load_config
        from src.fetchers.bse.fetcher import BseFetcher
        from src.fetchers.sse.fetcher import SseFetcher
        from src.fetchers.szse.fetcher import SzseFetcher
        from src.models.config import BseConfig, SseConfig, SzseConfig
        from src.normalizers.bse import normalize_bse_record
        from src.normalizers.sse import normalize_sse_record
        from src.normalizers.szse import normalize_szse_record

        asof = datetime.now(UTC)
        records: list[dict[str, Any]] = []

        if exchange == "sse":
            cfg = SseConfig.from_yaml(load_config("sse"))
            if stock_type:
                cfg.filters["STOCK_TYPE"] = stock_type
            fetcher = SseFetcher(cfg)
            for raw, source_url, ts in fetcher.iter_raw_records(asof):
                rec = normalize_sse_record(
                    raw,
                    source_url,
                    ts,
                    stock_type=cfg.filters.get("STOCK_TYPE", "1"),
                    include_raw=True,
                )
                records.append(rec.model_dump())
            fetcher.close()
        elif exchange == "bse":
            cfg = BseConfig.from_yaml(load_config("bse"))
            fetcher = BseFetcher(cfg)
            for raw, source_url, ts in fetcher.iter_raw_records(asof):
                rec = normalize_bse_record(raw, source_url, ts, include_raw=True)
                records.append(rec.model_dump())
            fetcher.close()
        elif exchange == "szse":
            cfg = SzseConfig.from_yaml(load_config("szse"))
            fetcher = SzseFetcher(cfg)
            for raw, source_url, ts in fetcher.iter_raw_records(asof):
                rec = normalize_szse_record(raw, source_url, ts, include_raw=True)
                records.append(rec.model_dump())
            fetcher.close()
        else:
            raise ValueError(f"Unsupported exchange for crawler: {exchange}")

        return records

    async def _fetch_via_akshare_universe(self, exchange: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._fetch_via_akshare_universe_sync, exchange)

    def _fetch_via_akshare_universe_sync(self, exchange: str) -> list[dict[str, Any]]:
        import akshare as ak

        methods: list[str]
        if exchange == "Shanghai_Stocks":
            methods = ["stock_info_sh_name_code", "stock_info_a_code_name"]
        elif exchange == "Shenzen_Stocks":
            methods = ["stock_info_sz_name_code", "stock_info_a_code_name"]
        else:
            methods = ["stock_info_bj_name_code", "stock_info_a_code_name"]

        records: dict[str, dict[str, Any]] = {}
        for method_name in methods:
            method = getattr(ak, method_name, None)
            if method is None:
                continue
            try:
                df = method()
            except Exception as exc:
                logger.warning("akshare universe method failed: %s -> %s", method_name, exc)
                continue

            for row in df.to_dict("records"):
                symbol = str(
                    row.get("code")
                    or row.get("证券代码")
                    or row.get("A股代码")
                    or row.get("股票代码")
                    or ""
                ).strip()
                if not symbol:
                    continue
                inferred = _infer_exchange_from_symbol(symbol)
                if inferred and inferred != exchange:
                    continue
                name = str(
                    row.get("name")
                    or row.get("证券简称")
                    or row.get("A股简称")
                    or row.get("股票简称")
                    or symbol
                ).strip()
                records[symbol] = {
                    "exchange": exchange,
                    "symbol": symbol,
                    "name": name,
                    "full_name": None,
                    "category": exchange,
                    "list_date": None,
                    "csrc_code": None,
                    "csrc_desc": None,
                    "province": None,
                    "status": None,
                    "source_url": f"akshare::{method_name}",
                    "asof": datetime.now(UTC),
                    "raw": _to_builtin(row),
                }
        return list(records.values())

    async def _fetch_detail_akshare(self, symbol: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._fetch_detail_akshare_sync, symbol)

    def _fetch_detail_akshare_sync(self, symbol: str) -> dict[str, Any] | None:
        import akshare as ak

        method = getattr(ak, "stock_individual_info_em", None)
        detail: dict[str, Any] = {}
        if method is not None:
            try:
                df = method(symbol=symbol)
                if df is not None:
                    rows = df.to_dict("records")
                    for row in rows:
                        key = row.get("item") or row.get("项目") or row.get("字段")
                        value = row.get("value") or row.get("值") or row.get("内容")
                        if key:
                            detail[str(key)] = _to_builtin(value)
            except Exception as exc:
                logger.warning("akshare stock_individual_info_em failed for %s: %s", symbol, exc)
        if not detail:
            spot_method = getattr(ak, "stock_zh_a_spot_em", None)
            if spot_method is not None:
                try:
                    spot_df = spot_method()
                    if spot_df is not None:
                        rows = spot_df.to_dict("records")
                        target = next(
                            (
                                row
                                for row in rows
                                if str(
                                    row.get("代码")
                                    or row.get("symbol")
                                    or row.get("证券代码")
                                    or ""
                                ).strip()
                                == symbol
                            ),
                            None,
                        )
                        if target:
                            detail = {str(k): _to_builtin(v) for k, v in target.items()}
                except Exception as exc:
                    logger.warning("akshare stock_zh_a_spot_em failed for %s: %s", symbol, exc)
        if not detail:
            return None
        detail["provider"] = "akshare"
        detail["symbol"] = symbol
        return detail

    async def _fetch_detail_yfinance(
        self, exchange: str, symbol: str
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._fetch_detail_yfinance_sync, exchange, symbol)

    def _fetch_detail_yfinance_sync(
        self, exchange: str, symbol: str
    ) -> dict[str, Any] | None:
        import yfinance as yf

        suffix = CANONICAL_TO_SUFFIX.get(exchange)
        ticker_symbol = f"{symbol}{suffix}" if suffix else symbol
        ticker = yf.Ticker(ticker_symbol)

        info: dict[str, Any] = {}
        fast_info = getattr(ticker, "fast_info", None)
        if fast_info:
            info["fast_info"] = _to_builtin(dict(fast_info))
        ticker_info = getattr(ticker, "info", None)
        if isinstance(ticker_info, dict):
            info["info"] = _to_builtin(ticker_info)

        if not info:
            return None
        info["provider"] = "yfinance"
        info["symbol"] = ticker_symbol
        return info
