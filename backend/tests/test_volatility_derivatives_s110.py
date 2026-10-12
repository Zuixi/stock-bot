"""spec §S1.10 情绪 · 波动率与衍生品 —— **纯函数**不变量（不联网、不连库）。

真 PG 的写入/幂等/PIT 落库用例在 `tests/test_volatility_derivatives_s110_db.py`。

本文件锁死的是"**一旦写错就会静默错数**"的那几条：

1. **升贴水的符号约定**：`基差 = 期货 − 现货`，负值 = 贴水。spec §S1.10 的验收判据是
   「升贴水负值时 UI 明示『贴水』语义」——用 probe 实测过的真实数字做**手算回归锁**，
   换符号（`现货 − 期货`）必须当场红。
2. **PIT 两档**：QVIX 15:00 / 期货结算 16:00，都带时区；**绝回退 `period`**。
3. **缺数不静默为 0**：任一腿缺 → `value = NULL` + `missing_reason`（0 点的基差是
   "期货与现货完全平水"这一事实断言，与"这天没有数据"完全相反）。
4. **上游异常的响亮守卫**：QVIX 返回 0 行 / close 整列空 / 首个非空日 ≠ 期权上市日、
   `fut_daily` 分片撞 2000 行上限、`trade_cal` 空集、基差/现货超出合理上界
   —— 五种形态都必须 `RuntimeError`，**不得**静默落 NULL 或落错值。
5. **主力连续的"非交易日行"必须计数**（上游 CSV 实测含 3 个周末行并带真实数值）。
"""

from __future__ import annotations

import inspect
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.services import market_activity_service as market_svc
from app.services import volatility_derivatives_service as svc
from app.services.industry_registry import (
    INDUSTRIES,
    VOLATILITY_DERIVATIVES_CALIBER_VERSION,
    VOLATILITY_DERIVATIVES_METRIC_KEYS,
)

_CN = ZoneInfo("Asia/Shanghai")


# ── 假客户端（只为喂形状；不联网）──────────────────────────────────────


class _FakeAkShare:
    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self._frames = frames
        self.calls: list[str] = []

    async def fetch_qvix(self, series: str) -> pd.DataFrame:
        self.calls.append(series)
        return self._frames[series]


class _FakeTuShare:
    """只实现本模块用到的两个方法；`fut_rows` 控制每次分片返回多少行。"""

    def __init__(self, *, cal: pd.DataFrame | None = None, fut_rows: int = 5) -> None:
        self._cal = cal
        self._fut_rows = fut_rows

    async def fetch_trade_cal(self, **kwargs: object) -> pd.DataFrame:
        return pd.DataFrame() if self._cal is None else self._cal

    async def fetch_fut_daily(self, **kwargs: object) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "ts_code": ["IF.CFX"] * self._fut_rows,
                "trade_date": [
                    (date(2026, 1, 1) + timedelta(days=i)).strftime("%Y%m%d")
                    for i in range(self._fut_rows)
                ],
                "close": [4000.0] * self._fut_rows,
                "settle": [4001.0] * self._fut_rows,
            }
        )


def _qvix_frame(days: list[str], closes: list[float | None]) -> pd.DataFrame:
    return pd.DataFrame(
        {"date": days, "open": closes, "high": closes, "low": closes, "close": closes}
    )


def _leg(
    product: str,
    *,
    fut_settle: float | None,
    fut_close: float | None,
    spot_close: float | None,
    contract: str | None = "IF2612.CFX",
    delist: date | None = date(2026, 12, 18),
    expected_from: date = date(2010, 4, 16),
) -> svc.FuturesLeg:
    return svc.FuturesLeg(
        product=product,
        contract=contract,
        fut_settle=fut_settle,
        fut_close=fut_close,
        spot_close=spot_close,
        expected_from=expected_from,
        delist_date=delist,
        multiplier=300.0,
    )


# ── 1. registry / 服务的一致性（"11 个、5 期权、4 品种"是 spec 判据）──────


class TestRegistryWiring:
    def test_service_keys_are_exactly_the_spec_eleven(self) -> None:
        assert len(svc.INGEST_METRIC_KEYS) == 11
        assert set(svc.INGEST_METRIC_KEYS) == set(VOLATILITY_DERIVATIVES_METRIC_KEYS)
        assert set(svc.ALL_METRIC_KEYS) == set(VOLATILITY_DERIVATIVES_METRIC_KEYS)

    def test_eleven_keys_are_registered_in_the_market_scope(self) -> None:
        registered = {m.key for m in INDUSTRIES["market"].metrics}
        assert set(VOLATILITY_DERIVATIVES_METRIC_KEYS) <= registered

    def test_qvix_covers_five_products_and_four_futures(self) -> None:
        # spec §S1.10：期权波动率覆盖 ≥3 个品种；股指期货升贴水 = IF/IC/IH/IM 四品种。
        assert len(svc.QVIX_KEYS) == 5
        assert len(svc.BASIS_KEYS) == 4
        assert set(svc.DISCOUNT_RATE_KEYS) == {"ic_discount_rate", "im_discount_rate"}

    def test_units_match_registry(self) -> None:
        """字典 §4.2 规则 3：单位随 key 在 registry 固定 —— 服务里那张表是它的投影。"""
        registry = {m.key: (m.name, m.unit, m.freq, m.tier) for m in INDUSTRIES["market"].metrics}
        for key, unit in svc.UNITS.items():
            assert registry[key][1] == unit, f"{key} 单位漂移：{unit!r} != {registry[key][1]!r}"
            assert registry[key][2] == "daily"
        assert all(svc.UNITS[k] == "%" for k in svc.QVIX_KEYS)
        assert all(svc.UNITS[k] == "点" for k in svc.BASIS_KEYS)
        assert all(svc.UNITS[k] == "%" for k in svc.DISCOUNT_RATE_KEYS)

    def test_qvix_is_third_party_tier_not_official(self) -> None:
        """官方 iVIX 自 2018-02-22 暂停发布 ⇒ 现存 QVIX 是第三方编制，**不得**记 official。"""
        registry = {m.key: m.tier for m in INDUSTRIES["market"].metrics}
        assert {registry[k] for k in svc.QVIX_KEYS} == {"highfreq"}
        assert (
            "2018-02-22"
            in {m.key: m.description for m in INDUSTRIES["market"].metrics}[svc.QVIX_KEYS[0]]
        )

    def test_sign_convention_is_written_into_every_basis_description(self) -> None:
        """符号约定必须**写死在 registry**，不能让前端猜（spec §S1.10 的验收判据）。"""
        descriptions = {m.key: m.description for m in INDUSTRIES["market"].metrics}
        for key in svc.BASIS_KEYS:
            text = descriptions[key]
            assert "基差 = 期货 − 现货" in text
            assert "正值 = 升水" in text and "负值 = 贴水" in text
            assert "futures_minus_spot" in text
        for key in svc.DISCOUNT_RATE_KEYS:
            text = descriptions[key]
            assert "符号与基差一致" in text
            assert "不年化" in text
            assert "对冲成本" in text  # spec 点名的专业用法

    def test_caliber_version_is_registered(self) -> None:
        calibers = {m.key: m.caliber_version for m in INDUSTRIES["market"].metrics}
        for key in svc.INGEST_METRIC_KEYS:
            assert calibers[key] == VOLATILITY_DERIVATIVES_CALIBER_VERSION == "s1.10-v1"


# ── 2. PIT：两档时点 + 带时区 + 不回退 period ────────────────────────────


class TestPit:
    def test_qvix_published_at_is_close_1500_cn(self) -> None:
        for key in svc.QVIX_KEYS:
            row = svc._row(date(2026, 9, 30), svc.MetricValue(key, 13.84))
            expected = datetime(2026, 9, 30, 15, 0, tzinfo=_CN)
            assert row["published_at"] == expected, key
            assert row["published_at"].tzinfo is not None
            assert row["published_at"].date() == row["period"]  # 不回退/不偏移

    def test_basis_published_at_is_settlement_1600_cn(self) -> None:
        for key in (*svc.BASIS_KEYS, *svc.DISCOUNT_RATE_KEYS):
            row = svc._row(date(2026, 10, 12), svc.MetricValue(key, -62.18))
            expected = datetime(2026, 10, 12, 16, 0, tzinfo=_CN)
            assert row["published_at"] == expected, key

    def test_published_at_is_never_the_period_midnight(self) -> None:
        """`published_at` 不得等于 `period` 的 00:00（那正是"回退 period"的形态）。"""
        for key in svc.INGEST_METRIC_KEYS:
            row = svc._row(date(2026, 10, 12), svc.MetricValue(key, 1.0))
            assert row["published_at"].hour != 0

    def test_row_shape(self) -> None:
        row = svc._row(date(2026, 10, 12), svc.MetricValue("if_basis", -62.1798))
        assert row["industry_key"] == "market"
        assert row["stock_id"] == 0
        assert row["freq"] == "daily"
        assert row["source_tier"] == "derived"
        assert row["caliber_version"] == VOLATILITY_DERIVATIVES_CALIBER_VERSION
        assert row["value"] == -62.1798

    def test_null_row_keeps_missing_reason_in_extra(self) -> None:
        metric = svc.MetricValue(
            "qvix_50etf", None, svc.MISSING_SOURCE_NOT_UPDATED, {"series": "50etf"}
        )
        row = svc._row(date(2026, 10, 12), metric)
        assert row["value"] is None
        assert row["extra"]["missing_reason"] == "source_not_updated"
        assert row["extra"]["series"] == "50etf"

    def test_upsert_metric_rows_is_the_same_pit_guarded_function(self) -> None:
        """复用而不是复制：**同一个函数对象**（两份守卫迟早漂移，漂移 = 又一次静默 NULL）。"""
        assert svc.upsert_metric_rows is market_svc.upsert_metric_rows
        assert inspect.iscoroutinefunction(svc.upsert_metric_rows)

    @pytest.mark.asyncio
    async def test_guard_refuses_rows_without_pit_columns(self) -> None:
        """缺 `published_at` / `caliber_version` 的行必须被拒（两列可空 → 会静默落 NULL）。"""
        rows = [
            {
                "industry_key": "market",
                "stock_id": 0,
                "metric_key": "if_basis",
                "source": "x",
                "source_tier": "derived",
                "freq": "daily",
                "period": date(2026, 10, 12),
                "value": 1.0,
                "unit": "点",
                "extra": None,
                "published_at": None,
                "caliber_version": None,
            }
        ]
        with pytest.raises(RuntimeError, match="published_at"):
            await svc.upsert_metric_rows(None, rows)  # type: ignore[arg-type]


# ── 3. 升贴水：符号约定 + 缺数不静默为 0 ────────────────────────────────


class TestBasisSignConvention:
    """用 Step-0 实测过的真实数字做**手算回归锁**（2026-10-12）。"""

    def test_if_basis_is_settle_minus_spot_and_negative_means_discount(self) -> None:
        legs = {
            "IF": _leg("IF", fut_settle=4225.8, fut_close=4233.6, spot_close=4287.9798),
        }
        out = svc.basis_metrics_for_day(date(2026, 10, 12), legs=legs)
        value = out["if_basis"].value
        assert value is not None
        assert value == pytest.approx(4225.8 - 4287.9798, abs=1e-9)  # −62.1798
        assert value < 0  # 负值 = 贴水（spec 判据要求 UI 明示"贴水"）
        assert out["if_basis"].extra["sign_convention"] == "futures_minus_spot"
        assert "贴水" in out["if_basis"].extra["sign_hint"]
        # 收盘口径留痕（与行情商/媒体口径对拍）：实测 probe 的 close 口径 = −54.38
        assert out["if_basis"].extra["basis_close"] == pytest.approx(4233.6 - 4287.9798, abs=1e-9)
        assert out["if_basis"].extra["basis_close"] == pytest.approx(-54.3798, abs=1e-3)

    def test_premium_day_is_positive(self) -> None:
        """期货高于现货 = 升水 = 正值（与"负值 = 贴水"同一约定的另一侧）。"""
        legs = {"IF": _leg("IF", fut_settle=4400.0, fut_close=4400.0, spot_close=4300.0)}
        out = svc.basis_metrics_for_day(date(2026, 10, 12), legs=legs)
        assert out["if_basis"].value == pytest.approx(100.0)

    def test_discount_rate_has_the_same_sign_as_basis(self) -> None:
        """贴水率**不翻转符号**（翻转正好是 spec §S1.10 要防的正负号误读）。

        数字取自库里实测的 2026-10-12 IC：结算价 6972.6、现货收盘 7093.0205
        ⇒ 基差 −120.4205、贴水率 −1.6977%（与 `industry_metrics` 落库值逐位一致）。
        """
        legs = {"IC": _leg("IC", fut_settle=6972.6, fut_close=6986.0, spot_close=7093.0205)}
        out = svc.basis_metrics_for_day(date(2026, 10, 12), legs=legs)
        basis = out["ic_basis"].value
        rate = out["ic_discount_rate"].value
        assert basis is not None and rate is not None
        assert basis == pytest.approx(-120.4205, abs=1e-9)
        assert rate == pytest.approx(basis / 7093.0205 * 100.0, abs=1e-12)
        assert rate == pytest.approx(-1.6977, abs=1e-4)
        assert (rate < 0) == (basis < 0)  # 同号：负 = 贴水
        assert "不年化" in out["ic_discount_rate"].extra["annualized_note"]

    def test_annualized_pct_is_extra_evidence_not_the_value(self) -> None:
        legs = {
            "IC": _leg(
                "IC",
                fut_settle=6973.6,
                fut_close=6986.0,
                spot_close=7093.0205,
                delist=date(2026, 12, 18),
            )
        }
        day = date(2026, 10, 12)
        out = svc.basis_metrics_for_day(day, legs=legs)
        days = (date(2026, 12, 18) - day).days
        assert out["ic_discount_rate"].extra["days_to_delivery"] == days == 67
        rate = out["ic_discount_rate"].value
        assert rate is not None
        assert out["ic_discount_rate"].extra["annualized_pct"] == pytest.approx(
            rate * 365.0 / days, abs=1e-9
        )

    def test_missing_futures_leg_is_null_with_reason_not_zero(self) -> None:
        legs = {"IF": _leg("IF", fut_settle=None, fut_close=None, spot_close=4287.9798)}
        out = svc.basis_metrics_for_day(date(2026, 10, 12), legs=legs)
        assert out["if_basis"].value is None
        assert out["if_basis"].missing_reason == svc.MISSING_FUTURES_LEG
        assert out["if_basis"].value != 0

    def test_missing_spot_leg_is_null_with_reason_not_zero(self) -> None:
        legs = {"IF": _leg("IF", fut_settle=4225.8, fut_close=4233.6, spot_close=None)}
        out = svc.basis_metrics_for_day(date(2026, 10, 12), legs=legs)
        assert out["if_basis"].value is None
        assert out["if_basis"].missing_reason == svc.MISSING_SPOT_INDEX

    def test_zero_spot_is_null_with_reason_not_infinite(self) -> None:
        legs = {"IF": _leg("IF", fut_settle=4225.8, fut_close=4233.6, spot_close=0.0)}
        out = svc.basis_metrics_for_day(date(2026, 10, 12), legs=legs)
        assert out["if_basis"].value is None
        assert out["if_basis"].missing_reason == svc.MISSING_BASIS_SPOT_ZERO

    def test_discount_rate_also_goes_null_when_basis_goes_null(self) -> None:
        legs = {
            "IC": _leg("IC", fut_settle=None, fut_close=None, spot_close=7093.0205),
            "IM": _leg("IM", fut_settle=None, fut_close=None, spot_close=7021.47),
        }
        out = svc.basis_metrics_for_day(date(2026, 10, 12), legs=legs)
        assert out["ic_discount_rate"].value is None
        assert out["ic_discount_rate"].missing_reason == svc.MISSING_FUTURES_LEG
        assert out["im_discount_rate"].value is None
        assert out["im_discount_rate"].missing_reason == svc.MISSING_FUTURES_LEG

    def test_only_products_present_in_legs_produce_rows(self) -> None:
        """品种上市前**不落行**（没有该品种就没有基差）——落 NULL 会把"没上市"说成"缺数"。"""
        legs = {"IF": _leg("IF", fut_settle=4225.8, fut_close=4233.6, spot_close=4287.9798)}
        out = svc.basis_metrics_for_day(date(2011, 6, 1), legs=legs)
        assert set(out) == {"if_basis"}

    def test_out_of_range_basis_ratio_raises_instead_of_landing(self) -> None:
        """品种↔现货指数配错 / 量纲错必须响亮失败，不得把错值落库。"""
        legs = {"IF": _leg("IF", fut_settle=9000.0, fut_close=9000.0, spot_close=4287.98)}
        with pytest.raises(RuntimeError, match="超出合理上界"):
            svc.basis_metrics_for_day(date(2026, 10, 12), legs=legs)

    def test_expected_from_is_the_product_first_listing_day(self) -> None:
        legs = {
            "IM": _leg(
                "IM", fut_settle=1.0, fut_close=1.0, spot_close=1.0, expected_from=date(2022, 7, 22)
            )
        }
        out = svc.basis_metrics_for_day(date(2022, 7, 22), legs=legs)
        assert out["im_basis"].extra["expected_from"] == "2022-07-22"


# ── 4. QVIX：上市前不落行 / 上游缺行落 NULL / 异常响亮失败 ───────────────


def _series_frame(**overrides: object) -> svc.QvixSeries:
    base: dict[str, object] = {
        "series": "50etf",
        "metric_key": "qvix_50etf",
        "listed": date(2015, 2, 9),
        "raw_days": frozenset({date(2015, 2, 9)}),
        "value_days": {date(2015, 2, 9): 28.63},
        "non_trading_rows": 0,
    }
    base.update(overrides)
    return svc.QvixSeries(**base)  # type: ignore[arg-type]


#: 5 条序列各自的期权上市日（假帧的默认首行必须落在它上面，否则会触发"首个非空日"守卫）。
_ALL_LISTING_DAYS = set(svc.QVIX_OPTION_LISTED.values())


def _fake_all(overrides: dict[str, pd.DataFrame] | None = None) -> _FakeAkShare:
    """给 5 条序列各造一个**首行 = 该序列上市日**的帧；需要变形的序列用 `overrides` 覆盖。"""
    frames: dict[str, pd.DataFrame] = {}
    for series, listed in svc.QVIX_OPTION_LISTED.items():
        if overrides and series in overrides:
            frames[series] = overrides[series]
        else:
            frames[series] = _qvix_frame([listed.isoformat()], [20.0])
    return _FakeAkShare(frames)


class TestQvix:
    def test_value_is_taken_from_the_series(self) -> None:
        out = svc.qvix_metrics_for_day(date(2015, 2, 9), series_frames={"50etf": _series_frame()})
        assert out["qvix_50etf"].value == 28.63
        assert out["qvix_50etf"].missing_reason is None
        assert out["qvix_50etf"].extra["caliber"] == "close"
        assert out["qvix_50etf"].extra["expected_from"] == "2015-02-09"
        assert out["qvix_50etf"].extra["option_listed_from"] == "2015-02-09"

    def test_no_row_before_the_option_listing_day(self) -> None:
        frame = _series_frame(
            listed=date(2023, 6, 5),
            value_days={date(2023, 6, 5): 20.0},
            metric_key="qvix_kcb_etf",
            series="kcb",
        )
        out = svc.qvix_metrics_for_day(date(2015, 2, 9), series_frames={"kcb": frame})
        assert out == {}  # 期权不存在 ⇒ 无隐含波动率可算 ⇒ 不落行

    def test_missing_upstream_day_is_null_with_reason_not_zero(self) -> None:
        frame = _series_frame(
            raw_days=frozenset({date(2015, 2, 9), date(2015, 2, 10)}),
            value_days={date(2015, 2, 9): 28.63},  # 2-10 上游没给值（发布滞后/缺行）
        )
        out = svc.qvix_metrics_for_day(date(2015, 2, 10), series_frames={"50etf": frame})
        assert out["qvix_50etf"].value is None
        assert out["qvix_50etf"].missing_reason == svc.MISSING_SOURCE_NOT_UPDATED
        assert out["qvix_50etf"].value != 0


class TestQvixFetchGuards:
    async def _fetch(
        self, overrides: dict[str, pd.DataFrame] | None = None, days: set[date] | None = None
    ) -> dict[str, svc.QvixSeries]:
        return await svc.fetch_qvix_series(
            _fake_all(overrides),
            date(2015, 1, 1),
            date(2026, 12, 31),
            trade_days=_ALL_LISTING_DAYS if days is None else days,
        )

    async def test_rows_are_split_into_axis_and_values(self) -> None:
        """日期轴（含 NaN 占位行）与"有值日"必须分开：前者判"上游有没有这天"。"""
        frame = _qvix_frame(["2015-02-09", "2015-02-10", "2015-02-11"], [28.63, None, 28.5])
        out = await self._fetch(
            {"50etf": frame}, days={date(2015, 2, 9), date(2015, 2, 10), date(2015, 2, 11)}
        )
        got = out["50etf"]
        assert got.listed == date(2015, 2, 9)
        assert got.value_days == {date(2015, 2, 9): 28.63, date(2015, 2, 11): 28.5}
        assert got.raw_days == frozenset({date(2015, 2, 9), date(2015, 2, 10), date(2015, 2, 11)})
        assert got.non_trading_rows == 0

    async def test_non_trading_rows_are_counted(self) -> None:
        """上游 CSV 实测含周末行并带真实数值（2026-08-22 周六等）→ 只落交易日，但必须计数。"""
        frame = _qvix_frame(["2015-02-09", "2026-09-10", "2026-09-12"], [20.0, 15.0, 15.67])
        out = await self._fetch(
            {"50etf": frame},
            days=_ALL_LISTING_DAYS | {date(2026, 9, 10)},  # 2026-09-12 是周六 → 非交易日
        )
        assert out["50etf"].raw_days == frozenset(
            {date(2015, 2, 9), date(2026, 9, 10), date(2026, 9, 12)}
        )
        assert out["50etf"].non_trading_rows == 1

    async def test_empty_frame_raises(self) -> None:
        empty = pd.DataFrame(columns=["date", "close"])
        with pytest.raises(RuntimeError, match="返回 0 行"):
            await self._fetch(dict.fromkeys(svc.QVIX_OPTION_LISTED, empty))

    async def test_all_nan_close_raises(self) -> None:
        with pytest.raises(RuntimeError, match="整列为空"):
            await self._fetch({"50etf": _qvix_frame(["2015-02-09"], [None])})

    async def test_first_non_null_must_equal_the_option_listing_day(self) -> None:
        """上游改品种/补更长历史必须当场发现，不得静默接受。"""
        frame = _qvix_frame(["2015-01-05", "2015-02-09"], [1.0, 28.63])
        with pytest.raises(RuntimeError, match="首个非空日"):
            await self._fetch({"50etf": frame})

    async def test_missing_columns_raise(self) -> None:
        with pytest.raises(RuntimeError, match="缺列"):
            await self._fetch({"50etf": pd.DataFrame({"date": ["2015-02-09"], "value": [1.0]})})

    async def test_window_without_rows_raises(self) -> None:
        with pytest.raises(RuntimeError, match="窗口"):
            await svc.fetch_qvix_series(
                _fake_all(), date(2020, 1, 1), date(2020, 12, 31), trade_days={date(2020, 1, 2)}
            )


# ── 5. 取数分片与交易日分母的响亮守卫 ───────────────────────────────────


class TestFetchGuards:
    @pytest.mark.asyncio
    async def test_fut_daily_shard_hitting_the_row_cap_raises(self) -> None:
        """分片内仍撞 2000 行上限 = 分片粒度不够，继续跑就是静默丢行。"""
        with pytest.raises(RuntimeError, match="单次上限"):
            await svc.fetch_futures_continuous(
                _FakeTuShare(fut_rows=svc.FUT_DAILY_ROW_CAP),
                "IF.CFX",
                date(2020, 1, 1),
                date(2026, 10, 12),
            )

    @pytest.mark.asyncio
    async def test_fut_daily_with_no_rows_returns_empty_frame(self) -> None:
        frame = await svc.fetch_futures_continuous(
            _FakeTuShare(fut_rows=0), "IM.CFX", date(2020, 1, 1), date(2020, 6, 1)
        )
        assert not len(frame)

    @pytest.mark.asyncio
    async def test_empty_trade_calendar_raises(self) -> None:
        """交易日分母空集 ⇒ "覆盖率 100%" 就成了空话，必须响亮失败。"""
        with pytest.raises(RuntimeError, match="trade_cal"):
            await svc.fetch_trade_days(_FakeTuShare(cal=None), date(2026, 1, 1), date(2026, 1, 31))

    @pytest.mark.asyncio
    async def test_trade_calendar_is_clipped_to_the_window(self) -> None:
        cal = pd.DataFrame({"cal_date": ["20251231", "20260102", "20260105", "20260106"]})
        days = await svc.fetch_trade_days(_FakeTuShare(cal=cal), date(2026, 1, 1), date(2026, 1, 5))
        assert days == [date(2026, 1, 2), date(2026, 1, 5)]

    @pytest.mark.asyncio
    async def test_missing_date_column_raises_for_trade_cal(self) -> None:
        with pytest.raises(KeyError):
            await svc.fetch_trade_days(
                _FakeTuShare(cal=pd.DataFrame({"x": ["20260102"]})),
                date(2026, 1, 1),
                date(2026, 1, 5),
            )


def test_parse_day_accepts_all_upstream_shapes() -> None:
    assert svc._parse_day("20261012") == date(2026, 10, 12)
    assert svc._parse_day("2026-10-12") == date(2026, 10, 12)
    assert svc._parse_day("2026/10/12") == date(2026, 10, 12)
    assert svc._parse_day(date(2026, 10, 12)) == date(2026, 10, 12)
    assert svc._parse_day(datetime(2026, 10, 12, 8, 0)) == date(2026, 10, 12)
    assert svc._parse_day(pd.Timestamp("2026-10-12")) == date(2026, 10, 12)
