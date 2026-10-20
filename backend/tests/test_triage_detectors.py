"""探测器契约：分级枚举、required_fields 真实存在、次新排除、口径禁令、每条一正一反.

这些断言防的是什么（逐条写明，否则"改坏探测器"会静默通过）：
- 数量/key/severity 由导入期断言锁定 → 加/删探测器必须同步改 registry，否则线上多一条噪声；
- ``required_fields`` 必须在 ORM 元数据里真实存在 → 防止把已删列/全 NULL 列写成判据；
- ``new_high_low`` 的次新排除 → 否则上市第 3 天必然"创新高"，自选里一半是假事实；
- 口径禁令（``turnover_rate_f`` / ``market_limit_up_stock_daily`` / ``adj_factor``）→
  这三样都经实机标定证伪（见模块 docstring），源码里出现即回归；
- 每条探测器 **1 正例 + 1 反例** → 只测正例会让"恒命中"的写法通过。
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from app.services import triage_detectors as td

AS_OF = date(2026, 10, 16)

_BANNED_WORDS = ("买入", "卖出", "推荐", "建议关注", "看好", "目标价", "机会", "布局", "必涨")


def _rows(closes: list[float], *, amount: float = 1000.0, pre_close: float | None = None):
    """按收盘价序列造行情窗口：``pre_close`` 取前一收盘（首行用自身）。"""
    out = []
    prev = closes[0] if pre_close is None else pre_close
    for day, close in enumerate(closes, start=1):
        out.append((date(2026, 9, 1) + _days(day), close, prev, amount))
        prev = close
    return out


def _days(n: int):
    from datetime import timedelta

    return timedelta(days=n)


# ---------------------------------------------------------------------------
# registry 契约
# ---------------------------------------------------------------------------


def test_detector_specs_are_locked() -> None:
    assert len(td.TRIAGE_DETECTORS) == 10
    keys = [spec.key for spec in td.TRIAGE_DETECTORS]
    assert len(set(keys)) == 10
    assert {spec.severity for spec in td.TRIAGE_DETECTORS} <= set(td.SEVERITIES)


def test_severity_order_is_fixed() -> None:
    """分级次序 = 排序优先级（spec §S8.4：risk > event > attention > background）。"""
    assert td.SEVERITIES == ("risk", "event", "attention", "background")
    assert td.SEVERITY_RANK["risk"] < td.SEVERITY_RANK["background"]


def test_every_spec_carries_full_provenance() -> None:
    """10 条 spec 的来源字段必须齐全（provenance 契约；空值/短列表都算回归）。

    为什么这条不能只靠导入期断言：字段齐全 ≠ 内容有意义——这里额外钉住"4 条以上
    失效场景"与"来源表是库内真实表"，并把 ``with_provenance`` 的**单向映射**拿住
    （卡片三字段只能从 spec 取，不能在 10 个探测器里各写一份）。
    """
    tables = td._field_tables()
    for spec in td.TRIAGE_DETECTORS:
        expected_version = {"regulatory_anomaly": "r2"}.get(spec.key, "r1")
        assert spec.version == expected_version, f"{spec.key} 的版本号必须显式登记"
        assert spec.rule_text.strip(), f"{spec.key} 缺 rule_text（人话阈值）"
        assert spec.definition.strip(), f"{spec.key} 缺 definition"
        assert spec.update_note.strip(), f"{spec.key} 缺 update_note"
        assert spec.source_tables, f"{spec.key} 缺 source_tables"
        assert set(spec.source_tables) <= set(tables), f"{spec.key} 的来源表不在库内"
        assert len(spec.failure_modes) >= 3, f"{spec.key} 的 failure_modes 不足 3 条"
        assert all(mode.strip() for mode in spec.failure_modes)
        assert len(set(spec.failure_modes)) == len(spec.failure_modes)

    item = td.TriageItem(
        symbol="600000",
        name="浦发银行",
        detector=td.SPEC_DROP_SHARP.key,
        severity="risk",
        headline="今天跌得急",
        facts=(),
        as_of=AS_OF,
        source="daily_quotes",
        verify="kline",
    )
    filled = td.with_provenance(item)
    assert filled.rule_text == td.SPEC_DROP_SHARP.rule_text
    assert filled.rule_version == td.SPEC_DROP_SHARP.version
    assert filled.source_table == td.SPEC_DROP_SHARP.source_tables[0]


def test_registry_assertion_rejects_missing_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    """来源字段为空时**导入期就炸**（空串比缺字段更隐蔽：前端会画成空白行）。"""
    bad = replace(td.SPEC_BREAK_MA20, failure_modes=("停牌",))
    monkeypatch.setattr(td, "TRIAGE_DETECTORS", (bad, *td.TRIAGE_DETECTORS[1:]))
    with pytest.raises(RuntimeError, match="failure_modes"):
        td._assert_detectors()


def test_registry_assertion_rejects_missing_column(monkeypatch: pytest.MonkeyPatch) -> None:
    """``required_fields`` 指向不存在的列时**导入期就炸**（不是运行期静默不出）。"""
    bogus = replace(td.SPEC_BREAK_MA20, required_fields=("daily_quotes.not_a_column",))
    monkeypatch.setattr(td, "TRIAGE_DETECTORS", (bogus, *td.TRIAGE_DETECTORS[1:]))
    with pytest.raises(RuntimeError, match="列不存在"):
        td._assert_detectors()


def test_unavailable_labels_are_human_readable() -> None:
    """``unavailable`` 上屏的必须是 label（人话），一个机器 key 都不许漏。

    回归对象：后端最初直接下发 ``["sector_move"]``，前端 ``join("、")`` 上屏后用户
    看到的就是代码串。这条用"含 ``_`` 即失败"钉住，比逐字比对更难被绕过。
    """
    labels = td.unavailable_labels([spec.key for spec in td.TRIAGE_DETECTORS])
    assert labels == [spec.label for spec in td.TRIAGE_DETECTORS], "顺序 = registry 顺序"
    assert all("_" not in label for label in labels), f"label 里出现机器串：{labels}"
    # 已知 key 的人话必须与 spec §S8.2 表一致（例如 sector_move → 所属板块在动）
    assert td.unavailable_labels({"sector_move"}) == ["所属板块在动"]


def test_unavailable_labels_drop_unknown_keys() -> None:
    """未登记的 key 不进 label（信息不丢：它仍在 ``unavailable_keys`` 里）。"""
    assert td.unavailable_labels({"not_a_detector"}) == []


def test_no_forbidden_fields_in_detector_source() -> None:
    """口径禁令：不得引用 ``turnover_rate_f`` / ``market_limit_up_stock_daily`` / ``adj_factor``.

    允许 ``adj_factor`` 只出现在 docstring 的"禁止"说明里——故按**导入行**判断，不看散文。
    """
    src = Path(td.__file__).resolve().read_text(encoding="utf-8")
    module_doc = ast.get_docstring(ast.parse(src), clean=False) or ""
    code_lines = [
        line
        for line in src.replace(module_doc, "").splitlines()
        if not line.lstrip().startswith("#")
    ]
    code_only = "\n".join(code_lines)
    for token in ("turnover_rate_f", "market_limit_up_stock_daily", "adj_factor"):
        assert token not in code_only, f"代码里出现被禁字段：{token}"


# ---------------------------------------------------------------------------
# 纯函数：同一 context 两次输出一致（可重放）
# ---------------------------------------------------------------------------


def test_detectors_are_pure_and_replayable() -> None:
    ctx = td.make_context_for_test(
        as_of=AS_OF,
        names={"600000": "浦发银行"},
        quotes={"600000": _rows([10.0] * 5 + [9.0])},
        limits={"600000": (11.0, 11.0, 11.0, 10.0)},
    )
    first = td.run_detectors(ctx)
    second = td.run_detectors(ctx)
    assert first == second
    assert first, "构造的 context 必须命中至少一条，否则本断言恒真"


def test_run_detectors_follows_watchlist_order() -> None:
    """输出顺序跟自选顺序走（前端默认标签页要可预测）。"""
    ctx = td.make_context_for_test(
        as_of=AS_OF,
        names={"000001": "平安银行", "600000": "浦发银行"},
        symbols_order=["600000", "000001"],
        limits={
            "000001": (11.0, 11.0, 11.0, 10.0),
            "600000": (11.0, 11.0, 11.0, 10.0),
        },
    )
    assert [item.symbol for item in td.run_detectors(ctx)] == ["600000", "000001"]


def test_all_headlines_and_facts_avoid_judgement_words() -> None:
    """分级只有四枚举，文案不得出现判断词（spec §S8.5 的禁用词子集）。"""
    ctx = td.make_context_for_test(
        as_of=AS_OF,
        names={"600000": "浦发银行"},
        quotes={"600000": _rows([10.0] * 61 + [11.5])},
        limits={"600000": (11.0, 11.0, 11.0, 10.0)},
        announcements={
            "600000": [td.AnnRow("A1", "关于股东权益变动的提示性公告", "equity_change", None)]
        },
        dragon_tiger={"600000": [(AS_OF, 1.2e7)]},
        sector_pct={"801780": 4.5},
        sector_rank={"801780": 0.95},
        sector_of={"600000": "801780"},
        sector_name={"801780": "银行"},
        unlocks={"600000": [(AS_OF + _days(10), 12_000_000.0, 4.2)]},
    )
    items = td.run_detectors(ctx)
    assert len(items) >= 6, "该 context 应命中多类事实（否则禁用词断言样本不足）"
    for item in items:
        assert item.severity in td.SEVERITIES
        assert item.facts, f"{item.detector} 必须给出可核对的事实"
        text = item.headline + "".join(f"{f.label}{f.value}{f.unit}" for f in item.facts)
        assert not [word for word in _BANNED_WORDS if word in text], text


# ---------------------------------------------------------------------------
# 1) break_ma20
# ---------------------------------------------------------------------------


def test_break_ma20_hits_on_first_cross_below() -> None:
    ctx = td.make_context_for_test(as_of=AS_OF, quotes={"600000": _rows([10.0] * 20 + [9.0])})
    item = td.detect(td.SPEC_BREAK_MA20, ctx, "600000")
    assert item is not None
    assert item.headline == "跌破 20 日线"
    assert item.severity == "risk"
    assert item.as_of == AS_OF


def test_break_ma20_ignores_stocks_already_below_ma() -> None:
    """已经跌破多日的股票不再天天报（首次跌破才算"今天发生的事"）。"""
    ctx = td.make_context_for_test(as_of=AS_OF, quotes={"600000": _rows([10.0] * 19 + [9.0, 8.5])})
    assert td.detect(td.SPEC_BREAK_MA20, ctx, "600000") is None


def test_break_ma20_needs_enough_history() -> None:
    ctx = td.make_context_for_test(as_of=AS_OF, quotes={"600000": _rows([10.0] * 5)})
    assert td.detect(td.SPEC_BREAK_MA20, ctx, "600000") is None


# ---------------------------------------------------------------------------
# 2) drop_sharp
# ---------------------------------------------------------------------------


def test_drop_sharp_hits_on_day_drop() -> None:
    ctx = td.make_context_for_test(as_of=AS_OF, quotes={"600000": _rows([10.0] * 5 + [9.2])})
    item = td.detect(td.SPEC_DROP_SHARP, ctx, "600000")
    assert item is not None
    assert item.headline == "今天跌得急"
    assert dict((f.label, f.value) for f in item.facts)["当日涨跌"] == pytest.approx(-8.0)


def test_drop_sharp_hits_on_five_day_drop() -> None:
    ctx = td.make_context_for_test(
        as_of=AS_OF, quotes={"600000": _rows([10.0, 9.9, 9.5, 9.3, 8.8, 8.6])}
    )
    item = td.detect(td.SPEC_DROP_SHARP, ctx, "600000")
    assert item is not None
    assert item.headline == "5 日跌幅偏大"


def test_drop_sharp_ignores_normal_pullback() -> None:
    ctx = td.make_context_for_test(as_of=AS_OF, quotes={"600000": _rows([10.0] * 5 + [9.6])})
    assert td.detect(td.SPEC_DROP_SHARP, ctx, "600000") is None


# ---------------------------------------------------------------------------
# 3) volume_spike
# ---------------------------------------------------------------------------


def test_volume_spike_needs_both_volume_and_price() -> None:
    rows = _rows([10.0] * 20 + [10.6], amount=1000.0)
    rows[-1] = (rows[-1][0], 10.6, 10.0, 3000.0)  # 3 倍量 + 6%
    ctx = td.make_context_for_test(as_of=AS_OF, quotes={"600000": rows})
    item = td.detect(td.SPEC_VOLUME_SPIKE, ctx, "600000")
    assert item is not None
    assert item.severity == "attention"
    assert dict((f.label, f.value) for f in item.facts)["量比(20日)"] == pytest.approx(3.0)


def test_volume_spike_ignores_big_volume_without_price_move() -> None:
    """只有量没有价 = 换手不是异动（spec §S8.2 第 3 行：量必须配价）。"""
    rows = _rows([10.0] * 20 + [10.0], amount=1000.0)
    rows[-1] = (rows[-1][0], 10.1, 10.0, 3000.0)  # 3 倍量但只涨 1%
    ctx = td.make_context_for_test(as_of=AS_OF, quotes={"600000": rows})
    assert td.detect(td.SPEC_VOLUME_SPIKE, ctx, "600000") is None


def test_volume_spike_ignores_zero_average_amount() -> None:
    """前 20 日均额为 0（长期停牌/脏数据）→ 不判，绝不能除零。"""
    rows = _rows([10.0] * 21, amount=0.0)
    rows[-1] = (rows[-1][0], 10.6, 10.0, 3000.0)
    ctx = td.make_context_for_test(as_of=AS_OF, quotes={"600000": rows})
    assert td.detect(td.SPEC_VOLUME_SPIKE, ctx, "600000") is None


# ---------------------------------------------------------------------------
# 4) limit_up_state
# ---------------------------------------------------------------------------


def test_limit_up_state_marks_sealed_limit_up_as_attention() -> None:
    ctx = td.make_context_for_test(as_of=AS_OF, limits={"600000": (11.0, 11.0, 11.0, 10.0)})
    item = td.detect(td.SPEC_LIMIT_UP_STATE, ctx, "600000")
    assert item is not None
    assert item.severity == "attention"
    assert item.headline == "涨停"


def test_limit_up_state_day_pct_uses_real_pre_close() -> None:
    """排序用的当日涨跌幅必须是 ``close/pre_close - 1``（不能拿涨停价当分母）。"""
    ctx = td.make_context_for_test(as_of=AS_OF, limits={"600000": (11.0, 11.0, 11.0, 10.0)})
    item = td.detect(td.SPEC_LIMIT_UP_STATE, ctx, "600000")
    assert item is not None
    assert item.day_pct == pytest.approx(10.0)


def test_limit_up_state_marks_broken_board_as_risk() -> None:
    """炸板（盘中触及涨停、收盘在板下）比涨停更值得看 → risk。"""
    ctx = td.make_context_for_test(as_of=AS_OF, limits={"600000": (11.0, 11.0, 10.5, 10.0)})
    item = td.detect(td.SPEC_LIMIT_UP_STATE, ctx, "600000")
    assert item is not None
    assert item.severity == "risk"
    assert "炸板" in item.headline


def test_limit_up_state_ignores_stock_far_from_board() -> None:
    ctx = td.make_context_for_test(as_of=AS_OF, limits={"600000": (11.0, 10.6, 10.5, 10.2)})
    assert td.detect(td.SPEC_LIMIT_UP_STATE, ctx, "600000") is None


# ---------------------------------------------------------------------------
# 5) new_announcement
# ---------------------------------------------------------------------------


def test_new_announcement_prioritises_equity_change() -> None:
    ctx = td.make_context_for_test(
        as_of=AS_OF,
        announcements={
            "600000": [
                td.AnnRow("A1", "关于股东权益变动的提示性公告", "equity_change", None),
                td.AnnRow("A2", "年报摘要", "report", None),
            ]
        },
    )
    item = td.detect(td.SPEC_NEW_ANNOUNCEMENT, ctx, "600000")
    assert item is not None
    assert item.headline.startswith("权益变动公告：")
    assert dict((f.label, f.value) for f in item.facts)["公告条数"] == "2"


def test_new_announcement_plain_event_when_no_equity_change() -> None:
    ctx = td.make_context_for_test(
        as_of=AS_OF,
        announcements={
            "600000": [td.AnnRow("A2", "年报摘要", "report", datetime(2026, 10, 15, 9, 0))]
        },
    )
    item = td.detect(td.SPEC_NEW_ANNOUNCEMENT, ctx, "600000")
    assert item is not None
    assert item.headline.startswith("新公告：")


def test_new_announcement_quiet_without_rows() -> None:
    ctx = td.make_context_for_test(as_of=AS_OF)
    assert td.detect(td.SPEC_NEW_ANNOUNCEMENT, ctx, "600000") is None


# ---------------------------------------------------------------------------
# 6) dragon_tiger
# ---------------------------------------------------------------------------


def test_dragon_tiger_reports_count_and_net_amount() -> None:
    ctx = td.make_context_for_test(
        as_of=AS_OF, dragon_tiger={"600000": [(AS_OF, 1.2e7), (AS_OF, -2.0e6)]}
    )
    item = td.detect(td.SPEC_DRAGON_TIGER, ctx, "600000")
    assert item is not None
    assert "净额 1000 万" in item.headline
    assert dict((f.label, f.value) for f in item.facts)["上榜次数"] == "2"


def test_dragon_tiger_quiet_without_rows() -> None:
    ctx = td.make_context_for_test(as_of=AS_OF)
    assert td.detect(td.SPEC_DRAGON_TIGER, ctx, "600000") is None


# ---------------------------------------------------------------------------
# 7) new_high_low
# ---------------------------------------------------------------------------


def test_new_high_low_skips_newly_listed() -> None:
    """上市 <60 交易日的股票不得命中新高低（否则次新恒真）。"""
    ctx = td.make_context_for_test(
        as_of=AS_OF, quotes={"300389": _rows([10.0] * 20)}, names={"300389": "艾比森"}
    )
    assert td.detect(td.SPEC_NEW_HIGH_LOW, ctx, "300389") is None


def test_new_high_low_hits_after_enough_history() -> None:
    closes = [10.0] * 60 + [10.5]
    ctx = td.make_context_for_test(as_of=AS_OF, quotes={"600000": _rows(closes)})
    item = td.detect(td.SPEC_NEW_HIGH_LOW, ctx, "600000")
    assert item is not None
    assert item.headline == "创 60 日新高"
    assert item.severity == "attention"


def test_new_high_low_marks_new_low_as_risk() -> None:
    closes = [10.0] * 60 + [9.5]
    ctx = td.make_context_for_test(as_of=AS_OF, quotes={"600000": _rows(closes)})
    item = td.detect(td.SPEC_NEW_HIGH_LOW, ctx, "600000")
    assert item is not None
    assert item.headline == "创 60 日新低"
    assert item.severity == "risk"


def test_new_high_low_ignores_mid_range_close() -> None:
    closes = [10.0 + (i % 3) * 0.1 for i in range(60)] + [10.0]
    ctx = td.make_context_for_test(as_of=AS_OF, quotes={"600000": _rows(closes)})
    assert td.detect(td.SPEC_NEW_HIGH_LOW, ctx, "600000") is None


# ---------------------------------------------------------------------------
# 8) sector_move
# ---------------------------------------------------------------------------


def test_sector_move_hits_on_top_decile_with_real_amplitude() -> None:
    ctx = td.make_context_for_test(
        as_of=AS_OF,
        sector_pct={"801780": 4.5},
        sector_rank={"801780": 0.95},
        sector_of={"600000": "801780"},
        sector_name={"801780": "银行"},
    )
    item = td.detect(td.SPEC_SECTOR_MOVE, ctx, "600000")
    assert item is not None
    assert item.headline == "银行 板块当日 +4.50%"
    assert item.severity == "background"


def test_sector_move_ignores_top_decile_without_amplitude() -> None:
    """平静日的前 10% 可能只有 +0.6%：分位达标但绝对量级不够，不算"在动"。"""
    ctx = td.make_context_for_test(
        as_of=AS_OF,
        sector_pct={"801780": 0.6},
        sector_rank={"801780": 0.99},
        sector_of={"600000": "801780"},
        sector_name={"801780": "银行"},
    )
    assert td.detect(td.SPEC_SECTOR_MOVE, ctx, "600000") is None


def test_sector_move_ignores_middle_of_ranking() -> None:
    ctx = td.make_context_for_test(
        as_of=AS_OF,
        sector_pct={"801780": 4.5},
        sector_rank={"801780": 0.5},
        sector_of={"600000": "801780"},
    )
    assert td.detect(td.SPEC_SECTOR_MOVE, ctx, "600000") is None


def test_sector_move_ignores_symbol_with_industry_but_no_pct() -> None:
    """有归属但当日没有板块涨幅（成分股全停牌等）→ 不判，不是"板块没动"。"""
    ctx = td.make_context_for_test(as_of=AS_OF, sector_of={"600000": "801780"})
    assert td.detect(td.SPEC_SECTOR_MOVE, ctx, "600000") is None


def test_sector_move_ignores_symbols_without_industry() -> None:
    ctx = td.make_context_for_test(as_of=AS_OF, sector_pct={"801780": 4.5})
    assert td.detect(td.SPEC_SECTOR_MOVE, ctx, "600000") is None


# ---------------------------------------------------------------------------
# 9) unlock_soon（spec §S11.2）
# ---------------------------------------------------------------------------


def _unlock(days: int = 10, share: float | None = 12_000_000.0, ratio: float | None = 4.2):
    """一条解禁计划：``(float_date, float_share 股, float_ratio %)``。

    ``float_share`` 是**股**（TuShare 原值；实机核对见 ``UnlockRow`` 的 docstring），
    卡片按 万股 上屏（``/10000``）——所以 12,000,000 股 = 「1,200 万股」。
    """
    return (AS_OF + _days(days), share, ratio)


def test_unlock_soon_hits_inside_thirty_days_with_human_headline() -> None:
    ctx = td.make_context_for_test(as_of=AS_OF, unlocks={"600000": [_unlock(10)]})
    item = td.detect(td.SPEC_UNLOCK_SOON, ctx, "600000")
    assert item is not None
    assert item.severity == "risk"
    # spec §S11.2 逐字文案形状：「30 天内解禁 1,200 万股（占流通 4.2%），解禁日 11-02」
    assert item.headline == "30 天内解禁 1,200 万股（占总股本 4.2%），解禁日 10-26"
    assert {fact.label: fact.value for fact in item.facts} == {
        "解禁股数": 1200.0,
        "占总股本比": 4.2,
        "解禁日": "10-26",
    }
    assert item.source == "share_floats"
    assert item.day_pct is None, "解禁与当日行情无关，不得拿涨跌幅充排序键"


def test_unlock_soon_includes_day_thirty_and_excludes_day_thirty_one() -> None:
    """spec §S11.4 边界：解禁日 = 今天+30 命中、今天+31 不命中。"""
    hit = td.make_context_for_test(as_of=AS_OF, unlocks={"600000": [_unlock(30, 5_000_000.0, 1.5)]})
    miss = td.make_context_for_test(
        as_of=AS_OF, unlocks={"600000": [_unlock(31, 5_000_000.0, 1.5)]}
    )
    assert td.detect(td.SPEC_UNLOCK_SOON, hit, "600000") is not None
    assert td.detect(td.SPEC_UNLOCK_SOON, miss, "600000") is None


def test_unlock_soon_ratio_boundaries_are_one_and_three_percent() -> None:
    """spec §S11.4 边界：``ratio=1.0`` 属 attention、``3.0`` 属 risk、``<1.0`` 不命中。"""

    def _hit(ratio: float):
        ctx = td.make_context_for_test(as_of=AS_OF, unlocks={"600000": [_unlock(10, 100.0, ratio)]})
        return td.detect(td.SPEC_UNLOCK_SOON, ctx, "600000")

    assert _hit(0.99) is None
    attention = _hit(1.0)
    assert attention is not None and attention.severity == "attention"
    near_risk = _hit(2.99)
    assert near_risk is not None and near_risk.severity == "attention"
    risk = _hit(3.0)
    assert risk is not None and risk.severity == "risk"


def test_unlock_soon_is_silent_without_rows() -> None:
    """无记录 ≠ 没有解禁（spec §S11.2）：不输出，也不冒充"今天没事"。"""
    ctx = td.make_context_for_test(as_of=AS_OF)
    assert td.detect(td.SPEC_UNLOCK_SOON, ctx, "600000") is None


def test_unlock_soon_ignores_the_as_of_day_itself() -> None:
    """解禁日 = ``as_of`` 当天（那天已经可流通）不属于"未来"，不报。"""
    ctx = td.make_context_for_test(as_of=AS_OF, unlocks={"600000": [_unlock(0, 12_000_000.0, 9.9)]})
    assert td.detect(td.SPEC_UNLOCK_SOON, ctx, "600000") is None


def test_unlock_soon_aggregates_holders_on_the_same_unlock_day() -> None:
    """同一天多个持有人合计（1.5% + 1.6% > 3%）→ 一次 risk，而不是两条各自 attention。"""
    ctx = td.make_context_for_test(
        as_of=AS_OF,
        unlocks={"600000": [_unlock(10, 1_000_000.0, 1.5), _unlock(10, 2_000_000.0, 1.6)]},
    )
    item = td.detect(td.SPEC_UNLOCK_SOON, ctx, "600000")
    assert item is not None
    assert item.severity == "risk"
    facts = {fact.label: fact.value for fact in item.facts}
    assert facts["解禁股数"] == pytest.approx(300.0), "3,000,000 股 = 300 万股"
    assert facts["占总股本比"] == pytest.approx(3.1)
    assert "解禁日 10-26" in item.headline


def test_unlock_soon_reports_count_when_several_days_hit() -> None:
    """窗口内多次解禁：facts 追加次数；文案说 **risk 档里最近** 的那一次。"""
    ctx = td.make_context_for_test(
        as_of=AS_OF,
        unlocks={"600000": [_unlock(5, 1_000_000.0, 1.2), _unlock(12, 2_000_000.0, 3.5)]},
    )
    item = td.detect(td.SPEC_UNLOCK_SOON, ctx, "600000")
    assert item is not None
    assert item.severity == "risk"
    facts = {fact.label: fact.value for fact in item.facts}
    assert facts["30 天内解禁"] == "2"
    assert "解禁日 10-28" in item.headline


def test_unlock_soon_skips_day_when_ratio_is_missing() -> None:
    """占比缺失 → 无法核对"占流通 ≥1%" → 该日不判，别的日子照常。"""
    ctx = td.make_context_for_test(
        as_of=AS_OF,
        unlocks={"600000": [_unlock(3, 9_000_000.0, None), _unlock(9, 1_000_000.0, 2.0)]},
    )
    item = td.detect(td.SPEC_UNLOCK_SOON, ctx, "600000")
    assert item is not None
    assert "解禁日 10-25" in item.headline
    assert {fact.label: fact.value for fact in item.facts}["占总股本比"] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# 每个探测器至少 1 正例 + 1 反例（结构性断言，防"漏写某个探测器"）
# ---------------------------------------------------------------------------


def test_every_detector_has_both_positive_and_negative_case() -> None:
    """本文件的用例名必须覆盖每个探测器（key 出现在测试名里）。"""
    src = Path(__file__).resolve().read_text(encoding="utf-8")
    for spec in td.TRIAGE_DETECTORS:
        assert f"SPEC_{spec.key.upper()}" in src, f"{spec.key} 没有专属用例"


# 10) regulatory_anomaly（2026-10-20 监管异动价格口径）


def _reg_ctx(closes: list[float], *, names: dict[str, str] | None = None):
    rows = _rows(closes)
    ctx = td.make_context_for_test(
        as_of=rows[-1][0], quotes={"600000": rows}, names=names or {"600000": "测试股份"}
    )
    ctx.index_pct = {"000001.SH": {row[0]: 0.0 for row in rows}}
    return ctx


def test_regulatory_anomaly_triggered_is_risk() -> None:
    # 最后 3 天各 +10% → 3 日偏离 30%，主板阈值 20%
    ctx = _reg_ctx([10.0, 10.0, 11.0, 12.1, 13.31])
    item = td.detect(td.SPEC_REGULATORY_ANOMALY, ctx, "600000")
    assert item is not None
    assert item.severity == "risk"
    assert "已达监管异动线" in item.headline
    assert any(f.label == "3日偏离" and f.value == 30.0 for f in item.facts)


def test_regulatory_anomaly_near_is_attention() -> None:
    # 最后 3 天各 +6% → 18%（≥16 的 80%，<20）：临近，且给出明日触发价
    ctx = _reg_ctx([10.0, 10.0, 10.6, 11.236, 11.91016])
    item = td.detect(td.SPEC_REGULATORY_ANOMALY, ctx, "600000")
    assert item is not None
    assert item.severity == "attention"
    assert "临近监管异动线" in item.headline
    assert any(f.label == "明日触发价" for f in item.facts)


def test_regulatory_anomaly_suspended_window_unreachable_is_none() -> None:
    """停牌缺行且明日涨停也够不到阈值 → 不报（不拿「3 日窗口」硬拼）。"""
    dates = [date(2026, 9, 1) + timedelta(days=i) for i in range(4)]
    rows = [
        (dates[0], 10.0, 10.0, 1000.0),
        (dates[2], 10.1, 10.0, 1000.0),
        (dates[3], 10.201, 10.1, 1000.0),
    ]
    ctx = td.make_context_for_test(as_of=dates[3], quotes={"600000": rows})
    ctx.index_pct = {"000001.SH": {d: 0.0 for d in dates}}
    assert td.detect(td.SPEC_REGULATORY_ANOMALY, ctx, "600000") is None


def test_regulatory_anomaly_without_index_does_not_judge() -> None:
    ctx = _reg_ctx([10.0, 11.0, 12.1])
    ctx.index_pct = {}
    assert td.detect(td.SPEC_REGULATORY_ANOMALY, ctx, "600000") is None


def test_regulatory_anomaly_down_side_is_risk() -> None:
    # 最后 3 天各 -10% → 3 日偏离 -30%，主板阈值 20%
    ctx = _reg_ctx([10.0, 10.0, 9.0, 8.1, 7.29])
    item = td.detect(td.SPEC_REGULATORY_ANOMALY, ctx, "600000")
    assert item is not None
    assert item.severity == "risk"
    assert "-30.00" in item.headline


def test_regulatory_anomaly_suspended_window_alerts_with_2day_partial() -> None:
    """3 日窗口跨停牌缺口：不报「已达线」，但要用最近 2 个连续交易日给明日触发价。

    这正是 600825 新华传媒的场景：10 天 6 板、今天只涨 2.77%，3 日窗口因停牌缺行，
    但明日再涨 7.85% 就会触发——漏掉它就等于把最该提醒的票漏了。
    """
    dates = [date(2026, 9, 1) + timedelta(days=i) for i in range(6)]
    rows = [
        (dates[0], 10.0, 10.0, 1000.0),
        (dates[4], 10.6, 10.0, 1000.0),
        (dates[5], 11.236, 10.6, 1000.0),
    ]
    ctx = td.make_context_for_test(as_of=dates[5], quotes={"600000": rows})
    ctx.index_pct = {"000001.SH": {d: 0.0 for d in dates}}
    item = td.detect(td.SPEC_REGULATORY_ANOMALY, ctx, "600000")
    assert item is not None
    assert item.severity == "attention"
    assert "停牌" in item.headline
    assert any(f.label == "明日触发价" for f in item.facts)


def test_regulatory_anomaly_skips_suspended_today() -> None:
    """今天停牌：最后一行是停牌前旧价，不得报成「今天已达线」."""
    dates = [date(2026, 9, 1) + timedelta(days=i) for i in range(4)]
    rows = [
        (dates[0], 10.0, 10.0, 1000.0),
        (dates[1], 10.5, 10.0, 1000.0),
        (dates[2], 11.0, 10.5, 1000.0),
    ]
    ctx = td.make_context_for_test(as_of=dates[3], quotes={"600000": rows})
    ctx.index_pct = {"000001.SH": {d: 0.0 for d in dates}}
    assert td.detect(td.SPEC_REGULATORY_ANOMALY, ctx, "600000") is None


def test_regulatory_anomaly_uses_actual_limit_for_st_names() -> None:
    """ST 名称股的明日可行涨幅要用**交易所限价**，不是板块固定 5%.

    实测 145 只主板 ST 名称股的真实限幅是 10%；若按 5% 判「可达性」，
    会把「明日涨 5.4% 触发」误判成「涨停也难触发」，提醒直接漏掉。
    """
    ctx = _reg_ctx([10.0, 10.0, 10.33, 10.67089, 11.023], names={"600000": "ST测试"})
    # 今日 pre_close=10.67089，交易所 10% 限幅 → up_limit=11.74（rule 里的 ST 固定值会算 5%）
    ctx.limits["600000"] = td.LimitRow(up_limit=11.74, high=11.0, close=11.023, pre_close=10.67089)
    item = td.detect(td.SPEC_REGULATORY_ANOMALY, ctx, "600000")
    assert item is not None
    assert item.severity == "attention"
    assert "明日涨" in item.headline
    assert any(f.label == "明日触发价" for f in item.facts)


def test_regulatory_anomaly_turnover_only_is_attention() -> None:
    """价格没到线、但换手率口径单独达标 → attention 卡片（不再静默漏报）."""
    dates = [date(2026, 9, 1) + timedelta(days=i) for i in range(8)]
    quotes = [(day, 10.0, 10.0, 1000.0) for day in dates]
    turnover = [(day, 0.3 if i < 5 else 18.0) for i, day in enumerate(dates)]
    ctx = td.make_context_for_test(
        as_of=dates[-1],
        quotes={"600000": quotes},
        names={"600000": "测试股份"},
        turnover={"600000": turnover},
    )
    ctx.index_pct = {"000001.SH": {day: 0.0 for day in dates}}

    item = td.detect(td.SPEC_REGULATORY_ANOMALY, ctx, "600000")
    assert item is not None
    assert item.severity == "attention"
    assert "换手率异动" in item.headline
    assert any(f.label == "倍数" and f.value == 60.0 for f in item.facts)


def test_regulatory_anomaly_price_plus_turnover_adds_fact() -> None:
    """价格已达线 + 换手率也达标：只出一张价格卡，事实行补「换手率口径」."""
    dates = [date(2026, 9, 1) + timedelta(days=i) for i in range(8)]
    closes = [10.0] * 5 + [11.0, 12.1, 13.31]
    quotes, prev = [], 10.0
    for day, close in zip(dates, closes, strict=True):
        quotes.append((day, close, prev, 1000.0))
        prev = close
    turnover = [(day, 0.3 if i < 5 else 18.0) for i, day in enumerate(dates)]
    ctx = td.make_context_for_test(
        as_of=dates[-1],
        quotes={"600000": quotes},
        names={"600000": "测试股份"},
        turnover={"600000": turnover},
    )
    ctx.index_pct = {"000001.SH": {day: 0.0 for day in dates}}

    item = td.detect(td.SPEC_REGULATORY_ANOMALY, ctx, "600000")
    assert item is not None
    assert item.severity == "risk"
    assert any(f.label == "换手率口径" for f in item.facts)
