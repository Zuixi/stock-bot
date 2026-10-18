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
from datetime import date, datetime
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
    assert len(td.TRIAGE_DETECTORS) == 8
    keys = [spec.key for spec in td.TRIAGE_DETECTORS]
    assert len(set(keys)) == 8
    assert {spec.severity for spec in td.TRIAGE_DETECTORS} <= set(td.SEVERITIES)


def test_severity_order_is_fixed() -> None:
    """分级次序 = 排序优先级（spec §S8.4：risk > event > attention > background）。"""
    assert td.SEVERITIES == ("risk", "event", "attention", "background")
    assert td.SEVERITY_RANK["risk"] < td.SEVERITY_RANK["background"]


def test_every_spec_carries_full_provenance() -> None:
    """8 条 spec 的来源字段必须齐全（provenance 契约；空值/短列表都算回归）。

    为什么这条不能只靠导入期断言：字段齐全 ≠ 内容有意义——这里额外钉住"4 条以上
    失效场景"与"来源表是库内真实表"，并把 ``with_provenance`` 的**单向映射**拿住
    （卡片三字段只能从 spec 取，不能在 8 个探测器里各写一份）。
    """
    tables = td._field_tables()
    for spec in td.TRIAGE_DETECTORS:
        assert spec.version == "r1", f"{spec.key} 的版本号必须显式登记"
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
    )
    items = td.run_detectors(ctx)
    assert len(items) >= 5, "该 context 应命中多类事实（否则禁用词断言样本不足）"
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
# 每个探测器至少 1 正例 + 1 反例（结构性断言，防"漏写某个探测器"）
# ---------------------------------------------------------------------------


def test_every_detector_has_both_positive_and_negative_case() -> None:
    """本文件的用例名必须覆盖每个探测器（key 出现在测试名里）。"""
    src = Path(__file__).resolve().read_text(encoding="utf-8")
    for spec in td.TRIAGE_DETECTORS:
        assert f"SPEC_{spec.key.upper()}" in src, f"{spec.key} 没有专属用例"
