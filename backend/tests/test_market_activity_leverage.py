"""spec §S1.9 交易活跃度与杠杆资金 + §S1.11 估值与风险溢价 —— 纯单测（不触 DB / 不触 Redis）。

覆盖四类不变量：

1. **登记一致性**：registry ↔ 服务的 `metric_key` / 单位投影（字典 §4.2 规则 3：
   单位与频率随 key 固定在 registry，服务层不得各写一套）；13 个 spec 指标一个不少。
2. **缺数恒 `None`、绝不 0**（spec §S1.9 验收 + ADR 0014）：`no_quote_rows` /
   `source_empty` / `index_not_in_source` / `treasury_yield_missing` / `pe_missing`
   五条缺数路径；**0 与 `None` 严格区分**（0 是事实断言）。
3. **公式与口径**：`erp_10y = 1/PE × 100 − 10Y`；`融资买入额占比 = 融资买入额 /
   全市场成交额 × 100`；7 个指数换手率的 `extra.caliber` 恒为自由流通口径。
4. **PIT**：`published_at` 按产地分三档（15:00 / 18:00 / 19:00 Asia/Shanghai），
   **不回退 `period`**（ADR 0011 / Ruling 34-35）。

真库用例在 `tests/test_market_activity_leverage_db.py`（事务回滚，不污染库）。
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.services import market_activity_service as svc
from app.services.industry_registry import (
    INDUSTRIES,
    MARKET_ACTIVITY_ALL_KEYS,
    MARKET_ACTIVITY_CALIBER_VERSION,
    MARKET_ACTIVITY_METRIC_KEYS,
    MARKET_ACTIVITY_METRICS,
    MARKET_SENTIMENT,
    MARKET_SENTIMENT_SOURCE,
)

_CN = ZoneInfo("Asia/Shanghai")

#: spec §S1.9 逐字列的 7 个指数（顺序 = spec 顺序）。
SPEC_INDEX_LABELS = ("上证", "深证", "北证50", "科创综指", "创业板", "上证50", "沪深300")

#: 直取的 5 个 / 自建的 2 个。
DIRECT_CODES = ("000001.SH", "399001.SZ", "399006.SZ", "000016.SH", "000300.SH")
SELF_BUILT_CODES = ("899050.BJ", "000680.SH")


# ── 1) 登记一致性 ─────────────────────────────────────────────────────


def test_market_scope_registers_all_spec_metrics() -> None:
    """`INDUSTRIES['market']` 一个 scope 同时登记 §S1.8 与 §S1.9/§S1.11 的全部指标。"""
    assert INDUSTRIES["market"] is MARKET_SENTIMENT
    keys = {m.key for m in MARKET_SENTIMENT.metrics}
    assert set(svc.ALL_METRIC_KEYS) <= keys, "服务落库的 key 必须全部登记在 registry"
    assert set(MARKET_ACTIVITY_ALL_KEYS) <= keys
    # §S1.8 的 8 个仍在一个 scope 里（同表同读面，不是第二个 scope）
    assert len([m for m in MARKET_SENTIMENT.metrics if m.key.startswith("market_")]) >= 2


def test_thirteen_spec_metrics_present() -> None:
    """spec §S1.9 的 11 个 + §S1.11 的 2 个 = 13 个，一个都不能少。"""
    assert len(MARKET_ACTIVITY_METRIC_KEYS) == 13
    assert len(set(MARKET_ACTIVITY_METRIC_KEYS)) == 13
    # ERP 的 PE 底座额外登记（不属于 spec 承诺的 13 个）
    assert set(MARKET_ACTIVITY_ALL_KEYS) - set(MARKET_ACTIVITY_METRIC_KEYS) == {"pe_csi300"}


def test_service_unit_projection_matches_registry() -> None:
    """服务的 `UNITS` 必须是 registry `MetricDef.unit` 的逐字投影。"""
    registry_units = {m.key: (m.unit or None) for m in MARKET_ACTIVITY_METRICS}
    assert svc.UNITS == registry_units


def test_service_key_sets_match_registry() -> None:
    assert set(svc.ALL_METRIC_KEYS) == set(MARKET_ACTIVITY_ALL_KEYS)
    assert set(svc.INGEST_METRIC_KEYS) == set(MARKET_ACTIVITY_METRIC_KEYS)
    assert len(svc.INGEST_METRIC_KEYS) == 13


def test_every_metric_is_daily_and_carries_caliber_version() -> None:
    for m in MARKET_ACTIVITY_METRICS:
        assert m.freq == "daily", m.key
        assert m.caliber_version == MARKET_ACTIVITY_CALIBER_VERSION, m.key
        assert m.sources, m.key  # 源优先级非空


def test_seven_indices_registered_and_distinct() -> None:
    """换手率恰好 7 个，且 ts_code / metric_key 都不重复（防复制粘贴漏改）。"""
    assert len(svc.INDEX_TURNOVER_SPECS) == 7
    codes = [s.ts_code for s in svc.INDEX_TURNOVER_SPECS]
    keys = [s.metric_key for s in svc.INDEX_TURNOVER_SPECS]
    assert len(set(codes)) == 7
    assert len(set(keys)) == 7
    assert set(codes) == set(DIRECT_CODES) | set(SELF_BUILT_CODES)
    assert keys == list(svc.TURNOVER_RATIO_KEYS)


def test_seven_indices_assertion_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """少一个指数必须当场 `RuntimeError`（不允许静默少一行）。"""
    monkeypatch.setattr(svc, "INDEX_TURNOVER_SPECS", svc.INDEX_TURNOVER_SPECS[:6])
    with pytest.raises(RuntimeError, match="7 个指数换手率"):
        svc._assert_seven_indices()


def test_turnover_caliber_is_documented_as_free_float() -> None:
    """spec §S1.9 行为判据：换手率口径必须**可查**（自由流通 vs 总股本）。

    7 个换手率的 `description` 都要写明"自由流通股本"；自建的 2 个还要写明口径是
    成分加权（否则读面分不清"上游直取"与"本地加权"）。
    """
    by_key = {m.key: m for m in MARKET_ACTIVITY_METRICS}
    for spec in svc.INDEX_TURNOVER_SPECS:
        desc = by_key[spec.metric_key].description
        assert "自由流通股本" in desc, spec.metric_key
        assert spec.ts_code in desc, spec.metric_key
    for key in ("idx_turnover_ratio_bse50", "idx_turnover_ratio_star"):
        assert "加权平均" in by_key[key].description, key


def test_erp_description_states_pe_caliber_and_index() -> None:
    """spec §S1.11 验收：PE 口径与指数必须明示（整体法 / 沪深300）。"""
    desc = {m.key: m for m in MARKET_ACTIVITY_METRICS}["erp_10y"].description
    assert "整体法" in desc
    assert "沪深300" in desc
    assert "000300.SH" in desc
    # 公式必须可查
    assert "1/PE" in desc


# ── 2) 公式与口径 ─────────────────────────────────────────────────────


def _values(**kwargs: object) -> dict[str, svc.MetricValue]:
    """以"全部源都有值"为默认，按需覆盖单个入参。"""
    base: dict[str, object] = {
        "turnover": (10000.0, 5500),
        "margin": {"totals": {"rzye": 2.5e12, "rzmre": 8.0e10, "rqye": 3.0e10}},
        "direct": {code: (1.5, 0.4) for code in DIRECT_CODES},
        "self_built": {
            code: svc.MetricValue("", 2.5, None, {"caliber": "free_float_weighted"})
            for code in SELF_BUILT_CODES
        },
        "treasury": 1.8,
        "pe": 12.5,
    }
    base.update(kwargs)
    return svc.compute_day_metrics(date(2026, 10, 9), **base)  # type: ignore[arg-type]


def test_compute_day_metrics_returns_all_thirteen_keys() -> None:
    values = _values()
    assert set(values) == set(svc.INGEST_METRIC_KEYS)


def test_erp_formula_is_earnings_yield_minus_treasury() -> None:
    """`erp_10y = 1/PE × 100 − 10Y`：用 12.5 与 1.8 手算 = 8.0 − 1.8 = 6.2。"""
    mv = _values(pe=12.5, treasury=1.8)["erp_10y"]
    assert mv.value == pytest.approx(6.2)
    assert mv.missing_reason is None
    assert mv.extra["pe_caliber"] == svc.ERP_PE_CALIBER
    assert mv.extra["pe_index_code"] == "000300.SH"
    assert mv.extra["earnings_yield_pct"] == pytest.approx(8.0)
    assert mv.extra["treasury_yield"] == pytest.approx(1.8)


def test_margin_buy_ratio_formula() -> None:
    """`融资买入额占比 = 融资买入额 / 全市场成交额 × 100`。

    800 亿元 / 10000 亿元 × 100 = 8.0%。
    """
    mv = _values(turnover=(10000.0, 5500))["margin_financing_buy_ratio"]
    assert mv.value == pytest.approx(8.0)
    assert mv.extra["numerator_yuan"] == pytest.approx(8.0e10)
    assert mv.extra["denominator_yi"] == pytest.approx(10000.0)


def test_margin_units_are_yi_yuan() -> None:
    """两融金额原生单位元 → 落库亿元（2.5e12 元 = 25000 亿元）。"""
    values = _values()
    assert values["margin_financing_balance"].value == pytest.approx(25000.0)
    assert values["margin_short_balance"].value == pytest.approx(300.0)


def test_market_turnover_amount_keeps_extra_n_rows() -> None:
    mv = _values(turnover=(19166.9, 5559))["market_turnover_amount"]
    assert mv.value == pytest.approx(19166.9)
    assert mv.extra["n_rows"] == 5559
    assert mv.extra["source"] == "daily_quotes.amount"


def test_direct_turnover_extra_records_caliber_and_total_rate() -> None:
    """直取的换手率：caliber = free_float，并把总股本口径留痕供对账。"""
    mv = _values()["idx_turnover_ratio_sse"]
    assert mv.value == pytest.approx(1.5)
    assert mv.extra["caliber"] == "free_float"
    assert mv.extra["source_column"] == "turnover_rate_f"
    assert mv.extra["turnover_rate_total"] == pytest.approx(0.4)


def test_self_built_turnover_extra_records_attribution() -> None:
    """自建的换手率：口径 = free_float_weighted，归属 = 当前指数权重快照（C 类留痕）。"""
    mv = _values()["idx_turnover_ratio_bse50"]
    assert mv.value == pytest.approx(2.5)
    assert mv.extra["caliber"] == "free_float_weighted"
    assert mv.extra["upstream"].startswith("self_built")
    assert mv.extra["ts_code"] == "899050.BJ"


# ── 3) 缺数恒 None、绝不 0 ────────────────────────────────────────────


def test_no_quote_rows_yields_none_not_zero() -> None:
    """全市场无行情行：成交额与两融占比都落 NULL，**不落 0**。"""
    values = _values(turnover=None)
    for key in ("market_turnover_amount", "margin_financing_buy_ratio"):
        assert values[key].value is None, key
        assert values[key].missing_reason == svc.MISSING_NO_QUOTE_ROWS, key


def test_zero_short_balance_is_a_fact_not_a_gap() -> None:
    """融券余额 0 是"当天真的没有融券"这一**事实断言**，不是缺数。"""
    values = _values(margin={"totals": {"rzye": 1.0e12, "rzmre": 1.0e10, "rqye": 0.0}})
    mv = values["margin_short_balance"]
    assert mv.value == 0.0
    assert mv.missing_reason is None


def test_missing_margin_row_yields_none() -> None:
    values = _values(margin=None)
    for key in svc.MARGIN_KEYS:
        assert values[key].value is None, key
        assert values[key].missing_reason == svc.MISSING_SOURCE_EMPTY, key


def test_index_absent_from_source_yields_none_with_reason() -> None:
    """直取指数在上游没有行（如北证50 在 index_dailybasic 返回 0 行）→ 缺数。"""
    values = _values(direct={})
    mv = values["idx_turnover_ratio_sse"]
    assert mv.value is None
    assert mv.missing_reason == svc.MISSING_INDEX_NOT_IN_SOURCE
    assert mv.extra["upstream"] == "tushare.index_dailybasic"


def test_self_built_absent_row_yields_none_with_reason() -> None:
    values = _values(self_built={})
    mv = values["idx_turnover_ratio_bse50"]
    assert mv.value is None
    assert mv.missing_reason == svc.MISSING_SOURCE_EMPTY


def test_missing_treasury_yields_none_and_erp_reason() -> None:
    values = _values(treasury=None)
    assert values["cn_treasury_yield_10y"].value is None
    assert values["cn_treasury_yield_10y"].missing_reason == svc.MISSING_SOURCE_EMPTY
    assert values["erp_10y"].value is None
    assert values["erp_10y"].missing_reason == svc.MISSING_TREASURY_MISSING


@pytest.mark.parametrize("pe", [None, 0.0])
def test_missing_or_zero_pe_yields_none_with_reason(pe: float | None) -> None:
    """PE 缺失或为 0（1/PE 无定义）→ ERP 落 NULL + `pe_missing`，**不落 inf**。"""
    values = _values(pe=pe)
    mv = values["erp_10y"]
    assert mv.value is None
    assert mv.missing_reason == svc.MISSING_PE_MISSING
    assert mv.extra["pe"] == pe


def test_zero_turnover_denominator_yields_none_not_inf() -> None:
    """成交额为 0 时分母无定义 → 占比 NULL（不落 0、不落无穷）。"""
    mv = _values(turnover=(0.0, 0))["margin_financing_buy_ratio"]
    assert mv.value is None
    # 与"无行情行"分开的原因码：分母为 0 是"成交额 0 元"，不是"没有行"
    assert mv.missing_reason == svc.MISSING_ZERO_TURNOVER_DENOMINATOR


# ── 4) PIT ────────────────────────────────────────────────────────────


def test_published_at_is_timezone_aware_cn_close() -> None:
    got = svc.published_at_for(date(2026, 10, 9))
    assert got == datetime(2026, 10, 9, 15, 0, tzinfo=_CN)
    assert got.tzinfo is not None  # naive 会让读面按服务器时区解释（Ruling 35）


@pytest.mark.parametrize(
    ("metric_key", "expected_hour"),
    [
        ("market_turnover_amount", 15),
        ("idx_turnover_ratio_sse", 15),
        ("erp_10y", 15),
        ("margin_financing_balance", 18),
        ("margin_financing_buy_ratio", 18),
        ("margin_short_balance", 18),
        ("cn_treasury_yield_10y", 19),
    ],
)
def test_publish_time_is_per_source(metric_key: str, expected_hour: int) -> None:
    """`published_at` 按**产地各自的可得出处**分档（15:00 / 18:00 / 19:00）。"""
    assert svc._publish_time(metric_key).hour == expected_hour


def test_row_carries_published_at_and_caliber_version() -> None:
    """落库行必须带 `published_at`（非空、= 当日 15:00 CN）与 `caliber_version`。"""
    row = svc._row(date(2026, 10, 9), svc.MetricValue("market_turnover_amount", 19166.9))
    assert row["published_at"] == datetime(2026, 10, 9, 15, 0, tzinfo=_CN)
    assert row["caliber_version"] == MARKET_ACTIVITY_CALIBER_VERSION
    assert row["industry_key"] == "market"
    assert row["stock_id"] == 0
    assert row["source_tier"] == "derived"


def test_null_row_still_carries_missing_reason() -> None:
    """NULL 值的行也要留 `missing_reason`（有行 ≠ 有值，ADR 0014）。"""
    row = svc._row(
        date(2026, 10, 9),
        svc.MetricValue("erp_10y", None, svc.MISSING_PE_MISSING, {"pe": None}),
    )
    assert row["value"] is None
    assert row["extra"]["missing_reason"] == svc.MISSING_PE_MISSING
    assert row["extra"]["pe"] is None


def test_margin_row_source_is_tushare_margin() -> None:
    row = svc._row(date(2026, 10, 9), svc.MetricValue("margin_financing_balance", 1.0))
    assert row["source"] == "tushare_margin"
    assert row["source_tier"] == "official"


def test_treasury_row_source_is_chinabond() -> None:
    row = svc._row(date(2026, 10, 9), svc.MetricValue("cn_treasury_yield_10y", 1.8))
    assert row["source"] == "chinabond_yield_curve"
    assert row["source_tier"] == "official"


# ── 5) 市场汇总口径（三所求和）────────────────────────────────────────


def test_margin_by_day_sums_the_three_exchanges() -> None:
    """一行 = 一个交易所 → 落地值 = 三所**求和**；逐所拆分留痕在 `extra`。"""
    import pandas as pd

    frame = pd.DataFrame(
        [
            {
                "trade_date": "20261009",
                "exchange_id": "SSE",
                "rzye": 1.0e12,
                "rzmre": 5.0e10,
                "rqye": 1.0e10,
            },
            {
                "trade_date": "20261009",
                "exchange_id": "SZSE",
                "rzye": 2.0e12,
                "rzmre": 3.0e10,
                "rqye": 2.0e10,
            },
            {
                "trade_date": "20261009",
                "exchange_id": "BSE",
                "rzye": 1.0e10,
                "rzmre": 1.0e8,
                "rqye": 1.0e5,
            },
        ]
    )
    agg = svc.margin_by_day(frame)[date(2026, 10, 9)]
    assert agg["n_exchanges"] == 3
    assert agg["totals"]["rzye"] == pytest.approx(3.01e12)
    assert set(agg["by_exchange"]) == {"SSE", "SZSE", "BSE"}
    values = _values(margin=agg)
    assert values["margin_financing_balance"].value == pytest.approx(30100.0)


def test_margin_by_day_handles_nulls_without_turning_them_into_zero() -> None:
    """某所该字段为 NaN 时不计入求和（但 `by_exchange` 仍留该所的 None 痕迹）。"""
    import pandas as pd

    frame = pd.DataFrame(
        [
            {
                "trade_date": "20261009",
                "exchange_id": "SSE",
                "rzye": 1.0e12,
                "rzmre": None,
                "rqye": None,
            },
            {
                "trade_date": "20261009",
                "exchange_id": "SZSE",
                "rzye": 1.0e12,
                "rzmre": None,
                "rqye": None,
            },
        ]
    )
    agg = svc.margin_by_day(frame)[date(2026, 10, 9)]
    assert agg["totals"]["rzye"] == pytest.approx(2.0e12)
    assert agg["by_exchange"]["SSE"]["rzmre"] is None


def test_market_activity_registry_uses_same_scope_key() -> None:
    """不新建 scope：所有 S1.9/S1.11 指标都挂在既有的 `market` scope 下。"""
    assert MARKET_SENTIMENT.key == "market"
    assert MARKET_SENTIMENT.sw_l3_codes == []
    assert MARKET_SENTIMENT.is_industry is False


def test_sentiment_metrics_still_registered_in_same_scope() -> None:
    """§S1.8 的 8 个指标仍在同一个 scope（合并登记不得把老的冲掉）。"""
    keys = {m.key for m in MARKET_SENTIMENT.metrics}
    assert MARKET_SENTIMENT_SOURCE  # 只作引用断言，防 import 被误删
    for key in (
        "advance_decline_ratio",
        "limit_up_down_ratio",
        "above_ma20_ratio",
        "limit_up_break_ratio",
        "max_consecutive_limit_up",
        "first_board_promotion_ratio",
        "market_advance_count",
        "market_decline_count",
    ):
        assert key in keys, key


# ── 6) Ruling 59 fix round 1：PIT 落库守卫 / 自建换手的"无快照期"分诊 ────


class _FakeResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows
        #: 模拟 `INSERT ... ON CONFLICT DO UPDATE` 的 rowcount（DO UPDATE 也计入）。
        self.rowcount = 1

    def all(self) -> list[object]:
        return self._rows


class _QuoteDaySession:
    """只回答 `_quote_days` 的假会话（返回给定交易日）。"""

    def __init__(self, days: list[date]) -> None:
        self._days = days
        self.executed = 0

    async def execute(self, stmt: object) -> _FakeResult:
        self.executed += 1
        return _FakeResult([(d,) for d in self._days])


class _FakeTushare:
    """按交易日返回成分股 `turnover_rate_f` 的假上游（记录调用次数）。"""

    def __init__(self, quotes_by_day: dict[date, dict[str, float]]) -> None:
        self._quotes = quotes_by_day
        self.calls: list[str] = []

    async def fetch_daily_basic_by_trade_date(self, trade_date: str, **_: object) -> object:
        import pandas as pd

        self.calls.append(trade_date)
        day = datetime.strptime(trade_date, "%Y%m%d").date()
        rows = [
            {"ts_code": code, "turnover_rate_f": value}
            for code, value in self._quotes.get(day, {}).items()
        ]
        return pd.DataFrame(rows, columns=["ts_code", "turnover_rate_f"])


def _weight_frame(period: date, members: dict[str, float], index_code: str = "899050.BJ") -> object:
    import pandas as pd

    return pd.DataFrame(
        [
            {
                "index_code": index_code,
                "con_code": code,
                "trade_date": period.strftime("%Y%m%d"),
                "weight": weight,
                "period_date": period,
            }
            for code, weight in members.items()
        ]
    )


def test_upsert_batched_refuses_rows_missing_published_at() -> None:
    """缺 `published_at` 的行**响亮失败**（可空列漏带会静默落 NULL，Ruling 59）。"""
    import asyncio

    row = svc._row(date(2026, 10, 9), svc.MetricValue("erp_10y", 5.0))
    row["published_at"] = None
    with pytest.raises(RuntimeError, match="published_at"):
        asyncio.run(svc._upsert_batched(None, [row]))  # type: ignore[arg-type]


def test_upsert_batched_refuses_naive_published_at() -> None:
    """`published_at` 必须带时区（Ruling 35 的反面：naive 时间不是 PIT）。"""
    import asyncio

    row = svc._row(date(2026, 10, 9), svc.MetricValue("erp_10y", 5.0))
    row["published_at"] = datetime(2026, 10, 9, 15, 0)  # noqa: DTZ001 — 故意构造 naive
    with pytest.raises(RuntimeError, match="published_at"):
        asyncio.run(svc._upsert_batched(None, [row]))  # type: ignore[arg-type]


def test_upsert_batched_refuses_rows_missing_caliber_version() -> None:
    import asyncio

    row = svc._row(date(2026, 10, 9), svc.MetricValue("erp_10y", 5.0))
    row["caliber_version"] = None
    with pytest.raises(RuntimeError, match="caliber_version"):
        asyncio.run(svc._upsert_batched(None, [row]))  # type: ignore[arg-type]


def test_upsert_batched_lets_pit_rows_through() -> None:
    """带齐两列的行照常落库（守卫不是恒真）。"""
    import asyncio

    session = _QuoteDaySession([])
    rows = [svc._row(date(2026, 10, 9), svc.MetricValue("erp_10y", 5.0))]
    assert asyncio.run(svc._upsert_batched(session, rows)) == 1  # type: ignore[arg-type]


def test_self_built_pre_snapshot_days_do_not_call_upstream_nor_raise() -> None:
    """**Ruling 59 Job 2 的根因回归**：快照期之前的交易日 = 结构性无解。

    这些日子既不该去上游取数，也不该被匹配率守卫当成"键空间对不上"（旧实现在
    `_cov` 为空时把中位数默认成 0.0，于是 585 个快照前的交易日让整条回补崩掉）。
    """
    import asyncio

    frame = _weight_frame(date(2026, 9, 30), {"600000.SH": 60.0, "600001.SH": 40.0})
    days = [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]
    session = _QuoteDaySession(days)
    client = _FakeTushare({})
    out = asyncio.run(
        svc._self_built_rows(session, frame, days[0], days[-1], tushare=client)  # type: ignore[arg-type]
    )
    assert client.calls == [], "快照期之前的日子不得打上游"
    assert set(out) == set(days)
    for day in days:
        assert out[day].value is None
        assert out[day].missing_reason == svc.MISSING_NO_WEIGHT_SNAPSHOT


def test_self_built_mixed_window_only_fetches_evaluable_days() -> None:
    """窗口里既有快照前的日子也有快照后的日子：只取可评估的那天。"""
    import asyncio

    frame = _weight_frame(date(2026, 9, 30), {"600000.SH": 60.0, "600001.SH": 40.0})
    pre, eligible = date(2026, 9, 29), date(2026, 9, 30)
    session = _QuoteDaySession([pre, eligible])
    client = _FakeTushare({eligible: {"600000.SH": 2.0, "600001.SH": 3.0}})
    out = asyncio.run(
        svc._self_built_rows(session, frame, pre, eligible, tushare=client)  # type: ignore[arg-type]
    )
    assert client.calls == ["20260930"], "只应取可评估的那一天"
    assert out[pre].missing_reason == svc.MISSING_NO_WEIGHT_SNAPSHOT
    # (60×2.0 + 40×3.0) / 100 = 2.4
    assert out[eligible].value == pytest.approx(2.4)
    assert out[eligible].extra["weight_coverage"] == pytest.approx(1.0)


def test_self_built_member_match_guard_still_bites_at_095() -> None:
    """键空间真对不上时守卫**照样抛错**（门槛仍是 0.95，没有被"修空"削弱）。"""
    import asyncio

    frame = _weight_frame(date(2026, 9, 30), {"600000.SH": 100.0})
    day = date(2026, 9, 30)
    session = _QuoteDaySession([day])
    client = _FakeTushare({day: {"999999.SH": 1.0}})  # 一只都对不上
    with pytest.raises(RuntimeError, match="成分匹配率中位数"):
        asyncio.run(
            svc._self_built_rows(session, frame, day, day, tushare=client)  # type: ignore[arg-type]
        )
    assert svc.MIN_MEMBER_COVERAGE == 0.95, "门槛不得被削弱"


def test_erp_extension_keys_are_the_two_input_anchored_metrics() -> None:
    """ERP 与 10Y 收益率是**唯一**不依赖 `daily_quotes` 的两个 key（Ruling 59）。"""
    assert svc.ERP_EXTENSION_KEYS == ("cn_treasury_yield_10y", "erp_10y")
    # 这两个 key 必须在覆盖率对账的分母里（不能因为改了落库天就漏出对账）
    assert set(svc.ERP_EXTENSION_KEYS) <= set(svc.INGEST_METRIC_KEYS)


def test_treasury_fetch_clips_to_the_requested_window() -> None:
    """逐年翻窗会带回窗口外的日子 —— 必须裁剪，否则落库集合被"多出来的一年"灌满。"""
    import asyncio

    import pandas as pd

    class _FakeAkshare:
        async def fetch_china_bond_yield(self, start_date: str, end_date: str) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {"曲线名称": "中债国债收益率曲线", "日期": date(2026, 3, 2), "10年": 2.0},
                    {"曲线名称": "中债国债收益率曲线", "日期": date(2026, 10, 9), "10年": 1.6864},
                    {"曲线名称": "中债国债收益率曲线", "日期": date(2026, 12, 31), "10年": 1.5},
                ]
            )

    out = asyncio.run(svc.fetch_treasury_10y(_FakeAkshare(), date(2026, 10, 1), date(2026, 10, 9)))
    assert out == {date(2026, 10, 9): pytest.approx(1.6864)}
