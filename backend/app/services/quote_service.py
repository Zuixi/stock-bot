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

# 交易所码 → TuShare 代码后缀（EXCHANGE_TO_TUSHARE 是 SSE/SZSE/BSE，不适用）
EXCHANGE_TO_TS_SUFFIX: dict[str, str] = {
    "Shanghai_Stocks": "SH",
    "Shenzen_Stocks": "SZ",
    "Beijing_Stocks": "BJ",
}


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
            suffix = EXCHANGE_TO_TS_SUFFIX[exchange]
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


async def backfill_missing_adj_factors(
    db: AsyncSession, *, start: date, end: date
) -> dict[str, int]:
    """回补 ``[start, end]`` 内缺失的 ``adj_factor``（UPDATE-only，加法式修复）。

    与 :func:`backfill_adj_factor` 的分工：那个是**懒加载**（单股全历史，触发条件
    是"最新行没有因子"）；这个是**批量补洞**，治的是因子已拉过、个别交易日的行
    为 NULL —— 补灌缺失交易日时新插入的行只有 TuShare ``daily`` 的 OHLC，没有
    因子，而 upsert 的 COALESCE 与懒加载的"最新行"判据都不会再碰它们。后果就是
    K 线卡片的复权开关永久禁用（``get_kline`` 要求请求窗口内**全部**行有因子）。

    只改写**缺口行**（``update_adj_factors`` 按 ``(stock_id, trade_date)`` 匹配，传入的
    日期集合即缺口集合），因此 ``rows`` 就是真正被填上的缺口行数，不含同值重写。

    返回 ``{"stocks": 处理的缺口股票数, "rows": 填入的缺口行数, "failed": 失败股票数}``。
    单股失败（外呼/权限/脏数据）只记数不中断：一次批量修复不该被一只股票毁掉。
    """
    from app.core.providers.tushare_client import get_tushare_client  # noqa: PLC0415

    stats = {"stocks": 0, "rows": 0, "failed": 0}
    pairs = await quote_repo.list_missing_adj_factor_pairs(db, start, end)
    if not pairs:  # 无缺口 → 不外呼
        return stats
    gaps: dict[int, set[date]] = {}
    for stock_id, trade_date in pairs:
        gaps.setdefault(stock_id, set()).add(trade_date)

    client = get_tushare_client()
    start_str, end_str = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    for stock_id, missing_days in gaps.items():
        stock = await stock_repo.get_stock_by_id(db, stock_id)
        suffix = EXCHANGE_TO_TS_SUFFIX.get(stock.exchange) if stock is not None else None
        if stock is None or suffix is None:
            stats["failed"] += 1
            logger.warning("[adj_factor repair] stock_id=%s unusable (missing/suffix)", stock_id)
            continue
        try:
            df = await client.fetch_adj_factor(
                ts_code=f"{stock.symbol}.{suffix}",
                start_date=start_str,
                end_date=end_str,
            )
            factors = [
                (d, f) for d, f in map_adj_factor_rows(df.to_dict("records")) if d in missing_days
            ]
            if factors:
                stats["rows"] += await quote_repo.update_adj_factors(db, stock_id, factors)
            await db.commit()
            stats["stocks"] += 1
        except Exception as exc:  # noqa: BLE001 — 单股失败不中断整批修复
            await db.rollback()
            stats["failed"] += 1
            logger.warning("[adj_factor repair] %s.%s failed: %s", stock.symbol, suffix, exc)

    logger.info(
        "[adj_factor repair] %s..%s stocks=%d rows=%d failed=%d",
        start_str,
        end_str,
        stats["stocks"],
        stats["rows"],
        stats["failed"],
    )
    return stats
