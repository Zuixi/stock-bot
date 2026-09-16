"""对账收敛器：数据面完整性由对账保证，不依赖"任务准点跑"（level-triggered）。

期望集 = trade_cal 最近 N 个交易日（截止昨日，T-1 语义）；完整判据 = 行数量级
（≥ 0.8×全市场数，防 partial 行挡住补齐的死锁）。cron 只负责 timeliness，
任何停摆（宿主睡眠/容器挂起/任务异常）在下一个对账触发点自动收敛。
设计与事故时间线见 plans/2026-09-17-data-sync-self-healing.md。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select

from app.models.daily_basic import DailyBasicIndicator
from app.models.market_data import MarketSentimentDaily, StockPriceLimit
from app.models.quote import DailyQuote
from app.models.stock import Stock
from app.services.market_data_service import _d, _get_tushare, _today_sh

logger = logging.getLogger(__name__)

COMPLETENESS_RATIO = 0.8
DEFAULT_WINDOW_DAYS = 10

# 对账域（顺序即依赖序：sentiment 依赖 quotes + price_limits 完备）
DOMAINS: tuple[str, ...] = ("daily_quotes", "daily_basic", "price_limits", "sentiment")

_ROW_MODEL: dict[str, type[Any]] = {
    "daily_quotes": DailyQuote,
    "daily_basic": DailyBasicIndicator,
    "price_limits": StockPriceLimit,
    "sentiment": MarketSentimentDaily,
}


async def expected_trade_dates(
    client: Any, *, window_days: int = DEFAULT_WINDOW_DAYS, today: date | None = None
) -> tuple[list[date], bool]:
    """最近 window_days 个交易日（截止**昨日**，升序）。

    当日 TuShare 数据未出全，拉了会写 partial（9/16 事故）——当日不属于期望集。
    trade_cal 不可用时降级为工作日启发式并返回 degraded=True（节假日会多试几个
    空日，ingest 空结果无害）。
    """
    today = today or _today_sh()
    end = today - timedelta(days=1)
    start = end - timedelta(days=window_days * 2 + 5)  # 覆盖节假日的日历跨度
    try:
        df = await client.fetch_trade_cal(
            start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"), is_open="1"
        )
        days = sorted({_d(rec["cal_date"]) for rec in df.to_dict("records") if rec.get("cal_date")})
        degraded = False
    except Exception:
        logger.warning("trade_cal unavailable — degrading to weekday heuristic", exc_info=True)
        days = [
            start + timedelta(days=i)
            for i in range((end - start).days + 1)
            if (start + timedelta(days=i)).weekday() < 5
        ]
        degraded = True
    return days[-window_days:], degraded


async def _stock_universe_count(db: Any) -> int:

    result = await db.execute(select(func.count()).select_from(Stock))
    return int(result.scalar() or 0)


async def _row_counts(db: Any, domain: str, dates: list[date]) -> dict[date, int]:
    """各期望日的行数（域 → 表模型分派；sentiment 一行即一天）。"""
    model = _ROW_MODEL[domain]
    stmt = (
        select(model.trade_date, func.count())
        .where(model.trade_date.in_(dates))
        .group_by(model.trade_date)
    )
    rows = (await db.execute(stmt)).all()
    return {d: int(c) for d, c in rows}  # type: ignore[call-overload]


async def _refetch_daily_quotes(db: Any, day: date) -> dict[str, Any]:
    from app.services.tushare_ingest import TuShareIngestService  # noqa: PLC0415

    return await TuShareIngestService().ingest_daily_quotes(db, day.strftime("%Y%m%d"))


async def _refetch_daily_basic(db: Any, day: date) -> dict[str, Any]:
    from app.services.tushare_ingest import TuShareIngestService  # noqa: PLC0415

    return await TuShareIngestService().ingest_daily_basic(db, day.strftime("%Y%m%d"))


async def _refetch_price_limits(db: Any, day: date) -> dict[str, Any]:
    from app.services import market_data_service  # noqa: PLC0415

    # 显式 trade_date → todo=[day]：partial 日也会被强制重拉（upsert 合并）
    return await market_data_service.ingest_stock_price_limits(db, trade_date=day)


async def _refetch_sentiment(db: Any, day: date) -> dict[str, Any]:
    from app.services import limit_up_service  # noqa: PLC0415

    return await limit_up_service.persist_snapshot(db, None, as_of=day)


async def _commit(db: Any) -> None:
    """sentiment 的 get_snapshot 走独立会话——底座补数必须先提交才可见。"""
    await db.commit()


async def _domain_status(
    counts: dict[date, int], expected: list[date], threshold: int
) -> dict[str, Any]:
    missing = [d for d in expected if counts.get(d, 0) == 0]
    partial = [d for d in expected if 0 < counts.get(d, 0) < threshold]
    latest = max((d for d in expected if counts.get(d, 0) > 0), default=None)
    return {"latest_in_db": latest, "missing_days": missing, "partial_days": partial}


async def reconcile_market_data(
    db: Any,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    apply: bool = True,
    only: set[str] | None = None,
    client: Any = None,
    today: date | None = None,
) -> dict[str, Any]:
    """对账并（默认）补齐窗口内缺口；apply=False 为只读巡检（freshness 端点复用）。

    幂等可重放：所有回补走既有 upsert/DO-NOTHING ingest，任意重跑收敛到同一结果。
    """
    scope = set(only) if only is not None else set(DOMAINS)
    unknown = scope - set(DOMAINS)
    if unknown:
        raise ValueError(f"unknown reconcile domains: {sorted(unknown)}")
    client = client or _get_tushare()
    expected, degraded = await expected_trade_dates(client, window_days=window_days, today=today)
    universe = await _stock_universe_count(db)
    threshold = int(universe * COMPLETENESS_RATIO)

    result: dict[str, Any] = {
        "as_of": (today or _today_sh()).isoformat(),
        "expected_latest": expected[-1].isoformat() if expected else None,
        "expected_days": len(expected),
        "universe": universe,
        "degraded_calendar": degraded,
        "apply": apply,
        "domains": {},
    }

    # ── 底座域（quotes → basic → price_limits），sentiment 前统一 commit ──
    refetched_any = False
    for domain in ("daily_quotes", "daily_basic", "price_limits"):
        counts = await _row_counts(db, domain, expected)
        status = await _domain_status(counts, expected, threshold)
        todo = sorted(status["missing_days"] + status["partial_days"])
        refetched: list[date] = []
        if apply and todo and domain in scope:
            for day in todo:
                await globals()[f"_refetch_{domain}"](db, day)
                refetched.append(day)
            refetched_any = True
            status["latest_in_db"] = max(
                (d for d in expected if d in refetched or counts.get(d, 0) > 0), default=None
            )
        result["domains"][domain] = {
            "latest_in_db": status["latest_in_db"].isoformat() if status["latest_in_db"] else None,
            "missing_days": [d.isoformat() for d in status["missing_days"]],
            "partial_days": [d.isoformat() for d in status["partial_days"]],
            "refetched": [d.isoformat() for d in refetched],
            "status": _status_word(apply, bool(refetched), bool(todo)),
        }

    # 底座补数必须先提交：persist_snapshot → get_snapshot 走独立会话，
    # 未提交的 upsert 对它不可见（READ COMMITTED），会把刚补的日子误判 partial。
    if apply and (refetched_any or "sentiment" in scope):
        await _commit(db)

    # ── 派生域（sentiment）：仅 quotes + price_limits 完备的期望日才补 ──
    if expected:
        quotes_counts = await _row_counts(db, "daily_quotes", expected)
        limits_counts = await _row_counts(db, "price_limits", expected)
        base_complete = {
            d
            for d in expected
            if quotes_counts.get(d, 0) >= threshold and limits_counts.get(d, 0) >= threshold
        }
    else:
        base_complete = set()
    sent_counts = await _row_counts(db, "sentiment", expected)
    sent_status = await _domain_status(sent_counts, expected, threshold=1)
    sent_todo = [d for d in sent_status["missing_days"] if d in base_complete]
    sent_refetched: list[date] = []
    if apply and sent_todo and "sentiment" in scope:
        for day in sent_todo:
            await _refetch_sentiment(db, day)
            sent_refetched.append(day)
        if sent_refetched:
            await _commit(db)  # 情绪行落库 + persist 内已失效 calendar 缓存
    result["domains"]["sentiment"] = {
        "latest_in_db": sent_status["latest_in_db"].isoformat()
        if sent_status["latest_in_db"]
        else None,
        "missing_days": [d.isoformat() for d in sent_status["missing_days"]],
        "partial_days": [],
        "refetched": [d.isoformat() for d in sent_refetched],
        "status": _status_word(apply, bool(sent_refetched), bool(sent_status["missing_days"])),
    }

    _log_summary(result)
    return result


def _status_word(apply: bool, refetched: bool, has_gaps: bool) -> str:
    if refetched:
        return "refetched"
    if has_gaps and not apply:
        return "stale"
    return "ok"


def _log_summary(result: dict[str, Any]) -> None:
    gaps = {
        dom: v["missing_days"] + v["partial_days"]
        for dom, v in result["domains"].items()
        if v["missing_days"] or v["partial_days"]
    }
    if gaps:
        logger.warning(
            "RECONCILE gaps=%s apply=%s degraded_calendar=%s",
            gaps,
            result["apply"],
            result["degraded_calendar"],
        )
    else:
        logger.info("RECONCILE all domains ok (expected_latest=%s)", result["expected_latest"])
