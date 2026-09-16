"""对账收敛器契约（plans/2026-09-17-data-sync-self-healing.md L2）。

正确性不依赖"任务准点跑"：以 trade_cal 为期望集、行数量级（≥0.8×全市场）
为完整判据，缺啥补啥；apply=False 只读巡检供 freshness 端点复用。
测试风格：monkeypatch 内部接缝（行数统计/回补动作/期望集），无 DB 无网络。
"""

from datetime import date
from typing import Any

import pandas as pd
import pytest

from app.services import reconciliation_service as rc

# 2026-09-14(一) 15(二) 16(三) 17(四) 18(五)：一周连续交易日
D14, D15, D16, D17 = (date(2026, 9, d) for d in (14, 15, 16, 17))


class _CalClient:
    """trade_cal 替身：返回给定开市日。"""

    def __init__(self, open_days: list[date], *, explode: bool = False) -> None:
        self.open_days = open_days
        self.explode = explode

    async def fetch_trade_cal(self, **kw: Any) -> pd.DataFrame:
        if self.explode:
            raise RuntimeError("tushare down")
        start = kw.get("start_date", "00000000")
        end = kw.get("end_date", "99991231")
        days = [d for d in self.open_days if start <= d.strftime("%Y%m%d") <= end]
        return pd.DataFrame({"cal_date": [d.strftime("%Y%m%d") for d in days]})


# ---------------------------------------------------------------- expected set


async def test_expected_dates_from_trade_cal_capped_to_window() -> None:
    client = _CalClient([D14, D15, D16, D17])
    days, degraded = await rc.expected_trade_dates(client, window_days=3, today=date(2026, 9, 18))
    assert days == [D15, D16, D17]  # 截止昨日(9/17)，窗口截尾
    assert degraded is False


async def test_expected_dates_degrade_to_weekday_heuristic() -> None:
    client = _CalClient([], explode=True)
    days, degraded = await rc.expected_trade_dates(
        client,
        window_days=2,
        today=date(2026, 9, 19),  # 周六；昨日=9/18 周五
    )
    assert days == [D17, date(2026, 9, 18)]  # 工作日启发式，截止昨日
    assert degraded is True


async def test_expected_dates_never_include_today() -> None:
    client = _CalClient([D14, D15, D16, D17])
    days, _ = await rc.expected_trade_dates(client, window_days=10, today=D17)
    assert D17 not in days  # T-1 语义：当日数据不属于期望集


# ---------------------------------------------------------------- reconcile core

UNIVERSE = 5546  # 全市场量级，阈值 = 0.8×UNIVERSE = 4436


@pytest.fixture
def _seams(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """接缝替身：固定期望集与各域行数，记录回补动作。"""
    state: dict[str, Any] = {
        "expected": [D14, D15, D16],
        "counts": {
            "daily_quotes": {D14: UNIVERSE, D15: 2, D16: 0},  # 9/15 partial、9/16 缺
            "daily_basic": {D14: UNIVERSE, D15: 0, D16: 0},
            "price_limits": {D14: UNIVERSE, D15: UNIVERSE, D16: 0},
            "sentiment": {D14: 1, D15: 0, D16: 0},
        },
        "refetch": {"daily_quotes": [], "daily_basic": [], "price_limits": [], "sentiment": []},
        "commits": 0,
    }

    async def _expected(
        client: Any, *, window_days: int, today: Any = None
    ) -> tuple[list[date], bool]:
        return state["expected"], False

    async def _universe(db: Any) -> int:
        return UNIVERSE

    async def _counts(db: Any, domain: str, dates: list[date]) -> dict[date, int]:
        return {d: state["counts"][domain].get(d, 0) for d in dates}

    async def _commit(db: Any) -> None:
        state["commits"] += 1

    monkeypatch.setattr(rc, "expected_trade_dates", _expected)
    monkeypatch.setattr(rc, "_stock_universe_count", _universe)
    monkeypatch.setattr(rc, "_row_counts", _counts)
    monkeypatch.setattr(rc, "_commit", _commit)
    for domain in state["refetch"]:
        name = f"_refetch_{domain}"

        def _make(d: str):
            async def _refetch(db: Any, day: date) -> dict[str, Any]:
                state["refetch"][d].append(day)
                return {"upserted": 1}

            return _refetch

        monkeypatch.setattr(rc, name, _make(domain))
    return state


async def test_reconcile_refetches_missing_and_partial_days(_seams: dict[str, Any]) -> None:
    result = await rc.reconcile_market_data(db=object())
    q = result["domains"]["daily_quotes"]
    assert q["missing_days"] == [D16.isoformat()]
    assert q["partial_days"] == [D15.isoformat()]  # 2 行 < 阈值 → partial，重拉解死锁
    assert q["refetched"] == [D15.isoformat(), D16.isoformat()]  # 按日期升序补
    assert q["status"] == "refetched"
    assert _seams["refetch"]["daily_basic"] == [D15, D16]


async def test_reconcile_sentiment_only_for_complete_base_days(_seams: dict[str, Any]) -> None:
    """sentiment 只在 quotes+limits 完备的期望日补——9/15 quotes partial、9/16 缺 → 都不补。"""
    await rc.reconcile_market_data(db=object())
    assert _seams["refetch"]["sentiment"] == []


async def test_reconcile_sentiment_persisted_once_base_complete(_seams: dict[str, Any]) -> None:
    _seams["counts"]["daily_quotes"] = {D14: UNIVERSE, D15: UNIVERSE, D16: UNIVERSE}
    _seams["counts"]["price_limits"] = {D14: UNIVERSE, D15: UNIVERSE, D16: UNIVERSE}
    await rc.reconcile_market_data(db=object())
    assert _seams["refetch"]["sentiment"] == [D15, D16]


async def test_reconcile_dry_run_reports_stale_without_refetch(_seams: dict[str, Any]) -> None:
    result = await rc.reconcile_market_data(db=object(), apply=False)
    assert all(not v for v in _seams["refetch"].values())
    assert result["domains"]["daily_quotes"]["status"] == "stale"
    assert result["domains"]["sentiment"]["status"] == "stale"


async def test_reconcile_only_scope_limits_refetch(_seams: dict[str, Any]) -> None:
    await rc.reconcile_market_data(db=object(), only={"daily_quotes"})
    assert _seams["refetch"]["daily_quotes"] == [D15, D16]
    assert _seams["refetch"]["daily_basic"] == []
    # only 域外不补，但报告里仍给出该域状态（freshness 巡检复用）
    result = await rc.reconcile_market_data(db=object(), apply=False, only={"daily_quotes"})
    assert "daily_basic" in result["domains"]


async def test_reconcile_all_good_is_noop(_seams: dict[str, Any]) -> None:
    for domain in ("daily_quotes", "daily_basic", "price_limits"):
        _seams["counts"][domain] = {d: UNIVERSE for d in _seams["expected"]}
    _seams["counts"]["sentiment"] = {d: 1 for d in _seams["expected"]}
    result = await rc.reconcile_market_data(db=object())
    assert all(not v for v in _seams["refetch"].values())
    assert all(dom["status"] == "ok" for dom in result["domains"].values())


async def test_reconcile_commits_between_base_and_sentiment(_seams: dict[str, Any]) -> None:
    """sentiment 的 get_snapshot 走独立会话——底座补数必须先 commit 才可见。"""
    _seams["counts"]["daily_quotes"] = {D14: UNIVERSE, D15: 2, D16: 0}
    await rc.reconcile_market_data(db=object())
    assert _seams["commits"] >= 1
