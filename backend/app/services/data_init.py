"""First-boot data initialisation.

Detects an empty ``stocks`` table and asynchronously seeds the database
with stock universe + trailing 3-year daily quote coverage from TuShare Pro.

This runs as a background ``asyncio.Task`` so it never blocks the API.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from app.core.database import async_session_factory
from app.models.stock import Stock

if TYPE_CHECKING:
    from app.services.tushare_ingest import TuShareIngestService

logger = logging.getLogger(__name__)

EXCHANGES = ["Shanghai_Stocks", "Shenzen_Stocks", "Beijing_Stocks"]
DAILY_BACKFILL_YEARS = 3
DAILY_BACKFILL_CONCURRENCY = 3
DAILY_BACKFILL_BATCH_SIZE = 100

_init_task: asyncio.Task | None = None


async def maybe_seed_on_startup() -> None:
    """Check DB; if empty, kick off a background seed task.

    Also ensures SW industry data is imported from local XLS files
    even if stock data already exists.
    """
    global _init_task

    try:
        async with async_session_factory() as db:
            count = (await db.execute(select(func.count()).select_from(Stock))).scalar_one()
    except Exception:
        logger.warning("data_init: cannot query stocks table — skipping seed check", exc_info=True)
        return

    # Always check if SW industry data needs importing
    try:
        from app.services.sw_industry_service import import_all as import_sw_all  # noqa: PLC0415
        from app.services.sw_industry_service import is_sw_data_loaded  # noqa: PLC0415

        if not await is_sw_data_loaded():
            logger.info("data_init: SW industry data missing — importing")
            result = await import_sw_all()
            logger.info("data_init: SW industry import -> %s", result)
        else:
            logger.info("data_init: SW industry data already loaded")

        # Custom-tag overlay is additive & idempotent — must run even when SW
        # tables are already loaded (pre-overlay deployments upgrading would
        # otherwise never receive the OTHER→SW merge).
        from app.services.sw_industry_service import import_custom_tags_from_sql  # noqa: PLC0415

        await import_custom_tags_from_sql()
    except Exception:
        logger.warning("data_init: SW industry check/import failed", exc_info=True)

    if count > 0:
        logger.info("data_init: %d stocks already in DB — starting coverage check", count)
        _init_task = asyncio.create_task(_seed_database(skip_universe=True))
        return

    logger.info("data_init: empty database detected — starting background seed")
    _init_task = asyncio.create_task(_seed_database(skip_universe=False))


async def _seed_database(skip_universe: bool) -> None:
    """Pull stock universe (if needed) + ensure trailing 3-year daily coverage."""
    from app.core.providers.tushare_client import get_tushare_client  # noqa: PLC0415
    from app.services.tushare_ingest import TuShareIngestService  # noqa: PLC0415

    try:
        client = get_tushare_client()
    except Exception:
        logger.error(
            "data_init: TuShare client init failed — is TUSHARE_TOKEN set in .env? "
            "stock_basic requires 2000+ credits. See https://tushare.pro/document/1?doc_id=108",
            exc_info=True,
        )
        return

    service = TuShareIngestService(client=client)

    # Step 1: Ingest stock universe for all exchanges if DB is empty.
    if not skip_universe:
        for exchange in EXCHANGES:
            try:
                async with async_session_factory() as db:
                    result = await service.ingest_stock_universe(db, exchange)
                    logger.info("data_init: universe %s -> %s", exchange, result)
            except Exception:
                logger.error("data_init: universe ingest failed for %s", exchange, exc_info=True)

    # Step 2: Ensure daily quotes cover the trailing 3 years.
    # Step 2.5: Ensure daily basic indicators cover trailing 1 year.
    # These two use independent APIs/DB tables — run them in parallel.
    quote_task = asyncio.create_task(
        _ensure_trailing_three_year_daily_quotes(service),
        name="daily-quotes-backfill",
    )
    basic_task = asyncio.create_task(
        _ensure_trailing_one_year_daily_basic(service),
        name="daily-basic-backfill",
    )
    await asyncio.gather(quote_task, basic_task)

    # Step 3: Ingest index daily data (past year for dashboard indices)
    await _ensure_trailing_one_year_index_daily(service)

    logger.info("data_init: seed complete")


def last_completed_trading_day(today: date | None = None) -> date:
    """最近一个**已收盘**的工作日（今天本身不算）。

    这是单股覆盖任务的窗口上界。用 ``date.today()`` 当上界会在盘中取回半个市场，
    把一条当日行写进 ``daily_quotes``，于是 ``max(trade_date)`` 指向未收盘的今天，
    所有按日聚合的端点集体降级（实测 2026-09-17 只写入 1 行）。口径复用
    :func:`app.services.market_service.last_weekday`，交易日历（节假日）仍不在
    启发式范围内——节假日只会空跑一次拉取，不会再写脏行。
    """
    from app.services.market_service import last_weekday  # noqa: PLC0415 — 函数内导入避免耦合

    return last_weekday((today or date.today()) - timedelta(days=1))


def _chunked(items: list[dict], size: int) -> list[list[dict]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


async def _ensure_trailing_three_year_daily_quotes(service: TuShareIngestService) -> None:
    # 上界 = 最后一个已收盘工作日；同值既当 fetch 窗口又当覆盖率期望，
    # 否则 list_stocks_missing_daily_coverage 会把"今天"算进期望窗口。
    asof_date = last_completed_trading_day()
    async with async_session_factory() as db:
        missing = await service.list_stocks_missing_daily_coverage(
            db,
            years=DAILY_BACKFILL_YEARS,
            asof_date=asof_date,
        )

    if not missing:
        logger.info("data_init: all stocks already cover trailing %d years", DAILY_BACKFILL_YEARS)
        return

    logger.info(
        "data_init: %d stocks need daily backfill for trailing %d years",
        len(missing),
        DAILY_BACKFILL_YEARS,
    )
    sem = asyncio.Semaphore(DAILY_BACKFILL_CONCURRENCY)
    total_upserted = 0
    failed = 0

    async def _run_one(item: dict) -> int:
        async with sem:
            async with async_session_factory() as db:
                result = await service.ingest_daily_quotes_for_stock(
                    db,
                    stock_id=item["stock_id"],
                    exchange=item["exchange"],
                    symbol=item["symbol"],
                    start_date=_quote_backfill_start(item),
                    end_date=item["asof_date"],
                    save_raw=False,
                )
                await db.commit()
                return int(result.get("upserted", 0))

    batches = _chunked(missing, DAILY_BACKFILL_BATCH_SIZE)
    for batch_index, batch in enumerate(batches, start=1):
        tasks = [_run_one(item) for item in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        batch_upserted = 0
        for result in results:
            if isinstance(result, BaseException):
                failed += 1
                logger.warning("data_init: stock backfill task failed: %s", result)
                continue
            batch_upserted += int(result)
        total_upserted += batch_upserted
        logger.info(
            "data_init: backfill batch %d/%d done (stocks=%d, upserted=%d, failed=%d)",
            batch_index,
            len(batches),
            len(batch),
            batch_upserted,
            failed,
        )

    logger.info(
        "data_init: trailing %d-year backfill complete (stocks=%d, upserted=%d, failed=%d)",
        DAILY_BACKFILL_YEARS,
        len(missing),
        total_upserted,
        failed,
    )


#: 个股尾部缺口的容忍天数：超过才算缺（与 `list_stocks_missing_daily_coverage` 同值）。
QUOTE_BACKFILL_TOLERANCE_DAYS = 10


def _quote_backfill_start(item: dict) -> date:
    """单股回填的**起点**：只补尾巴，不要把三年重拉一遍。

    2026-10-21 实测：库里有 21 只被标成 gap 的股票（几乎全是 ST 停牌股，`max(trade_date)`
    落在 4~9 月），而旧代码一律从 `expected_start`（三年前）开拉 → 每次重启白跑
    **13,543 行** upsert，且数值一点没变。请求仍然要发（源侧真的可能补数据，我们
    没有停牌日历无法提前判定），但窗口应该是「库里最大日 + 1」。

    例外：**头部**缺口（`current_min` 比 `expected_start` 晚出容忍）真的需要全窗口，
    目前新上市股就是这种形态（如 920856：上市日 11 天后才有首条行情）。
    """
    expected: date = item["expected_start"]
    current_max: date | None = item.get("current_max")
    current_min: date | None = item.get("current_min")
    if current_max is None:
        return expected  # 一行都没有：整窗口补
    if current_min is not None and (current_min - expected).days > QUOTE_BACKFILL_TOLERANCE_DAYS:
        return expected  # 头部也缺：从 expected 拉（尾部顺带补齐）
    return current_max + timedelta(days=1)


#: 指数日线增量回填时额外回拉的日历日数：吸收源侧事后修正（重算收盘价/成交量）。
#: 没有重叠会漏掉「已入库日的数值改了」这种修正；重叠 7 天只多拉 ~5 行，代价可忽。
INDEX_BACKFILL_OVERLAP_DAYS = 7

#: 启动时校验「最近 N 个交易日」的指数覆盖：max(trade_date) 已到 asof 也可能有**内部缺口**
#: （源侧断档、某天入库失败）。每日 job 只回看 14 天，超过 14 天的缺口不会被修；
#: 这里按交易日序列显式比对，发现缺口就只补「缺口起点 - 7 天 … asof」这一段，不退回全年。
INDEX_RECENT_COVERAGE_DAYS = 35


def _index_fetch_window(existing: date | None, asof: date) -> tuple[str, str] | None:
    """算出某个指数本次需要拉的 ``(start, end)``；已覆盖到 ``asof`` → ``None``（不发请求）。

    为什么要有它：该函数原本**无条件**拉「asof-365 … asof」再 upsert，于是每次重启容器
    都会把 6 个指数 × 242 行重拉一遍（还顺手写一年 JSONL 快照）。个股行情与基本面早就
    是「先查缺口再拉」，指数这一支是漏网的。
    """
    if existing is None:
        return (asof - timedelta(days=365)).strftime("%Y%m%d"), asof.strftime("%Y%m%d")
    if existing >= asof:
        return None
    start = max(existing - timedelta(days=INDEX_BACKFILL_OVERLAP_DAYS), asof - timedelta(days=365))
    return start.strftime("%Y%m%d"), asof.strftime("%Y%m%d")


def _recent_coverage_window(
    recent_days: list[date], covered: set[date], asof: date
) -> tuple[str, str] | None:
    """最近 ``INDEX_RECENT_COVERAGE_DAYS`` 个交易日里缺了哪些 → 需要补的窗口.

    返回 ``None`` 表示这段全覆盖。起点回退 ``INDEX_BACKFILL_OVERLAP_DAYS`` 天，
    既吸收源侧修正，也保证 ingest 的区间是连续的（不会补出“只有缺口两天”的碎片请求）。
    """
    if not recent_days:
        return None
    missing = [day for day in recent_days if day not in covered]
    if not missing:
        return None
    start = max(
        min(missing) - timedelta(days=INDEX_BACKFILL_OVERLAP_DAYS),
        asof - timedelta(days=365),
    )
    return start.strftime("%Y%m%d"), asof.strftime("%Y%m%d")


def _merge_windows(
    first: tuple[str, str] | None, second: tuple[str, str] | None
) -> tuple[str, str] | None:
    """两个回填窗口取并集（``YYYYMMDD`` 字符串的字典序 == 日期序）。"""
    if first is None:
        return second
    if second is None:
        return first
    return (min(first[0], second[0]), max(first[1], second[1]))


async def _ensure_trailing_one_year_index_daily(service: TuShareIngestService) -> None:
    """回填大盘指数日线：**只拉缺的**（库内已覆盖到上一交易日则一次请求都不发）。

    除了 ``max(trade_date)``，还会比对**最近 35 个交易日**的覆盖（内部缺口）；
    缺任一天只补「缺口起点 - 7 天 … asof」，不退回全年。当前 6 个目标指数 35/35 齐全。

    窗口上界同个股覆盖任务一样只到「最后一个已收盘工作日」：盘中用 ``date.today()``
    会在 ``index_dailies`` 写入未收盘的当日行，令 ``/market/indices`` 各卡片 ``as_of``
    不一致（实测 2026-09-17 只写入 14 个指数中的 11 个）。
    """
    from app.repositories import index_repo  # noqa: PLC0415
    from app.services.market_service import _TARGET_INDICES  # noqa: PLC0415

    asof = last_completed_trading_day()
    codes = [idx["ts_code"] for idx in _TARGET_INDICES]
    async with async_session_factory() as db:
        existing = await index_repo.max_trade_date_by_code(db, codes)
        recent_days = await index_repo.list_recent_quote_days(db, asof, INDEX_RECENT_COVERAGE_DAYS)
        covered = (
            await index_repo.trade_dates_by_code(db, codes, recent_days[0], asof)
            if recent_days
            else {}
        )

    todo: list[tuple[dict[str, str], str, str]] = []
    for idx in _TARGET_INDICES:
        code = idx["ts_code"]
        window = _index_fetch_window(existing.get(code), asof)
        # 一行都没有时 `_index_fetch_window` 已经给整年窗口；只有“已覆盖到 asof”或
        # “落后但 max 之前的缺口”才需要再比对最近 35 天。
        if existing.get(code) is not None:
            window = _merge_windows(
                window,
                _recent_coverage_window(recent_days, covered.get(code, set()), asof),
            )
        if window is not None:
            todo.append((idx, window[0], window[1]))

    if not todo:
        logger.info(
            "data_init: %d indices already cover through %s — 0 API calls",
            len(_TARGET_INDICES),
            asof,
        )
        return

    logger.info(
        "data_init: %d/%d indices need daily backfill through %s",
        len(todo),
        len(_TARGET_INDICES),
        asof,
    )
    for idx, start_date, end_date in todo:
        try:
            async with async_session_factory() as db:
                result = await service.ingest_index_daily(
                    db,
                    ts_code=idx["ts_code"],
                    start_date=start_date,
                    end_date=end_date,
                )
                logger.info(
                    "data_init: index %s [%s..%s] -> upserted=%s",
                    idx["ts_code"],
                    start_date,
                    end_date,
                    result.get("upserted", 0),
                )
        except Exception:
            logger.error("data_init: index ingest failed for %s", idx["ts_code"], exc_info=True)


async def _ensure_trailing_one_year_daily_basic(service: TuShareIngestService) -> None:
    """Backfill daily_basic_indicators for trailing 1 year by trade_date.

    TuShare daily_basic supports trade_date-based batch fetch (entire market
    per call) which is far more efficient than per-stock iteration.
    """
    from datetime import datetime as _dt

    asof_date = last_completed_trading_day()
    # Fetch up to 1 year back (250 trading days ≈ ~370 calendar days)
    start = asof_date - timedelta(days=370)

    # Get the list of trading days in this range
    try:
        df_cal = await service.client.fetch_trade_cal(
            exchange="SSE",
            start_date=start.strftime("%Y%m%d"),
            end_date=asof_date.strftime("%Y%m%d"),
            is_open="1",
        )
        if df_cal.empty:
            logger.warning("data_init: trade_cal empty, skipping daily_basic backfill")
            return
        trade_dates = sorted(df_cal["cal_date"].astype(str).tolist())
    except Exception as exc:
        logger.warning("data_init: trade_cal failed for daily_basic backfill: %s", exc)
        return

    # Check which dates are already covered (sample a few to detect gap)
    async with async_session_factory() as db:
        from sqlalchemy import select

        from app.models.daily_basic import DailyBasicIndicator

        result = await db.execute(select(DailyBasicIndicator.trade_date).distinct())
        existing_dates = {d for (d,) in result.all()}

    missing_dates = [
        td for td in trade_dates if _dt.strptime(td, "%Y%m%d").date() not in existing_dates
    ]

    if not missing_dates:
        logger.info("data_init: daily_basic already covers trailing 1 year")
        return

    logger.info(
        "data_init: %d trade dates need daily_basic backfill (out of %d total)",
        len(missing_dates),
        len(trade_dates),
    )

    # Process dates concurrently, respecting TuShare rate limits.
    total_upserted = 0
    total_saved = 0
    failed = 0
    sem = asyncio.Semaphore(4)  # 4 concurrent API calls — safe, client lock serializes

    async def _run_one_date(td: str) -> tuple[int, int]:
        async with sem:
            async with async_session_factory() as db:
                result = await service.ingest_daily_basic(db, td)
                await db.commit()
            return result.get("upserted", 0), result.get("saved", 0)

    batch_size = 20
    for i in range(0, len(missing_dates), batch_size):
        batch = missing_dates[i : i + batch_size]
        tasks = [_run_one_date(td) for td in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        batch_upserted = 0
        batch_failed = 0
        for r in results:
            if isinstance(r, BaseException):
                batch_failed += 1
                logger.warning(
                    "data_init: daily_basic backfill task failed: %s",
                    r,
                )
            else:
                batch_upserted += r[0]
                total_saved += r[1]
        total_upserted += batch_upserted
        failed += batch_failed

        logger.info(
            "data_init: daily_basic backfill batch %d/%d done (upserted=%d, failed=%d)",
            i // batch_size + 1,
            (len(missing_dates) + batch_size - 1) // batch_size,
            batch_upserted,
            batch_failed,
        )

    logger.info(
        "data_init: daily_basic trailing 1-year backfill complete "
        "(dates=%d, upserted=%d, saved=%d, failed=%d)",
        len(missing_dates),
        total_upserted,
        total_saved,
        failed,
    )
