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
        return await asyncio.to_thread(self._save_sync, api_name, df, params, exchange=exchange)

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

        fd, tmp_path_str = tempfile.mkstemp(dir=str(directory), prefix=f".{tag}_", suffix=".tmp")
        tmp_path = Path(tmp_path_str)
        try:
            with open(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(meta, ensure_ascii=False) + "\n")
                for record in df.to_dict("records"):
                    fh.write(
                        json.dumps(_serialize_row(record), ensure_ascii=False, default=str) + "\n"
                    )
            tmp_path.replace(target)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

        logger.info(
            "Saved %d rows to %s (api=%s, exchange=%s)",
            len(df),
            target,
            api_name,
            exchange,
        )
        return target

    # ------------------------------------------------------------------
    # 读取侧：P5 的"已付费请求不重复付"（失败时回放，而不是再外呼一次）
    # ------------------------------------------------------------------

    async def load_dataframe(
        self,
        api_name: str,
        params: dict[str, Any] | None = None,
        *,
        day: str | None = None,
    ) -> pd.DataFrame | None:
        """从本地 JSONL 回放一次响应；找不到/损坏一律 ``None``（调用方据此走外呼）。

        只读 ``day``（默认今天，UTC）目录：跨天不回放，避免"用旧快照冒充新日"。
        ``params`` 是**子集匹配**（元数据里的 params 全部是字符串）。
        """
        return await asyncio.to_thread(self._load_sync, api_name, params, day)

    async def latest_raw_path(
        self,
        api_name: str,
        *,
        day: str | None = None,
    ) -> Path | None:
        """当天该 api 最新的 JSONL 路径（不解析内容），供账本记录/排障。"""
        directory = self._day_dir(api_name, day)
        if not directory.is_dir():
            return None
        files = sorted(directory.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        return files[0] if files else None

    def _day_dir(self, api_name: str, day: str | None) -> Path:
        date_str = day or datetime.now(UTC).strftime("%Y-%m-%d")
        return self.base_dir / api_name / date_str

    def _load_sync(
        self,
        api_name: str,
        params: dict[str, Any] | None,
        day: str | None,
    ) -> pd.DataFrame | None:
        directory = self._day_dir(api_name, day)
        if not directory.is_dir():
            return None

        wanted = {k: str(v) for k, v in (params or {}).items()}
        files = sorted(directory.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in files:
            try:
                meta, rows = _read_jsonl(path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                logger.warning("Unusable raw JSONL %s: %s", path, exc)
                continue
            if meta.get("api") not in (None, api_name):
                continue
            file_params = {str(k): str(v) for k, v in (meta.get("params") or {}).items()}
            if any(file_params.get(k) != v for k, v in wanted.items()):
                continue
            return pd.DataFrame(rows)
        return None

    def latest_raw_path_sync(self, api_name: str, *, day: str | None = None) -> Path | None:
        """同步版（供已在 to_thread 内的调用方复用）。"""
        directory = self._day_dir(api_name, day)
        if not directory.is_dir():
            return None
        files = sorted(directory.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        return files[0] if files else None


def _read_jsonl(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """读取 ``save_dataframe`` 写出的 JSONL：首行 ``__meta__``，其余为数据行。"""
    meta: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if lineno == 0 and isinstance(payload, dict) and payload.get("__meta__"):
                meta = payload
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return meta, rows


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
