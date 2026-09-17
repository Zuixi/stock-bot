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
    "market_moneyflow_daily",
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


async def test_market_moneyflow_daily_registered_post_close() -> None:
    """大盘资金流日线必须真注册：docstring 声明的 16:20 盘后调度曾长期缺失。

    `market_moneyflow_daily_job` 随 jobs.py 引入，但从未在 runner.py 注册 —— 它只在
    worker 手动任务路径里被间接触发，scheduler 侧"每天 16:20 自动跑"从未发生，
    这也是 market_moneyflow_daily 表自 2026-09-03 起陈旧的直接原因。
    """
    scheduler = create_scheduler()
    scheduler.start()
    try:
        job = scheduler.get_job("market_moneyflow_daily")
        assert job is not None, "market_moneyflow_daily job not registered"
        assert isinstance(job.trigger, CronTrigger)
        assert str(job.trigger.timezone) == "Asia/Shanghai"
        fields = {f.name: str(f) for f in job.trigger.fields}
        assert fields["day_of_week"] == "mon-fri"
        assert fields["hour"] == "16"
        assert fields["minute"] == "20"
        assert _job_misfire(scheduler, "market_moneyflow_daily") is None
    finally:
        scheduler.shutdown(wait=False)
