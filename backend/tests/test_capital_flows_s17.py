"""spec §S1.7 —— 资金流四件套的**纯函数**不变量（不联网、不连库）。

只有真库/真上游才能证的部分在 `tests/test_capital_flows_s17_db.py` 与
`backend/scripts/spike_s17_capital_flows_sources.py`（Step-0 实机探针）。

本文件锁四类：

**A. 登记面自洽**：§S1.7 的 key、单位、频率与 registry 逐字一致；**两融是"引用"而不是
"重做"**（`margin_*` 必须在 §S1.9 的登记面里、且**不**出现在 §S1.7 的登记面里）；
陆股通持仓的频率恒为 `quarterly`；产业资本是 `event`。

**B. 方法论禁令（Ruling 19）机械可检**：§S1.7 的全部文案**不得**出现「外资」——
把「香港中央結算持仓」叫"外资"是方法论错误（名义持有人汇总账户）。

**C. ETF 份额变化**：值 = 六只篮子成员份额日变化之和（万份→亿份）；成员缺失 →
整日 NULL + `partial_basket_coverage`（**不归一**，因为份额变化是可加量）；
逐成员用**自己的**前一个可得日做差。

**D. 南向**：值 = 累计值的一阶差分；**口径断点前整段丢弃**（该列 2023-11-24 前是日频
净买入/百万元，混算 = 二阶差分）；断点日无累计前值 → NULL + `prev_connect_day_missing`；
上游缺值日**不**成为下一天的基准；口径回退守卫在 `south_money` 跌破阈值时**响亮失败**。

**E. Ruling 61 的读面取数口**：`extra.missing_reason` / 覆盖率字段的提取口径。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from app.services import capital_flows_service as svc
from app.services import industry_metric_service as metric_service
from app.services import industry_registry as registry
from app.services.market_activity_service import published_at_for as activity_published_at

# ── A. 登记面自洽 ────────────────────────────────────────────────────────


def test_registry_and_service_key_sets_match() -> None:
    """服务落库的 key 集与 registry 的登记项必须逐字相等（导入时已守卫，这里再锁一次）。"""
    assert set(svc.ALL_METRIC_KEYS) == set(registry.CAPITAL_FLOWS_ALL_KEYS)
    assert set(svc.INGEST_METRIC_KEYS) == set(registry.CAPITAL_FLOWS_METRIC_KEYS)


def test_margin_leg_is_referenced_not_rebuilt() -> None:
    """两融（spec 契约表自己写着「已有（S1.9）」）必须**只引用不重做**。

    判据是机械的：`margin_financing_balance` / `margin_short_balance` 必须存在于
    §S1.9 的登记面（`MARKET_ACTIVITY_ALL_KEYS`），且**不**出现在 §S1.7 的登记面里
    —— 重复登记会让 `find_metric` 的白名单出现两份可能漂移的定义。
    """
    for key in registry.CAPITAL_FLOWS_REUSED_KEYS:
        assert key in registry.MARKET_ACTIVITY_ALL_KEYS, f"{key} 不在 §S1.9 的登记面里"
        assert key not in registry.CAPITAL_FLOWS_ALL_KEYS, f"{key} 被 §S1.7 重复登记了"
        assert key not in svc.ALL_METRIC_KEYS


def test_units_and_freq_projection_matches_registry() -> None:
    """`UNITS` 是 registry 的投影；单位/频率随 key 固定在 registry（字典 §4.2 规则 3）。"""
    defs = {m.key: m for m in registry.CAPITAL_FLOWS_METRICS}
    assert set(svc.UNITS) == set(registry.CAPITAL_FLOWS_METRIC_KEYS)
    for key, unit in svc.UNITS.items():
        assert defs[key].unit == unit, f"{key} 的单位与 registry 不一致"
        assert defs[key].freq == "daily"


def test_landed_source_matches_registry_whitelist() -> None:
    """落库写进的 `source` 必须**逐字**出现在 registry 声明的 `sources` 里。

    这是 best-practices「数据源与采集」已沉淀的坑（`source` 名写错 ⇒ 读面的源优先级
    裁决永远匹配不到真实行，序列看起来"有行但没数据"）。S1.10 曾因此单独出过一次修复，
    本批次用一条纯单测把它锁在 §S1.7 上。
    """
    defs = {m.key: m for m in registry.CAPITAL_FLOWS_METRICS}
    for key in svc.INGEST_METRIC_KEYS:
        assert svc._SOURCES[key] in defs[key].sources, (
            f"{key} 落库写的 source={svc._SOURCES[key]!r} 不在 registry 的 "
            f"{defs[key].sources!r} 里 —— 读面会匹配不到，序列等于没数据"
        )


def test_landed_freq_and_pending_freq() -> None:
    """陆股通持仓恒为季度；产业资本是事件驱动（spec §S1.7 的表格逐字要求）。"""
    defs = {m.key: m for m in registry.CAPITAL_FLOWS_METRICS}
    assert defs["northbound_holding_quarterly"].freq == "quarterly"
    assert defs["industrial_capital_net_change"].freq == "event"
    assert registry.FREQ_LABELS["event"] == "事件驱动"
    assert registry.FREQ_LABELS["quarterly"] == "季度"


def test_pending_keys_are_registered_but_never_ingested() -> None:
    """「待接」的语义 = **已登记**（读面给出骨架而非 404）但**一行都不落**。"""
    assert set(registry.CAPITAL_FLOWS_PENDING_KEYS) == {"industrial_capital_net_change"}
    for key in registry.CAPITAL_FLOWS_PENDING_KEYS:
        assert key in registry.CAPITAL_FLOWS_ALL_KEYS
        assert key not in svc.INGEST_METRIC_KEYS
        assert key not in svc.UNITS


# ── B. 方法论禁令（Ruling 19）────────────────────────────────────────────


#: 允许出现禁用词的**上下文标记**：只有"在讲这条禁令"时才准提到它。
_BAN_CONTEXT_MARKERS = ("不得", "禁止", "禁用", "而非", "不出现", "方法论")


def test_no_foreign_capital_naming_in_s17_texts() -> None:
    """**不得**把「香港中央結算持仓」称作"外资"（Ruling 19）—— 机械可检。

    判据不是"这个词一次都不许出现"（禁令本身必须可写下来），而是：**凡是出现的地方，
    同一行必须带一个否定/禁令标记**。于是"把某个指标命名为外资…"会被抓住，
    而"不得称「外资」"这种规定性文字不算违规。
    """
    banned = ("外资", "foreign")
    texts = {
        "registry missing reasons": str(registry.CAPITAL_FLOWS_MISSING_REASONS),
        "registry basket caliber": registry.ETF_BASKET_CALIBER,
        "service docstring": svc.__doc__ or "",
        "service southbound guard": "".join(
            line
            for line in Path(svc.__file__).read_text(encoding="utf-8").splitlines(keepends=True)
            if "south_money" in line
        ),
    }
    for m in registry.CAPITAL_FLOWS_METRICS:
        texts[f"MetricDef {m.key}"] = f"{m.key} {m.name} {m.group} {m.unit} {m.description}"
    for where, text in texts.items():
        for i, line in enumerate(text.splitlines(), start=1):
            for word in banned:
                if word not in line:
                    continue
                assert any(marker in line for marker in _BAN_CONTEXT_MARKERS), (
                    f"{where}:{i} 出现禁用命名 {word!r} 且该行没有禁令标记（Ruling 19）：{line!r}"
                )
    # 正例：必须显式使用「陆股通持仓」
    holding = next(m for m in registry.CAPITAL_FLOWS_METRICS if m.key.endswith("holding_quarterly"))
    assert "陆股通持仓" in holding.name
    assert "名义持有人" in holding.description


def test_holding_metric_documents_quarterly_lag() -> None:
    """spec 判据「频率标识为季度，且 UI 明示滞后」→ 滞后说明必须在 registry 文案里。"""
    holding = next(
        m for m in registry.CAPITAL_FLOWS_METRICS if m.key == "northbound_holding_quarterly"
    )
    assert "滞后" in holding.description
    assert "季度" in holding.description


# ── C. ETF 份额变化 ─────────────────────────────────────────────────────


def _shares(
    offsets: dict[str, dict[str, float]] | None = None,
) -> dict[str, dict[date, float]]:
    """造一个篮子份额字典：默认每只成员三天、每天都 +100 万份（= 0.01 亿份）。"""
    if offsets is not None:
        return {
            code: {date.fromisoformat(d): v for d, v in per_day.items()}
            for code, per_day in offsets.items()
        }
    days = ("2026-01-05", "2026-01-06", "2026-01-07")
    return {
        m.ts_code: {date.fromisoformat(d): 1_000_000.0 + i * 100.0 for i, d in enumerate(days)}
        for m in svc.ETF_BASKET
    }


def test_etf_basket_shape_and_caliber_versions() -> None:
    """篮子形状是**口径的一部分**：沪深300 族三只、其余一族一只，且 ETF 版本独立。

    v3（2026-10-17）把沪深300 族从 1 只补到 3 只（510310 / 159919）：只取一只会
    系统性低估该族净申购 —— 而沪深300 族正是「国家队」主战场。
    """
    by_family: dict[str, list[str]] = {}
    for member in svc.ETF_BASKET:
        by_family.setdefault(member.family, []).append(member.ts_code)
    assert len(svc.ETF_BASKET) == 8
    assert sorted(by_family["沪深300"]) == ["159919.SZ", "510300.SH", "510310.SH"]
    assert {k: len(v) for k, v in by_family.items() if k != "沪深300"} == {
        "上证50": 1,
        "中证500": 1,
        "中证1000": 1,
        "科创50": 1,
        "创业板指": 1,
    }
    # 版本必须解耦：改篮子不该把南向的行也标新版本
    assert registry.ETF_SHARE_CALIBER_VERSION != registry.CAPITAL_FLOWS_CALIBER_VERSION
    assert svc.CALIBER_VERSIONS["etf_share_change"] == registry.ETF_SHARE_CALIBER_VERSION
    assert svc.CALIBER_VERSIONS["southbound_net_buy"] == registry.CAPITAL_FLOWS_CALIBER_VERSION
    # 口径文案必须写出八只（文案与常量不得漂移）
    assert "八只" in registry.ETF_BASKET_CALIBER
    for code in ("510310", "159919"):
        assert code in registry.ETF_BASKET_CALIBER


def test_etf_change_is_sum_of_member_deltas_in_yi() -> None:
    """值 = Σ 成员份额日变化 ÷ 1e4（万份 → 亿份）；逐成员的原始量必须留痕可复核。"""
    shares = _shares()
    out = svc.etf_share_change_by_day(shares, date(2026, 1, 1), date(2026, 12, 31))
    assert set(out) == {date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)}
    # 序列首日没有前一个份额日：结构性不可得，且**原因码与"篮子不全"分开**
    assert out[date(2026, 1, 5)].value is None
    assert out[date(2026, 1, 5)].missing_reason == svc.MISSING_NO_PREV_FUND_DAY
    per_day = 100.0 * len(svc.ETF_BASKET) / svc.WAN_PER_YI
    for day, metric in out.items():
        if day == date(2026, 1, 5):
            continue
        assert metric.value == pytest.approx(per_day)
        assert metric.missing_reason is None
        assert metric.extra["n_with_share"] == len(svc.ETF_BASKET)
        assert metric.extra["member_coverage"] == 1.0
        assert len(metric.extra["by_member"]) == len(svc.ETF_BASKET)
        assert metric.extra["delta_wan"] == pytest.approx(100.0 * len(svc.ETF_BASKET))


def test_etf_change_missing_member_is_null_not_renormalised() -> None:
    """成员缺当日值 → 整日 NULL + `partial_basket_coverage`；**不**按覆盖率归一。

    份额变化是**可加量**：缺一只成员时按覆盖率放大会得到一个假的"全市场净申购"。
    """
    shares = _shares()
    victim = svc.ETF_BASKET[2].ts_code
    del shares[victim][date(2026, 1, 7)]
    out = svc.etf_share_change_by_day(shares, date(2026, 1, 1), date(2026, 12, 31))
    assert out[date(2026, 1, 7)].value is None
    assert out[date(2026, 1, 7)].missing_reason == svc.MISSING_PARTIAL_BASKET_COVERAGE
    assert out[date(2026, 1, 7)].extra["missing_members"] == [victim]
    assert out[date(2026, 1, 7)].extra["n_with_share"] == len(svc.ETF_BASKET) - 1
    # 前一天不受影响（它没缺成员）
    assert out[date(2026, 1, 6)].value is not None


def test_etf_change_uses_member_own_previous_day() -> None:
    """逐成员取**自己的**前一个可得日：一只成员少一天时，不该把两天的变化记成一天。"""
    shares = _shares()
    # 510300 少 2026-01-06（停牌/无数据），它的 01-07 基准应是 01-05
    shares["510300.SH"].pop(date(2026, 1, 6))
    out = svc.etf_share_change_by_day(shares, date(2026, 1, 1), date(2026, 12, 31))
    day = date(2026, 1, 7)
    row = next(d for d in out[day].extra["by_member"] if d["ts_code"] == "510300.SH")
    assert row["prev_trade_date"] == "2026-01-05"
    assert row["delta_wan"] == pytest.approx(200.0)  # 01-05 → 01-07 的累积变化
    # 而 01-06 那天这只成员缺行 ⇒ 整日 NULL（不拿 5 只拼一个数）
    assert out[date(2026, 1, 6)].value is None


def test_etf_change_respects_window() -> None:
    """落库天只取窗口内 —— 窗口外的取数（用于算 T−1）不得被落成行。"""
    shares = _shares()
    out = svc.etf_share_change_by_day(shares, date(2026, 1, 7), date(2026, 1, 7))
    assert set(out) == {date(2026, 1, 7)}


# ── D. 南向净买入 ───────────────────────────────────────────────────────


def _hsgt(rows: list[tuple[str, float | None]]) -> pd.DataFrame:
    return pd.DataFrame({"trade_date": [d for d, _v in rows], "south_money": [v for _d, v in rows]})


def test_southbound_is_first_difference_of_cumulative() -> None:
    frame = _hsgt(
        [
            ("20231124", 28838.31),
            ("20231127", 28841.79),
            ("20231128", 28811.25),
        ]
    )
    out = svc.southbound_net_buy_by_day(frame, date(2023, 11, 1), date(2023, 12, 31))
    # 断点日无累计前值 → NULL + prev_connect_day_missing（它的前值是旧口径）
    assert out[date(2023, 11, 24)].value is None
    assert out[date(2023, 11, 24)].missing_reason == svc.MISSING_PREV_CONNECT_DAY
    assert out[date(2023, 11, 27)].value == pytest.approx(3.48)
    assert out[date(2023, 11, 28)].value == pytest.approx(-30.54)
    # 首尾累计值必须在 extra 里，能逐日手算复核
    assert out[date(2023, 11, 27)].extra["cumulative_yi"] == pytest.approx(28841.79)
    assert out[date(2023, 11, 27)].extra["prev_cumulative_yi"] == pytest.approx(28838.31)
    assert out[date(2023, 11, 27)].extra["prev_trade_date"] == "2023-11-24"


def test_southbound_drops_pre_break_vintage_entirely() -> None:
    """口径断点前的行**整段丢弃**：那里是日频净买入，差分 = 二阶差分 = 纯噪声。"""
    frame = _hsgt(
        [
            ("20231123", -5750.96),
            ("20231124", 28838.31),
            ("20231127", 28841.79),
        ]
    )
    out = svc.southbound_net_buy_by_day(frame, date(2023, 11, 1), date(2023, 12, 31))
    assert date(2023, 11, 23) not in out, "断点前的旧口径行不得落库"
    assert out[date(2023, 11, 24)].value is None, "断点日不得拿旧口径值当基准"
    assert out[date(2023, 11, 27)].value == pytest.approx(3.48)


def test_southbound_missing_value_does_not_become_next_base() -> None:
    """上游缺值日 → NULL + `source_empty`，且**不**成为下一天的基准（否则跨日记成一天）。"""
    frame = _hsgt(
        [
            ("20231124", 28838.31),
            ("20231127", None),
            ("20231128", 28811.25),
        ]
    )
    out = svc.southbound_net_buy_by_day(frame, date(2023, 11, 1), date(2023, 12, 31))
    assert out[date(2023, 11, 27)].value is None
    assert out[date(2023, 11, 27)].missing_reason == svc.MISSING_SOURCE_EMPTY
    # 缺值日**要**成为下一天的基准（值为 None）⇒ 下一天落"跨缺口"而不是一个
    # 把两天变化记成一天的假差分
    assert out[date(2023, 11, 28)].value is None
    assert out[date(2023, 11, 28)].missing_reason == svc.MISSING_PREV_VALUE


def test_southbound_regime_guard_fires_on_vintage_rollback() -> None:
    """口径回退守卫：断点后出现 ≤ 阈值的累计值 ⇒ 响亮失败（**不得**继续差分落库）。"""
    frame = _hsgt(
        [
            ("20231124", 28838.31),
            ("20231127", -5750.96),  # 旧口径的日频值混了进来
        ]
    )
    with pytest.raises(RuntimeError, match="口径回退守卫"):
        svc.southbound_net_buy_by_day(frame, date(2023, 11, 1), date(2023, 12, 31))


def test_southbound_floor_is_below_observed_minimum() -> None:
    """阈值必须低于实测最低累计值（28811.25）—— 否则会把正常日子误判成口径回退。"""
    assert 0 < svc.SOUTHBOUND_CUMULATIVE_FLOOR < 28811.25


def test_hsgt_chunk_is_narrower_than_row_cap() -> None:
    """分片必须窄于上游 300 行上限（否则会被静默截断成"最近 300 行"）。"""
    assert svc.HSGT_CHUNK_DAYS * 3 // 4 < svc.HSGT_ROW_CAP  # ≈135 行/片，留足余量
    assert svc.FUND_SHARE_ROW_CAP == 2000


# ── E. 行构造与 PIT ─────────────────────────────────────────────────────


def test_row_carries_pit_caliber_and_missing_reason() -> None:
    day = date(2026, 1, 7)
    metric = svc.MetricValue("etf_share_change", 12.436, None, {"caliber": "x"})
    row = svc._row(day, metric)
    assert row["industry_key"] == "market" and row["stock_id"] == 0
    assert row["metric_key"] == "etf_share_change"
    assert row["source"] == registry.ETF_SHARE_SOURCE
    assert row["source_tier"] == "derived"
    assert row["unit"] == "亿份"
    assert row["freq"] == "daily"
    # ETF 篮子变更（6 → 8）有自己的口径版本，与南向解耦
    assert row["caliber_version"] == registry.ETF_SHARE_CALIBER_VERSION
    assert row["published_at"].tzinfo is not None, "PIT 必须带时区（Ruling 35 的反面）"
    assert row["published_at"].date() == day
    assert row["published_at"].utcoffset() is not None
    # 上海时区 21:00 = UTC 13:00（不写死时区名，只锁"当日的北京时间 21:00"）
    assert row["published_at"].astimezone(registry_timezone()).hour == 21

    null_row = svc._row(day, svc.MetricValue("southbound_net_buy", None, "source_empty", {}))
    assert null_row["value"] is None
    assert null_row["extra"]["missing_reason"] == "source_empty"


def registry_timezone() -> Any:
    """与 service 同源的上海时区（避免测试里再写一份字符串）。"""
    return svc._CN_TZ


def test_publish_time_per_metric() -> None:
    assert svc._publish_time("etf_share_change") == svc.ETF_SHARE_PUBLISH
    assert svc._publish_time("southbound_net_buy") == svc.CONNECT_FLOW_PUBLISH


def test_published_at_for_is_tz_aware() -> None:
    stamp = svc.published_at_for(date(2026, 1, 7), svc.ETF_SHARE_PUBLISH)
    assert stamp.tzinfo is not None
    assert stamp.isoformat().startswith("2026-01-07T21:00:00+08:00")
    # 与 §S1.9 的同名工具口径一致（同一张表的两列，不能有两套"带时区"约定）
    assert svc.published_at_for(date(2026, 1, 7), svc.ETF_SHARE_PUBLISH) == (
        activity_published_at(date(2026, 1, 7), svc.ETF_SHARE_PUBLISH)
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("20261012", date(2026, 10, 12)),
        ("2026-10-12", date(2026, 10, 12)),
        (date(2026, 10, 12), date(2026, 10, 12)),
    ],
)
def test_parse_day_accepts_upstream_shapes(raw: Any, expected: date) -> None:
    assert svc._parse_day(raw) == expected


# ── F. Ruling 61：读面的缺数取数口 ──────────────────────────────────────


def _row_stub(extra: Any) -> Any:
    return SimpleNamespace(extra=extra)


def test_missing_info_reads_reason_and_coverage() -> None:
    reason, coverage = metric_service._missing_info(
        _row_stub({"missing_reason": "source_empty", "coverage": 0.98})
    )
    assert reason == "source_empty"
    assert coverage == 0.98


def test_missing_info_coverage_key_precedence() -> None:
    """覆盖率字段名各批次不同，读面按**固定顺序**取第一个存在的（不猜、不合并）。"""
    assert metric_service.COVERAGE_EXTRA_KEYS == (
        "coverage",
        "weight_coverage",
        "member_coverage",
    )
    _r, cov = metric_service._missing_info(
        _row_stub({"weight_coverage": 0.96, "member_coverage": 0.5})
    )
    assert cov == 0.96
    _r, cov2 = metric_service._missing_info(_row_stub({"member_coverage": 0.5}))
    assert cov2 == 0.5


def test_missing_info_rejects_non_string_reason_and_non_numeric_coverage() -> None:
    """JSONB 里什么都可能有：非字符串原因码 / 布尔 / 字符串覆盖率一律当 `None`。"""
    assert metric_service._missing_info(_row_stub({"missing_reason": {"a": 1}}))[0] is None
    assert metric_service._missing_info(_row_stub({"missing_reason": ""}))[0] is None
    assert metric_service._missing_info(_row_stub({"coverage": True}))[1] is None
    assert metric_service._missing_info(_row_stub({"coverage": "0.9"}))[1] is None
    assert metric_service._missing_info(_row_stub(None)) == (None, None)
    assert metric_service._missing_info(None) == (None, None)
    assert metric_service._missing_info(_row_stub({"coverage": 0}))[1] == 0.0
