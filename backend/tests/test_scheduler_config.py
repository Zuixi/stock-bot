"""调度器硬化配置契约（plans/2026-09-17-data-sync-self-healing.md L1）。

宿主挂起/容器睡眠是常态：盘后日频任务 misfire_grace_time=None（迟到也执行，
醒来由 coalesce 合并补跑一次）；盘中高频任务补跑无意义且会堆积，例外 300s。
APScheduler 未 start 时 job 处于 pending、get_job 不可见 → 测试须真启动后断言。
"""

from apscheduler.triggers.cron import CronTrigger

from app.scheduler.runner import create_scheduler

# 盘中高频任务：醒来补一堆快照/轮询无意义，限制在 5 分钟宽限内
INTRADAY_JOB_IDS = (
    "sse_trade_hours",
    "sse_trade_close",
    "sse_post_close",
    "sector_moneyflow_poll",
    "announcements_poll",
)

# 盘后日频任务：晚几小时跑也正确（TuShare 数据已在），必须永不丢弃
DAILY_JOB_IDS = (
    "daily_quotes_backfill",
    "daily_basic_backfill",
    "price_limits_daily",
    "sentiment_daily",
    "dragon_tiger_daily",
    "northbound_daily",
    "block_trade_daily",
    "share_float_daily",
    "repurchase_daily",
    "global_index_daily",
)


def _job_misfire(scheduler, job_id: str) -> int | None:
    job = scheduler.get_job(job_id)
    assert job is not None, f"job {job_id} not registered"
    return job.misfire_grace_time


async def test_daily_jobs_never_drop_and_intraday_capped() -> None:
    scheduler = create_scheduler()
    scheduler.start()
    try:
        for job_id in DAILY_JOB_IDS:
            assert _job_misfire(scheduler, job_id) is None, job_id
        for job_id in INTRADAY_JOB_IDS:
            assert _job_misfire(scheduler, job_id) == 300, job_id
        for job in scheduler.get_jobs():
            if job.coalesce is not None:
                assert job.coalesce is True, job.id
    finally:
        scheduler.shutdown(wait=False)


async def test_all_cron_jobs_share_shanghai_timezone() -> None:
    scheduler = create_scheduler()
    scheduler.start()
    try:
        cron_jobs = [j for j in scheduler.get_jobs() if isinstance(j.trigger, CronTrigger)]
        assert cron_jobs, "no cron jobs registered"
        for job in cron_jobs:
            assert str(job.trigger.timezone) == "Asia/Shanghai", job.id
    finally:
        scheduler.shutdown(wait=False)


async def test_reconcile_triggers_registered_never_drop() -> None:
    """三处触发互为冗余：启动+2min one-shot、17:45 兜底、非交易日 10:00。"""
    from apscheduler.triggers.date import DateTrigger

    scheduler = create_scheduler()
    scheduler.start()
    try:
        startup = scheduler.get_job("startup_reconcile")
        assert startup is not None and isinstance(startup.trigger, DateTrigger)
        assert _job_misfire(scheduler, "startup_reconcile") is None

        post_chain = scheduler.get_job("reconcile_post_chain")
        assert post_chain is not None and isinstance(post_chain.trigger, CronTrigger)
        assert _job_misfire(scheduler, "reconcile_post_chain") is None

        catchup = scheduler.get_job("reconcile_weekend_catchup")
        assert catchup is not None and isinstance(catchup.trigger, CronTrigger)
        assert _job_misfire(scheduler, "reconcile_weekend_catchup") is None
    finally:
        scheduler.shutdown(wait=False)


async def test_concept_refresh_job_registered_after_close() -> None:
    """概念成分刷新：交易日 18:20（避开 17:45 对账、18:00 龙虎榜），吃全局 job_defaults。

    断言 trigger 的字符串形式而非 APScheduler 内部表达式对象（见 test_limit_up_repo 教训）。
    """
    scheduler = create_scheduler()
    scheduler.start()
    try:
        job = scheduler.get_job("concept_members_refresh")
        assert job is not None, "concept_members_refresh not registered"
        assert isinstance(job.trigger, CronTrigger)
        trigger = str(job.trigger)
        assert "day_of_week='mon-fri'" in trigger, trigger
        assert "hour='18'" in trigger, trigger
        assert "minute='20'" in trigger, trigger
        # 不设 per-job 宽限 → 继承 job_defaults 的 None（停摆后迟到也补跑）
        assert job.misfire_grace_time is None
    finally:
        scheduler.shutdown(wait=False)


async def test_concept_refresh_job_calls_service_and_commits(monkeypatch) -> None:
    """job 薄封装：开会话 → 调 ingest_concept_members → commit → 原样返回结果 dict。"""
    from contextlib import asynccontextmanager

    committed: list[bool] = []
    seen: list[object] = []

    class _Session:
        async def commit(self) -> None:
            committed.append(True)

    session = _Session()

    @asynccontextmanager
    async def fake_session_factory():
        yield session

    async def fake_ingest(db):
        seen.append(db)
        return {"boards": 1, "members_upserted": 2, "added": 2, "removed": 0}

    from app.services import concept_service

    monkeypatch.setattr(concept_service, "ingest_concept_members", fake_ingest)
    monkeypatch.setattr("app.core.database.async_session_factory", fake_session_factory)

    from app.scheduler import jobs

    result = await jobs.concept_members_refresh_job()
    assert result == {"boards": 1, "members_upserted": 2, "added": 2, "removed": 0}
    assert seen == [session]
    assert committed == [True]
