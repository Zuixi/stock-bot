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


def _d_opt(v: Any) -> date | None:
    """可空日期字段（share_float.ann_date / repurchase.end_date、exp_date 常为 NaN）。"""
    if v is None or pd.isna(v):
        return None
    return _d(str(v))


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
    """盘中轮询：industry/concept/region 三维当日快照 upsert。"""
    em = _get_eastmoney()
    today = _today_sh()
    result: dict[str, int] = {}
    for dimension in ("industry", "concept", "region"):
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
                "lead_stock_name": snap.lead_stock_name, "lead_stock_code": snap.lead_stock_code,
                "lead_stock_pct": snap.lead_stock_pct,
            })
    if cache is not None and rows:
        await cache.set(key, rows, ttl=SECTOR_MONEYFLOW_TTL)
    return rows[:limit]


MARKET_MONEYFLOW_CACHE_KEY = "market:market-moneyflow"
MARKET_MONEYFLOW_TTL = 60


async def ingest_market_moneyflow_daily(db: AsyncSession, days: int = 10) -> dict[str, int]:
    """大盘资金流日线（东财 fflow/daykline 沪深合成）幂等 upsert。"""
    client = _get_eastmoney()
    rows = await client.fetch_market_moneyflow_daily(days)
    upserted = await market_data_repo.upsert_market_moneyflow_daily(db, rows)
    logger.info("ingest_market_moneyflow_daily days=%s upserted=%s", days, upserted)
    return {"upserted": upserted}


async def get_market_moneyflow(cache: Any | None) -> dict[str, Any]:
    """大盘资金流：今日四档实时（ulist 合计，不落表）+ 近 30 日历史（表内）。"""
    if cache is not None:
        cached: dict[str, Any] | None = await cache.get(MARKET_MONEYFLOW_CACHE_KEY)
        if cached:
            return cached
    try:
        today = await _get_eastmoney().fetch_market_moneyflow_today()
    except Exception:
        logger.warning("market moneyflow today fetch failed", exc_info=True)
        today = None
    history: list[dict[str, Any]] = []
    from app.core.database import async_session_factory  # noqa: PLC0415

    async with async_session_factory() as db:
        for row in await market_data_repo.list_market_moneyflow_daily(db, 30):
            history.append({
                "date": row.trade_date.isoformat(), "main_net": row.main_net,
                "super_large_net": row.super_large_net, "large_net": row.large_net,
                "mid_net": row.mid_net, "small_net": row.small_net,
                "main_ratio": row.main_ratio, "close": row.close,
                "pct_change": row.pct_change, "amount": row.amount,
            })
    payload = {"today": today, "history": history}
    if cache is not None and (history or today):
        await cache.set(MARKET_MONEYFLOW_CACHE_KEY, payload, ttl=MARKET_MONEYFLOW_TTL)
    return payload



def _map_top_list_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    """top_list → dragon tiger rows（金额元；reason 列 String(160)，超长截断防 DB 报错）。"""
    rows: list[dict[str, Any]] = []
    for rec in df.to_dict("records"):
        rows.append({
            "trade_date": _d(rec["trade_date"]), "ts_code": rec["ts_code"],
            "symbol": rec["ts_code"].split(".")[0],
            "name": rec.get("name"), "close": _f(rec.get("close")),
            "pct_change": _f(rec.get("pct_change")), "turnover_rate": _f(rec.get("turnover_rate")),
            "amount": _f(rec.get("amount")),
            "l_buy": _f(rec.get("l_buy")), "l_sell": _f(rec.get("l_sell")),
            "l_amount": _f(rec.get("l_amount")), "net_amount": _f(rec.get("net_amount")),
            "net_rate": _f(rec.get("net_rate")), "amount_rate": _f(rec.get("amount_rate")),
            "float_values": _f(rec.get("float_values")),
            "reason": str(rec.get("reason") or "")[:160],
        })
    return rows


def _map_block_trade_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    """block_trade → block trade rows（price 元 / vol 万股 / amount 万元）。"""
    rows: list[dict[str, Any]] = []
    for rec in df.to_dict("records"):
        rows.append({
            "trade_date": _d(rec["trade_date"]), "ts_code": rec["ts_code"],
            "symbol": rec["ts_code"].split(".")[0],
            "price": _f(rec.get("price")), "volume": _f(rec.get("vol")),
            "amount": _f(rec.get("amount")), "buyer": rec.get("buyer"), "seller": rec.get("seller"),
        })
    return rows


def _dedupe_block_trade_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按去重键（date+code+buyer+seller+price+volume）同批去重，保留末次出现。

    ON CONFLICT 只处理与既有行的冲突、不处理同批 INSERT 内自冲突，须先在 Python 端去重。
    """
    deduped = {
        (r["trade_date"], r["ts_code"], r["buyer"], r["seller"], r["price"], r["volume"]): r
        for r in rows
    }
    return list(deduped.values())


def _map_hsgt_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    """moneyflow_hsgt 全列字符串 → northbound rows（north_money 万元，缺失/NaN 置 None）。"""
    rows: list[dict[str, Any]] = []
    for rec in df.to_dict("records"):
        raw = rec.get("north_money")
        net: float | None = None
        if raw is not None:
            try:
                f = float(raw)
            except (TypeError, ValueError):
                f = float("nan")
            net = None if math.isnan(f) else f
        rows.append({"trade_date": _d(rec["trade_date"]), "net_amount": net})
    return rows


async def ingest_northbound(db: AsyncSession, days: int = 30) -> dict[str, int]:
    """盘后采集：近 N 日沪深港通北向净流入（moneyflow_hsgt，幂等 upsert）。"""
    client = _get_tushare()
    start = (_today_sh() - timedelta(days=days)).strftime("%Y%m%d")
    end = _today_sh().strftime("%Y%m%d")
    df = await client.fetch_moneyflow_hsgt(start_date=start, end_date=end)
    upserted = await market_data_repo.upsert_northbound(db, _map_hsgt_rows(df))
    logger.info("ingest_northbound days=%s upserted=%s", days, upserted)
    return {"upserted": upserted}


NORTHBOUND_CACHE_KEY = "market:northbound:{days}"
NORTHBOUND_TTL = 300


async def get_northbound_series(cache: CacheClient | None, days: int = 30) -> list[dict[str, Any]]:
    """北向净流入日序列（升序，Redis 300s 共享缓存）。"""
    key = NORTHBOUND_CACHE_KEY.format(days=days)
    if cache is not None:
        cached = await cache.get(key)
        if cached:
            rows_cached: list[dict[str, Any]] = cached
            return rows_cached

    from app.core.database import async_session_factory  # noqa: PLC0415

    rows: list[dict[str, Any]] = []
    async with async_session_factory() as db:
        for n in await market_data_repo.list_northbound(db, days):
            rows.append({"date": n.trade_date.isoformat(), "net_amount": n.net_amount})
    if cache is not None and rows:
        await cache.set(key, rows, ttl=NORTHBOUND_TTL)
    return rows


def _map_share_float_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    """share_float → share float rows（float_share 万股 / float_ratio %；ann_date 可空）。"""
    rows: list[dict[str, Any]] = []
    for rec in df.to_dict("records"):
        rows.append({
            "ann_date": _d_opt(rec.get("ann_date")), "float_date": _d(rec["float_date"]),
            "ts_code": rec["ts_code"], "symbol": rec["ts_code"].split(".")[0],
            "float_share": _f(rec.get("float_share")), "float_ratio": _f(rec.get("float_ratio")),
            "holder_name": rec.get("holder_name"), "share_type": rec.get("share_type"),
        })
    return rows


def _map_repurchase_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    """repurchase → repurchase rows（vol 股 / amount 元；exp_date 常为 NaN → None）。"""
    rows: list[dict[str, Any]] = []
    for rec in df.to_dict("records"):
        rows.append({
            "ann_date": _d(rec["ann_date"]), "ts_code": rec["ts_code"],
            "symbol": rec["ts_code"].split(".")[0],
            "end_date": _d_opt(rec.get("end_date")), "proc": str(rec.get("proc") or "")[:16],
            "exp_date": _d_opt(rec.get("exp_date")),
            "vol": _f(rec.get("vol")), "amount": _f(rec.get("amount")),
            "high_limit": _f(rec.get("high_limit")), "low_limit": _f(rec.get("low_limit")),
        })
    return rows


def _dedupe_repurchase_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按去重键（ann_date+ts_code+proc）同批去重，保留末次出现。

    ON CONFLICT 只处理与既有行的冲突、不处理同批 INSERT 内自冲突，须先在 Python 端去重。
    """
    return list({
        (r["ann_date"], r["ts_code"], r["proc"]): r for r in rows
    }.values())


_CATCHUP_LOOKBACK_DAYS = 10


async def _open_trading_days_since(
    client: Any, last_collected: date | None, today: date
) -> list[date]:
    """(last_collected, today] 内的交易日，升序。

    last_collected 为 None（空表/首次）时从 today-回看窗口 起算，窗口整体不超过
    _CATCHUP_LOOKBACK_DAYS 个日历日——调度错过的日子逐日补拉，避免"只拉当天、
    错过即永久缺口 / 当日 18:00 数据未发布则整天丢失"。
    """
    start = last_collected + timedelta(days=1) if last_collected else today - timedelta(
        days=_CATCHUP_LOOKBACK_DAYS
    )
    if start > today:
        return []
    df = await client.fetch_trade_cal(
        start_date=start.strftime("%Y%m%d"), end_date=today.strftime("%Y%m%d"), is_open="1"
    )
    days = [_d(rec["cal_date"]) for rec in df.to_dict("records") if rec.get("cal_date")]
    return days[-_CATCHUP_LOOKBACK_DAYS:]


async def ingest_dragon_tiger(db: AsyncSession, trade_date: date | None = None) -> dict[str, int]:
    """盘后采集龙虎榜个股明细（top_list，幂等 upsert）。

    指定 trade_date → 只拉当日；None（调度/CLI 缺省）→ 补漏模式：自表内最新
    交易日起把缺失的交易日逐日拉齐（含当日；当日榜单未发布则下次运行自动重试）。
    """
    client = _get_tushare()
    if trade_date is not None:
        df = await client.fetch_top_list(trade_date.strftime("%Y%m%d"))
        upserted = await market_data_repo.upsert_dragon_tiger(db, _map_top_list_rows(df))
        logger.info("ingest_dragon_tiger trade_date=%s upserted=%s", trade_date, upserted)
        return {"upserted": upserted, "days": 1}
    last = await market_data_repo.max_dragon_tiger_date(db)
    days = await _open_trading_days_since(client, last, _today_sh())
    upserted = 0
    for day in days:
        try:
            df = await client.fetch_top_list(day.strftime("%Y%m%d"))
        except Exception:
            logger.warning("dragon_tiger fetch failed for %s", day, exc_info=True)
            continue
        upserted += await market_data_repo.upsert_dragon_tiger(db, _map_top_list_rows(df))
    logger.info(
        "ingest_dragon_tiger catchup days=%s upserted=%s", [d.isoformat() for d in days], upserted
    )
    return {"upserted": upserted, "days": len(days)}


async def ingest_block_trades(db: AsyncSession, trade_date: date | None = None) -> dict[str, int]:
    """盘后采集大宗交易明细（block_trade）。

    行无稳定业务主键 → DO NOTHING；同批重复行先 Python 端去重（见 _dedupe_block_trade_rows）。
    指定 trade_date → 只拉当日；None → 补漏模式（同 ingest_dragon_tiger）。
    """
    client = _get_tushare()
    if trade_date is not None:
        df = await client.fetch_block_trade(trade_date.strftime("%Y%m%d"))
        rows = _dedupe_block_trade_rows(_map_block_trade_rows(df))
        upserted = await market_data_repo.upsert_block_trades(db, rows)
        logger.info("ingest_block_trades trade_date=%s upserted=%s", trade_date, upserted)
        return {"upserted": upserted, "days": 1}
    last = await market_data_repo.max_block_trade_date(db)
    days = await _open_trading_days_since(client, last, _today_sh())
    upserted = 0
    for day in days:
        try:
            df = await client.fetch_block_trade(day.strftime("%Y%m%d"))
        except Exception:
            logger.warning("block_trade fetch failed for %s", day, exc_info=True)
            continue
        rows = _dedupe_block_trade_rows(_map_block_trade_rows(df))
        upserted += await market_data_repo.upsert_block_trades(db, rows)
    logger.info(
        "ingest_block_trades catchup days=%s upserted=%s", [d.isoformat() for d in days], upserted
    )
    return {"upserted": upserted, "days": len(days)}


DRAGON_TIGER_CACHE_KEY = "market:dragon-tiger:{date}"
DRAGON_TIGER_TTL = 300
DRAGON_TIGER_CACHE_LIMIT = 100  # 端点 limit 上限（le=100）：缓存全量再按请求切片


async def get_dragon_tiger(
    cache: CacheClient | None, date_iso: str | None = None, limit: int = 15
) -> list[dict[str, Any]]:
    """某交易日龙虎榜明细（date 缺省取表内最新 trade_date；Redis 300s 共享缓存）。"""
    day = datetime.fromisoformat(date_iso).date() if date_iso else None

    from app.core.database import async_session_factory  # noqa: PLC0415

    rows: list[dict[str, Any]] = []
    async with async_session_factory() as db:
        effective = day or await market_data_repo.max_dragon_tiger_date(db)
        if effective is None:
            return []
        key = DRAGON_TIGER_CACHE_KEY.format(date=effective.isoformat())
        if cache is not None:
            cached = await cache.get(key)
            if cached:
                rows_cached: list[dict[str, Any]] = cached
                return rows_cached[:limit]
        for t in await market_data_repo.list_dragon_tiger(
            db, effective, DRAGON_TIGER_CACHE_LIMIT
        ):
            rows.append({
                "trade_date": t.trade_date.isoformat(), "ts_code": t.ts_code,
                "symbol": t.ts_code.split(".")[0], "name": t.name, "close": t.close,
                "pct_change": t.pct_change, "turnover_rate": t.turnover_rate, "amount": t.amount,
                "l_buy": t.l_buy, "l_sell": t.l_sell, "l_amount": t.l_amount,
                "net_amount": t.net_amount, "reason": t.reason,
            })
    if cache is not None and rows:
        await cache.set(key, rows, ttl=DRAGON_TIGER_TTL)
    return rows[:limit]


BLOCK_TRADES_CACHE_KEY = "market:block-trades:{date}:{symbol}"
BLOCK_TRADES_TTL = 300
BLOCK_TRADES_CACHE_LIMIT = 100  # 端点 limit 上限（le=100）：缓存全量再按请求切片


async def get_block_trades(
    cache: CacheClient | None,
    date_iso: str | None = None,
    symbol: str | None = None,
    limit: int = 15,
) -> list[dict[str, Any]]:
    """某交易日大宗交易明细（date 缺省取表内最新；symbol 为 6 位代码；Redis 300s 共享缓存）。"""
    day = datetime.fromisoformat(date_iso).date() if date_iso else None

    from app.core.database import async_session_factory  # noqa: PLC0415

    rows: list[dict[str, Any]] = []
    async with async_session_factory() as db:
        effective = day or await market_data_repo.max_block_trade_date(db)
        if effective is None:
            return []
        key = BLOCK_TRADES_CACHE_KEY.format(date=effective.isoformat(), symbol=symbol or "all")
        if cache is not None:
            cached = await cache.get(key)
            if cached:
                rows_cached: list[dict[str, Any]] = cached
                return rows_cached[:limit]
        rows = await market_data_repo.list_block_trades(
            db, effective, symbol, BLOCK_TRADES_CACHE_LIMIT
        )
    if cache is not None and rows:
        await cache.set(key, rows, ttl=BLOCK_TRADES_TTL)
    return rows[:limit]


async def ingest_share_floats(db: AsyncSession, days: int = 7) -> dict[str, int]:
    """盘后采集：近 N 日公告的限售解禁计划（share_float，TuShare 按 ann_date 过滤，DO NOTHING）。"""
    client = _get_tushare()
    start = (_today_sh() - timedelta(days=days)).strftime("%Y%m%d")
    end = _today_sh().strftime("%Y%m%d")
    df = await client.fetch_share_float(start_date=start, end_date=end)
    upserted = await market_data_repo.upsert_share_floats(db, _map_share_float_rows(df))
    logger.info("ingest_share_floats days=%s upserted=%s", days, upserted)
    return {"upserted": upserted}


async def ingest_repurchases(db: AsyncSession, days: int = 7) -> dict[str, int]:
    """盘后采集：近 N 日股票回购进度（repurchase，进度/数量会修订 → DO UPDATE 幂等 upsert）。"""
    client = _get_tushare()
    start = (_today_sh() - timedelta(days=days)).strftime("%Y%m%d")
    end = _today_sh().strftime("%Y%m%d")
    df = await client.fetch_repurchase(start_date=start, end_date=end)
    rows = _dedupe_repurchase_rows(_map_repurchase_rows(df))
    upserted = await market_data_repo.upsert_repurchases(db, rows)
    logger.info("ingest_repurchases days=%s upserted=%s", days, upserted)
    return {"upserted": upserted}


SHARE_FLOATS_CACHE_KEY = "market:share-floats:{start}:{end}:{symbol}"
SHARE_FLOATS_TTL = 300
SHARE_FLOATS_CACHE_LIMIT = 100  # 端点 limit 上限（le=100）：缓存全量再按请求切片


async def get_share_floats(
    cache: CacheClient | None,
    start_iso: str | None = None,
    end_iso: str | None = None,
    symbol: str | None = None,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """解禁时间表（按 float_date 过滤，缺省近 30 天至未来 90 天——解禁是未来事件；Redis 300s）。"""
    end = datetime.fromisoformat(end_iso).date() if end_iso else _today_sh() + timedelta(days=90)
    start = (
        datetime.fromisoformat(start_iso).date() if start_iso else _today_sh() - timedelta(days=30)
    )
    key = SHARE_FLOATS_CACHE_KEY.format(
        start=start.isoformat(), end=end.isoformat(), symbol=symbol or "all"
    )
    if cache is not None:
        cached = await cache.get(key)
        if cached:
            rows_cached: list[dict[str, Any]] = cached
            return rows_cached[:limit]

    from app.core.database import async_session_factory  # noqa: PLC0415

    rows: list[dict[str, Any]] = []
    async with async_session_factory() as db:
        rows = await market_data_repo.list_share_floats(
            db, start, end, symbol, SHARE_FLOATS_CACHE_LIMIT
        )
    if cache is not None and rows:
        await cache.set(key, rows, ttl=SHARE_FLOATS_TTL)
    return rows[:limit]


REPURCHASES_CACHE_KEY = "market:repurchases:{start}:{end}:{symbol}"
REPURCHASES_TTL = 300
REPURCHASES_CACHE_LIMIT = 100  # 端点 limit 上限（le=100）：缓存全量再按请求切片


async def get_repurchases(
    cache: CacheClient | None,
    start_iso: str | None = None,
    end_iso: str | None = None,
    symbol: str | None = None,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """股票回购进度（按 ann_date 过滤，缺省近 30 天至今；Redis 300s 共享缓存）。"""
    end = datetime.fromisoformat(end_iso).date() if end_iso else _today_sh()
    start = (
        datetime.fromisoformat(start_iso).date() if start_iso else _today_sh() - timedelta(days=30)
    )
    key = REPURCHASES_CACHE_KEY.format(
        start=start.isoformat(), end=end.isoformat(), symbol=symbol or "all"
    )
    if cache is not None:
        cached = await cache.get(key)
        if cached:
            rows_cached2: list[dict[str, Any]] = cached
            return rows_cached2[:limit]

    from app.core.database import async_session_factory  # noqa: PLC0415

    rows: list[dict[str, Any]] = []
    async with async_session_factory() as db:
        rows = await market_data_repo.list_repurchases(
            db, start, end, symbol, REPURCHASES_CACHE_LIMIT
        )
    if cache is not None and rows:
        await cache.set(key, rows, ttl=REPURCHASES_TTL)
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
        elif job == "northbound":
            result = await ingest_northbound(db)
        elif job == "market_moneyflow":
            result = await ingest_market_moneyflow_daily(
                db, days=int(args[1]) if len(args) > 1 else 10
            )
        elif job == "dragon_tiger":
            result = await ingest_dragon_tiger(db, _d(args[1]) if len(args) > 1 else None)
        elif job == "block_trades":
            result = await ingest_block_trades(db, _d(args[1]) if len(args) > 1 else None)
        elif job == "share_floats":
            result = await ingest_share_floats(db, days=int(args[1]) if len(args) > 1 else 7)
        elif job == "repurchases":
            result = await ingest_repurchases(db, days=int(args[1]) if len(args) > 1 else 7)
        elif job == "announcements":
            from app.services import announcement_service  # noqa: PLC0415

            result = await announcement_service.ingest_announcements(
                db, days=int(args[1]) if len(args) > 1 else 3
            )
        else:
            raise SystemExit(
                f"unknown job: {job}; available: global_index_daily, "
                "backfill_global_index, sector_moneyflow, northbound, dragon_tiger, "
                "block_trades, share_floats, repurchases, announcements"
            )
        await db.commit()
    print(job, "->", result)


if __name__ == "__main__":
    asyncio.run(_main())
