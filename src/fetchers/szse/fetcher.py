"""SZSE stock list fetcher with pagination."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.models.config import SzseConfig
from src.models.stock import RawSzseRecord

from .client import szseApiClient, szseApiError


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


class SzseFetcher:
	"""Fetcher for SZSE stock universe.

	Handles pagination iteration, deduplication, and progress tracking.
	"""

	# Maximum consecutive empty/error pages before stopping
	MAX_CONSECUTIVE_FAILURES = 3
	# Maximum pages to fetch (safety limit)
	MAX_PAGES = 500
	# Maximum errors to record in manifest
	MAX_ERROR_SAMPLES = 10

	def __init__(self, config: SzseConfig):
		self.config = config
		self.client = szseApiClient(config)

	def close(self) -> None:
		"""Close resources."""
		self.client.close()

	def __enter__(self) -> "SzseFetcher":
		return self

	def __exit__(self, *args: Any) -> None:
		self.close()

	def _extract_sections(self, payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
		"""Extract valid report sections from payload."""
		sections: list[dict[str, Any]] = []
		for item in payload:
			if not isinstance(item, dict):
				continue
			data = item.get("data")
			if isinstance(data, list):
				sections.append(item)
		return sections

	def _extract_page_info(self, sections: list[dict[str, Any]]) -> dict[str, Any]:
		"""Extract pagination info from the first section metadata."""
		for section in sections:
			meta = section.get("metadata")
			if isinstance(meta, dict):
				return meta
		return {}

	def _should_stop(
		self,
		records: list[dict[str, Any]],
		page_info: dict[str, Any],
		progress: FetchProgress,
		consecutive_empty: int,
	) -> bool:
		"""Determine if pagination should stop.

		Stop conditions (in priority order):
		1. Response contains pagecount and we've reached it
		2. Current page data is empty
		3. Current page has fewer records than pagesize (last page)
		4. Too many consecutive empty/error pages
		5. Safety limit reached
		"""
		# total pages
		page_count = page_info.get("pagecount")
		if page_count is not None:
			try:
				if progress.page_no >= int(page_count):
					logger.info(f"Reached total pages: {page_count}")
					return True
			except (ValueError, TypeError):
				pass

		# total records
		record_count = page_info.get("recordcount")
		if record_count is not None:
			try:
				if progress.total_records >= int(record_count):
					logger.info(f"Reached total records: {record_count}")
					return True
			except (ValueError, TypeError):
				pass

		# Empty page
		if not records:
			logger.info(f"Empty page at {progress.page_no}")
			return True

		# Last page by pagesize
		page_size = page_info.get("pagesize")
		if page_size is not None:
			try:
				if len(records) < int(page_size):
					logger.info(f"Last page detected: {len(records)} < {page_size}")
					return True
			except (ValueError, TypeError):
				pass

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
		"""Extract stock symbol from SZSE record."""
		for key in ("agdm", "bgdm", "abgdm", "cdrdm", "zqdm", "code", "dm"):
			symbol = record.get(key)
			if symbol and symbol != "-":
				return str(symbol)
		return None

	def _build_source_url(self, page_no: int) -> str:
		"""Build source URL for tracking."""
		return (
			f"{self.config.endpoint}?SHOWTYPE=JSON&CATALOGID=1110"
			f"&TABKEY=tab1&PAGENO={page_no}"
		)

	def iter_raw_records(
		self,
		asof: datetime | None = None,
	) -> Iterator[tuple[RawSzseRecord, str, datetime]]:
		"""Iterate over all stock records with pagination.

		Yields:
			Tuple of (raw_record, source_url, asof_timestamp)
		"""
		if asof is None:
			asof = datetime.now(timezone.utc)

		progress = FetchProgress()
		consecutive_empty = 0

		logger.info(
			f"Starting SZSE fetch: endpoint={self.config.endpoint}"
		)

		try:
			while True:
				progress.page_no += 1
				source_url = self._build_source_url(progress.page_no)

				try:
					payload = self.client.query_page(progress.page_no)
					consecutive_empty = 0

					sections = self._extract_sections(payload)
					page_info = self._extract_page_info(sections)

					records: list[dict[str, Any]] = []
					for section in sections:
						data = section.get("data")
						if isinstance(data, list):
							records.extend([item for item in data if isinstance(item, dict)])

					logger.debug(
						f"Page {progress.page_no}: {len(records)} records, "
						f"total so far: {progress.total_records}"
					)

					for raw_data in records:
						symbol = self._get_symbol(raw_data)
						if symbol is None:
							logger.debug(f"Record without symbol, skipping: {raw_data}")
							continue

						if symbol in progress.unique_symbols:
							logger.debug(f"Duplicate symbol: {symbol}")
							continue

						progress.unique_symbols.add(symbol)
						progress.total_records += 1

						try:
							raw_record = RawSzseRecord.model_validate(raw_data)
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

					if self._should_stop(records, page_info, progress, consecutive_empty):
						break

					delay_min = self.config.rate_limit.page_delay_min
					delay_max = self.config.rate_limit.page_delay_max
					if delay_min is not None and delay_max is not None and delay_max >= delay_min:
						time.sleep(random.uniform(delay_min, delay_max))
					elif self.config.rate_limit.page_delay > 0:
						time.sleep(self.config.rate_limit.page_delay)

				except szseApiError as e:
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
