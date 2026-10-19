"""接线契约：派生指标收敛**挂在全量对账之后**（不新开 cron、不挂在分域对账上）.

为什么单独测接线（而不是只测 `converge()` 本身）：

1. **顺序**由代码保证才有意义 —— 收敛必须在"行情/限价/温度计都补齐"之后跑，
   否则算在半截底座上（这正是 2026-10-12 薄行的成因）；
2. **目标日同源** —— 收敛吃对账算出的 `expected_latest`（`trade_cal` 截止昨日），
   不自己另算一份日历，否则两处口径会漂；
3. **异常边界独立** —— 派生指标失败不得把"对账成功"变成"job 失败"，
   告警也要挂在**自己**的 id（`market_metrics_refresh`）上。

全部离线（打桩），不碰 PG / Redis / 网络。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.scheduler import jobs


@pytest.fixture(autouse=True)
def _stub_facts_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    """把对账尾巴上的「涨停事实」（2026-10-17 T+0 加）在本文件打桩.

    它自带 `_is_workday()` 守卫并会触 PG / 东财；而这些用例只关心"收敛是否被
    按顺序调用"。不打桩时，一旦某用例把 `_is_workday` 打成 True，尾巴就会真连库
    （实测抛 `'object' object has no attribute 'execute'` 与 6379 连接失败）。
    """

    async def _noop() -> None:
        return None

    monkeypatch.setattr(jobs, "_refresh_limit_up_facts", _noop)


def _patch_reconcile(
    monkeypatch: pytest.MonkeyPatch, *, result: dict[str, Any] | None
) -> dict[str, Any]:
    calls: dict[str, Any] = {}

    async def _fake(only: Any, job_ids: Any) -> Any:
        calls["only"] = only
        calls["job_ids"] = job_ids
        return result

    monkeypatch.setattr(jobs, "_run_reconcile", _fake)
    return calls


def _patch_converge(monkeypatch: pytest.MonkeyPatch, calls: dict[str, Any]) -> None:
    from app.services import derived_metrics_service

    async def _fake(**kwargs: Any) -> dict[str, Any]:
        calls["converge"] = kwargs
        return {"status": "ok", "target": None, "alerts": []}

    monkeypatch.setattr(derived_metrics_service, "converge", _fake)


async def test_reconcile_job_converges_with_expected_latest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """全量对账成功后，收敛用**对账的 `expected_latest`** 当目标日（同一事实源）."""
    reconcile_calls = _patch_reconcile(
        monkeypatch, result={"expected_latest": "2026-10-14", "domains": {}}
    )
    calls: dict[str, Any] = {}
    _patch_converge(monkeypatch, calls)

    await jobs.reconcile_market_data_job()

    assert reconcile_calls["only"] is None, "必须是全量对账（分域对账不触发收敛）"
    assert calls["converge"]["target"] == date(2026, 10, 14)
    # 告警 id 必须写死在调用点（`test_job_alerts` 的字面量门禁认它）
    assert calls["converge"]["alert_job_id"] == "market_metrics_refresh"


async def test_reconcile_job_falls_back_to_local_target_when_reconcile_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """对账失败（返回 None）⇒ 仍尝试收敛，但目标日交给收敛器的本地口径（`None`）.

    为什么不直接放弃：对账可能只是某个域失败，而收敛器自带前置闸门
    （底座没齐就跳过），不需要在 job 里替它再判一次。
    """
    _patch_reconcile(monkeypatch, result=None)
    calls: dict[str, Any] = {}
    _patch_converge(monkeypatch, calls)

    await jobs.reconcile_market_data_job()

    assert calls["converge"]["target"] is None


async def test_reconcile_job_skips_converge_when_no_gated_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """对账成功但窗口里**一个日子都过不了门禁** ⇒ **跳过**本轮收敛，不得瞎猜日期.

    与上面"对账失败"那条刻意区分（失败时仍传 `None`，让收敛器用本地口径 + 自带前置
    闸门）：这里有对账结果，只是 `expected_latest` 为空。此时若仍传 `None`，收敛器会
    退回"`daily_quotes` 最近一天"—— 而那正是**没过门禁**的那天，等于把半截底座算出来
    的指标当当日值发出去。所以宁可这一轮不收敛（下一轮对账补上后自然追平）。
    """
    _patch_reconcile(
        monkeypatch,
        result={
            "domains": {},
            "expected_latest": None,
            "expected_latest_source": "none",
            "calendar_expected_latest": "2026-10-16",
        },
    )
    calls: dict[str, Any] = {}
    _patch_converge(monkeypatch, calls)

    await jobs.reconcile_market_data_job()

    assert "converge" not in calls


async def test_partial_quote_backfill_does_not_converge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**分域**对账（16:30 / 16:45 的 quotes / basic）**不得**触发收敛.

    那两个时刻 T-1 的限价与温度计还没补齐，此时算派生指标只会算在半截底座上。
    """
    from app.services import derived_metrics_service

    _patch_reconcile(monkeypatch, result={"expected_latest": "2026-10-14"})
    monkeypatch.setattr(jobs, "_is_workday", lambda: True)

    async def _explode(**_k: Any) -> Any:  # pragma: no cover
        raise AssertionError("分域对账不得触发派生指标收敛")

    monkeypatch.setattr(derived_metrics_service, "converge", _explode)

    await jobs.daily_quotes_backfill_job()
    await jobs.daily_basic_backfill_job()


async def test_converge_failure_does_not_escape_the_job_and_alerts_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """收敛抛错 ⇒ 记 `market_metrics_refresh` 告警，**不**冒泡（对账的成败不被它改写）."""
    from app.services import derived_metrics_service

    _patch_reconcile(monkeypatch, result={"expected_latest": "2026-10-14"})

    async def _boom(**_k: Any) -> Any:
        raise RuntimeError("源侧全挂")

    monkeypatch.setattr(derived_metrics_service, "converge", _boom)

    alerts: list[tuple[Any, Any]] = []

    async def _fake_alert(job_ids: Any, exc: BaseException) -> None:
        alerts.append((job_ids, exc))

    monkeypatch.setattr(jobs, "_alert_job_failure", _fake_alert)

    await jobs.reconcile_market_data_job()  # 不得抛

    assert len(alerts) == 1
    assert alerts[0][0] == "market_metrics_refresh"
    assert isinstance(alerts[0][1], RuntimeError)


async def test_converge_alert_job_id_is_alert_wired_in_jobs_source() -> None:
    """`market_metrics_refresh` 必须以字面量出现在 `jobs.py`（现有告警接线门禁的契约）.

    与 `tests/test_job_alerts.py::test_every_registered_scheduler_job_is_alert_wired`
    同一条纪律：收敛器不是独立注册的 job，但它登记告警时同样需要一个稳定 id。
    """
    import pathlib

    source = pathlib.Path(jobs.__file__).read_text()
    assert '"market_metrics_refresh"' in source


# ---------------------------------------------------------------------------
# 密闭性守卫：job 层测试**绝不**触真实 PG / 网络
# ---------------------------------------------------------------------------


async def test_job_layer_never_touches_the_real_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """加了"自持 session"的收敛之后，job 层仍必须密闭（默认门禁不许连库）.

    为什么需要这条：`derived_metrics_service.converge()` **自己开 engine 连接**（advisory
    lock 要专用连接）。于是 `reconcile_market_data_job` 的既有测试里只要漏打桩 converge，
    默认门禁就会在**本机有 PG 时静默连上 5433/6380** —— 测试看起来还全绿，但已经不密闭了
    （2026-10-16 实测到的正是这个形态）。这条守卫把"漏打桩"变成当场变红。
    """
    from app.services import derived_metrics_service

    hits: list[int] = []
    real_engine = derived_metrics_service.engine

    class _SpyEngine:
        def connect(self) -> Any:
            hits.append(1)
            return real_engine.connect()

    monkeypatch.setattr(derived_metrics_service, "engine", _SpyEngine())

    async def _fake_converge(**kwargs: Any) -> dict[str, Any]:
        return {"status": "ok", "target": None, "alerts": []}

    monkeypatch.setattr(derived_metrics_service, "converge", _fake_converge)
    _patch_reconcile(monkeypatch, result={"expected_latest": "2026-10-14"})

    await jobs.reconcile_market_data_job()

    assert hits == [], f"job 层测试触了真实 engine {len(hits)} 次 —— 漏打桩 converge"


# ---------------------------------------------------------------------------
# 手动轨道：`MarketDataJobType` 的 `market_metrics` + worker 分支
# ---------------------------------------------------------------------------


def test_market_metrics_is_a_registered_job_type() -> None:
    """任务类型必须登记在 `MarketDataJobType` 里，否则 API 层直接 422（补跑入口形同虚设）."""
    from typing import get_args

    from app.schemas.task import MarketDataJobType

    assert "market_metrics" in set(get_args(MarketDataJobType))


def test_worker_knows_the_market_metrics_type() -> None:
    from app.workers import market_data_worker as mdw

    assert "market_metrics" in mdw._KNOWN_TYPES


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("breadth", ["breadth"]),
        ("breadth, activity", ["breadth", "activity"]),
        (["breadth", "volatility"], ["breadth", "volatility"]),
        (("breadth",), ["breadth"]),
        (" , ", None),
    ],
)
def test_opt_legs_parses_strings_and_lists(raw: Any, expected: Any) -> None:
    """`legs` 参数接受逗号分隔串或列表；空值一律当"全跑"（`None`），不造空列表."""
    from app.workers.market_data_worker import _opt_legs

    assert _opt_legs(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("2026-10-14", date(2026, 10, 14)),
        ("20261014", date(2026, 10, 14)),
        (" 2026-10-14 ", date(2026, 10, 14)),
    ],
)
def test_opt_target_accepts_iso_and_compact(raw: Any, expected: Any) -> None:
    """`target` 两种写法都收（运维口：任务参数历来 `YYYYMMDD`，排障语境写 ISO）."""
    from app.workers.market_data_worker import _opt_target

    assert _opt_target(raw) == expected


def test_opt_target_rejects_garbage() -> None:
    """看不懂的日期**响亮失败**（不静默当"没给" —— 那会把指定日变成"最近交易日"）."""
    from app.workers.market_data_worker import _opt_target

    with pytest.raises(ValueError, match="target 无法解析"):
        _opt_target("2026/10/14")


def test_opt_legs_rejects_unsupported_type() -> None:
    """不支持的形态**响亮失败**（不静默当"全跑"—— 那会让"我只想补一条腿"变成全量）."""
    from app.workers.market_data_worker import _opt_legs

    with pytest.raises(ValueError, match="legs 参数类型"):
        _opt_legs(123)


async def test_worker_dispatches_market_metrics_to_converge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """worker 分支把 `target`/`legs`/`window` 透传给**同一个** `converge`（双轨同源）."""
    from app.services import derived_metrics_service
    from app.workers.market_data_worker import MarketDataWorker
    from tests.test_market_data_worker import _fake_session_factory

    seen: dict[str, Any] = {}

    async def _fake_converge(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"status": "ok", "target": "2026-10-14", "legs": [], "alerts": []}

    monkeypatch.setattr(derived_metrics_service, "converge", _fake_converge)
    monkeypatch.setattr(
        "app.workers.market_data_worker.async_session_factory", _fake_session_factory
    )

    worker = MarketDataWorker()
    result = await worker.process(
        task_id=None,
        payload={
            "type": "market_metrics",
            "params": {"target": "2026-10-14", "legs": "breadth,activity", "window": 3},
        },
    )

    assert result["status"] == "completed", "任务生命周期状态必须恒为 completed"
    assert result["type"] == "market_metrics"
    # 收敛状态嵌在 `converge` 下：与任务状态**不得撞键**（撞了就看不出"被闸门挡掉"）
    assert result["converge"]["status"] == "ok"
    assert seen["target"] == date(2026, 10, 14)
    assert seen["legs"] == ["breadth", "activity"]
    assert seen["window"] == 3
    assert seen["alert_job_id"] == "market_metrics_refresh"


async def test_worker_market_metrics_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不给参数 ⇒ 目标日本地判定（`None`）、四条腿全跑、窗口取默认常量."""
    from app.services import derived_metrics_service
    from app.workers.market_data_worker import MarketDataWorker
    from tests.test_market_data_worker import _fake_session_factory

    seen: dict[str, Any] = {}

    async def _fake_converge(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"status": "ok"}

    monkeypatch.setattr(derived_metrics_service, "converge", _fake_converge)
    monkeypatch.setattr(
        "app.workers.market_data_worker.async_session_factory", _fake_session_factory
    )

    out = await MarketDataWorker().process(task_id=None, payload={"type": "market_metrics"})

    assert out["status"] == "completed"
    assert seen["target"] is None
    assert seen["legs"] is None
    assert seen["window"] == derived_metrics_service.CONVERGE_WINDOW_TRADING_DAYS
