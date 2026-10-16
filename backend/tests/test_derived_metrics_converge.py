"""`derived_metrics_service`（派生指标收敛器）—— 窗口规划 / 闸门 / 单腿隔离 / 锁.

分层（与 `test_market_sentiment_composite_db.py` 同一套约定）：

1. **纯函数**（窗口与落后天数）：不连库、不 mock，逐位钉住判据；
2. **编排**（`_converge_inner`）：用**假 session** + 打桩的腿/闸门/composite，
   验证"闸门未放行就不算""单腿失败不外溢""喂 composite 的腿没齐就不算 composite"
   这三条**行为**契约 —— 它们是这次事故的直接防线；
3. **真库**（``-m e2e``，默认 deselect）：薄行被发布闸门改判 + 重算自愈
   （2026-10-12 复现），需要 ``TEST_DATABASE_URL``。

**为什么不在这里跑端到端 `converge()` 写真库**：它自带 session + advisory lock，
会**提交**真实写入；端到端验收放在部署后的真实库检查里（见计划第 7 步），
本文件只做"回滚事务内"的真库断言。
"""

from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from app.services import derived_metrics_service as dms

CN = ZoneInfo("Asia/Shanghai")


# ---------------------------------------------------------------------------
# 1. 纯函数：窗口规划 / 落后天数
# ---------------------------------------------------------------------------


def _days(n: int, start: date = date(2026, 9, 1)) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def test_plan_window_covers_whole_window_when_leg_has_no_rows() -> None:
    """该腿一行都没有 ⇒ 从窗口下界算到 target（不是"跳过"、也不是只算 target 一天）."""
    days = _days(10)
    assert dms.plan_leg_window(target=days[-1], window_lo=days[0], latest=None) == (
        days[0],
        days[-1],
    )


def test_plan_window_is_none_when_caught_up_and_no_rescan() -> None:
    """追平且不要求重扫 ⇒ `None`（本轮**零外呼** —— 日更不该天天重算全窗口）."""
    days = _days(10)
    assert dms.plan_leg_window(target=days[-1], window_lo=days[0], latest=days[-1]) is None
    # 超过 target 也算追平（源侧领先时不得回退去重算）
    assert dms.plan_leg_window(target=days[-1], window_lo=days[0], latest=days[-1]) is None


def test_plan_window_rescans_when_always_rescan() -> None:
    """`always_rescan`（波动率腿）⇒ 追平也重扫窗口.

    理由：QVIX 的静态 CSV 迟到补数，先落的 NULL 行必须能被同一窗口覆盖成有值行。
    """
    days = _days(10)
    assert dms.plan_leg_window(
        target=days[-1], window_lo=days[0], latest=days[-1], always_rescan=True
    ) == (days[0], days[-1])


def test_plan_window_starts_from_lagging_leg_when_behind() -> None:
    """落后 ⇒ 从它自己的落库点起补（**含**该日：那天可能是写薄的一行）."""
    days = _days(10)
    assert dms.plan_leg_window(target=days[-1], window_lo=days[5], latest=days[3]) == (
        days[3],
        days[-1],
    )
    # 落后点晚于窗口下界时取落后点；早于窗口下界时取窗口下界（不为一条腿拉全量）
    assert dms.plan_leg_window(target=days[-1], window_lo=days[5], latest=days[7]) == (
        days[5],
        days[-1],
    )


def test_lag_trading_days_counts_days_after_latest_up_to_target() -> None:
    days = _days(10)
    assert dms.lag_trading_days(days, latest=days[-1], target=days[-1]) == 0
    assert dms.lag_trading_days(days, latest=days[-3], target=days[-1]) == 2
    assert dms.lag_trading_days(days, latest=days[0], target=days[-1]) == 9
    # 一行都没有 ⇒ None（不是 0："没有数据"与"数据是今天的"必须区分）
    assert dms.lag_trading_days(days, latest=None, target=days[-1]) is None
    # 领先不会算出负数
    assert dms.lag_trading_days(days, latest=days[-1] + timedelta(days=3), target=days[-1]) == 0


# ---------------------------------------------------------------------------
# 2. 编排：假 session + 打桩
# ---------------------------------------------------------------------------


class _FakeSession:
    """只提供编排用到的那几个方法（`execute` 不该被调到 —— 全部打桩了）."""

    def __init__(self) -> None:
        self.rollbacks = 0
        self.commits = 0

    async def execute(self, *_a: Any, **_k: Any) -> Any:  # pragma: no cover
        raise AssertionError("编排层不该直接用 session.execute（应走打桩的腿/闸门）")

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


TARGET = date(2026, 10, 14)
WINDOW_DAYS = _days(10, start=date(2026, 10, 1))
LAG_DAYS = _days(20, start=date(2026, 9, 15))

#: 每条腿的**第一个** key（打桩用它当腿的身份：真实实现取整条腿的 min(max(period))）。
LEG_FIRST_KEY: dict[str, str] = {
    "breadth": "above_ma20_ratio",
    "activity": "idx_turnover_ratio_csi300",
    "volatility": "qvix_50etf",
    "capital_flows": "etf_share_change",
}


def _patch_common(
    monkeypatch: pytest.MonkeyPatch,
    *,
    target: date = TARGET,
    latest_by_leg: dict[str, date | None] | None = None,
) -> dict[str, Any]:
    """把交易日、前置闸门、leg_specs、_latest_period 全打桩；返回记录用的 state."""
    state: dict[str, Any] = {
        "runs": [],
        "latest": dict(latest_by_leg or {}),
        "default_latest": target,
    }

    async def _fake_recent(db: Any, *, as_of: date, limit: int) -> list[date]:
        return LAG_DAYS[-limit:]

    async def _fake_prereq(db: Any, day: date) -> tuple[bool, dict[str, Any]]:
        return state.get("ready", True), {"stubbed": True}

    async def _fake_latest(db: Any, metric_keys: Any) -> date | None:
        # 每腿的 key 集不同 ⇒ 用第一个 key 当身份（打桩用）
        key = next(iter(metric_keys))
        return state["latest"].get(key, state["default_latest"])

    state["first_key"] = {v: k for k, v in LEG_FIRST_KEY.items()}

    monkeypatch.setattr(dms, "_recent_trade_days", _fake_recent)
    monkeypatch.setattr(dms, "prerequisites_ready", _fake_prereq)
    monkeypatch.setattr(dms, "_latest_period", _fake_latest)
    return state


def _stub_legs(monkeypatch: pytest.MonkeyPatch, state: dict[str, Any]) -> None:
    """三条喂 composite 的腿 + 一条独立腿（资金流）；runner 只记录调用."""
    specs = (
        dms.LegSpec("breadth", "宽度", ("above_ma20_ratio",)),
        dms.LegSpec("activity", "活跃度", ("idx_turnover_ratio_csi300",)),
        dms.LegSpec("volatility", "波动率", ("qvix_50etf",), always_rescan=True),
        dms.LegSpec("capital_flows", "资金流", ("etf_share_change",), feeds_composite=False),
    )
    monkeypatch.setattr(dms, "leg_specs", lambda: specs)

    def _make(name: str) -> Any:
        async def _runner(db: Any, day_lo: date, day_hi: date) -> dict[str, Any]:
            if name in state.get("fail_legs", ()):
                raise RuntimeError(f"{name} 源侧不可用")
            state["runs"].append((name, day_lo, day_hi))
            # 跑完即追平到窗口右端（真实现里是 upsert 之后 min(max(period)) 前进）；
            # `stuck_latest` 表达"跑了但源侧仍没数据"（QVIX 那类），此时不得追平
            state["latest"][LEG_FIRST_KEY[name]] = state.get("stuck_latest", {}).get(name, day_hi)
            return {"rows_upserted": 7}

        return _runner

    monkeypatch.setitem(dms._LEG_RUNNERS, "breadth", _make("breadth"))
    monkeypatch.setitem(dms._LEG_RUNNERS, "activity", _make("activity"))
    monkeypatch.setitem(dms._LEG_RUNNERS, "volatility", _make("volatility"))
    monkeypatch.setitem(dms._LEG_RUNNERS, "capital_flows", _make("capital_flows"))


async def test_converge_gated_when_prerequisites_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """底座没齐 ⇒ **一条腿都不跑**、status=gated、composite 不动."""
    state = _patch_common(monkeypatch)
    state["ready"] = False
    _stub_legs(monkeypatch, state)
    called: list[Any] = []

    from app.services import market_sentiment_composite_service as composite_svc

    async def _fake_upsert(*_a: Any, **_k: Any) -> Any:  # pragma: no cover
        called.append(1)
        raise AssertionError("底座没齐时不得算 composite")

    monkeypatch.setattr(composite_svc, "upsert_composite_range", _fake_upsert)

    result = await dms._converge_inner(_FakeSession(), target=TARGET, legs_filter=None, window=10)

    assert result.status == "gated"
    assert state["runs"] == []
    assert not called
    assert result.composite is None


async def test_converge_runs_all_legs_and_composite_when_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """齐备时四条腿都跑，composite 按 **target**（不是 max(成分 period)）重算."""
    state = _patch_common(monkeypatch)
    state["default_latest"] = date(2026, 10, 9)  # 四条腿都落后 ⇒ 都该跑
    _stub_legs(monkeypatch, state)

    from app.services import market_sentiment_composite_service as composite_svc

    seen: dict[str, Any] = {}

    async def _fake_upsert(db: Any, *, day_lo: date, day_hi: date) -> Any:
        seen["window"] = (day_lo, day_hi)
        return composite_svc.BackfillStats(
            period_lo=day_lo,
            period_hi=day_hi,
            rows_upserted=10,
            value_days=10,
            null_days=0,
            missing_reasons={},
            participating_days={},
            eras=[],
            rows_dropped_other_caliber={},
            sources={},
            days_below_publish_weight=0,
        )

    async def _fake_row(db: Any, day: date) -> Any:
        return type(
            "R",
            (),
            {
                "value": 55.5,
                "extra": {
                    "participants": [f"leg_{i}" for i in range(10)],
                    "effective_weight_sum": 0.88,
                    "missing_reason": None,
                },
            },
        )()

    monkeypatch.setattr(composite_svc, "upsert_composite_range", _fake_upsert)
    monkeypatch.setattr(composite_svc, "get_metric_row_at", _fake_row)

    result = await dms._converge_inner(_FakeSession(), target=TARGET, legs_filter=None, window=10)

    assert result.status == "ok"
    assert [name for name, _lo, _hi in state["runs"]] == [
        "breadth",
        "activity",
        "volatility",
        "capital_flows",
    ]
    # 窗口右端恒为 target（这正是"不再长出薄行"的保证）
    assert seen["window"][1] == TARGET
    assert result.composite is not None
    assert result.composite["status"] == "ok"
    assert result.composite["effective_weight_at_target"] == 0.88
    assert result.composite["legs_at_target"] == 10
    assert result.alerts == []


async def test_leg_failure_is_isolated_and_does_not_block_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """活动腿抛错 ⇒ 只该腿 failed、**rollback 一次**，其余腿照跑，composite 仍算."""
    state = _patch_common(monkeypatch)
    state["default_latest"] = date(2026, 10, 9)
    # **非成分腿**（资金流）失败：不得阻断 composite（它不喂合成）
    state["fail_legs"] = {"capital_flows"}
    _stub_legs(monkeypatch, state)
    db = _FakeSession()

    from app.services import market_sentiment_composite_service as composite_svc

    async def _fake_upsert(*_a: Any, **_k: Any) -> Any:
        return composite_svc.BackfillStats(
            period_lo=WINDOW_DAYS[0],
            period_hi=TARGET,
            rows_upserted=1,
            value_days=1,
            null_days=0,
            missing_reasons={},
            participating_days={},
            eras=[],
            rows_dropped_other_caliber={},
            sources={},
        )

    async def _fake_row(*_a: Any, **_k: Any) -> Any:
        return type(
            "R",
            (),
            {
                "value": 50.0,
                "extra": {"participants": ["a"] * 8, "effective_weight_sum": 0.85},
            },
        )()

    monkeypatch.setattr(composite_svc, "upsert_composite_range", _fake_upsert)
    monkeypatch.setattr(composite_svc, "get_metric_row_at", _fake_row)

    result = await dms._converge_inner(db, target=TARGET, legs_filter=None, window=10)

    by_key = {leg.key: leg for leg in result.legs}
    assert by_key["capital_flows"].status == "failed"
    assert "RuntimeError" in (by_key["capital_flows"].error or "")
    assert by_key["breadth"].status == "ok"
    assert by_key["activity"].status == "ok"
    assert by_key["volatility"].status == "ok"
    assert db.rollbacks == 1, "失败腿必须 rollback 且只回滚自己那一次"
    assert result.status == "converged_with_gaps"
    assert "legs_failed:capital_flows" in result.alerts
    # **非成分腿**失败不阻断 composite：它不喂合成，合成照样算
    assert result.composite is not None and result.composite["status"] == "ok"


async def test_feeder_leg_failure_blocks_composite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**成分腿**失败 ⇒ composite 跳过（否则会按残缺成分算出薄行）."""
    state = _patch_common(monkeypatch)
    state["default_latest"] = date(2026, 10, 9)
    state["fail_legs"] = {"activity"}
    _stub_legs(monkeypatch, state)

    from app.services import market_sentiment_composite_service as composite_svc

    async def _explode(*_a: Any, **_k: Any) -> Any:  # pragma: no cover
        raise AssertionError("成分腿失败时不得算 composite")

    monkeypatch.setattr(composite_svc, "upsert_composite_range", _explode)

    result = await dms._converge_inner(_FakeSession(), target=TARGET, legs_filter=None, window=10)
    assert result.composite is not None and result.composite["status"] == "skipped"
    assert "activity" in result.composite["reason"]


async def test_composite_skipped_when_feeder_leg_lags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """喂 composite 的腿没追平 ⇒ composite **不算**（避免长出薄行），并告警."""
    state = _patch_common(monkeypatch)
    state["default_latest"] = date(2026, 10, 9)
    state["stuck_latest"] = {"volatility": date(2026, 10, 9)}
    _stub_legs(monkeypatch, state)
    monkeypatch.setattr(dms, "_after_gate_alert_hour", lambda: True)

    from app.services import market_sentiment_composite_service as composite_svc

    async def _explode(*_a: Any, **_k: Any) -> Any:  # pragma: no cover
        raise AssertionError("喂 composite 的腿没齐时不得算 composite")

    monkeypatch.setattr(composite_svc, "upsert_composite_range", _explode)

    result = await dms._converge_inner(_FakeSession(), target=TARGET, legs_filter=None, window=10)

    assert result.composite is not None
    assert result.composite["status"] == "skipped"
    assert "volatility" in result.composite["reason"]
    assert any(a.startswith("composite_lagging_legs:") for a in result.alerts)


async def test_composite_skipped_silently_before_gate_hour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """盘前（<17:00）腿没齐是**正常**的"上游还没到" ⇒ 跳过但不告警（防告警疲劳）."""
    state = _patch_common(monkeypatch)
    state["default_latest"] = date(2026, 10, 9)
    state["stuck_latest"] = {"volatility": date(2026, 10, 9)}
    _stub_legs(monkeypatch, state)
    monkeypatch.setattr(dms, "_after_gate_alert_hour", lambda: False)

    result = await dms._converge_inner(_FakeSession(), target=TARGET, legs_filter=None, window=10)
    assert result.composite is not None and result.composite["status"] == "skipped"
    assert result.alerts == []


async def test_composite_alert_when_effective_weight_below_publish_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """底线：目标日生效权重 < 发布闸门 ⇒ 告警（用户看到的值不齐备）."""
    state = _patch_common(monkeypatch)
    state["default_latest"] = date(2026, 10, 9)
    _stub_legs(monkeypatch, state)

    from app.services import market_sentiment_composite_service as composite_svc

    async def _fake_upsert(*_a: Any, **_k: Any) -> Any:
        return composite_svc.BackfillStats(
            period_lo=WINDOW_DAYS[0],
            period_hi=TARGET,
            rows_upserted=1,
            value_days=0,
            null_days=1,
            missing_reasons={"below_publish_weight": 1},
            participating_days={},
            eras=[],
            rows_dropped_other_caliber={},
            sources={},
            days_below_publish_weight=1,
        )

    async def _fake_row(*_a: Any, **_k: Any) -> Any:
        return type(
            "R",
            (),
            {
                "value": None,
                "extra": {
                    "participants": ["if_basis", "ic_basis", "im_basis"],
                    "effective_weight_sum": 0.23,
                    "missing_reason": "below_publish_weight",
                },
            },
        )()

    monkeypatch.setattr(composite_svc, "upsert_composite_range", _fake_upsert)
    monkeypatch.setattr(composite_svc, "get_metric_row_at", _fake_row)

    result = await dms._converge_inner(_FakeSession(), target=TARGET, legs_filter=None, window=10)

    assert result.composite is not None
    assert result.composite["effective_weight_at_target"] == 0.23
    assert result.composite["missing_reason_at_target"] == "below_publish_weight"
    assert "composite_below_publish_weight:0.23" in result.alerts


async def test_leg_too_stale_is_not_auto_backfilled(monkeypatch: pytest.MonkeyPatch) -> None:
    """落后超过上限 ⇒ 该腿 failed 且**不调用 runner**（日更不深夜要五年历史）."""
    state = _patch_common(monkeypatch)
    _stub_legs(monkeypatch, state)
    # 用一条 20 个交易日的 lag 序列 + 很旧的落库点 ⇒ 落后 > 上限
    monkeypatch.setattr(dms, "CONVERGE_MAX_LAG_TRADING_DAYS", 3)
    state["latest"] = {"above_ma20_ratio": date(2026, 1, 1)}

    result = await dms._converge_inner(
        _FakeSession(), target=TARGET, legs_filter=["breadth"], window=10
    )

    breadth = next(leg for leg in result.legs if leg.key == "breadth")
    assert breadth.status == "failed"
    assert "上限" in (breadth.error or "")
    assert all(name != "breadth" for name, _lo, _hi in state["runs"]), "不得真的去跑回补"


async def test_unknown_leg_filter_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """手工入口传了不存在的腿名 ⇒ **响亮失败**（不静默当成"全跑"）."""
    _patch_common(monkeypatch)
    with pytest.raises(ValueError, match="未知的腿"):
        await dms._converge_inner(_FakeSession(), target=TARGET, legs_filter=["nope"], window=10)


# ---------------------------------------------------------------------------
# 3. `converge()` 的门面：锁竞争 / 告警登记
# ---------------------------------------------------------------------------


class _FakeLockConn:
    def __init__(self, *, granted: bool) -> None:
        self.granted = granted
        self.unlocked = False
        self.closed = False

    async def execute(self, stmt: Any = "", *_a: Any, **_k: Any) -> Any:
        sql = str(stmt)
        if "advisory_unlock" in sql:
            self.unlocked = True
        elif "advisory_lock" in sql and not self.granted:
            pass  # 没抢到锁：服务层据此直接返回 locked
        return type("Res", (), {"scalar": lambda _self: self.granted})()

    async def close(self) -> None:
        self.closed = True


class _FakeEngine:
    def __init__(self, conn: _FakeLockConn) -> None:
        self._conn = conn

    def connect(self) -> Any:
        """`engine.connect()` 返回 **awaitable 连接**（服务层写的是 `await engine.connect()`）."""
        conn = self._conn

        async def _connect() -> Any:
            return conn

        return _connect()


async def test_converge_returns_locked_without_doing_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """拿不到 advisory lock ⇒ `status=locked`，**一行都不写**、不开工作 session."""
    conn = _FakeLockConn(granted=False)
    monkeypatch.setattr(dms, "engine", _FakeEngine(conn))

    def _explode() -> Any:  # pragma: no cover
        raise AssertionError("拿不到锁时不得开工作 session")

    monkeypatch.setattr(dms, "async_session_factory", _explode)

    payload = await dms.converge(target=TARGET)

    assert payload["status"] == "locked"
    assert payload["legs"] == []
    assert conn.closed is True


async def test_converge_unlocks_and_registers_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """跑到告警 ⇒ 解锁 + 把告警登记到 `job:failures`（运维端点可见）."""
    conn = _FakeLockConn(granted=True)
    monkeypatch.setattr(dms, "engine", _FakeEngine(conn))

    async def _fake_inner(db: Any, *, target: Any, legs_filter: Any, window: int) -> Any:
        return dms.ConvergeResult(status="gated", target=TARGET, alerts=["boom"])

    monkeypatch.setattr(dms, "_converge_inner", _fake_inner)

    recorded: list[tuple[str, str]] = []

    from app.services import job_alert_service

    async def _fake_record(job_id: str, error: str, **_k: Any) -> None:
        recorded.append((job_id, error))

    monkeypatch.setattr(job_alert_service, "record_job_failure", _fake_record)

    payload = await dms.converge(target=TARGET, alert_job_id="market_metrics_refresh")

    assert payload["status"] == "gated"
    assert payload["alerts"] == ["boom"]
    assert recorded == [("market_metrics_refresh", "boom")]
    assert conn.unlocked is True and conn.closed is True


async def test_converge_without_alert_id_does_not_touch_alert_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没给 `alert_job_id`（手工入口）⇒ 不写告警登记表（无 job id 可挂）."""
    conn = _FakeLockConn(granted=True)
    monkeypatch.setattr(dms, "engine", _FakeEngine(conn))

    async def _fake_inner(db: Any, *, target: Any, legs_filter: Any, window: int) -> Any:
        return dms.ConvergeResult(status="gated", target=TARGET, alerts=["boom"])

    monkeypatch.setattr(dms, "_converge_inner", _fake_inner)

    from app.services import job_alert_service

    async def _explode(*_a: Any, **_k: Any) -> None:  # pragma: no cover
        raise AssertionError("没给 job id 时不得登记告警")

    monkeypatch.setattr(job_alert_service, "record_job_failure", _explode)

    payload = await dms.converge(target=TARGET)
    assert payload["alerts"] == ["boom"]


# ---------------------------------------------------------------------------
# 2b. 覆盖守卫：登记的 market 指标必须"有人日更"（**这次事故的根因**）
# ---------------------------------------------------------------------------


def test_every_registered_market_metric_is_owned_or_explained() -> None:
    """**根因守卫**：不许再有"登记齐全、却没人写"的指标.

    2026-10-16 的事故形态就是：指标、读面、手工回补脚本全都齐全，唯独没有定时任务写它 ——
    手工脚本不算写入方（没人会记得天天跑）。这条断言把"漏接调度"提前到"加 key 那次测试"。
    """
    dms.assert_market_keys_covered()  # 不满足即抛（缺归属 / 死豁免 / 两处重复声明）


def test_coverage_guard_is_not_vacuous(monkeypatch: pytest.MonkeyPatch) -> None:
    """破坏验证：临时"新增"一条没人写的 market 指标 ⇒ 守卫必须变红（断言非空转）."""
    from app.services import industry_registry as registry

    class _FakeMetric:
        key = "a_brand_new_market_metric"

    monkeypatch.setattr(
        registry,
        "MARKET_SENTIMENT_METRICS_ALL",
        [*registry.MARKET_SENTIMENT_METRICS_ALL, _FakeMetric()],
    )
    with pytest.raises(RuntimeError, match="既不在任何一条腿上"):
        dms.assert_market_keys_covered()


def test_coverage_guard_rejects_stale_exemption(monkeypatch: pytest.MonkeyPatch) -> None:
    """反向：豁免清单里的死条目（key 已不存在）也要红 —— 否则清单会越来越不可信."""
    monkeypatch.setitem(dms.CONVERGE_EXCLUDED_KEYS, "metric_that_no_longer_exists", "旧理由")
    with pytest.raises(RuntimeError, match="已不存在的 key"):
        dms.assert_market_keys_covered()


def test_coverage_guard_rejects_double_declaration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """反向：同一个 key 既被腿覆盖又在豁免清单里 ⇒ 两处声明必然漂移，必须红."""
    leg_key = next(iter(dms.leg_specs()[0].metric_keys))
    monkeypatch.setitem(dms.CONVERGE_EXCLUDED_KEYS, leg_key, "重复声明")
    with pytest.raises(RuntimeError, match="两处声明必然漂移"):
        dms.assert_market_keys_covered()


# ---------------------------------------------------------------------------
# 3b. 只读排障面：`leg_lag_report`（新鲜度巡检用）
# ---------------------------------------------------------------------------


async def test_leg_lag_report_lists_every_leg_with_lag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """四条腿都要出现，且带"推到哪天 / 落后几天 / 是否喂 composite"——只读、不外呼."""
    state = _patch_common(monkeypatch)
    _stub_legs(monkeypatch, state)
    # 目标日 = 探针序列的末日；落后天数按"末日之前还有几个交易日"算
    state["latest"] = {
        "above_ma20_ratio": LAG_DAYS[-1],  # 追平
        "idx_turnover_ratio_csi300": LAG_DAYS[14],  # 落后 5
        "qvix_50etf": LAG_DAYS[-1],  # 追平
        "etf_share_change": LAG_DAYS[16],  # 落后 3
    }

    report = await dms.leg_lag_report(_FakeSession())

    assert report["target"] == LAG_DAYS[-1].isoformat()
    by_key = {leg["key"]: leg for leg in report["legs"]}
    assert set(by_key) == set(dms.LEG_KEYS)
    assert by_key["breadth"]["lag_trading_days"] == 0
    assert by_key["activity"]["lag_trading_days"] == 5
    assert by_key["capital_flows"]["lag_trading_days"] == 3
    assert by_key["capital_flows"]["feeds_composite"] is False
    assert by_key["breadth"]["feeds_composite"] is True


async def test_leg_lag_report_is_empty_when_no_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`daily_quotes` 为空 ⇒ 明确回空（不猜目标日、不编落后天数）."""

    async def _no_days(db: Any, *, as_of: date, limit: int) -> list[date]:
        return []

    monkeypatch.setattr(dms, "_recent_trade_days", _no_days)
    assert await dms.leg_lag_report(_FakeSession()) == {"target": None, "legs": []}


# ---------------------------------------------------------------------------
# 4. 真库（`-m e2e`）：薄行被闸门改判 + 重算自愈（2026-10-12 复现）
# ---------------------------------------------------------------------------

ASYNC_URL = os.environ.get("TEST_DATABASE_URL", "")
SYNC_URL = ASYNC_URL.replace("+asyncpg", "")


def _pg_reachable() -> bool:
    if not ASYNC_URL:
        return False

    async def _go() -> bool:
        try:
            conn = await asyncpg.connect(SYNC_URL)
        except Exception:  # noqa: BLE001 — 连不上就 skip，不猜库
            return False
        await conn.close()
        return True

    return asyncio.run(_go())


requires_pg = pytest.mark.skipif(
    not _pg_reachable(), reason="需要 TEST_DATABASE_URL 指向可达的 PostgreSQL"
)


@pytest.mark.e2e
@requires_pg
def test_thin_day_is_rewritten_to_null_then_healed_on_rerun() -> None:
    """**2026-10-12 的复现与自愈**（真库，全事务回滚）.

    场景：三条基差腿推到 10-12，行情锚定的腿只到 10-09（源侧可得日不齐）。

    断言：
    1. 第一次按 10-12 算 ⇒ 该日只有 3/11 条腿、生效权重 0.23 ⇒ **不发值**
       （`value=NULL` + `below_publish_weight`，值移入 `extra.withheld_value`）；
    2. 行情锚定的腿补到 10-12 之后**重算** ⇒ 同一天被覆盖成齐备值（10 条腿 / 0.88），
       `missing_reason` 被清掉 —— 这就是"收敛器重跑即自愈"，不需要手工修数据。
    """
    from sqlalchemy import text as sql_text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from app.services import market_sentiment_composite_service as svc

    day_lo = date(2026, 10, 8)
    days = [date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 12)]
    # 三条基差腿（10-08 起）+ 行情锚定的腿（只到 10-09）
    basis = {
        "if_basis": 1.0,
        "ic_basis": 2.0,
        "im_basis": 3.0,
    }
    quote_legs = {
        "idx_turnover_ratio_csi300": 10.0,
        "margin_financing_balance": 20.0,
        "margin_financing_buy_ratio": 30.0,
        "above_ma20_ratio": 40.0,
        "advance_decline_ratio": 50.0,
        "limit_up_break_ratio": 60.0,
        "max_consecutive_limit_up": 70.0,
    }

    def _snap(row: Any) -> dict[str, Any]:
        """把 ORM 行**立刻**拷成纯 Python 值.

        ⚠️ 必须这样做：`get_metric_row_at` 走 `populate_existing` 读库，刷新的是
        identity map 里**同一个对象** —— 跨着后面的 upsert 去看这个引用，
        看到的是**被刷新后的新值**（2026-10-16 实测：断言 `thin.value is None`
        却拿到补齐后的 13.9696，差点把"自愈成功"误判成"闸门失效"）。
        """
        return {
            "value": None if row is None or row.value is None else float(row.value),
            "extra": dict(getattr(row, "extra", None) or {}),
        }

    def _obs(days_for_leg: list[date], base: float) -> list[Any]:
        return [
            svc.ConstituentObservation(
                period=day,
                value=base + i,
                published_at=datetime.combine(day, datetime.min.time(), tzinfo=CN),
            )
            for i, day in enumerate(days_for_leg)
        ]

    async def _go() -> tuple[Any, Any]:
        engine = create_async_engine(ASYNC_URL)
        try:
            async with engine.connect() as conn:
                trans = await conn.begin()
                session = AsyncSession(bind=conn)
                try:
                    # 需要 126 个有效样本才产出分位 ⇒ 前面垫 130 天历史（同一天里不含薄日）
                    hist = [date(2026, 4, 1) + timedelta(days=i) for i in range(130)]
                    observations: dict[str, list[Any]] = {}
                    for key, base in basis.items():
                        observations[key] = _obs(hist, base) + _obs(days, base)
                    for key, base in quote_legs.items():
                        # **行情锚定的腿停在 10-09**：10-12 那天没有行
                        observations[key] = _obs(hist, base) + _obs(days[:2], base)
                    await svc.upsert_composite_range(
                        session, day_lo=day_lo, day_hi=day_lo, observations=observations
                    )
                    await svc.upsert_composite_range(
                        session,
                        day_lo=days[-1],
                        day_hi=days[-1],
                        observations=observations,
                    )
                    thin = _snap(await svc.get_metric_row_at(session, days[-1]))

                    # 行情锚定的腿补到 10-12 ⇒ 重算同一天
                    for key, base in quote_legs.items():
                        observations[key] = _obs(hist, base) + _obs(days, base)
                    await svc.upsert_composite_range(
                        session,
                        day_lo=days[-1],
                        day_hi=days[-1],
                        observations=observations,
                    )
                    healed = _snap(await svc.get_metric_row_at(session, days[-1]))
                    await session.flush()
                    return thin, healed
                finally:
                    await session.close()
                    await trans.rollback()
        finally:
            await engine.dispose()

    async def _baseline_count() -> int:
        engine = create_async_engine(ASYNC_URL)
        try:
            async with engine.connect() as conn:
                return int(
                    (
                        await conn.execute(
                            sql_text(
                                "SELECT count(*) FROM industry_metrics "
                                "WHERE industry_key='market' AND metric_key=:k AND period >= :lo"
                            ),
                            {"k": svc.METRIC_KEY, "lo": date(2026, 4, 1)},
                        )
                    ).scalar()
                    or 0
                )
        finally:
            await engine.dispose()

    baseline = asyncio.run(_baseline_count())
    thin, healed = asyncio.run(_go())

    assert thin["value"] is None, "3/11 条腿的薄值不得发布"
    assert thin["extra"]["missing_reason"] == "below_publish_weight"
    assert thin["extra"]["withheld_value"] is not None  # 值不丢，可复核
    assert thin["extra"]["effective_weight_sum"] == 0.23
    assert len(thin["extra"]["participants"]) == 3

    assert healed["value"] is not None, "补齐各腿后重算必须产出值"
    assert "missing_reason" not in healed["extra"]
    assert "withheld_value" not in healed["extra"]
    assert len(healed["extra"]["participants"]) == 10
    assert healed["extra"]["effective_weight_sum"] == 0.88
    assert healed["value"] != thin["extra"]["withheld_value"]

    # 收尾：确认这次真库跑**没有留下任何行**（回滚语义的机械核对）。
    # 判据用"前后计数相等"而不是"计数为 0"：本仓库的 `TEST_DATABASE_URL` 可能是
    # 一个空库、也可能是带真实历史的库，两种都要能跑（空库时两边都是 0）。
    from app.services import market_sentiment_composite_service as _svc

    async def _count() -> int:
        engine = create_async_engine(ASYNC_URL)
        try:
            async with engine.connect() as conn:
                return int(
                    (
                        await conn.execute(
                            sql_text(
                                "SELECT count(*) FROM industry_metrics "
                                "WHERE industry_key='market' AND metric_key=:k AND period >= :lo"
                            ),
                            {"k": _svc.METRIC_KEY, "lo": date(2026, 4, 1)},
                        )
                    ).scalar()
                    or 0
                )
        finally:
            await engine.dispose()

    assert asyncio.run(_count()) == baseline, "测试必须回滚，库里不得留下行"
