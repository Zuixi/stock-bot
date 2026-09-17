"""Scheduled job definitions for SSE index snapshot + daily data backfill."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

JITTER_SEC = 30

# 容器内 datetime.now() 是 UTC：交易时段/工作日守卫必须显式按上海时钟判断，
# 否则盘中任务（资金流轮询、SSE 快照）在 Docker 部署下永远不触发。
_SH_TZ = ZoneInfo("Asia/Shanghai")


async def _alert_job_failure(job_ids: str | tuple[str, ...], exc: BaseException) -> None:
    """把 job 失败登记到 `job:failures`（/market/data-freshness 的 failed_jobs）。

    APScheduler 不把触发本次执行的 job id 传给函数，所以同一 callable 注册在多个 id 下时
    （reconcile 3 个触发点、SSE 交易时段 2 个 tick）逐个登记：宁可多一行同源记录，也不要把
    失败挂到一个这次并没有触发的 id 上。job_alert_service 自身吞掉 Redis 异常——告警绝不
    能反过来把"已处理的 job 失败"升级成"未处理的崩溃"。
    """
    from app.services.job_alert_service import record_job_failure  # noqa: PLC0415

    for job_id in (job_ids,) if isinstance(job_ids, str) else job_ids:
        await record_job_failure(job_id, repr(exc))


def _is_workday() -> bool:
    return datetime.now(_SH_TZ).weekday() < 5


def _in_trading_hours() -> bool:
    """Return True if current Shanghai time is within 9:25-15:05 (with small buffer)."""
    now = datetime.now(_SH_TZ)
    start = now.replace(hour=9, minute=25, second=0, microsecond=0)
    end = now.replace(hour=15, minute=5, second=0, microsecond=0)
    return start <= now <= end


async def _collect_sse_snapshots(job_ids: str | tuple[str, ...] = "sse_trade_hours") -> None:
    from app.services import sse_scraper_service  # noqa: PLC0415

    try:
        count = await sse_scraper_service.fetch_and_save()
        logger.info("SSE snapshot collection complete: %d rows", count)
    except Exception as exc:
        logger.exception("SSE snapshot collection failed")
        await _alert_job_failure(job_ids, exc)


async def sse_trade_hours_job() -> None:
    """Collect SSE snapshots during trading hours (9:30-15:00).

    Called by APScheduler every 10 minutes on weekdays.
    Adds a random jitter before fetching to avoid predictable patterns.
    """
    if not _is_workday():
        logger.debug("Skipping SSE collection — not a workday")
        return
    if not _in_trading_hours():
        logger.debug("Skipping SSE collection — outside trading hours")
        return

    jitter = random.uniform(0, JITTER_SEC)
    logger.info("SSE trade-hours job triggered, jitter=%.1fs", jitter)
    await asyncio.sleep(jitter)
    await _collect_sse_snapshots(("sse_trade_hours", "sse_trade_close"))


async def sse_post_close_job() -> None:
    """Collect the final closing snapshot at 15:30.

    A single post-close sweep to ensure we have the official closing data.
    """
    if not _is_workday():
        logger.debug("Skipping post-close collection — not a workday")
        return

    logger.info("SSE post-close job triggered")
    await _collect_sse_snapshots("sse_post_close")


# ------------------------------------------------------------------
# Daily data ingestion (reconciliation thin-wrappers, L3)
# ------------------------------------------------------------------


async def _run_reconcile(only: set[str] | None, job_ids: str | tuple[str, ...]) -> None:
    """对账薄封装：会话生命周期 + 异常边界统一在这里（job_ids = 注册它的 scheduler id）。"""
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services import reconciliation_service  # noqa: PLC0415

    try:
        async with async_session_factory() as db:
            result = await reconciliation_service.reconcile_market_data(db, only=only)
        logger.info(
            "Reconcile done (only=%s): %s",
            sorted(only) if only else "all",
            {k: v["status"] for k, v in result["domains"].items()},
        )
    except Exception as exc:
        logger.exception("Reconcile failed (only=%s)", sorted(only) if only else "all")
        await _alert_job_failure(job_ids, exc)


async def daily_quotes_backfill_job() -> None:
    """全市场日线对账补齐（16:30 Mon-Fri）。

    旧语义"只补 T-1 + exists-skip"有两个结构性缺陷：停摆一天=永久洞（9/10、
    9/11、9/14、9/15 四次事故），partial 行挡住全量回补形成死锁（9/16）。
    对账语义：窗口内（默认 10 个交易日）缺失日与行数不足日一律重拉。
    """
    if not _is_workday():
        logger.debug("Skipping daily quotes backfill — not a workday")
        return

    logger.info("Daily quotes backfill job triggered")
    await _run_reconcile(only={"daily_quotes"}, job_ids="daily_quotes_backfill")


async def daily_basic_backfill_job() -> None:
    """每日指标（daily_basic）对账补齐（16:45 Mon-Fri，quotes 之后）。"""
    if not _is_workday():
        logger.debug("Skipping daily basic backfill — not a workday")
        return

    logger.info("Daily basic backfill job triggered")
    await _run_reconcile(only={"daily_basic"}, job_ids="daily_basic_backfill")


async def reconcile_market_data_job() -> None:
    """全量对账（四域：quotes/basic/price_limits/sentiment）。

    触发：scheduler 启动 +2min、每交易日 17:45 兜底、非交易日 10:00、worker 手动。
    **无工作日守卫**：周末/节假日启动也要能补上一个交易日的洞（宿主周五晚上
    挂起、周六开机是真实场景）。幂等：无缺口时零 TuShare 请求。
    """
    logger.info("Full reconcile job triggered")
    # 同一 callable 挂在 3 个触发点（启动 +2min / 17:45 兜底 / 非交易日 10:00）上
    await _run_reconcile(
        only=None,
        job_ids=("startup_reconcile", "reconcile_post_chain", "reconcile_weekend_catchup"),
    )


# ------------------------------------------------------------------
# Industry research metrics (dual-track: worker via MQ, scheduler direct)
# ------------------------------------------------------------------


async def industry_metrics_refresh_job() -> None:
    """Refresh industry research metrics (17:05 Mon-Fri, after quote backfills)."""
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services import industry_metric_service  # noqa: PLC0415

    logger.info("Industry metrics refresh job triggered")
    try:
        async with async_session_factory() as db:
            result = await industry_metric_service.ingest_industry_metrics(db, "pig")
            await db.commit()
        logger.info(
            "Industry metrics refresh done: source=%s upserted=%s signal=%s",
            result.get("source"),
            result.get("upserted"),
            result.get("signal"),
        )
    except Exception as exc:
        logger.exception("Industry metrics refresh failed")
        await _alert_job_failure("industry_metrics_refresh", exc)


async def financial_backfill_job() -> None:
    """Backfill financial statements for stocks missing them.

    可幂等、可续跑：每次处理一批尚未有财报数据的股票（默认 200 只），
    通过 FinancialWorker 同源的 ingest 方法直接入库；重复调度会逐批
    补齐全市场，新上市标的下次运行自动被覆盖。
    节奏受 TuShare 全局限流（约 0.5s/请求）控制，无需额外限速。
    """
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services.financial_backfill import (  # noqa: PLC0415
        DEFAULT_BATCH_SIZE,
        backfill_financial_batch,
    )

    logger.info("Financial backfill job triggered")
    try:
        async with async_session_factory() as db:
            result = await backfill_financial_batch(db, batch_size=DEFAULT_BATCH_SIZE)
        logger.info(
            "Financial backfill done: processed=%s failed=%s",
            result.get("processed"),
            result.get("failed"),
        )
    except Exception as exc:
        logger.exception("Financial backfill failed")
        await _alert_job_failure("financial_backfill", exc)


async def securities_refresh_job() -> None:
    """Refresh industry ETF/CB daily bars (17:10 Mon-Fri, after industry_metrics).

    日增量窗口（SCHEDULED_BACKFILL_DAYS）而非全年回补：幂等 upsert 兜底偶发缺口，
    避免每个交易日对 TuShare 打满整年请求。首年历史由手动任务全量回补。
    """
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services import securities_service  # noqa: PLC0415

    logger.info("Securities refresh job triggered")
    try:
        async with async_session_factory() as db:
            result = await securities_service.ingest_industry_securities(
                db, "pig", backfill_days=securities_service.SCHEDULED_BACKFILL_DAYS
            )
            await db.commit()
        logger.info(
            "Securities refresh done: etf_upserted=%s cb_upserted=%s",
            result.get("etf_upserted"),
            result.get("cb_upserted"),
        )
    except Exception as exc:
        logger.exception("Securities refresh failed")
        await _alert_job_failure("securities_refresh", exc)


async def global_index_daily_job() -> None:
    """全球+A股指数日线刷新（每日 17:30，覆盖美盘前一日与亚欧当日）。"""
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services import market_data_service  # noqa: PLC0415

    logger.info("Global index daily job triggered")
    try:
        async with async_session_factory() as db:
            result = await market_data_service.ingest_global_index_daily(db)
            await db.commit()
        logger.info("Global index daily done: %s", result)
    except Exception as exc:
        logger.exception("Global index daily job failed")
        await _alert_job_failure("global_index_daily", exc)


async def sector_moneyflow_job() -> None:
    """板块资金流盘中轮询（交易日 9:00-15:55 每 5 分钟，job 内交易时段守卫）。"""
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services import market_data_service  # noqa: PLC0415

    if not _is_workday() or not _in_trading_hours():
        return
    try:
        async with async_session_factory() as db:
            result = await market_data_service.ingest_sector_moneyflow(db)
            await db.commit()
        logger.info("Sector moneyflow poll done: %s", result)
    except Exception as exc:
        logger.exception("Sector moneyflow poll failed")
        await _alert_job_failure("sector_moneyflow_poll", exc)


async def market_moneyflow_daily_job() -> None:
    """大盘资金流日线（交易日 16:20 盘后，幂等 upsert 近 10 日窗口）。"""
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services import market_data_service  # noqa: PLC0415

    if not _is_workday():
        return
    try:
        async with async_session_factory() as db:
            result = await market_data_service.ingest_market_moneyflow_daily(db)
            await db.commit()
        logger.info("Market moneyflow daily done: %s", result)
    except Exception as exc:
        logger.exception("Market moneyflow daily job failed")
        await _alert_job_failure("market_moneyflow_daily", exc)


async def northbound_daily_job() -> None:
    """北向资金每日净流入（交易日 16:10 盘后，幂等 upsert 近 30 日窗口）。"""
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services import market_data_service  # noqa: PLC0415

    if not _is_workday():
        return
    try:
        async with async_session_factory() as db:
            result = await market_data_service.ingest_northbound(db)
            await db.commit()
        logger.info("Northbound daily done: %s", result)
    except Exception as exc:
        logger.exception("Northbound daily job failed")
        await _alert_job_failure("northbound_daily", exc)


async def dragon_tiger_daily_job() -> None:
    """龙虎榜每日明细（交易日 18:00 盘后；补漏模式——自表内最新交易日起逐日拉齐缺失交易日）。"""
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services import market_data_service  # noqa: PLC0415

    if not _is_workday():
        return
    try:
        async with async_session_factory() as db:
            result = await market_data_service.ingest_dragon_tiger(db)
            await db.commit()
        logger.info("Dragon tiger daily done: %s", result)
    except Exception as exc:
        logger.exception("Dragon tiger daily job failed")
        await _alert_job_failure("dragon_tiger_daily", exc)


async def block_trade_daily_job() -> None:
    """大宗交易每日明细（交易日 17:00 盘后；补漏模式逐日拉齐缺失交易日，DO NOTHING 去重）。"""
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services import market_data_service  # noqa: PLC0415

    if not _is_workday():
        return
    try:
        async with async_session_factory() as db:
            result = await market_data_service.ingest_block_trades(db)
            await db.commit()
        logger.info("Block trade daily done: %s", result)
    except Exception as exc:
        logger.exception("Block trade daily job failed")
        await _alert_job_failure("block_trade_daily", exc)


async def share_float_daily_job() -> None:
    """限售解禁每日计划（交易日 17:30 盘后，近 7 日窗口 DO NOTHING 去重）。"""
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services import market_data_service  # noqa: PLC0415

    if not _is_workday():
        return
    try:
        async with async_session_factory() as db:
            result = await market_data_service.ingest_share_floats(db)
            await db.commit()
        logger.info("Share float daily done: %s", result)
    except Exception as exc:
        logger.exception("Share float daily job failed")
        await _alert_job_failure("share_float_daily", exc)


async def repurchase_daily_job() -> None:
    """股票回购每日进度（交易日 17:40 盘后，近 7 日窗口 DO UPDATE 幂等 upsert）。"""
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services import market_data_service  # noqa: PLC0415

    if not _is_workday():
        return
    try:
        async with async_session_factory() as db:
            result = await market_data_service.ingest_repurchases(db)
            await db.commit()
        logger.info("Repurchase daily done: %s", result)
    except Exception as exc:
        logger.exception("Repurchase daily job failed")
        await _alert_job_failure("repurchase_daily", exc)


async def price_limits_daily_job() -> None:
    """权威涨跌停价补漏（交易日 16:50，晚于 16:30 的 quotes 回补）。

    按时区显式取上海日期：容器默认 UTC，naive datetime.now() 会取到前一天。
    补漏判据在 service 内（stock_price_limits 无该日行），所以即使某天任务没跑，
    下一次也会自动追平。

    注意窗口上界：daily_quotes 的对账补齐以 trade_cal 期望集为准（截止昨日，
    T-1 语义，见 reconciliation_service），所以本任务补到的最新交易日 =
    上一个交易日，与情绪快照的 `as_of` 语义一致。
    """
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services import market_data_service  # noqa: PLC0415

    if not _is_workday():
        return
    try:
        async with async_session_factory() as db:
            result = await market_data_service.ingest_stock_price_limits(db)
            await db.commit()
        logger.info("Price limits daily done: %s", result)
    except Exception as exc:
        logger.exception("Price limits daily job failed")
        await _alert_job_failure("price_limits_daily", exc)


async def sentiment_daily_job() -> None:
    """盘后情绪聚合落库（交易日 17:15，晚于 16:50 的限价补漏）。

    幂等 upsert（唯一键 trade_date）；降级日（部分行情/限价缺失/空候选）一律跳过，
    避免时序图被假低谷污染。传 cache=None 走无缓存 get_snapshot，同时规避 Redis JSON
    回读后 as_of 变 str 的 asyncpg 日期序列化坑。
    """
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services import limit_up_service  # noqa: PLC0415

    if not _is_workday():
        return
    try:
        async with async_session_factory() as db:
            result = await limit_up_service.persist_snapshot(db, None)
            await db.commit()
        logger.info("Sentiment daily done: %s", result)
    except Exception as exc:
        logger.exception("Sentiment daily job failed")
        await _alert_job_failure("sentiment_daily", exc)


async def announcements_poll_job() -> None:
    """巨潮公告轮询（8-22 点每 10 分钟，DO NOTHING 去重近 3 日窗口）。

    公告发布含非交易日/盘后时段 → 无交易时段与工作日守卫。
    """
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services import announcement_service  # noqa: PLC0415

    try:
        async with async_session_factory() as db:
            result = await announcement_service.ingest_announcements(db)
            await db.commit()
        logger.info("Announcements poll done: %s", result)
    except Exception as exc:
        logger.exception("Announcements poll failed")
        await _alert_job_failure("announcements_poll", exc)


# ------------------------------------------------------------------
# Stock universe refresh (weekly —— 名录元数据变化慢，但它是日线采集的 ts_code 映射
# 源：stocks 表冻结一天，当日全部新上市股票的行就被静默丢弃一天)
# ------------------------------------------------------------------


async def universe_refresh_job() -> None:
    """Refresh stock universe metadata (09:00 Sat, weekly).

    与 UniverseWorker 共用同一 ingest 方法：upsert 会刷新 stocks.asof 并写入
    stocks_history 快照。逐交易所隔离失败，最后统一失效列表类缓存（与 worker 一致）。
    交易所清单复用 models.stock.ExchangeName 单一事实源，不另立常量。
    """
    from typing import get_args  # noqa: PLC0415

    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.core.redis import CacheClient, get_redis_pool  # noqa: PLC0415
    from app.models.stock import ExchangeName  # noqa: PLC0415
    from app.services.tushare_ingest import TuShareIngestService  # noqa: PLC0415

    logger.info("Universe refresh job triggered")
    service = TuShareIngestService()
    for exchange in get_args(ExchangeName):
        try:
            async with async_session_factory() as db:
                result = await service.ingest_stock_universe(db, exchange)
                await db.commit()
            logger.info(
                "Universe refresh %s done: inserted=%s skipped=%s",
                exchange,
                result.get("inserted"),
                result.get("skipped"),
            )
        except Exception as exc:
            logger.exception("Universe refresh failed for %s", exchange)
            await _alert_job_failure("universe_refresh", exc)

    try:
        redis = await get_redis_pool()
        cache = CacheClient(redis)
        await cache.delete_pattern("stock:list:*")
        await cache.delete_pattern("stock:categories:*")
    except Exception as exc:
        logger.exception("Universe refresh cache invalidation failed (non-fatal)")
        # 降级但要可见：缓存未失效会让新名录在 TTL 内不可见
        await _alert_job_failure("universe_refresh", exc)
