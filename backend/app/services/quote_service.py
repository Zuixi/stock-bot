"""Quote service: K-line and latest quote with caching."""

import logging
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import CacheClient
from app.repositories import quote_repo, stock_repo
from app.schemas.quote import DailyQuoteOut, KlineResponse, LatestQuoteOut

logger = logging.getLogger(__name__)


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
            out.append((datetime.strptime(td, "%Y%m%d").date(), float(factor)))
        except (ValueError, TypeError):
            continue
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
) -> KlineResponse | None:
    stock = await stock_repo.get_stock_by_symbol(db, exchange, symbol)
    if stock is None:
        return None

    start_str = start_date.isoformat() if start_date else "all"
    end_str = end_date.isoformat() if end_date else "all"
    cache_key = f"quote:kline:{exchange}:{symbol}:{start_str}:{end_str}"
    cached = await cache.get(cache_key)
    if cached:
        return KlineResponse(**cached)

    quotes = await quote_repo.get_kline(db, stock.id, start_date, end_date)
    data = [DailyQuoteOut.model_validate(q) for q in quotes]
    response = KlineResponse(symbol=symbol, name=stock.name, exchange=exchange, data=data)
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
