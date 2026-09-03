"""Quote service: K-line and latest quote with caching."""

import logging
import math
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import CacheClient
from app.repositories import quote_repo, stock_repo
from app.schemas.quote import DailyQuoteOut, KlineResponse, LatestQuoteOut

logger = logging.getLogger(__name__)

# 回补冷却：因子未发布期间防高频重拉 TuShare（API 层与 service 共用，避免魔法串）
ADJ_FACTOR_BACKFILL_CD_KEY = "quote:adj-factor:backfill-cd:{exchange}:{symbol}"
ADJ_FACTOR_BACKFILL_CD_TTL = 300


def kline_cache_key(
    exchange: str,
    symbol: str,
    start_date: date | None,
    end_date: date | None,
    adjust: str = "raw",
) -> str:
    """K线缓存 key：adjust 维度隔离 raw/qfq，日期缺省为 all。"""
    start_str = start_date.isoformat() if start_date else "all"
    end_str = end_date.isoformat() if end_date else "all"
    return f"quote:kline:{exchange}:{symbol}:{start_str}:{end_str}:{adjust}"


def map_adj_factor_rows(rows: list[dict]) -> list[tuple[date, float]]:
    """TuShare adj_factor 行 → (trade_date, factor)，日期/因子非法的行跳过。"""
    from datetime import datetime  # noqa: PLC0415

    out: list[tuple[date, float]] = []
    for row in rows:
        td = str(row.get("trade_date", "")).strip()
        factor = row.get("adj_factor")
        if len(td) != 8 or factor is None:
            continue
        try:
            parsed_date = datetime.strptime(td, "%Y%m%d").date()
            factor_val = float(factor)
        except (ValueError, TypeError):
            continue
        if not math.isfinite(factor_val):  # NaN/Inf 跳过，防整批 UPDATE 失败
            continue
        out.append((parsed_date, factor_val))
    return out


def apply_qfq(rows: list[DailyQuoteOut]) -> list[DailyQuoteOut] | None:
    """前复权：按日期升序，OHLC × 当日因子 ÷ 末行因子（基准日不动）round(2)。

    volume/amount 不动；空列表或任一 adj_factor 缺失返回 None（调用方回退原始行情）。
    """
    if not rows:
        return None
    ordered = sorted(rows, key=lambda r: r.trade_date)
    if any(r.adj_factor is None for r in ordered):
        return None
    base = ordered[-1].adj_factor
    assert base is not None  # for type checkers; guaranteed non-None above

    out: list[DailyQuoteOut] = []
    for r in ordered:
        factor = r.adj_factor
        assert factor is not None  # for type checkers; guaranteed non-None above
        ratio = factor / base
        out.append(
            r.model_copy(
                update={
                    "open": round(r.open * ratio, 2) if r.open is not None else None,
                    "high": round(r.high * ratio, 2) if r.high is not None else None,
                    "low": round(r.low * ratio, 2) if r.low is not None else None,
                    "close": round(r.close * ratio, 2),
                }
            )
        )
    return out


async def get_kline(
    db: AsyncSession,
    cache: CacheClient,
    exchange: str,
    symbol: str,
    start_date: date | None = None,
    end_date: date | None = None,
    adjust: str = "raw",
) -> KlineResponse | None:
    stock = await stock_repo.get_stock_by_symbol(db, exchange, symbol)
    if stock is None:
        return None

    cache_key = kline_cache_key(exchange, symbol, start_date, end_date, adjust)
    cached = await cache.get(cache_key)
    if cached:
        return KlineResponse(**cached)

    quotes = await quote_repo.get_kline(db, stock.id, start_date, end_date)
    data = [DailyQuoteOut.model_validate(q) for q in quotes]

    factors_complete = bool(data) and all(q.adj_factor is not None for q in quotes)
    if adjust == "qfq" and factors_complete:
        data = apply_qfq(data) or data

    response = KlineResponse(
        symbol=symbol,
        name=stock.name,
        exchange=exchange,
        data=data,
        adjust=adjust,
        adjust_available=factors_complete,
    )
    # qfq 且因子不完整 → 不缓存（回补完成后由 delete_pattern 兜底失效）
    if not (adjust == "qfq" and not factors_complete):
        await cache.set(cache_key, response.model_dump(mode="json"), ttl=600)
    return response


async def get_latest_quote(
    db: AsyncSession, cache: CacheClient, exchange: str, symbol: str
) -> LatestQuoteOut | None:
    stock = await stock_repo.get_stock_by_symbol(db, exchange, symbol)
    if stock is None:
        return None

    cache_key = f"quote:latest:{exchange}:{symbol}"
    cached = await cache.get(cache_key)
    if cached:
        return LatestQuoteOut(**cached)

    quote = await quote_repo.get_latest_quote(db, stock.id)
    if quote is None:
        return None

    out = LatestQuoteOut(
        symbol=symbol,
        name=stock.name,
        exchange=exchange,
        trade_date=quote.trade_date,
        close=float(quote.close),
        volume=quote.volume,
        amount=float(quote.amount) if quote.amount else None,
    )
    await cache.set(cache_key, out.model_dump(mode="json"), ttl=600)
    return out


async def backfill_adj_factor(exchange: str, symbol: str) -> dict[str, Any]:
    """懒加载回补单股 adj_factor 全历史（幂等）；完成后失效该股 kline 缓存。

    幂等口径：最新交易日行已有因子即 skip（每日 ingest 追加 NULL 新行后可增量再触发）。
    每次真实外呼后写 300s 冷却 key（API 层守卫消费），防因子未发布期间高频重拉。
    """
    from app.core.database import async_session_factory  # noqa: PLC0415 — 与 market_service 同源
    from app.core.providers.tushare_client import get_tushare_client  # noqa: PLC0415
    from app.core.redis import get_redis_pool  # noqa: PLC0415

    cd_key = ADJ_FACTOR_BACKFILL_CD_KEY.format(exchange=exchange, symbol=symbol)
    try:
        async with async_session_factory() as db:
            stock = await stock_repo.get_stock_by_symbol(db, exchange, symbol)
            if stock is None:
                return {"symbol": symbol, "status": "skipped", "reason": "stock not found"}
            if await quote_repo.latest_adj_factor_present(db, stock.id):
                return {"symbol": symbol, "status": "skipped", "reason": "already backfilled"}
            # ts_code 后缀：SH/SZ/BJ（EXCHANGE_TO_TUSHARE 是 SSE/SZSE/BSE 交易所码，不适用）
            suffix = {
                "Shanghai_Stocks": "SH",
                "Shenzen_Stocks": "SZ",
                "Beijing_Stocks": "BJ",
            }[exchange]
            df = await get_tushare_client().fetch_adj_factor(ts_code=f"{symbol}.{suffix}")
            factors = map_adj_factor_rows(df.to_dict("records"))
            updated = await quote_repo.update_adj_factors(db, stock.id, factors) if factors else 0
            await db.commit()
        redis = await get_redis_pool()
        cache = CacheClient(redis)
        await cache.delete_pattern(f"quote:kline:{exchange}:{symbol}:*")
        # 外呼已发起（结果完整/不完整均算）——进入冷却
        await cache.set(cd_key, 1, ttl=ADJ_FACTOR_BACKFILL_CD_TTL)
        logger.info("[adj_factor backfill] %s.%s updated=%d", exchange, symbol, updated)
        return {"symbol": symbol, "status": "ok", "updated": updated}
    except Exception as exc:  # noqa: BLE001 — 后台任务兜底，失败不影响响应
        logger.warning("[adj_factor backfill] %s.%s failed: %s", exchange, symbol, exc)
        try:  # 失败的尝试同样冷却，避免失败风暴
            redis = await get_redis_pool()
            await CacheClient(redis).set(cd_key, 1, ttl=ADJ_FACTOR_BACKFILL_CD_TTL)
        except Exception:  # noqa: BLE001,S110 — 冷却写入失败可容忍，下个请求重试
            pass
        return {"symbol": symbol, "status": "error", "reason": str(exc)}
