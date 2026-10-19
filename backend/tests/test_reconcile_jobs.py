"""jobs 层对账薄封装契约（plans/2026-09-17-data-sync-self-healing.md L3/L2 触发）。

16:30/16:45 不再"只补 T-1 + exists-skip"（停摆一天=永久洞、partial 行死锁），
改为单域对账；全量对账 job 无工作日守卫——周末启动也要能补周五的洞。

2026-10-17：对账还要**顺带刷交易日历缓存**（读面判新鲜度靠它）。漏传 cache 不会报错，
只会让读面永远判不出"现在该到哪天"—— 典型的静默退化，故单独钉一条回归。
"""

from datetime import date, datetime
from typing import Any

import pytest

from app.core.redis import CacheClient
from app.scheduler import jobs

_REDIS_SENTINEL = object()


@pytest.fixture
def _env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    converge_calls: list[dict[str, Any]] = []

    class _Ctx:
        async def __aenter__(self) -> Any:
            return object()

        async def __aexit__(self, *exc: Any) -> None:
            return None

    async def _redis_pool() -> Any:
        # `get_redis_pool` 本身是懒连接（不会真的连 Redis），但测试里也不该建全局池：
        # 换成哨兵，既保持"绝不触 Redis"，又能断言它确实被传下去了。
        return _REDIS_SENTINEL

    async def _reconcile(db: Any, **kw: Any) -> dict[str, Any]:
        calls.append(kw)
        return {
            "domains": {
                "daily_quotes": {"status": "ok"},
                "daily_basic": {"status": "ok"},
                "price_limits": {"status": "ok"},
                "sentiment": {"status": "ok"},
            },
            "expected_latest": "2026-10-14",
        }

    async def _converge(**kw: Any) -> dict[str, Any]:
        converge_calls.append(kw)
        return {"status": "ok", "target": kw.get("target"), "alerts": []}

    monkeypatch.setattr(jobs, "_is_workday", lambda: True)
    # jobs 内为函数内 import → patch 源模块属性，调用时取到替身
    monkeypatch.setattr("app.core.database.async_session_factory", _Ctx)
    monkeypatch.setattr("app.core.redis.get_redis_pool", _redis_pool)
    monkeypatch.setattr("app.services.reconciliation_service.reconcile_market_data", _reconcile)
    # 2026-10-16 起全量对账尾步会串联派生指标收敛：它**自持 engine/session**，
    # 不打桩就会在默认门禁里真连 PG（本机 5433 可达 ⇒ 测试静默触库）。测试必须保持
    # "绝不触 PG/Redis/网络"（收敛自身的契约在 tests/test_derived_metrics_converge.py）。
    monkeypatch.setattr("app.services.derived_metrics_service.converge", _converge)
    return {"calls": calls, "converge": converge_calls}


def _scopes(env: dict[str, Any]) -> list[dict[str, Any]]:
    """只取 `only` 维度比对（`cache` 另行断言，避免把两件事混在一个 assert 里）。"""
    return [{"only": call["only"]} for call in env["calls"]]


async def test_quotes_job_is_single_domain_reconcile(_env: dict[str, Any]) -> None:
    await jobs.daily_quotes_backfill_job()
    assert _scopes(_env) == [{"only": {"daily_quotes"}}]


async def test_basic_job_is_single_domain_reconcile(_env: dict[str, Any]) -> None:
    await jobs.daily_basic_backfill_job()
    assert _scopes(_env) == [{"only": {"daily_basic"}}]


async def test_reconcile_job_passes_cache_so_calendar_gets_cached(_env: dict[str, Any]) -> None:
    """对账必须把 `CacheClient` 传下去：交易日历由它写入，读面据此判新鲜度.

    这是"接线类"缺陷的回归防护 —— 漏传不报错、不失败，只是读面永远判不出期望日
    （回落到"无法判定"档），线上要等有人问"为什么老是判不出"才会发现。
    """
    await jobs.daily_quotes_backfill_job()

    cache = _env["calls"][0]["cache"]
    assert isinstance(cache, CacheClient)
    assert cache._redis is _REDIS_SENTINEL  # 端到端：get_redis_pool → CacheClient → 对账


async def test_full_reconcile_job_no_scope_no_workday_guard(_env: dict[str, Any]) -> None:
    monkeypatch_none_workday = _env  # noqa: F841 — fixture already forces workday; guard absence见下
    await jobs.reconcile_market_data_job()
    assert _scopes(_env) == [{"only": None}]
    # 2026-10-16：全量对账之后必须**接着**收敛派生指标（同一 job、同一次触发）
    assert len(_env["converge"]) == 1
    assert _env["converge"][0]["alert_job_id"] == "market_metrics_refresh"


async def test_full_reconcile_job_runs_on_weekend(
    monkeypatch: pytest.MonkeyPatch, _env: dict[str, Any]
) -> None:
    monkeypatch.setattr(jobs, "_is_workday", lambda: False)
    await jobs.reconcile_market_data_job()
    assert _scopes(_env) == [{"only": None}]  # 对账无工作日守卫：周末也要补周五的洞
    assert len(_env["converge"]) == 1, "周末收敛也要跑（宿主周五晚挂起、周六开机的场景）"


def test_fetch_yesterday_helpers_removed() -> None:
    """exists-skip 语义已死——回归防护，防止"顺手"复活。"""
    assert not hasattr(jobs, "_fetch_yesterday_daily_quotes")
    assert not hasattr(jobs, "_fetch_yesterday_daily_basic")


# ---------------------------------------------------------------- worker trigger


async def test_worker_reconcile_job_type_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """market_data.fetch 队列新增 reconcile 类型：运维可随时手动触发对账。"""
    from app.schemas.task import MarketDataFetchRequest
    from app.workers import market_data_worker

    MarketDataFetchRequest(type="reconcile")  # Literal 放行

    calls: list[dict[str, Any]] = []

    class _Session:
        async def commit(self) -> None:
            return None

    class _Ctx:
        async def __aenter__(self) -> Any:
            return _Session()

        async def __aexit__(self, *exc: Any) -> None:
            return None

    async def _reconcile(db: Any, **kw: Any) -> dict[str, Any]:
        calls.append(kw)
        return {"domains": {}}

    monkeypatch.setattr(market_data_worker, "async_session_factory", _Ctx)
    monkeypatch.setattr("app.services.reconciliation_service.reconcile_market_data", _reconcile)
    result = await market_data_worker._run("reconcile", {})
    assert result["status"] == "completed"
    assert calls and "only" not in calls[0]


# ------------------------------------------------- cutoff 后的重试 tick（T+0）

_RETRY_TODAY = date(2026, 10, 16)


@pytest.fixture
def _retry(monkeypatch: pytest.MonkeyPatch, _env: dict[str, Any]) -> dict[str, Any]:
    """重试 tick 的接缝：期望日 / 当日是否齐备 / 是否真跑链.

    `_env` 已经把 `async_session_factory`、`get_redis_pool`、`reconcile_market_data_job`
    与收敛都打桩，这里只补两个判定输入。
    """
    from app.services import market_day_service

    state: dict[str, Any] = {"expected": _RETRY_TODAY, "complete": False}
    # jobs 内取的是 `datetime.now(_SH_TZ).date()`，用同一时区对齐
    monkeypatch.setattr(
        jobs,
        "datetime",
        type(
            "_DT",
            (),
            {"now": staticmethod(lambda tz=None: datetime(2026, 10, 16, 19, 0, tzinfo=tz))},
        ),
    )

    async def _resolve(cache: Any, *, now: Any = None) -> tuple[Any, Any]:
        return state["expected"], None

    async def _complete(db: Any, day: Any, *, universe: Any = None) -> bool:
        return bool(state["complete"])

    monkeypatch.setattr(market_day_service, "resolve_expected_day", _resolve)
    monkeypatch.setattr(market_day_service, "day_is_complete", _complete)
    return state


async def test_retry_skips_when_expected_is_not_today(
    _retry: dict[str, Any], _env: dict[str, Any]
) -> None:
    """期望日不是今天（今天休市 / 还没到 cutoff）⇒ 整条链不跑（零外呼）。"""
    _retry["expected"] = date(2026, 10, 15)

    await jobs.reconcile_retry_job()

    assert _env["calls"] == []
    assert _env["converge"] == []


async def test_retry_skips_when_today_already_complete(
    _retry: dict[str, Any], _env: dict[str, Any]
) -> None:
    """期望日就是今天、且今天已过齐备门禁 ⇒ 已追平，跳过（这是把成本压住的那一条）."""
    _retry["complete"] = True

    await jobs.reconcile_retry_job()

    assert _env["calls"] == []


async def test_retry_runs_chain_when_today_incomplete(
    _retry: dict[str, Any], _env: dict[str, Any]
) -> None:
    """今天还没齐备 ⇒ 补跑整条链，且失败登记挂到重试 tick 自己的 id 上。"""
    await jobs.reconcile_retry_job()

    assert [c["only"] for c in _env["calls"]] == [None]
    assert len(_env["converge"]) == 1


async def test_retry_fail_open_when_expectation_unknown(
    _retry: dict[str, Any], _env: dict[str, Any]
) -> None:
    """判不出期望日（Redis 缓存未命中）⇒ **照跑**（宁可多跑一轮幂等链，也不让缓存抖动阻断追平）."""
    _retry["expected"] = None

    await jobs.reconcile_retry_job()

    assert [c["only"] for c in _env["calls"]] == [None]


async def test_retry_ticks_are_registered_with_their_ids(_retry: dict[str, Any]) -> None:
    """重试 tick 的 id 必须登记在 `jobs.RECONCILE_RETRY_TICKS`（门禁要求 id 可被 grep 到，
    且失败登记要挂对 id —— 漏登记等于又一个静默失败）。"""
    assert jobs.RECONCILE_RETRY_JOB_IDS == ("reconcile_retry_1830", "reconcile_retry_2000")
    assert all(len(tick) == 3 for tick in jobs.RECONCILE_RETRY_TICKS)
