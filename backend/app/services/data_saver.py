"""Raw API data persistence to local JSONL files.

Every API fetch is saved to ``data/{api_name}/{date}/{exchange}_{ts}.jsonl``
so that raw responses survive database failures and can be replayed later.

Writes are atomic: data goes to a temporary file first, then is renamed into
place to avoid partial-write corruption.
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

_DEFAULT_BASE_DIR = Path(__file__).parents[2] / "data"


class DataSaver:
    """Persist raw DataFrames as JSONL files under a date-partitioned directory tree."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or _DEFAULT_BASE_DIR

    async def save_dataframe(
        self,
        api_name: str,
        df: pd.DataFrame,
        params: dict[str, Any],
        *,
        exchange: str = "",
    ) -> Path:
        """Write *df* to a JSONL file and return the final path.

        Directory layout::

            data/
              stock_basic/
                2026-04-17/
                  SSE_20260417_153012.jsonl
        """
        return await asyncio.to_thread(
            self._save_sync, api_name, df, params, exchange=exchange
        )

    def _save_sync(
        self,
        api_name: str,
        df: pd.DataFrame,
        params: dict[str, Any],
        *,
        exchange: str = "",
    ) -> Path:
        now = datetime.now(UTC)
        date_str = now.strftime("%Y-%m-%d")
        ts_str = now.strftime("%Y%m%d_%H%M%S")

        tag = f"{exchange}_{ts_str}" if exchange else ts_str
        directory = self.base_dir / api_name / date_str
        directory.mkdir(parents=True, exist_ok=True)

        target = directory / f"{tag}.jsonl"

        meta: dict[str, Any] = {
            "__meta__": True,
            "api": api_name,
            "params": {k: str(v) for k, v in params.items()},
            "exchange": exchange,
            "fetched_at": now.isoformat(),
            "record_count": len(df),
        }

        fd, tmp_path_str = tempfile.mkstemp(
            dir=str(directory), prefix=f".{tag}_", suffix=".tmp"
        )
        tmp_path = Path(tmp_path_str)
        try:
            with open(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(meta, ensure_ascii=False) + "\n")
                for record in df.to_dict("records"):
                    fh.write(
                        json.dumps(
                            _serialize_row(record), ensure_ascii=False, default=str
                        )
                        + "\n"
                    )
            tmp_path.replace(target)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

        logger.info(
            "Saved %d rows to %s (api=%s, exchange=%s)",
            len(df), target, api_name, exchange,
        )
        return target


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Ensure every value is JSON-serializable (handle numpy/pandas types)."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        if v is None or isinstance(v, (str, int, float, bool)):
            out[k] = v
        elif hasattr(v, "item"):
            out[k] = v.item()
        elif isinstance(v, (pd.Timestamp, datetime)):
            out[k] = v.isoformat()
        else:
            out[k] = str(v)
    return out
