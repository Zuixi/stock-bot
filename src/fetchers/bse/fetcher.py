"""BSE stock list fetcher with pagination.

Handles the BSE (Beijing Stock Exchange) listed company API.
"""

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.models.config import BseConfig
from src.models.stock import RawBseRecord

from .client import bseApiClient, bseApiError


logger = logging.getLogger(__name__)


@dataclass
class FetchProgress:
    """Progress tracking for fetch operation."""

    page_no: int = 0
    total_records: int = 0
    unique_symbols: set[str] = field(default_factory=set)
    failed_pages: int = 0
    retry_count: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

    @property
    def unique_count(self) -> int:
        return len(self.unique_symbols)

    @property
    def duration(self) -> float:
        return time.time() - self.start_time


class BseFetcher:
    """Fetcher for BSE stock universe.

    Handles pagination iteration, deduplication, and progress tracking.
    The BSE API returns a flat list of records per page.
    """

    # Maximum consecutive empty/error pages before stopping
    MAX_CONSECUTIVE_FAILURES = 3
    # Maximum pages to fetch (safety limit)
    MAX_PAGES = 100
    # Maximum errors to record in manifest
    MAX_ERROR_SAMPLES = 10

    def __init__(self, config: BseConfig):
        self.config = config
        self.client = bseApiClient(config)

    def close(self) -> None:
        """Close resources."""
        self.client.close()

    def __enter__(self) -> "BseFetcher":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _should_stop(
        self,
        records: list[Any],
        progress: FetchProgress,
        consecutive_empty: int,
    ) -> bool:
        """Determine if pagination should stop.

        Stop conditions (in priority order):
        1. Current page data is empty (no more records)
        2. Current page has fewer records than expected (last page)
        3. Too many consecutive empty/error pages
        4. Safety limit reached
        """
        # Empty page - we've reached the end
        if not records:
            logger.info(f"Empty page at {progress.page_no}, stopping")
            return True

        # Safety limit
        if progress.page_no >= self.MAX_PAGES:
            logger.warning(f"Safety limit reached: {self.MAX_PAGES} pages")
            return True

        # Consecutive failures
        if consecutive_empty >= self.MAX_CONSECUTIVE_FAILURES:
            logger.warning(f"Too many consecutive failures: {consecutive_empty}")
            return True

        return False

    def _get_symbol(self, record: dict[str, Any]) -> str | None:
        """Extract stock symbol from BSE record."""
        # BSE uses xxzqdm for stock code
        symbol = record.get("xxzqdm")
        if symbol and symbol != "-":
            return symbol
        return None

    def _build_source_url(self, page_no: int) -> str:
        """Build source URL for tracking."""
        return (
            f"{self.config.endpoint}?"
            f"page={page_no}&typejb=T&xxfcbj[]=2"
        )

    def iter_raw_records(
        self,
        asof: datetime | None = None,
    ) -> Iterator[tuple[RawBseRecord, str, datetime]]:
        """Iterate over all stock records with pagination.

        Yields:
            Tuple of (raw_record, source_url, asof_timestamp)
        """
        if asof is None:
            asof = datetime.now(timezone.utc)

        progress = FetchProgress()
        consecutive_empty = 0

        logger.info(
            f"Starting BSE fetch: endpoint={self.config.endpoint}"
        )

        try:
            while True:
                progress.page_no += 1
                source_url = self._build_source_url(progress.page_no)

                try:
                    data = self.client.query_page(progress.page_no)
                    consecutive_empty = 0

                    # BSE response structure: [{"content": [...records...], ...}, ...]
                    # or {"content": [...]} for single response
                    records = []

                    # Handle list-wrapped response (Page response)
                    if isinstance(data, list) and data:
                        first = data[0]
                        if isinstance(first, dict) and "content" in first:
                            content = first["content"]
                            if isinstance(content, list):
                                records = content
                        else:
                            records = data
                    # Handle dict response with content
                    elif isinstance(data, dict) and "content" in data:
                        content = data["content"]
                        if isinstance(content, list):
                            records = content
                    # Handle direct list
                    elif isinstance(data, list):
                        records = data

                    logger.debug(
                        f"Page {progress.page_no}: {len(records)} records, "
                        f"total so far: {progress.total_records}"
                    )

                    # Process records
                    for raw_data in records:
                        if not isinstance(raw_data, dict):
                            logger.warning(f"Skipping non-dict record: {raw_data}")
                            continue

                        symbol = self._get_symbol(raw_data)
                        if symbol is None:
                            logger.debug(f"Record without symbol, skipping: {raw_data.get('xxzqjc', 'unknown')}")
                            continue

                        # Deduplication
                        if symbol in progress.unique_symbols:
                            logger.debug(f"Duplicate symbol: {symbol}")
                            continue

                        progress.unique_symbols.add(symbol)
                        progress.total_records += 1

                        try:
                            raw_record = RawBseRecord.model_validate(raw_data)
                            yield raw_record, source_url, asof
                        except Exception as e:
                            logger.warning(f"Failed to parse record {symbol}: {e}")
                            if len(progress.errors) < self.MAX_ERROR_SAMPLES:
                                progress.errors.append({
                                    "type": "parse_error",
                                    "symbol": symbol,
                                    "error": str(e),
                                    "page": progress.page_no,
                                })

                    # Check stop condition
                    if self._should_stop(records, progress, consecutive_empty):
                        break

                    # Page delay
                    if self.config.rate_limit.page_delay > 0:
                        time.sleep(self.config.rate_limit.page_delay)

                except bseApiError as e:
                    consecutive_empty += 1
                    progress.failed_pages += 1
                    logger.error(f"Page {progress.page_no} failed: {e}")

                    if len(progress.errors) < self.MAX_ERROR_SAMPLES:
                        progress.errors.append({
                            "type": "api_error",
                            "page": progress.page_no,
                            "error": str(e),
                            "response_snippet": e.response_text[:200] if e.response_text else None,
                        })

                    if consecutive_empty >= self.MAX_CONSECUTIVE_FAILURES:
                        logger.error("Too many consecutive failures, stopping")
                        break

                except Exception as e:
                    consecutive_empty += 1
                    progress.failed_pages += 1
                    logger.exception(f"Unexpected error on page {progress.page_no}: {e}")

                    if len(progress.errors) < self.MAX_ERROR_SAMPLES:
                        progress.errors.append({
                            "type": "unexpected_error",
                            "page": progress.page_no,
                            "error": str(e),
                        })

                    if consecutive_empty >= self.MAX_CONSECUTIVE_FAILURES:
                        break

        finally:
            logger.info(
                f"Fetch completed: {progress.page_no} pages, "
                f"{progress.unique_count} unique records, "
                f"{progress.failed_pages} failed pages, "
                f"{progress.duration:.1f}s"
            )

    def get_progress_stats(self, progress: FetchProgress) -> dict[str, Any]:
        """Get progress statistics for manifest."""
        return {
            "total_pages": progress.page_no,
            "total_records": progress.total_records,
            "unique_records": progress.unique_count,
            "failed_pages": progress.failed_pages,
            "retry_count": progress.retry_count,
            "duration_seconds": progress.duration,
            "errors": progress.errors,
        }
