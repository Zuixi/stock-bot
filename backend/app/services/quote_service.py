"""Quote service: K-line and latest quote with caching."""

import logging
import math
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
