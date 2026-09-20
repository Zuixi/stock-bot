"""东财涨停池 → 盘中 snapshot（纯函数 + 夹具）。

合同要点（与 plans/2026-09-17-market-sentiment-overhaul.md Task 10 对齐）：

1. 纯函数 `build_intraday_snapshot`，**不**做 IO。`fetch_intraday_pool` 才是 IO 边界。
2. 输出与 `limit_up_service.get_snapshot` **同构**：消费侧（Task 11/12/13）能
   把盘中快照与收盘快照送进同一份 `LimitUpLadderOut` / `SectorLimitUpOut` /
   `YesterdayLimitUpOut` 解析路径。Task 10 不再发明第三套形状。
3. 板块聚合按 `board_name`（东财 hybk），不是 SW L3；Task 14 会换成东财 BK 体系。
4. `as_of_label` 形如 ``"盘中 09:30"``，前缀固定。
5. 失败可观测：空池 / None 池 → 仍有 shape（不全 KeyError），但 `degraded_reason`
   给出原因；`as_of` 不动。

fixture = ``tests/fixtures/eastmoney/zt_pool_20260917.json``，47 行真实形状
（`_map_zt_pool_row` 之后）。测试**只**读夹具，不调真实网络。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from app.services.intraday_sentiment_service import (
    build_intraday_snapshot,
    fetch_intraday_pool,
)

FIXTURE = Path(__file__).parent / "fixtures" / "eastmoney" / "zt_pool_20260917.json"


def _load_pool() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# ----- shape / keys 与 limit_up_service.get_snapshot 同构 ------------------------------


def test_output_keys_match_close_snapshot():
    """盘中快照必须与收盘快照同构（LimitUpLadderOut / SectorLimitUpOut / YesterdayLimitUpOut
    都能直接 .model_validate）。"""
    pool = _load_pool()
    snap = build_intraday_snapshot(
        pool, as_of=date(2026, 9, 17), captured_at=datetime(2026, 9, 17, 9, 30, 0)
    )
    expected = {
        "as_of",
        "as_of_prev",
        "as_of_quality",
        "source",
        "limits_present",
        "is_partial",
        "sw_coverage",
        "degraded_reason",
        "market_days",
        "breadth",
        "kpis",
        "echelons",
        "sectors",
        "yesterday",
        "as_of_label",
    }
    assert expected.issubset(snap.keys())


# ----- as_of_label / source 严格契约 --------------------------------------------------


def test_source_is_eastmoney_intraday_and_label_has_pankou_prefix():
    """`source="eastmoney_intraday"`，`as_of_label` 前缀固定 '盘中 '。"""
    pool = _load_pool()
    snap = build_intraday_snapshot(
        pool, as_of=date(2026, 9, 17), captured_at=datetime(2026, 9, 17, 9, 30, 0)
    )
    assert snap["source"] == "eastmoney_intraday"
    assert snap["as_of_label"] == "盘中 09:30"


def test_captured_at_minutes_only_is_hhmm():
    """as_of_label 用 ``%H:%M``（不带秒），与前端徽标宽度匹配。"""
    pool = _load_pool()
    snap = build_intraday_snapshot(
        pool,
        as_of=date(2026, 9, 17),
        captured_at=datetime(2026, 9, 17, 14, 5, 59),
    )
    assert snap["as_of_label"] == "盘中 14:05"


# ----- kpis / counts 严格契约（合部断言）---------------------------------------------


def test_zt_count_equals_pool_size_and_max_streak_is_3():
    pool = _load_pool()
    snap = build_intraday_snapshot(
        pool, as_of=date(2026, 9, 17), captured_at=datetime(2026, 9, 17, 10, 0, 0)
    )
    assert snap["kpis"]["zt_count"] == len(pool) == 47
    assert snap["kpis"]["max_streak"] == 3


def test_echelons_are_grouped_by_streak_and_ordered_descending():
    """档位 = streak 分组、label 形如 ``"{streak}连板"``，按 streak 降序。"""
    pool = _load_pool()
    snap = build_intraday_snapshot(
        pool, as_of=date(2026, 9, 17), captured_at=datetime(2026, 9, 17, 10, 0, 0)
    )
    streaks = [b["streak"] for b in snap["echelons"]]
    assert streaks == sorted(streaks, reverse=True)
    for b in snap["echelons"]:
        assert b["label"] == f"{b['streak']}连板"
        for s in b["stocks"]:
            assert s["streak"] == b["streak"]


def test_seal_fund_and_break_count_pass_through_for_known_symbol():
    """已知票（夹具里 000001）的 seal_fund / break_count / seal_time 必须**直通**。"""
    pool = _load_pool()
    snap = build_intraday_snapshot(
        pool, as_of=date(2026, 9, 17), captured_at=datetime(2026, 9, 17, 10, 0, 0)
    )
    all_stocks = [s for b in snap["echelons"] for s in b["stocks"]]
    target = next(s for s in all_stocks if s["symbol"] == "000001")
    # 夹具里这票 seal_fund=123456789.0、break_count=2、seal_time=09:25:00
    assert target["seal_fund"] == 123456789.0
    assert target["break_count"] == 2
    assert target["seal_time"] == "09:25:00"


# ----- 行业 / 板块聚合 ------------------------------------------------------------------


def test_sectors_aggregate_by_board_name_with_leader_streak_and_count():
    pool = _load_pool()
    snap = build_intraday_snapshot(
        pool, as_of=date(2026, 9, 17), captured_at=datetime(2026, 9, 17, 10, 0, 0)
    )
    # sectors.items 不空（夹具故意设计了多板块）
    assert snap["sectors"]["items"], "sectors.items must be non-empty for a 47-row fixture"
    # 板块 shape：每个 item 必含 board_name、max_streak、leader_symbol、zt_count
    for item in snap["sectors"]["items"]:
        assert {
            "board_name",
            "max_streak",
            "leader_symbol",
            "leader_name",
            "leader_streak",
            "zt_count",
        } <= item.keys()
        assert item["zt_count"] >= 1
        assert item["max_streak"] >= 1
    # unclassified_count = 0（Task 10 简化：所有夹具行都带 board_name）
    assert snap["sectors"]["unclassified_count"] == 0


# ----- 退化 / 边界 ----------------------------------------------------------------------


def test_empty_pool_returns_degraded_payload_not_crash():
    """空池 → payload 仍有 shape，degraded_reason="no_limit_up_rows"。"""
    snap = build_intraday_snapshot(
        [], as_of=date(2026, 9, 17), captured_at=datetime(2026, 9, 17, 9, 30, 0)
    )
    assert snap["degraded_reason"] == "no_limit_up_rows"
    assert snap["echelons"] == []
    assert snap["sectors"] == {"items": [], "unclassified_count": 0}
    assert snap["kpis"]["zt_count"] == 0
    assert snap["kpis"]["max_streak"] == 0
    # as_of 不被改写（as_of 仍为入参）
    assert snap["as_of"] == date(2026, 9, 17)
    # yesterday 必须是 None（盘中无"昨日→今日"语义）
    assert snap["yesterday"] is None
    # market_days 是单元素
    assert snap["market_days"] == [date(2026, 9, 17)]


def test_market_days_is_singleton_with_as_of():
    """盘中无窗口语义：market_days = [as_of]。"""
    pool = _load_pool()
    snap = build_intraday_snapshot(
        pool, as_of=date(2026, 9, 17), captured_at=datetime(2026, 9, 17, 10, 0, 0)
    )
    assert snap["market_days"] == [date(2026, 9, 17)]


def test_yesterday_is_none_for_intraday():
    """盘中无 '昨日→今日' 语义 → yesterday=None。"""
    pool = _load_pool()
    snap = build_intraday_snapshot(
        pool, as_of=date(2026, 9, 17), captured_at=datetime(2026, 9, 17, 10, 0, 0)
    )
    assert snap["yesterday"] is None


def test_breadth_uses_pool_size_with_zero_dt_zb():
    """盘中无 daily_quotes partial day → breadth = pool 行数 + 0/0。"""
    pool = _load_pool()
    snap = build_intraday_snapshot(
        pool, as_of=date(2026, 9, 17), captured_at=datetime(2026, 9, 17, 10, 0, 0)
    )
    assert snap["breadth"]["zt_count"] == len(pool) == 47
    assert snap["breadth"]["dt_count"] == 0
    assert snap["breadth"]["zb_count"] == 0


def test_limits_present_true_when_pool_non_empty():
    """非空池 → limits_present=True。空池 → False（与收盘口径一致）。"""
    snap_full = build_intraday_snapshot(
        _load_pool(),
        as_of=date(2026, 9, 17),
        captured_at=datetime(2026, 9, 17, 10, 0, 0),
    )
    assert snap_full["limits_present"] is True
    snap_empty = build_intraday_snapshot(
        [], as_of=date(2026, 9, 17), captured_at=datetime(2026, 9, 17, 9, 30, 0)
    )
    assert snap_empty["limits_present"] is False


# ----- IO 边界：fetch_intraday_pool 是 eastmoney_client 的薄封装 -----------------------


async def test_fetch_intraday_pool_delegates_to_eastmoney_client(monkeypatch):
    """`fetch_intraday_pool("20260917")` 必须**仅**调用
    `EastmoneyClient.fetch_limit_up_pool("20260917")`，并把返回值原样返回。"""
    from app.core.providers import eastmoney_client as em

    sentinel = [{"symbol": "000001", "name": "x", "streak": 1}]

    async def _fake(self, trade_date: str) -> list[dict]:
        assert trade_date == "20260917"
        return sentinel

    monkeypatch.setattr(em.EastmoneyClient, "fetch_limit_up_pool", _fake)
    # 改写 get_eastmoney_client 避免在本地拿真实 httpx 客户端
    from app.services import intraday_sentiment_service as svc

    monkeypatch.setattr(svc, "get_eastmoney_client", lambda: em.EastmoneyClient())
    out = await fetch_intraday_pool("20260917")
    assert out == sentinel
