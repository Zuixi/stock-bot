"""SSE stock list fetcher with pagination."""

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.models.config import SseConfig
from src.models.stock import RawSseRecord

from .client import SseCommonQueryClient, SseApiError


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


class SseFetcher:
    """Fetcher for SSE stock universe.

    Handles pagination iteration, deduplication, and progress tracking.
    """

    # Maximum consecutive empty/error pages before stopping
    MAX_CONSECUTIVE_FAILURES = 3
    # Maximum pages to fetch (safety limit)
    MAX_PAGES = 500
    # Maximum errors to record in manifest
    MAX_ERROR_SAMPLES = 10

    def __init__(self, config: SseConfig):
        self.config = config
        self.client = SseCommonQueryClient(config)

    def close(self) -> None:
        """Close resources."""
        self.client.close()

    def __enter__(self) -> "SseFetcher":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _should_stop(
        self,
        records: list[dict[str, Any]],
        page_help: dict[str, Any],
        progress: FetchProgress,
        consecutive_empty: int,
    ) -> bool:
        """Determine if pagination should stop.

        Stop conditions (in priority order):
        1. Response contains total/totalPages and we've reached it
        2. Current page data is empty
        3. Current page has fewer records than page_size (last page)
        4. Too many consecutive empty/error pages
        5. Safety limit reached
        """
        page_size = self.config.pagination.page_size

        # Check for total pages in response
        total_pages = page_help.get("totalPages") or page_help.get("totalPage")
        if total_pages is not None:
            try:
                if progress.page_no >= int(total_pages):
                    logger.info(f"Reached total pages: {total_pages}")
                    return True
            except (ValueError, TypeError):
                pass

        # Check for total records
        total = page_help.get("total")
        if total is not None:
            try:
                if progress.total_records >= int(total):
                    logger.info(f"Reached total records: {total}")
                    return True
            except (ValueError, TypeError):
                pass

        # Empty page
        if not records:
            logger.info(f"Empty page at {progress.page_no}")
            return True

        # Last page (fewer records than page_size)
        if len(records) < page_size:
            logger.info(f"Last page detected: {len(records)} < {page_size}")
            return True

        # Consecutive failures
        if consecutive_empty >= self.MAX_CONSECUTIVE_FAILURES:
            logger.warning(f"Too many consecutive failures: {consecutive_empty}")
            return True

        # Safety limit
        if progress.page_no >= self.MAX_PAGES:
            logger.warning(f"Safety limit reached: {self.MAX_PAGES} pages")
            return True

        return False

    def _get_symbol(self, record: dict[str, Any]) -> str | None:
        """Extract stock symbol from record."""
        # Priority: A_STOCK_CODE > B_STOCK_CODE > COMPANY_CODE
        symbol = record.get("A_STOCK_CODE")
        if symbol and symbol != "-":
            return symbol

        symbol = record.get("B_STOCK_CODE")
        if symbol and symbol != "-":
            return symbol

        symbol = record.get("COMPANY_CODE")
        if symbol and symbol != "-":
            return symbol

        return None

    def _build_source_url(self, page_no: int) -> str:
        """Build source URL for tracking."""
        stock_type = self.config.filters.get("STOCK_TYPE", "1")
        return (
            f"{self.config.endpoint}?sqlId={self.config.query.get('sqlId')}"
            f"&STOCK_TYPE={stock_type}&pageNo={page_no}"
        )

    def iter_raw_records(
        self,
        asof: datetime | None = None,
    ) -> Iterator[tuple[RawSseRecord, str, datetime]]:
        """Iterate over all stock records with pagination.

        Yields:
            Tuple of (raw_record, source_url, asof_timestamp)
        """
        if asof is None:
            asof = datetime.now(timezone.utc)

        progress = FetchProgress()
        consecutive_empty = 0

        logger.info(
            f"Starting SSE fetch: STOCK_TYPE={self.config.filters.get('STOCK_TYPE')}, "
            f"page_size={self.config.pagination.page_size}"
        )

        try:
            while True:
                progress.page_no += 1
                source_url = self._build_source_url(progress.page_no)

                try:
                    records, page_help = self.client.get_page_data(progress.page_no)
                    consecutive_empty = 0

                    logger.debug(
                        f"Page {progress.page_no}: {len(records)} records, "
                        f"total so far: {progress.total_records}"
                    )

                    # Process records
                    for raw_data in records:
                        symbol = self._get_symbol(raw_data)
                        if symbol is None:
                            logger.warning(f"Record without symbol: {raw_data}")
                            continue

                        # Deduplication
                        if symbol in progress.unique_symbols:
                            logger.debug(f"Duplicate symbol: {symbol}")
                            continue

                        progress.unique_symbols.add(symbol)
                        progress.total_records += 1

                        try:
                            raw_record = RawSseRecord.model_validate(raw_data)
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
                    if self._should_stop(records, page_help, progress, consecutive_empty):
                        break

                    # Page delay
                    if self.config.rate_limit.page_delay > 0:
                        time.sleep(self.config.rate_limit.page_delay)

                except SseApiError as e:
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
