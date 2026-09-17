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

# `_get_tushare` 必须一并替掉：`reconcile_market_data` 开头的
# `client = client or _get_tushare()` 比所有协作函数的 monkeypatch 都早执行，
# 没 token 就直接 ValueError。本机 backend/.env 里有真实 TUSHARE_TOKEN，会把这个
# 缺口完全掩盖（本地绿、CI 红 —— main 上实测已经红过一轮 7 例），所以这里不能
# 依赖环境。同一断言钉住「注入的 client 必须透传给期望集计算」，
# 避免只堵了默认分支而真正的注入路径仍是死角。
_CLIENT = object()


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
        # 因子补洞接缝：记录 (start, end) 调用；null_gaps = 该日仍为 NULL 的缺口行数；
        # repair_error 非空时模拟补洞整体失败（外呼/DB 故障）。
        "repairs": [],
        "null_gaps": {},
        "repair_error": None,
        "repair_unfilled": 0,
        "repair_remaining": 0,
    }

    async def _expected(
        client: Any, *, window_days: int, today: Any = None
    ) -> tuple[list[date], bool]:
        assert client is _CLIENT, "reconcile 必须把注入的 client 透传给期望集计算"
        return state["expected"], False

    async def _universe(db: Any) -> int:
        return UNIVERSE

    async def _counts(db: Any, domain: str, dates: list[date]) -> dict[date, int]:
        return {d: state["counts"][domain].get(d, 0) for d in dates}

    async def _commit(db: Any) -> None:
        state["commits"] += 1

    async def _repair(db: Any, *, start: date, end: date) -> dict[str, int]:
        state["repairs"].append((start, end))
        if state["repair_error"] is not None:
            raise state["repair_error"]
        rows = sum(n for d, n in state["null_gaps"].items() if start <= d <= end)
        return {
            "stocks": 1 if rows else 0,
            "rows": rows,
            "failed": 0,
            "unfilled": state["repair_unfilled"],
            "remaining": state["repair_remaining"],
        }

    monkeypatch.setattr(rc, "_get_tushare", lambda: _CLIENT)
    monkeypatch.setattr(rc, "expected_trade_dates", _expected)
    monkeypatch.setattr(rc, "_stock_universe_count", _universe)
    monkeypatch.setattr(rc, "_row_counts", _counts)
    monkeypatch.setattr(rc, "_commit", _commit)
    # 补洞服务的真实实现被换掉（离线）：它内部要查 DB / 外呼 TuShare。
    # 从 quote_service 模块属性打补丁 —— 被测代码在函数体内 import，能拿到替身。
    monkeypatch.setattr("app.services.quote_service.backfill_missing_adj_factors", _repair)
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


async def test_reconcile_reports_quotes_symbols_next_to_universe(_seams: dict[str, Any]) -> None:
    """threshold 的分母是 stocks 表行数 —— 该表冻结时全 ok 也说明不了名录是新的。

    所以结果里必须并列给出「名录数」与「最新期望日实际有行情的股票数」，
    缺 65 只（1.2%）这类漂移才可能被发现（0.8 容差永远报 ok）。
    """
    _seams["counts"]["daily_quotes"] = {D14: UNIVERSE, D15: UNIVERSE, D16: UNIVERSE - 61}
    for domain in ("daily_basic", "price_limits"):
        _seams["counts"][domain] = {d: UNIVERSE for d in _seams["expected"]}
    _seams["counts"]["sentiment"] = {d: 1 for d in _seams["expected"]}

    result = await rc.reconcile_market_data(db=object(), apply=False)

    assert result["universe"] == UNIVERSE
    assert result["quotes_symbols_latest"] == UNIVERSE - 61
    # 差 61 只仍全 ok —— 这正是原判据的盲区，并列暴露是唯一发现途径
    assert result["domains"]["daily_quotes"]["status"] == "ok"


async def test_reconcile_commits_between_base_and_sentiment(_seams: dict[str, Any]) -> None:
    """sentiment 的 get_snapshot 走独立会话——底座补数必须先 commit 才可见。"""
    _seams["counts"]["daily_quotes"] = {D14: UNIVERSE, D15: 2, D16: 0}
    await rc.reconcile_market_data(db=object())
    assert _seams["commits"] >= 1


# ---------------------------------------------------------------- adj_factor 补洞挂载


async def test_reconcile_repairs_adj_factor_for_refetched_quotes_day(
    _seams: dict[str, Any],
) -> None:
    """重拉某天后必须补该天的因子缺口（新插入的行因子为 NULL，懒加载永远碰不到）。"""
    _seams["expected"] = [D14]
    _seams["counts"]["daily_quotes"] = {D14: 0}  # 整天缺失 → 重拉
    _seams["null_gaps"] = {D14: 3}  # 该天 3 行因子为 NULL

    result = await rc.reconcile_market_data(db=object())

    assert _seams["refetch"]["daily_quotes"] == [D14]
    assert _seams["repairs"] == [(D14, D14)]  # 恰好只补本次重拉的那天
    assert result["adj_factor_repaired"] == {
        "days": [D14.isoformat()],
        "rows": 3,
        "failed": 0,
        "unfilled": 0,
        "remaining": 0,
        "error": None,
    }


async def test_reconcile_adj_repair_scoped_to_refetched_days_only(
    _seams: dict[str, Any],
) -> None:
    """不重拉的中间日不得被顺带扫描：非连续重拉日 → 两次调用，各只覆盖一天。"""
    _seams["counts"]["daily_quotes"] = {D14: 0, D15: UNIVERSE, D16: 0}  # 重拉 9/14 与 9/16
    _seams["null_gaps"] = {D14: 1, D15: 9, D16: 2}  # 9/15 的缺口不属于本次范围

    result = await rc.reconcile_market_data(db=object())

    assert _seams["refetch"]["daily_quotes"] == [D14, D16]
    assert _seams["repairs"] == [(D14, D14), (D16, D16)]  # 不是 (D14, D16) 的宽窗口
    assert result["adj_factor_repaired"]["days"] == [D14.isoformat(), D16.isoformat()]
    assert result["adj_factor_repaired"]["rows"] == 3  # 9/15 的 9 行不算


async def test_reconcile_dry_run_never_repairs_adj_factor(_seams: dict[str, Any]) -> None:
    """apply=False 是只读巡检（freshness 端点复用），绝不外呼/写库。"""
    _seams["counts"]["daily_quotes"] = {D14: 0}
    _seams["null_gaps"] = {D14: 3}

    result = await rc.reconcile_market_data(db=object(), apply=False)

    assert _seams["refetch"]["daily_quotes"] == []
    assert _seams["repairs"] == []
    assert result["adj_factor_repaired"] is None


def test_contiguous_runs_merges_adjacent_days() -> None:
    """连续重拉日必须聚成**一个**区间（每股一次外呼覆盖整段），非连续才拆（M6a）。"""
    assert rc._contiguous_runs([D14, D15, D16]) == [(D14, D16)]  # 合并
    assert rc._contiguous_runs([D14, D15, D17]) == [(D14, D15), (D17, D17)]  # 中段断开才拆
    assert rc._contiguous_runs([]) == []


async def test_reconcile_surfaces_adj_repair_remaining(_seams: dict[str, Any]) -> None:
    """补洞被预算截断时，`remaining` 必须透出到 reconcile 结果（I2：截断可见）。"""
    _seams["counts"]["daily_quotes"] = {D14: 0}
    _seams["repair_remaining"] = 42

    result = await rc.reconcile_market_data(db=object())

    assert result["adj_factor_repaired"]["remaining"] == 42


async def test_reconcile_survives_adj_repair_failure(_seams: dict[str, Any]) -> None:
    """补洞失败不得让对账失败；失败写进结果 + 会话清理 + **后续域照常跑**。

    会话不清理会让随后的 sentiment 补数报 PendingRollbackError —— 所以本测试除了
    断言 rollback/no-raise，还要证明 sentiment 域在补洞失败后仍然执行了（M6b）。
    """
    _seams["counts"]["daily_quotes"] = {D14: UNIVERSE, D15: UNIVERSE, D16: 0}  # 重拉 9/16
    _seams["counts"]["price_limits"] = {D14: UNIVERSE, D15: UNIVERSE, D16: UNIVERSE}
    _seams["counts"]["sentiment"] = {D14: 1, D15: 0, D16: 0}  # 9/15 缺、底座完备
    _seams["repair_error"] = RuntimeError("tushare 500")

    class _DB:
        def __init__(self) -> None:
            self.rollbacks = 0

        async def rollback(self) -> None:
            self.rollbacks += 1

    db = _DB()
    result = await rc.reconcile_market_data(db=db)  # 不抛

    assert db.rollbacks == 1
    assert result["adj_factor_repaired"]["error"] == "RuntimeError: tushare 500"
    assert result["adj_factor_repaired"]["rows"] == 0
    assert result["domains"]["daily_quotes"]["status"] == "refetched"  # 对账本身仍算成功
    assert _seams["refetch"]["sentiment"] == [D15]  # 补洞失败后 sentiment 域照常补
