"""SSE index scraper service — async adaptation of crons/SSE/index_basic.py.

Fetches intraday snapshots from the SSE yunhq JSONP API and persists them
to the ``sse_index_snapshots`` table.  Supports both real-time collection
and historical backfill via timestamp manipulation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from datetime import date, datetime, timedelta

import httpx

from app.core.database import async_session_factory
from app.repositories import sse_index_repo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SSE API constants (mirrored from crons/SSE/index_basic.py)
# ---------------------------------------------------------------------------

BASE_URL = "https://yunhq.sse.com.cn:32042/v1/csip/list/self/"
INDEX_CODES = (
    "000001_000002_000003_000009_000010_000016_000017_000020"
    "_000043_000044_000045_000046_000047_000090_000132_000133"
    "_000155_000680_000681_000688_000698_000699_950580"
)
SELECT_FIELDS = "prev_close,open,high,low,last,chg_rate,code,name"
JQUERY_VERSION = "3.7.1"
FIELD_NAMES = ("prev_close", "open", "high", "low", "last", "chg_rate", "code", "name")

MAX_RETRIES = 3

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

# Trading hours for A-shares
TRADE_START_HOUR, TRADE_START_MIN = 9, 30
TRADE_END_HOUR, TRADE_END_MIN = 15, 0
INTERVAL_MINUTES = 10

# Persistent httpx client (created lazily, reuses cookies across requests)
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client  # noqa: PLW0603
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            verify=False,  # noqa: S501  — SSE cert chain sometimes incomplete
        )
    return _http_client


async def close_http_client() -> None:
    global _http_client  # noqa: PLW0603
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None


# ---------------------------------------------------------------------------
# jQuery callback & JSONP helpers
# ---------------------------------------------------------------------------

def _generate_callback() -> str:
    rand_str = str(random.random())
    combined = JQUERY_VERSION + rand_str
    digits_only = re.sub(r"\D", "", combined)
    return f"jQuery{digits_only}"


def _parse_jsonp(text: str) -> dict:
    match = re.search(r"jQuery[\d_]+\((.+)\)$", text, re.DOTALL)
    if not match:
        raise ValueError(f"Cannot parse JSONP response: {text[:200]}")
    return json.loads(match.group(1))


def _build_headers() -> dict[str, str]:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": "https://yunhq.sse.com.cn/",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }


# ---------------------------------------------------------------------------
# Core fetch
# ---------------------------------------------------------------------------

async def fetch_snapshot(
    timestamp_ms: int | None = None,
    collect_time_override: datetime | None = None,
) -> list[dict]:
    """Fetch one snapshot from the SSE JSONP API.

    Args:
        timestamp_ms: Optional historical timestamp (ms).  When *None*,
            uses the current wall-clock time.
        collect_time_override: If provided, use this as ``collect_time``
            in the returned dicts instead of ``datetime.now()``.

    Returns:
        A list of dicts ready to be passed to ``bulk_upsert_snapshots``.
    """
    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)

    callback = _generate_callback()
    params = {
        "callback": callback,
        "select": SELECT_FIELDS,
        "_": timestamp_ms,
    }
    url = BASE_URL + INDEX_CODES
    client = _get_http_client()

    last_exc: BaseException | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info("SSE snapshot request (attempt %d/%d) ts=%d", attempt, MAX_RETRIES, timestamp_ms)
            resp = await client.get(url, params=params, headers=_build_headers())
            resp.raise_for_status()

            data = _parse_jsonp(resp.text)
            raw_list = data.get("list", [])
            if not raw_list:
                logger.warning("SSE returned empty list (market may be closed)")
                return []

            ct = collect_time_override or datetime.now()
            td = ct.date() if isinstance(ct, datetime) else ct

            records: list[dict] = []
            for item in raw_list:
                if len(item) < len(FIELD_NAMES):
                    continue
                mapped = dict(zip(FIELD_NAMES, item))
                records.append({
                    "code": str(mapped["code"]),
                    "name": str(mapped["name"]),
                    "trade_date": td,
                    "collect_time": ct,
                    "prev_close": mapped["prev_close"],
                    "open": mapped["open"],
                    "high": mapped["high"],
                    "low": mapped["low"],
                    "last": mapped["last"],
                    "chg_rate": mapped["chg_rate"],
                })

            logger.info("Fetched %d index snapshots", len(records))
            return records

        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                wait = 2 ** attempt + random.uniform(0, 1)
                logger.warning("SSE request failed: %s — retrying in %.1fs", exc, wait)
                await asyncio.sleep(wait)
            else:
                logger.error("SSE request failed after %d attempts: %s", MAX_RETRIES, exc)

    raise RuntimeError(f"SSE fetch failed after {MAX_RETRIES} retries") from last_exc


# ---------------------------------------------------------------------------
# Fetch + persist
# ---------------------------------------------------------------------------

async def fetch_and_save(timestamp_ms: int | None = None) -> int:
    """Fetch the current (or historical) snapshot and save to DB.

    Returns the number of rows upserted.
    """
    records = await fetch_snapshot(timestamp_ms=timestamp_ms)
    if not records:
        return 0

    async with async_session_factory() as db:
        count = await sse_index_repo.bulk_upsert_snapshots(db, records)
        await db.commit()

    logger.info("Saved %d SSE snapshot rows", count)
    return count


# ---------------------------------------------------------------------------
# Historical backfill
# ---------------------------------------------------------------------------

def _trading_days(start: date, end: date) -> list[date]:
    """Return weekdays between *start* and *end* (inclusive)."""
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _intraday_times(d: date) -> list[datetime]:
    """Generate 10-min interval datetimes for a given trading day (9:30..15:00)."""
    start = datetime(d.year, d.month, d.day, TRADE_START_HOUR, TRADE_START_MIN)
    end = datetime(d.year, d.month, d.day, TRADE_END_HOUR, TRADE_END_MIN)
    times: list[datetime] = []
    t = start
    while t <= end:
        times.append(t)
        t += timedelta(minutes=INTERVAL_MINUTES)
    return times


async def batch_backfill(start_date: date, end_date: date) -> int:
    """Backfill historical intraday snapshots for a date range.

    For each trading day, iterates over 10-min intervals (9:30–15:00),
    constructs the corresponding timestamp, and fetches + persists the data.
    Includes random delays between requests to avoid triggering anti-scraping.

    Returns the total number of rows upserted.
    """
    days = _trading_days(start_date, end_date)
    total_saved = 0
    total_points = sum(len(_intraday_times(d)) for d in days)

    logger.info(
        "Starting backfill: %d trading days, ~%d data points",
        len(days), total_points,
    )

    for day_idx, day in enumerate(days, 1):
        times = _intraday_times(day)
        logger.info("Backfill day %d/%d: %s (%d points)", day_idx, len(days), day, len(times))

        for t_idx, ct in enumerate(times, 1):
            ts_ms = int(ct.timestamp() * 1000)
            try:
                records = await fetch_snapshot(
                    timestamp_ms=ts_ms,
                    collect_time_override=ct,
                )
                if records:
                    async with async_session_factory() as db:
                        count = await sse_index_repo.bulk_upsert_snapshots(db, records)
                        await db.commit()
                    total_saved += count
                    logger.info(
                        "  [%s %s] saved %d rows (total: %d)",
                        day, ct.strftime("%H:%M"), count, total_saved,
                    )
                else:
                    logger.info("  [%s %s] no data", day, ct.strftime("%H:%M"))
            except Exception:
                logger.exception("  [%s %s] fetch failed, skipping", day, ct.strftime("%H:%M"))

            # Anti-scraping delay between requests
            if t_idx < len(times) or day_idx < len(days):
                delay = random.uniform(2, 6)
                await asyncio.sleep(delay)

    logger.info("Backfill complete: %d total rows saved", total_saved)
    return total_saved
