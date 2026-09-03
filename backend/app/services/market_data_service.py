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

from app.repositories import index_repo, market_data_repo

if TYPE_CHECKING:
    from app.core.redis import CacheClient
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


def _get_eastmoney() -> Any:
    from app.core.providers.eastmoney_client import get_eastmoney_client  # noqa: PLC0415

    return get_eastmoney_client()


# asyncpg 单条语句 bind 参数上限 32767；index_dailies 每行 9 列 → 单批最多 ~3600 行，
# 分批 upsert 以支持多年回补（见 upsert_index_dailies 的多行 INSERT）。
_UPSERT_CHUNK = 2000


async def _upsert_rows(db: AsyncSession, rows: list[IndexDaily]) -> int:
    upserted = 0
    for i in range(0, len(rows), _UPSERT_CHUNK):
        upserted += await index_repo.upsert_index_dailies(db, rows[i : i + _UPSERT_CHUNK])
    return upserted


async def _collect_index_rows(client: Any, start: str, end: str) -> list[IndexDaily]:
    """按注册表逐指数拉取并映射为 IndexDaily 行。

    单指数失败只告警并跳过（部分成功仍入库），避免一次抖动作废整个调度批次。
    """
    from app.models.index_daily import IndexDaily  # noqa: PLC0415

    rows: list[IndexDaily] = []
    for g in GLOBAL_INDICES:
        try:
            if g["source"] == "index_global":
                df: pd.DataFrame = await client.fetch_index_global(g["ts_code"], start, end)
            else:
                # fetch_index_daily 第二个位置参数是 trade_date，必须关键字传参
                df = await client.fetch_index_daily(
                    ts_code=g["ts_code"], start_date=start, end_date=end
                )
        except Exception:
            logger.warning("global index %s fetch failed", g["ts_code"], exc_info=True)
            continue
        for rec in df.to_dict("records"):
            rows.append(IndexDaily(**_map_index_global_row(rec), amount=None))
    return rows


async def ingest_global_index_daily(db: AsyncSession, lookback_days: int = 14) -> dict[str, int]:
    """全球指数 + A股三大指数近 N 日日线 → index_dailies（幂等 upsert）。"""
    client = _get_tushare()
    start = (_today_sh() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    end = _today_sh().strftime("%Y%m%d")
    rows = await _collect_index_rows(client, start, end)
    upserted = await _upsert_rows(db, rows)
    logger.info("ingest_global_index_daily lookback=%s upserted=%s", lookback_days, upserted)
    return {"upserted": upserted}


async def backfill_global_index_history(db: AsyncSession, years: int = 2) -> dict[str, int]:
    """一次性回补全球指数历史（供 spark30 与指数详情 K 线）。"""
    client = _get_tushare()
    start = (_today_sh() - timedelta(days=365 * years)).strftime("%Y%m%d")
    end = _today_sh().strftime("%Y%m%d")
    rows = await _collect_index_rows(client, start, end)
    upserted = await _upsert_rows(db, rows)
    logger.info("backfill_global_index_history years=%s upserted=%s", years, upserted)
    return {"upserted": upserted}


GLOBAL_INDICES_CACHE_KEY = "market:global-indices"
GLOBAL_INDICES_TTL = 60


async def get_global_index_cards(cache: CacheClient | None = None) -> list[dict[str, Any]]:
    """全球市场卡片：东财实时快照（60s 共享缓存）+ 近 30 日 spark + EOD 兜底。"""
    if cache is not None:
        cached = await cache.get(GLOBAL_INDICES_CACHE_KEY)
        if cached:
            cards_cached: list[dict[str, Any]] = cached
            return cards_cached

    quotes: dict[str, dict[str, Any]] = {}
    try:
        em = _get_eastmoney()
        snap = await em.fetch_index_snapshot([g["em_secid"] for g in GLOBAL_INDICES])
        quotes = {q["code"]: q for q in snap if q.get("code")}
    except Exception:
        logger.warning("global index snapshot fetch failed, falling back to EOD", exc_info=True)

    cards: list[dict[str, Any]] = []
    from app.core.database import async_session_factory  # noqa: PLC0415

    async with async_session_factory() as db:
        for g in GLOBAL_INDICES:
            spark: list[float] = []
            last_close: float | None = None
            try:
                rows = await index_repo.get_kline(db, g["ts_code"])
                spark = [float(r.close) for r in rows[-30:] if r.close is not None]
                last_close = spark[-1] if spark else None
            except Exception:
                logger.warning("spark fetch failed for %s", g["ts_code"], exc_info=True)

            q = quotes.get(_em_code(g["em_secid"]))
            now = datetime.now(_SH).isoformat(timespec="seconds")
            if q and q.get("price") is not None:
                cards.append({
                    "ts_code": g["ts_code"], "name": q.get("name") or g["name"],
                    "market": g["market"], "region": g["region"],
                    "price": q["price"], "change": q.get("change"),
                    "pct_change": q.get("pct_change"),
                    "spark": spark, "updated_at": now, "source": "realtime",
                })
            else:
                # 全球指数行 pre_close 为 NULL → 用相邻收盘价逐日差值算涨跌
                prev = spark[-2] if len(spark) >= 2 else None
                change = (
                    round(last_close - prev, 2)
                    if (last_close is not None and prev is not None) else None
                )
                pct = round(change / prev * 100, 2) if (change is not None and prev) else None
                cards.append({
                    "ts_code": g["ts_code"], "name": g["name"],
                    "market": g["market"], "region": g["region"],
                    "price": last_close, "change": change, "pct_change": pct,
                    "spark": spark, "updated_at": now, "source": "eod",
                })

    if cache is not None and any(c["price"] is not None for c in cards):
        await cache.set(GLOBAL_INDICES_CACHE_KEY, cards, ttl=GLOBAL_INDICES_TTL)
    return cards


def _em_code(secid: str) -> str:
    return secid.split(".", 1)[1]


SECTOR_MONEYFLOW_CACHE_KEY = "market:sector-moneyflow:{dimension}"
SECTOR_MONEYFLOW_TTL = 60
SECTOR_MONEYFLOW_CACHE_LIMIT = 100  # 端点 limit 上限（le=100）：缓存全量再按请求切片


async def ingest_sector_moneyflow(db: AsyncSession) -> dict[str, int]:
    """盘中轮询：industry/concept 两维当日快照 upsert。"""
    em = _get_eastmoney()
    today = _today_sh()
    result: dict[str, int] = {}
    for dimension in ("industry", "concept"):
        rows = await em.fetch_sector_moneyflow(dimension)
        result[dimension] = await market_data_repo.upsert_sector_moneyflow(
            db, today, dimension, rows
        )
    logger.info("ingest_sector_moneyflow %s", result)
    return result


async def get_sector_moneyflow(
    cache: CacheClient | None, dimension: str = "industry", limit: int = 15
) -> list[dict[str, Any]]:
    """当日板块主力资金流榜（Redis 60s 共享缓存；缓存 limit 上限行，按请求切片）。"""
    key = SECTOR_MONEYFLOW_CACHE_KEY.format(dimension=dimension)
    if cache is not None:
        cached = await cache.get(key)
        if cached:
            rows_cached: list[dict[str, Any]] = cached
            return rows_cached[:limit]

    from app.core.database import async_session_factory  # noqa: PLC0415

    rows: list[dict[str, Any]] = []
    async with async_session_factory() as db:
        for snap in await market_data_repo.list_sector_moneyflow(
            db, _today_sh(), dimension, SECTOR_MONEYFLOW_CACHE_LIMIT
        ):
            rows.append({
                "board_code": snap.board_code, "board_name": snap.board_name,
                "pct_change": snap.pct_change, "main_net_inflow": snap.main_net_inflow,
                "super_large_net": snap.super_large_net, "large_net": snap.large_net,
                "main_net_ratio": snap.main_net_ratio,
                "up_count": snap.up_count, "down_count": snap.down_count,
            })
    if cache is not None and rows:
        await cache.set(key, rows, ttl=SECTOR_MONEYFLOW_TTL)
    return rows[:limit]


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
        elif job == "sector_moneyflow":
            result = await ingest_sector_moneyflow(db)
        else:
            raise SystemExit(
                f"unknown job: {job}; available: global_index_daily, "
                "backfill_global_index, sector_moneyflow"
            )
        await db.commit()
    print(job, "->", result)


if __name__ == "__main__":
    asyncio.run(_main())
