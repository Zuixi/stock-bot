"""纯单元测试：AKShare fetcher（表驱动映射/护栏/隔离）+ 按覆盖清除键计算.

不触网：``_fetch_akshare_rows`` 注入假 client，fixture DataFrame 复刻实机验证的
接口形状（搜猪网 soozhu ``日期/价格``、新浪 LH0 ``date/close``，均为 ISO 日期字符串）。
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from app.services.industry_metric_service import (
    _AKSHARE_SPECS,
    _covered_purge_keys,
    _fetch_akshare_rows,
    _fetch_xuantian_capacity_rows,
)
from app.services.industry_registry import PIG_INDUSTRY

TODAY = date.today()
PAST2 = TODAY - timedelta(days=2)
PAST1 = TODAY - timedelta(days=1)
FUTURE = TODAY + timedelta(days=7)


def _soozhu_df(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["日期", "价格"])


def _lh_df(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["date", "close"])


def _default_dfs() -> dict[str, pd.DataFrame]:
    return {
        "fetch_hog_price_trend": _soozhu_df(
            [
                (PAST2.isoformat(), 20.5),
                (PAST1.isoformat(), -5.0),  # 现货护栏 0 < v < 100 之外 → 剔除
                (TODAY.isoformat(), 20.8),
            ]
        ),
        "fetch_corn_price": _soozhu_df(
            [
                (PAST2.isoformat(), 2.41),
                (PAST1.isoformat(), 2.43),
                (TODAY.isoformat(), 2.45),
            ]
        ),
        "fetch_soybean_meal_price": _soozhu_df(
            [
                (PAST2.isoformat(), 3.10),
                (PAST1.isoformat(), 3.12),
                (TODAY.isoformat(), 3.15),
            ]
        ),
        "fetch_lh_future_daily": _lh_df(
            [
                (PAST1.isoformat(), 999999.0),  # 期货护栏 v < 100000 之外 → 剔除
                (TODAY.isoformat(), 13500.0),
                (FUTURE.isoformat(), 14000.0),  # 未来日期 → 剔除
            ]
        ),
    }


class FakeClient:
    """假 AKShare client：按方法名返回 fixture DataFrame，``fail`` 中的方法抛错."""

    def __init__(
        self,
        dfs: dict[str, pd.DataFrame] | None = None,
        fail: tuple[str, ...] = (),
    ) -> None:
        self._dfs = {**_default_dfs(), **(dfs or {})}
        self._fail = set(fail)

    def _get(self, name: str) -> pd.DataFrame:
        if name in self._fail:
            raise RuntimeError(f"{name} upstream broken")
        return self._dfs[name]

    async def fetch_hog_price_trend(self) -> pd.DataFrame:
        return self._get("fetch_hog_price_trend")

    async def fetch_corn_price(self) -> pd.DataFrame:
        return self._get("fetch_corn_price")

    async def fetch_soybean_meal_price(self) -> pd.DataFrame:
        return self._get("fetch_soybean_meal_price")

    async def fetch_lh_future_daily(self) -> pd.DataFrame:
        return self._get("fetch_lh_future_daily")


async def test_fetch_maps_specs_and_drops_dirty_rows():
    rows = await _fetch_akshare_rows(PIG_INDUSTRY, client=FakeClient())

    by_key: dict[str, list[dict]] = {}
    for r in rows:
        by_key.setdefault(r["metric_key"], []).append(r)

    # 覆盖与护栏：hog 3 行剔 -5 → 2；corn/soy 各 3；LH 3 行剔 999999 与未来日期 → 1
    assert set(by_key) == {"hog_price", "corn_price", "soybean_meal_price", "lh_future_main"}
    assert len(by_key["hog_price"]) == 2
    assert len(by_key["corn_price"]) == 3
    assert len(by_key["soybean_meal_price"]) == 3
    assert len(by_key["lh_future_main"]) == 1

    # 字段映射全部来自规格表与 registry（source/tier/freq/unit）
    spec_by_metric = {s[0]: s for s in _AKSHARE_SPECS}
    for r in rows:
        m = PIG_INDUSTRY.metric(r["metric_key"])
        assert r["industry_key"] == "pig" and r["stock_id"] == 0
        assert r["source"] == spec_by_metric[r["metric_key"]][1]
        assert r["source_tier"] == m.tier and r["freq"] == "daily"
        assert r["unit"] == m.unit and r["extra"] is None

    hog = {(r["period"], r["value"]) for r in by_key["hog_price"]}
    assert hog == {(PAST2, 20.5), (TODAY, 20.8)}
    lh = by_key["lh_future_main"][0]
    assert lh["period"] == TODAY and lh["value"] == 13500.0

    assert FUTURE not in {r["period"] for r in rows}  # 未来日期已剔除
    assert -5.0 not in {r["value"] for r in rows}  # 现货护栏
    assert 999999.0 not in {r["value"] for r in rows}  # 期货护栏


async def test_fetch_isolates_per_metric_failure():
    rows = await _fetch_akshare_rows(PIG_INDUSTRY, client=FakeClient(fail=("fetch_corn_price",)))
    assert {r["metric_key"] for r in rows} == {
        "hog_price",
        "soybean_meal_price",
        "lh_future_main",
    }, "单指标上游失效不得牵连其他指标"


async def test_fetch_window_tails_long_history():
    dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(60)]
    big_lh = _lh_df([(d.isoformat(), 100.0 + i) for i, d in enumerate(dates)])
    rows = await _fetch_akshare_rows(
        PIG_INDUSTRY,
        months=1,  # max(45, 1*31) = 45：全历史只保留近端 45 行
        client=FakeClient(
            dfs={"fetch_lh_future_daily": big_lh},
            fail=("fetch_hog_price_trend", "fetch_corn_price", "fetch_soybean_meal_price"),
        ),
    )
    assert len(rows) == 45
    assert rows[0]["period"] == dates[15] and rows[0]["value"] == 115.0
    assert rows[-1]["period"] == dates[59] and rows[-1]["value"] == 159.0


def test_akshare_specs_sources_registered_in_registry():
    # fetcher 写入的 source 必须在 registry 声明，否则源优先级裁决永远匹配不到
    for metric_key, source, _attr, _dc, _vc, _vmax in _AKSHARE_SPECS:
        m = PIG_INDUSTRY.metric(metric_key)
        assert m is not None and source in m.sources, f"{metric_key}: {source} 未登记"


def test_covered_purge_keys_keeps_set_when_ratio_inputs_missing():
    assert _covered_purge_keys({"hog_price"}) == {"hog_price"}


def test_covered_purge_keys_adds_mom_when_sow_inventory_covered():
    # 能繁存栏被真实源（stats_gov/caaa）覆盖 → 连带清除 mock 派生的 sow_inventory_mom。
    # 注意：派生 mom 需 ≥2 个基础点，单篇 caaa 文章在第二个月落地前派生不出任何行——
    # 保守设计：宁可暂时缺 mom 也不让 mock 动量继续喂周期引擎。
    assert _covered_purge_keys({"sow_inventory"}) == {"sow_inventory", "sow_inventory_mom"}


def test_covered_purge_keys_adds_ratio_when_both_inputs_covered():
    covered = {"hog_price", "corn_price", "lh_future_main"}
    assert _covered_purge_keys(covered) == covered | {"hog_corn_ratio"}


class _FakeXuantianClient:
    def __init__(self, rows):
        self._rows = rows

    async def fetch_capacity(self):
        return self._rows


async def test_xuantian_rows_expand_to_metric_rows():
    client = _FakeXuantianClient(
        [
            {
                "period": date(2025, 6, 30),
                "freq": "quarterly",
                "values": {
                    "sow_inventory": 4043.0,
                    "pork_output": 1418.0,
                    "hog_inventory": 42447.0,
                    "hog_slaughter_quarterly": 17143.0,
                },
                "raw_period": "2025年二季度（末）",
            },
            {
                "period": date(2025, 7, 31),
                "freq": "monthly",
                "values": {"sow_inventory": 4042.0},
                "raw_period": "2025年7月",
            },
        ]
    )
    rows = await _fetch_xuantian_capacity_rows(PIG_INDUSTRY, client=client)
    assert len(rows) == 5  # 季度行 4 指标 + 月度行 1 指标
    by_key = {(r["metric_key"], r["period"].isoformat()): r for r in rows}
    sow_q = by_key[("sow_inventory", "2025-06-30")]
    assert sow_q["source"] == "xuantian"
    assert sow_q["source_tier"] == "official"
    assert sow_q["freq"] == "quarterly"
    assert sow_q["value"] == 4043.0
    assert sow_q["extra"]["raw_period"] == "2025年二季度（末）"
    sow_m = by_key[("sow_inventory", "2025-07-31")]
    assert sow_m["freq"] == "monthly"


async def test_xuantian_fetch_failure_returns_empty():
    class _Boom:
        async def fetch_capacity(self):
            raise RuntimeError("network down")

    rows = await _fetch_xuantian_capacity_rows(PIG_INDUSTRY, client=_Boom())
    assert rows == []


async def test_xuantian_unknown_metric_skipped():
    # registry 未定义的指标键静默跳过（跨行业复用防线）
    client = _FakeXuantianClient(
        [
            {
                "period": date(2025, 7, 31),
                "freq": "monthly",
                "values": {"nonexistent_metric": 1.0},
                "raw_period": "2025年7月",
            }
        ]
    )
    rows = await _fetch_xuantian_capacity_rows(PIG_INDUSTRY, client=client)
    assert rows == []
