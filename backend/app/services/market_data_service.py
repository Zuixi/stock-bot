"""Market-data face: ingest + read services.

数据源（字段/单位见 plans/2026-09-03-market-data-face.md「已验证数据源事实」）。
"""

from __future__ import annotations

import asyncio
import logging
import math
import sys
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import index_repo

if TYPE_CHECKING:
    from app.models.index_daily import IndexDaily

logger = logging.getLogger(__name__)

_SH = ZoneInfo("Asia/Shanghai")

GLOBAL_INDICES: list[dict[str, str]] = [
    {
        "ts_code": "000001.SH", "name": "上证指数", "market": "CN",
        "region": "asia", "em_secid": "1.000001", "source": "index_daily",
    },
    {
        "ts_code": "399001.SZ", "name": "深证成指", "market": "CN",
        "region": "asia", "em_secid": "0.399001", "source": "index_daily",
    },
    {
        "ts_code": "399006.SZ", "name": "创业板指", "market": "CN",
        "region": "asia", "em_secid": "0.399006", "source": "index_daily",
    },
    {
        "ts_code": "HSI", "name": "恒生指数", "market": "HK",
        "region": "asia", "em_secid": "100.HSI", "source": "index_global",
    },
    {
        "ts_code": "N225", "name": "日经225", "market": "JP",
        "region": "asia", "em_secid": "100.N225", "source": "index_global",
    },
    {
        "ts_code": "KS11", "name": "韩国KOSPI", "market": "KR",
        "region": "asia", "em_secid": "100.KS11", "source": "index_global",
    },
    {
        "ts_code": "DJI", "name": "道琼斯", "market": "US",
        "region": "americas", "em_secid": "100.DJIA", "source": "index_global",
    },
    {
        "ts_code": "SPX", "name": "标普500", "market": "US",
        "region": "americas", "em_secid": "100.SPX", "source": "index_global",
    },
    {
        "ts_code": "IXIC", "name": "纳斯达克", "market": "US",
        "region": "americas", "em_secid": "100.NDX", "source": "index_global",
    },
]


def _today_sh() -> date:
    return datetime.now(_SH).date()


def _f(v: Any) -> float | None:
    """tushare 返回 NaN 表示缺值。"""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return float(v)


def _d(v: str) -> date:
    return datetime.strptime(str(v), "%Y%m%d").date()


def _map_index_global_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "ts_code": row["ts_code"],
        "trade_date": _d(row["trade_date"]),
        "open": _f(row.get("open")),
        "high": _f(row.get("high")),
        "low": _f(row.get("low")),
        "close": _f(row.get("close")),
        "volume": _f(row.get("vol")),
    }


def _get_tushare() -> Any:
    from app.core.providers.tushare_client import get_tushare_client  # noqa: PLC0415

    return get_tushare_client()


# asyncpg 单条语句 bind 参数上限 32767；index_dailies 每行 9 列 → 单批最多 ~3600 行，
# 分批 upsert 以支持多年回补（见 upsert_index_dailies 的多行 INSERT）。
_UPSERT_CHUNK = 2000


async def _upsert_rows(db: AsyncSession, rows: list[IndexDaily]) -> int:
    upserted = 0
    for i in range(0, len(rows), _UPSERT_CHUNK):
        upserted += await index_repo.upsert_index_dailies(db, rows[i : i + _UPSERT_CHUNK])
    return upserted


async def ingest_global_index_daily(db: AsyncSession, lookback_days: int = 14) -> dict[str, int]:
    """全球指数 + A股三大指数近 N 日日线 → index_dailies（幂等 upsert）。"""
    from app.models.index_daily import IndexDaily  # noqa: PLC0415

    client = _get_tushare()
    start = (_today_sh() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    end = _today_sh().strftime("%Y%m%d")
    rows: list[IndexDaily] = []
    for g in GLOBAL_INDICES:
        if g["source"] == "index_global":
            df: pd.DataFrame = await client.fetch_index_global(g["ts_code"], start, end)
        else:
            # fetch_index_daily 第二个位置参数是 trade_date，必须关键字传参
            df = await client.fetch_index_daily(
                ts_code=g["ts_code"], start_date=start, end_date=end
            )
        for rec in df.to_dict("records"):
            rows.append(IndexDaily(**_map_index_global_row(rec), amount=None))
    upserted = await _upsert_rows(db, rows)
    logger.info("ingest_global_index_daily lookback=%s upserted=%s", lookback_days, upserted)
    return {"upserted": upserted}


async def backfill_global_index_history(db: AsyncSession, years: int = 2) -> dict[str, int]:
    """一次性回补全球指数历史（供 spark30 与指数详情 K 线）。"""
    from app.models.index_daily import IndexDaily  # noqa: PLC0415

    client = _get_tushare()
    start = (_today_sh() - timedelta(days=365 * years)).strftime("%Y%m%d")
    end = _today_sh().strftime("%Y%m%d")
    rows: list[IndexDaily] = []
    for g in GLOBAL_INDICES:
        if g["source"] == "index_global":
            df: pd.DataFrame = await client.fetch_index_global(g["ts_code"], start, end)
        else:
            df = await client.fetch_index_daily(
                ts_code=g["ts_code"], start_date=start, end_date=end
            )
        for rec in df.to_dict("records"):
            rows.append(IndexDaily(**_map_index_global_row(rec), amount=None))
    upserted = await _upsert_rows(db, rows)
    logger.info("backfill_global_index_history years=%s upserted=%s", years, upserted)
    return {"upserted": upserted}


async def _main() -> None:
    from app.core.database import async_session_factory  # noqa: PLC0415

    args = sys.argv[1:]
    job = args[0] if args else ""
    async with async_session_factory() as db:
        if job == "global_index_daily":
            result = await ingest_global_index_daily(db)
        elif job == "backfill_global_index":
            result = await backfill_global_index_history(
                db, years=int(args[1]) if len(args) > 1 else 2
            )
        else:
            raise SystemExit(
                f"unknown job: {job}; available: global_index_daily, backfill_global_index"
            )
        await db.commit()
    print(job, "->", result)


if __name__ == "__main__":
    asyncio.run(_main())
