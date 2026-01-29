"""Universe snapshot storage.

Directory structure:
    data/
      universe/
        snapshot=2026-01-30T12-00-00Z/
          manifest.json
          Shanghai_Stocks/
            class=STOCK_TYPE_1_主板A股.jsonl
          Shenzen_Stocks/
            ...
          Beijing_Stocks/
            ...
"""

import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import orjson

from src.models.manifest import FetchStats, UniverseManifest
from src.models.stock import StockRecord


logger = logging.getLogger(__name__)


def _safe_filename(s: str) -> str:
    """Convert string to safe filename."""
    # Replace problematic characters
    for char in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']:
        s = s.replace(char, '_')
    return s


def _format_timestamp(dt: datetime) -> str:
    """Format datetime for directory name (filesystem-safe ISO 8601)."""
    return dt.strftime("%Y-%m-%dT%H-%M-%SZ")


class SnapshotWriter:
    """Writer for a single universe snapshot.

    Manages file handles and writes records in JSONL format.
    """

    def __init__(self, snapshot_dir: Path, exchange: str):
        self.snapshot_dir = snapshot_dir
        self.exchange = exchange
        self.exchange_dir = snapshot_dir / exchange

        self._file_handles: dict[str, Any] = {}
        self._category_counts: dict[str, int] = defaultdict(int)
        self._total_count = 0

    def __enter__(self) -> "SnapshotWriter":
        self.exchange_dir.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close all file handles."""
        for fh in self._file_handles.values():
            fh.close()
        self._file_handles.clear()

    def _get_file_handle(self, category: str) -> Any:
        """Get or create file handle for category."""
        if category not in self._file_handles:
            safe_category = _safe_filename(category)
            filepath = self.exchange_dir / f"class={safe_category}.jsonl"
            self._file_handles[category] = open(filepath, "wb")
            logger.debug(f"Created file: {filepath}")
        return self._file_handles[category]

    def write_record(self, record: StockRecord) -> None:
        """Write a single stock record."""
        fh = self._get_file_handle(record.category)

        # Serialize to JSON bytes with orjson
        json_bytes = orjson.dumps(
            record.model_dump(mode="json", exclude_none=True),
            option=orjson.OPT_APPEND_NEWLINE,
        )
        fh.write(json_bytes)

        self._category_counts[record.category] += 1
        self._total_count += 1

    def get_stats(self) -> dict[str, int]:
        """Get category counts."""
        return dict(self._category_counts)

    def get_total_count(self) -> int:
        """Get total records written."""
        return self._total_count

    def get_output_files(self) -> list[str]:
        """Get list of output file paths relative to snapshot dir."""
        files = []
        for category in self._category_counts:
            safe_category = _safe_filename(category)
            files.append(f"{self.exchange}/class={safe_category}.jsonl")
        return sorted(files)


class UniverseStorage:
    """Storage manager for universe snapshots."""

    def __init__(self, base_dir: Path | str = "data/universe"):
        self.base_dir = Path(base_dir)

    def create_snapshot_dir(self, asof: datetime) -> Path:
        """Create and return snapshot directory path."""
        timestamp = _format_timestamp(asof)
        snapshot_dir = self.base_dir / f"snapshot={timestamp}"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        return snapshot_dir

    def open_writer(self, asof: datetime, exchange: str) -> SnapshotWriter:
        """Open a snapshot writer for the given exchange."""
        snapshot_dir = self.create_snapshot_dir(asof)
        return SnapshotWriter(snapshot_dir, exchange)

    def write_manifest(
        self,
        asof: datetime,
        manifest: UniverseManifest,
    ) -> Path:
        """Write manifest file to snapshot directory."""
        snapshot_dir = self.create_snapshot_dir(asof)
        manifest_path = snapshot_dir / "manifest.json"

        with open(manifest_path, "wb") as f:
            json_bytes = orjson.dumps(
                manifest.to_safe_dict(),
                option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS,
            )
            f.write(json_bytes)

        logger.info(f"Manifest written: {manifest_path}")
        return manifest_path

    def build_manifest(
        self,
        exchange: str,
        asof: datetime,
        config: Any,  # SseConfig or similar
        writer: SnapshotWriter,
        duration_seconds: float,
        failed_pages: int = 0,
        errors: list[dict[str, Any]] | None = None,
    ) -> UniverseManifest:
        """Build manifest from fetch results."""
        stats = FetchStats(
            total_pages=0,  # Will be set by caller if needed
            total_records=writer.get_total_count(),
            unique_records=writer.get_total_count(),
            failed_pages=failed_pages,
            retry_count=0,
            duration_seconds=duration_seconds,
            categories=writer.get_stats(),
        )

        manifest = UniverseManifest(
            exchange=exchange,
            asof=asof,
            endpoint=config.endpoint,
            query_params=config.query,
            filters=config.filters,
            pagination={
                "page_size": config.pagination.page_size,
                "cache_size": config.pagination.cache_size,
            },
            headers=config.get_safe_headers(),
            rate_limit={
                "requests_per_second": config.rate_limit.requests_per_second,
                "page_delay": config.rate_limit.page_delay,
            },
            retry={
                "max_attempts": config.retry.max_attempts,
                "backoff_multiplier": config.retry.backoff_multiplier,
                "initial_delay": config.retry.initial_delay,
            },
            timeout=config.timeout,
            stats=stats,
            errors=errors or [],
            output_files=writer.get_output_files(),
        )

        return manifest
