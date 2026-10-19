"""Scheduled job definitions for SSE index snapshot + daily data backfill."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

JITTER_SEC = 30

# 容器内 datetime.now() 是 UTC：交易时段/工作日守卫必须显式按上海时钟判断，
# 否则盘中任务（资金流轮询、SSE 快照）在 Docker 部署下永远不触发。
_SH_TZ = ZoneInfo("Asia/Shanghai")


async def _alert_job_failure(job_ids: str | tuple[str, ...], exc: BaseException) -> None:
    """把 job 失败登记到 `job:failures`（/market/data-freshness 的 failed_jobs）。

    APScheduler 不把触发本次执行的 job id 传给函数，所以同一 callable 注册在多个 id 下时
    （reconcile 3 个触发点 + 2 个重试 tick、SSE 交易时段 2 个 tick）逐个登记：宁可多一行
    同源记录，也不要把失败挂到一个这次并没有触发的 id 上。job_alert_service 自身吞掉 Redis
    异常——告警绝不能反过来把"已处理的 job 失败"升级成"未处理的崩溃"。
    """
    from app.services.job_alert_service import record_job_failure  # noqa: PLC0415

    for job_id in (job_ids,) if isinstance(job_ids, str) else job_ids:
        await record_job_failure(job_id, repr(exc))


#: 共享 callable `reconcile_market_data_job` 的**常规**触发点 id（失败登记逐个登记）。
RECONCILE_JOB_IDS: tuple[str, ...] = (
    "startup_reconcile",
    "reconcile_post_chain",
    "reconcile_weekend_catchup",
)

#: cutoff 之后的**重试 tick**：`(job_id, hour, minute)`，注册在 `runner.create_scheduler`。
#:
#: 为什么要有重试：17:45 是当天唯一一次机会，而上游披露时刻会浮动（QVIX 实测 16:53 才更新
#: CSV、交易所融资融券是 T+1）。没有重试的话，上游晚到半小时，页面就要如实报"落后 1 个
#: 交易日"一直到**次日 10:00** 的兜底对账。
#:
#: 为什么是"条件 tick"：收敛器的波动率腿声明了 `always_rescan=True`（QVIX 会迟到补数）
#: ⇒ **每轮都会真外呼**。`reconcile_retry_job` 先做零外呼的判定，只有当日真的还没齐备
#: 才跑整条链。
#:
#: id 字面量登记在此：`test_every_registered_scheduler_job_is_alert_wired` 要求每个注册
#: id 都能在 jobs.py 里找到（防止新增 job 时忘了接告警）。
RECONCILE_RETRY_TICKS: tuple[tuple[str, int, int], ...] = (
    ("reconcile_retry_1830", 18, 30),
    ("reconcile_retry_2000", 20, 0),
)

RECONCILE_RETRY_JOB_IDS: tuple[str, ...] = tuple(tick[0] for tick in RECONCILE_RETRY_TICKS)


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


async def _run_reconcile(
    only: set[str] | None, job_ids: str | tuple[str, ...]
) -> dict[str, Any] | None:
    """对账薄封装：会话生命周期 + 异常边界统一在这里（job_ids = 注册它的 scheduler id）。

    返回对账结果（失败时 `None`）—— 后置的派生指标收敛要用它算出的 `expected_latest`
    当目标日，不自己另算一份交易日历。

    顺带把 `CacheClient` 传进去：对账在 `degraded=False` 时会把交易日历写进 Redis，
    读面（hero 新鲜度）据此在不外呼 TuShare 的前提下判"现在该到哪天"。缓存拿不到
    **不算失败**（降级为不写缓存）—— 新鲜度是加法，不该反噬对账本身。
    """
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.core.redis import CacheClient, get_redis_pool  # noqa: PLC0415
    from app.services import reconciliation_service  # noqa: PLC0415

    cache: Any = None
    try:
        cache = CacheClient(await get_redis_pool())
    except Exception:  # noqa: BLE001 — 拿不到缓存只影响读面新鲜度，不影响对账
        logger.warning("Reconcile: cache unavailable; trade calendar not cached", exc_info=True)

    try:
        async with async_session_factory() as db:
            result = await reconciliation_service.reconcile_market_data(db, only=only, cache=cache)
        logger.info(
            "Reconcile done (only=%s): %s",
            sorted(only) if only else "all",
            {k: v["status"] for k, v in result["domains"].items()},
        )
        return result
    except Exception as exc:
        logger.exception("Reconcile failed (only=%s)", sorted(only) if only else "all")
        await _alert_job_failure(job_ids, exc)
        return None


async def daily_quotes_backfill_job() -> None:
    """全市场日线对账补齐（16:30 Mon-Fri）。

    旧语义"只补 T-1 + exists-skip"有两个结构性缺陷：停摆一天=永久洞（9/10、
    9/11、9/14、9/15 四次事故），partial 行挡住全量回补形成死锁（9/16）。
    对账语义：窗口内（默认 10 个交易日）缺失日与行数不足日一律重拉。

    ⚠️ 这里**不**串联派生指标收敛：16:30 时 T-1 的行情刚拉到、`daily_basic`/限价
    都还没补齐（16:45/16:50），此时算派生指标只会算在半截底座上。
    收敛挂在全量对账之后（见 :func:`reconcile_market_data_job`）。
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


async def reconcile_market_data_job(*, job_ids: str | tuple[str, ...] = RECONCILE_JOB_IDS) -> None:
    """全量对账（四域：quotes/basic/price_limits/sentiment）**+ 收敛 + 涨停事实**。

    触发：scheduler 启动 +2min、每交易日 17:45 兜底、每日 10:00、cutoff 后重试 tick。
    **无工作日守卫**：周末/节假日启动也要能补上一个交易日的洞（宿主周五晚上
    挂起、周六开机是真实场景）。幂等：无缺口时零 TuShare 请求。

    `job_ids` 只用于**失败登记**的归属（APScheduler 不把触发 id 传给函数，故默认把三个
    常规触发点逐个登记；重试 tick 经 `reconcile_retry_job` 传自己那两个 id 进来）。

    ## 为什么收敛挂在这里（2026-10-16）

    scope=`market` 的那一族派生指标（§S1.7–§S1.13）此前**只**由手工脚本写，
    没有任何定时任务碰过 —— 于是行情天天更新、页面上的"数据截至"却停在人工回补的最后
    一天。修法不是在盘后再加一个 cron（那会重新落入"任务准点跑才正确"的老坑：
    宿主睡眠、容器挂起、上游晚到都会让它算在半截数据上），而是**挂在对账之后**：
    对账已经把行情/限价/温度计收敛好，收敛器接着按同一个 `expected_latest`
    把派生指标推到同一天。**顺序由代码保证**。

    对账失败（返回 `None`）时**仍然**尝试收敛：底座可能只是部分域失败（例如
    `daily_basic` 的洞不影响宽度腿），而收敛器自带前置闸门（底座没齐就跳过），
    所以这里不需要替它再判一次。

    ## 2026-10-17：尾巴上又多了一环

    `_refresh_limit_up_facts()` —— 个股级涨停事实（`limit_up_stock_daily` 是巡检域，
    对账永不自愈它），必须在对账把当日行情与限价补齐之后才落得到当日。

    ## 2026-10-20（S12.3）：最后一环是离线产物自愈

    `_refresh_stale_artifacts()` —— regime / peer_history / triage 标定三个 JSON 产物
    过去**只**由手工脚本更新，页面上的"同类历史/规则标定"会一直停在人工回补那天。
    它挂在同一条链的尾巴（**不新开 cron**）：用对账算出的 `expected_latest` 当目标日，
    判据幂等（产物 `as_of < day` 才重建，新鲜时零查询）。失败只登记
    `artifacts_refresh:<产物名>` 告警，不反噬对账。
    """
    logger.info("Full reconcile job triggered")
    result = await _run_reconcile(only=None, job_ids=job_ids)
    await _converge_derived_metrics(result)
    await _refresh_limit_up_facts()
    await _refresh_stale_artifacts(result)


async def _refresh_limit_up_facts() -> None:
    """对账尾巴上的「个股级涨停事实 + 东财 EOD 增强」（`limit_up_stock_daily`）.

    **为什么串在代码里、而不是把它的 cron 挪到 17:55**：该表在 `INSPECT_DOMAINS` 里
    （**没有** `_refetch_*`，对账永不自愈它），而它的 `as_of` 取自
    `resolve_latest_complete_day` —— 只有在对账把当日行情与限价补齐**之后**调用它，
    才会落到当日。靠"17:45 那轮大概跑 40 秒，17:55 总够了"这类时间差排序，在 TuShare
    节流时（当日全量拉取可能好几分钟）必然偶发踩空；而踩空那天要等次日手工回补
    （东财涨停池只保留约 14 个交易日）。**顺序由代码保证，不靠时间差**（ADR 0016 同款）。

    17:20 的 cron **保留**：它是对账失败时的 T-1 兜底，两条轨道都幂等。
    """
    await limit_up_stock_facts_job()


async def _refresh_stale_artifacts(reconcile_result: dict[str, Any] | None) -> None:
    """对账尾巴最后一环：离线产物（regime / peer_history / triage 标定）自愈.

    目标日取对账算出的 `expected_latest`（过了齐备门禁的交易日）——与收敛器同源，
    不自己另算一份交易日历。三种情形：

    - 对账**失败**（`None`）/ 窗口内没有过门禁的日子（`expected_latest` 为空）⇒ 跳过并
      告警。不回退到"`daily_quotes` 最近一天"：那正是没过门禁的那天，在它上面重建
      等于把半截底座写进产物（与 `_converge_derived_metrics` 同一纪律）。
    - 正常 ⇒ `artifacts_service.refresh_stale_artifacts(db, day)`；它对**单个产物**的
      失败内部已经 catch + 告警 + 保留旧文件（spec §S12.3），不会抛到这里。
    - 产物全部新鲜（绝大多数轮次）⇒ **连数据库会话都不开**（`stale_artifacts` 只读文件）：
      自愈机制不该在无事可做的日子里多占一条连接（job 层测试也因此保持密闭）。
    - 万一抛到这里（例如 session 建不起来）⇒ **不得反噬对账**：告警挂在
      `artifacts_refresh` 上，reconcile job 仍然算成功。
    """
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services import artifacts_service  # noqa: PLC0415

    raw = (reconcile_result or {}).get("expected_latest")
    if not raw:
        logger.warning(
            "Artifacts refresh skipped: no gated trading day "
            "(source=%s, calendar_expected_latest=%s)",
            (reconcile_result or {}).get("expected_latest_source"),
            (reconcile_result or {}).get("calendar_expected_latest"),
        )
        return
    day = date.fromisoformat(str(raw))
    if not artifacts_service.stale_artifacts(day):
        logger.debug("Artifacts refresh skipped: all fresh (day=%s)", day)
        return
    try:
        async with async_session_factory() as db:
            payload = await artifacts_service.refresh_stale_artifacts(db, day)
    except Exception as exc:  # noqa: BLE001 — 产物自愈失败不得反噬对账 job 的成败
        logger.exception("Artifacts refresh failed (day=%s)", day)
        await _alert_job_failure("artifacts_refresh", exc)
        return
    logger.info(
        "Artifacts refresh done (day=%s): refreshed=%s skipped=%s failed=%s",
        day,
        payload["refreshed"],
        payload["skipped"],
        payload["failed"],
    )


async def reconcile_retry_job() -> None:
    """cutoff 之后的**重试 tick**（18:30 / 20:00）：当日还没齐备时补跑整条盘后链.

    为什么需要：17:45 是当天唯一一次机会，而上游披露时刻会浮动（QVIX 实测 16:53 才更新
    CSV，融资融券是 T+1）。没有重试的话，上游晚到半小时，页面就要一直如实报"落后 1 个
    交易日"到**次日 10:00** 的兜底对账。

    为什么先判再跑：`_converge_derived_metrics` 的波动率腿声明了 `always_rescan=True`
    （QVIX 会迟到补数），**每轮都会真外呼**。无脑多挂两个 tick 会把这个成本乘以 tick 数。
    所以先做**零外呼**的判定：

    - 日历判出"期望日 ≠ 今天"（今天不是交易日 / 还没到 cutoff）⇒ 跳过；
    - 期望日就是今天、且今天已过齐备门禁 ⇒ 跳过（已经追平，链条无事可做）；
    - 判不出期望日（缓存未命中）⇒ **照跑**：链条本身幂等，宁可多跑一轮，也不让
      "Redis 抖一下"变成当天不追平。
    """
    if not _is_workday():
        return
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.core.redis import CacheClient, get_redis_pool  # noqa: PLC0415
    from app.services import market_day_service  # noqa: PLC0415

    cache: Any = None
    try:
        cache = CacheClient(await get_redis_pool())
    except Exception:  # noqa: BLE001 — 缓存拿不到不影响本次补跑
        logger.warning("Reconcile retry: cache unavailable", exc_info=True)

    today = datetime.now(_SH_TZ).date()
    try:
        expected, reason = await market_day_service.resolve_expected_day(cache)
    except Exception:  # noqa: BLE001 — 判定失败按"未知"处理 → 照跑
        expected, reason = None, "judge_failed"

    if expected is not None and expected != today:
        logger.debug("Reconcile retry skipped: expected=%s reason=%s", expected, reason)
        return
    if expected == today:
        try:
            async with async_session_factory() as db:
                if await market_day_service.day_is_complete(db, expected):
                    logger.debug("Reconcile retry skipped: %s already complete", expected)
                    return
        except Exception:  # noqa: BLE001 — 判不了就照跑（幂等）
            logger.warning("Reconcile retry: completeness probe failed", exc_info=True)

    logger.info("Reconcile retry: %s not complete yet — running the post-close chain", today)
    # 失败登记挂到重试 tick 自己的 id 上（不是那三个常规触发点）
    await reconcile_market_data_job(job_ids=RECONCILE_RETRY_JOB_IDS)


async def _converge_derived_metrics(reconcile_result: dict[str, Any] | None) -> None:
    """对账之后：把 §S1.7–§S1.13 的派生指标收敛到 `expected_latest`。

    目标日取**对账算出的** `expected_latest` —— 它是"上界里最新一个过三元组门禁的
    交易日"（上界本身由日历与时钟决定：交易日 17:30 后含当日，见
    `reconciliation_service.expected_trade_dates`），与行情域的期望集完全同源。

    三种情形，语义不同：

    - 对账**失败**（`reconcile_result is None`）⇒ 传 `None`，由收敛器退回
      "`daily_quotes` 的最近交易日"这个本地口径。这是**刻意**的：底座可能只是部分域
      失败（例如 `daily_basic` 的洞不影响宽度腿），收敛器自带前置闸门，不必替它再判一次。
    - 对账成功但 `expected_latest` 为 `None`（窗口内一个日子都过不了门禁）⇒ **跳过**
      本轮收敛并告警。不回退到"`daily_quotes` 最近一天"——那正是没过门禁的那天，
      在它上面算指标等于把半截底座的值发出去。
    - 正常 ⇒ 收敛到该日（可能已从当日逐日回退，`expected_latest_source='fallback'`）。

    **异常边界独立**：派生指标失败不得让"对账成功"变成"job 失败"
    （它们是两件事，告警也挂在两个不同的 id 上）。
    """
    from app.services import derived_metrics_service  # noqa: PLC0415

    target: date | None = None
    if reconcile_result:
        raw = reconcile_result.get("expected_latest")
        if not raw:
            source = reconcile_result.get("expected_latest_source")
            logger.warning(
                "Derived metrics converge skipped: no gated trading day in window "
                "(source=%s, calendar_expected_latest=%s)",
                source,
                reconcile_result.get("calendar_expected_latest"),
            )
            return
        target = date.fromisoformat(str(raw))
    try:
        payload = await derived_metrics_service.converge(
            target=target, alert_job_id="market_metrics_refresh"
        )
    except Exception as exc:  # noqa: BLE001 — 收敛失败不得反噬对账 job 的成败
        logger.exception("Derived metrics converge failed (target=%s)", target)
        await _alert_job_failure("market_metrics_refresh", exc)
        return
    logger.info(
        "Derived metrics converge done: status=%s target=%s alerts=%s",
        payload.get("status"),
        payload.get("target"),
        payload.get("alerts"),
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
            # force=False：定时轨道吃满三层门控（L1 调度门可短路玄田请求）
            result = await industry_metric_service.ingest_industry_metrics(db, "pig", force=False)
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


async def board_snapshot_eod_job() -> None:
    """板块资金流**当日收盘全量**落库（交易日 15:05；与 `board_snapshot_rewrite_job` 分工）。

    为什么必须是"全量"而不是一次 Top-100（Ruling 11）：`clist` 的 `pz` 上限是 100，盘中
    `sector_moneyflow_poll` 每 5 分钟按 `fid=f62`（主力净流入）只拿得到 Top-100，当日表行
    是"每次轮询 Top-100 的并集、旧行永不刷新"，既不是完整当日态、也不是
    `/market/hot-boards` 要的 `f3`（涨幅）全集。收盘后分页翻全（industry ≈5 页 /
    concept ≈6 页 / region 1 页）把当日**所有**板块刷新一次并写 `capture_kind='eod_full'`；
    读表闸门只认这一批。

    A 类数据（东财板块资金流无历史接口）当天不落就永久丢失。覆盖不足（`fetched < total`）
    与外呼失败都要登记 `job:failures`：静默截断会让读表闸门照开、T6 拿子集当完整日。
    双轨制：与 worker 的 `board_snapshot_eod` 分支调**同一个** service 方法。
    """
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services import market_data_service  # noqa: PLC0415

    if not _is_workday():
        return
    try:
        async with async_session_factory() as db:
            result = await market_data_service.ingest_sector_moneyflow_eod_full(db)
            await db.commit()
        logger.info("Board snapshot EOD done: %s", result)
    except Exception as exc:
        logger.exception("Board snapshot EOD job failed")
        await _alert_job_failure("board_snapshot_eod", exc)
        return
    shortfalls = market_data_service.eod_alert_reasons(result)
    if shortfalls:
        await _alert_job_failure("board_snapshot_eod", RuntimeError("; ".join(shortfalls)))


async def market_panorama_daily_job() -> None:
    """A股全景三张日表写入（交易日 17:25，晚于 16:30 行情/16:50 限价）。

    纯本地派生（B 类），不调外部源；幂等 upsert。`resolve_latest_complete_day` 拿
    判据日（不拿"今天"硬查），行情尚未齐时早退/写 0 行，由次日 job 或回补脚本补。
    """
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services import market_day_service, market_panorama_ingest_service  # noqa: PLC0415

    if not _is_workday():
        return
    try:
        async with async_session_factory() as db:
            md = await market_day_service.resolve_latest_complete_day(db)
            if md is None:
                logger.warning("Panorama daily: no resolved market day")
                return
            breadth_n, industry_n = await market_panorama_ingest_service.refresh_day(db, md.day)
            await db.commit()
        logger.info(
            "Panorama daily done: day=%s breadth=%s industry=%s",
            md.day,
            breadth_n,
            industry_n,
        )
        if breadth_n == 0:
            await _alert_job_failure(
                "market_panorama_daily",
                RuntimeError(f"{md.day} 没有可写的广度行（行情未齐/非交易日）"),
            )
    except Exception as exc:
        logger.exception("Panorama daily job failed")
        await _alert_job_failure("market_panorama_daily", exc)


async def board_snapshot_rewrite_job() -> None:
    """当日**没有全量捕获**才补做收盘全量（交易日 15:35；容器重启错过 15:05 的兜底）。

    只承诺**当日**：东财板块资金流没有历史接口（A 类），补写跨日无意义 —— 昨天没抓到就是
    永久缺失，本 job 不会（也无法）去补昨天。守卫是"当日有没有 `eod_full` 行"而不是
    "有没有行"（Ruling 11）：只落了盘中 Top-100 子集的日子同样要补上收盘全量。

    双轨制：与 worker 的 `board_snapshot_rewrite` 分支调同一个 service 方法
    （`ingest_sector_moneyflow_if_missing`）。
    """
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services import market_data_service  # noqa: PLC0415

    if not _is_workday():
        return
    try:
        async with async_session_factory() as db:
            result = await market_data_service.ingest_sector_moneyflow_if_missing(db)
            await db.commit()
        logger.info("Board snapshot rewrite done: %s", result)
    except Exception as exc:
        logger.exception("Board snapshot rewrite job failed")
        await _alert_job_failure("board_snapshot_rewrite", exc)
        return
    shortfalls = market_data_service.eod_alert_reasons(result)
    if shortfalls:
        await _alert_job_failure("board_snapshot_rewrite", RuntimeError("; ".join(shortfalls)))


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
    """北向 ``moneyflow_hsgt.north_money`` 日序列（交易日 16:10 盘后，幂等 upsert 近 30 日窗口）。

    ⚠️ 该列在 ``2024-08-19`` 换过语义：之前是**净买入**，之后是**成交额**（Ruling 62/63）。
    ingest 按交易日逐行打 ``amount_kind``，读面据此念数——本任务**不删**这条活任务。
    """
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

    注意窗口上界（2026-10-17 起有 T+0）：期望集上界由日历与时钟决定 —— 交易日
    **17:30 之后**才含当日（见 `reconciliation_service.expected_trade_dates` 与
    `market_day_service.T0_CUTOFF`）。本 job 跑在 16:50，**在 cutoff 之前**，故它补到的
    最新交易日仍是上一个交易日，与情绪快照的 `as_of` 语义一致；**当日限价由 17:45 的
    全量对账（`_refetch_price_limits`）补**——那一步必须在齐备门禁之前完成，因为门禁的
    第三项就是"该日有限价"。
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

    2026-10-17（T+0）后它在链上的角色：**17:15 早于 cutoff（17:30）**，所以这一轮只落
    上一个交易日（幂等，多半是 no-op）；**当日那一行由 17:45 的全量对账写**（`sentiment`
    域的 `_refetch_sentiment`，且只在 quotes+限价都过门禁的日子才写）。保留本 cron 是
    兜底：对账整体失败时，至少还有 T-1 的聚合。

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


async def limit_up_stock_facts_job() -> None:
    """个股级涨停事实落库 + 东财 EOD 增强（交易日 17:20，晚于 16:50 限价补漏）。

    2026-10-17（T+0）后：**当日**那一行由 `reconcile_market_data_job` 尾步
    （`_refresh_limit_up_facts`）驱动 —— 本表在 `INSPECT_DOMAINS` 里、对账不会自愈它，
    而它的 `as_of` 取自"最新完整交易日"，只有在对账补齐当日行情与限价之后调用才会落到
    当日。17:20 这一轮（早于 cutoff）只覆盖上一个交易日，保留作对账失败时的兜底。

    **为什么顺序是"先落库再增强"**：增强是 UPDATE-only（当天事实行必须先存在），而当日
    限价 16:50 才入库、事实行本 job 才写 —— 因此增强只能挂在落库之后（15:05 单挂一个
    增强 job 必然命中 0 行，每天白造一条告警）。

    `as_of` 走与 `persist_snapshot` 同款的完整性判据（最新**完整**交易日），不直接用
    "今天"：脏的最新日（partial）不得被当成事实落库日。

    best-effort 边界：东财外呼失败/空池/0 命中一律只 `logger.warning` + 登记
    `job:failures`（A 类装饰丢失必须可见），**不回滚**已提交的事实、不让 job 整体失败。
    """
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services import limit_up_service, market_day_service  # noqa: PLC0415

    if not _is_workday():
        return
    try:
        async with async_session_factory() as db:
            md = await market_day_service.resolve_latest_complete_day(db)
            if md is None:
                logger.warning("Limit-up stock facts skipped: no quotes")
                return
            result = await limit_up_service.persist_and_enrich_stock_facts(db, md.day)
            await db.commit()
        logger.info("Limit-up stock facts done: day=%s %s", md.day, result)
    except Exception as exc:
        logger.exception("Limit-up stock facts job failed")
        await _alert_job_failure("limit_up_stock_facts", exc)
        return
    reason = result.get("enrich_reason")
    if reason:
        # "池里有票但当日事实行 0 命中"才是 A 类数据静默丢失的信号；空池/外呼失败同样登记
        await _alert_job_failure(
            "limit_up_stock_facts",
            RuntimeError(f"EOD enrichment {reason} (trade_date={md.day})"),
        )


async def intraday_sentiment_poll_job() -> None:
    """盘中分时点入库（交易日 9:25-15:05 每 5 分钟，job 内交易时段守卫）。

    cron 只圈住 ``mon-fri 9-15``（含 15:00 收盘 tick），非交易时段/非工作日由
    ``_is_workday``/``_in_trading_hours`` 早返回拦掉——与 ``sector_moneyflow_job``
    同款守卫：09:00-09:25 的盘前 tick 与 15:05 后的 tick 不落到网络调用。

    盘中"今日"语义固定为上海时区当前日期。东财涨停池抓取/DB 写入失败时整段静默
    （``logger.exception`` + 登记 ``job:failures``），由下一次 5min 轮询自愈——任务
    **不**触发对账路径（与其它盘中轮询的"失败非致命"约定一致）：一次 5min 轮询漏
    一次不等于数据洞，cron 下一拍会补；只有真正阻塞整日的事件才升级到对账。
    """
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.repositories import market_data_repo  # noqa: PLC0415
    from app.services import intraday_sentiment_service  # noqa: PLC0415

    if not _is_workday():
        logger.debug("intraday_sentiment_poll skipped: not a workday")
        return
    if not _in_trading_hours():
        logger.debug("intraday_sentiment_poll skipped: outside trading hours")
        return

    now_sh = datetime.now(_SH_TZ)
    today = now_sh.date()
    try:
        pool_rows = await intraday_sentiment_service.fetch_intraday_pool(today.strftime("%Y%m%d"))
        if not pool_rows:
            logger.info("intraday_sentiment_poll skipped: empty pool (trade_date=%s)", today)
            return
        async with async_session_factory() as db:
            await market_data_repo.upsert_intraday_snapshot(
                db, today, captured_at=now_sh, rows=pool_rows
            )
            await db.commit()
        logger.info(
            "intraday_sentiment_poll upserted: trade_date=%s zt=%d captured_at=%s",
            today,
            len(pool_rows),
            now_sh.isoformat(),
        )
    except Exception as exc:
        logger.exception("intraday_sentiment_poll failed (trade_date=%s)", today)
        await _alert_job_failure("intraday_sentiment_poll", exc)
        return


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
# Concept board members (daily full snapshot + diff)
# ------------------------------------------------------------------


async def concept_members_refresh_job() -> dict[str, int] | None:
    """概念板块成分刷新（交易日 18:20 盘后，幂等全量 upsert + 差分）。

    时间选择：避开 17:45 全量对账与 18:00 龙虎榜；东财成分表收盘后稳定；全量 500+
    板块约 5 分钟节流请求。**不设 per-job `misfire_grace_time`** → 继承全局
    `job_defaults`（None），宿主挂起/栈停摆后迟到也补跑（ingest 幂等，重跑只产生
    0 变更行）。不做缓存失效：概念读路径按 TTL 缓存，"写后不主动失效"是当前约定。

    板块列表抓取失败时 `ingest_concept_members` 向上抛（见其 docstring）——此处
    `logger.exception` 记录后吞掉，与其它盘后 job 一致（调度器不因单任务失败停摆）。
    返回结果 dict（失败时 None）；日志按 key 取名，绝不位置解包（T4 契约 8 键）。
    """
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services import concept_service  # noqa: PLC0415

    logger.info("Concept members refresh job triggered")
    try:
        async with async_session_factory() as db:
            result = await concept_service.ingest_concept_members(db)
            await db.commit()
        logger.info(
            "Concept members refresh done: boards=%s members_upserted=%s added=%s removed=%s "
            "failed_boards=%s partial_boards=%s unresolved=%s dropped_members=%s",
            result.get("boards"),
            result.get("members_upserted"),
            result.get("added"),
            result.get("removed"),
            result.get("failed_boards"),
            result.get("partial_boards"),
            result.get("unresolved"),
            result.get("dropped_members"),
        )
        return result
    except Exception as exc:
        logger.exception("Concept members refresh failed")
        await _alert_job_failure("concept_members_refresh", exc)
        return None


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
