"""Eastmoney research-report list client (``reportapi.eastmoney.com``).

Verified source (2026-11-11 plan, http only — https endpoint unreachable from
the container network):

    http://reportapi.eastmoney.com/report/list

The endpoint requires a browser ``User-Agent`` and
``Referer: http://data.eastmoney.com/report/``; without them it answers with an
empty payload.  Query parameters are held fixed (``qType=0``, ``industryCode=*``
…) so a typo cannot silently degrade into "give me everything".

Design notes:
- ``publishDate`` is ``"YYYY-MM-DD 00:00:00.000"`` **Shanghai wall-clock**; we
  attach ``Asia/Shanghai`` explicitly instead of letting the container TZ leak
  into ``published_at`` (PIT ordering depends on it).
- Pagination is bounded by ``max_pages``.  Reaching that cap while
  ``hits > len(rows)`` raises instead of returning a silently truncated list.
- Empty / ``None`` / ``"nan"`` numeric cells map to ``None`` — never ``0``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

_REPORT_URL = "http://reportapi.eastmoney.com/report/list"
_PDF_URL_TEMPLATE = "https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf?{encode_url}"
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "http://data.eastmoney.com/report/",
}
_EMPTY_NUMERIC = {"", "nan", "None", "null", "-", "--"}


def _num(value: Any) -> float | None:
    """研报列表数值列 → float；空串 / None / ``"nan"`` / 占位符 → None。"""
    if value is None:
        return None
    if isinstance(value, str):
        if value.strip() in _EMPTY_NUMERIC:
            return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _int_or_raw(value: Any) -> int | str | None:
    """``ratingChange`` → int（东财用数值码）；非数值时保留原文本。"""
    number = _num(value)
    if number is not None and number.is_integer():
        return int(number)
    if isinstance(value, str) and value.strip() and value.strip() not in _EMPTY_NUMERIC:
        return value.strip()
    return None


def _parse_publish_date(value: Any) -> datetime | None:
    """``"YYYY-MM-DD 00:00:00.000"`` → 上海 aware datetime（取前 10 位日期）。"""
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        day = date.fromisoformat(value[:10])
    except ValueError:
        logger.warning("eastmoney report: unparsable publishDate %r", value)
        return None
    return datetime(day.year, day.month, day.day, tzinfo=_SHANGHAI)


def _pdf_url(info_code: str, encode_url: Any) -> str | None:
    if not isinstance(encode_url, str) or not encode_url.strip():
        return None
    return _PDF_URL_TEMPLATE.format(info_code=info_code, encode_url=encode_url.strip())


def _map_record(record: dict[str, Any]) -> dict[str, Any] | None:
    """单条原始记录 → 归一字段；缺 ``infoCode`` 时返回 None（调用方记日志）。"""
    info_code = record.get("infoCode")
    if info_code is None or str(info_code).strip() == "":
        return None
    source_item_id = str(info_code).strip()
    return {
        "source_item_id": source_item_id,
        "title": record.get("title"),
        "stock_code": record.get("stockCode"),
        "stock_name": record.get("stockName"),
        "org_name": record.get("orgSName") or record.get("orgName"),
        "published_at": _parse_publish_date(record.get("publishDate")),
        "rating": record.get("emRatingName"),
        "rating_change": _int_or_raw(record.get("ratingChange")),
        "target_price": _num(
            record.get("indvAimPriceT")
            if record.get("indvAimPriceT") not in (None, "")
            else record.get("indvAimPriceL")
        ),
        "eps_this_year": _num(record.get("predictThisYearEps")),
        "eps_next_year": _num(record.get("predictNextYearEps")),
        "eps_next_two_year": _num(record.get("predictNextTwoYearEps")),
        "pe_this_year": _num(record.get("predictThisYearPe")),
        "pe_next_year": _num(record.get("predictNextYearPe")),
        "pe_next_two_year": _num(record.get("predictNextTwoYearPe")),
        "industry": record.get("indvInduName"),
        "industry_code": record.get("indvInduCode"),
        "researcher": record.get("researcher"),
        "pdf_url": _pdf_url(source_item_id, record.get("encodeUrl")),
    }


class EastmoneyReportClient:
    """Async client for the Eastmoney sell-side research-report list.

    ``client`` may be injected (tests use a fake); otherwise an internal
    :class:`httpx.AsyncClient` is created lazily and closed by :meth:`aclose`
    or ``async with``.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        timeout: float = 20.0,
        retries: int = 3,
        backoff: float = 0.4,
        report_url: str = _REPORT_URL,
    ) -> None:
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout
        self._retries = max(1, retries)
        self._backoff = backoff
        self._report_url = report_url

    async def __aenter__(self) -> EastmoneyReportClient:
        self._ensure_client()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or getattr(self._client, "is_closed", False):
            self._client = httpx.AsyncClient(timeout=self._timeout, follow_redirects=True)
            self._owns_client = True
        return self._client

    async def aclose(self) -> None:
        client = self._client
        if self._owns_client:
            if client is not None and not getattr(client, "is_closed", False):
                await client.aclose()
            self._client = None
            self._owns_client = False

    async def _get_page(
        self, page: int, begin_date: date, end_date: date, page_size: int
    ) -> dict[str, Any]:
        """GET 一页，失败退避重试；重试用尽抛 ``RuntimeError``。"""
        params = {
            "pageSize": str(page_size),
            "pageNo": str(page),
            "qType": "0",
            "beginTime": begin_date.isoformat(),
            "endTime": end_date.isoformat(),
            "industryCode": "*",
            "industry": "*",
            "rating": "",
            "ratingChange": "",
            "orgCode": "",
            "code": "*",
            "rcode": "",
            "p": "1",
            "pageNum": str(page),
        }
        client = self._ensure_client()
        last_error: Exception | None = None
        for attempt in range(1, self._retries + 1):
            try:
                response = await client.get(self._report_url, params=params, headers=_HEADERS)
                raiser = getattr(response, "raise_for_status", None)
                if raiser is not None:
                    raised = raiser()
                    if inspect.isawaitable(raised):
                        await raised
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError(f"unexpected payload type: {type(payload)!r}")
                return payload
            except Exception as exc:  # noqa: BLE001 - retried then re-raised
                last_error = exc
                logger.warning(
                    "eastmoney report list page %s attempt %s/%s failed: %s",
                    page,
                    attempt,
                    self._retries,
                    exc,
                )
                if attempt < self._retries:
                    await asyncio.sleep(self._backoff * attempt)
        raise RuntimeError(
            f"eastmoney report list page {page} failed after {self._retries} attempts: {last_error}"
        ) from last_error

    async def fetch_reports(
        self,
        begin_date: date,
        end_date: date,
        *,
        page_size: int = 100,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """按日期区间抓取研报列表（分页跑满仍未覆盖 ``hits`` 时报错）。"""
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        skipped = 0
        hits = 0
        page = 1
        while page <= max_pages:
            payload = await self._get_page(page, begin_date, end_date, page_size)
            raw_hits = payload.get("hits")
            try:
                hits = int(raw_hits) if raw_hits is not None else 0
            except (TypeError, ValueError):
                hits = 0
            records = payload.get("data") or []
            if not records:
                break
            for record in records:
                if not isinstance(record, dict):
                    continue
                mapped = _map_record(record)
                if mapped is None:
                    skipped += 1
                    logger.warning(
                        "eastmoney report: skipping record without infoCode: %r",
                        record.get("title"),
                    )
                    continue
                if mapped["source_item_id"] in seen:
                    logger.warning(
                        "eastmoney report: duplicate infoCode %s skipped",
                        mapped["source_item_id"],
                    )
                    continue
                seen.add(mapped["source_item_id"])
                rows.append(mapped)
            if hits and len(rows) + skipped >= hits:
                break
            if len(records) < page_size:
                break
            page += 1

        if hits > len(rows) + skipped:
            raise RuntimeError(
                "eastmoney report list truncated: hits="
                f"{hits} fetched={len(rows)} skipped={skipped} "
                f"(max_pages={max_pages})"
            )
        return rows
