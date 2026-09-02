"""纯函数测试：猪周期规则引擎（无 DB、无 I/O）。"""

import pytest

from app.services import cycle_engine as ce
from app.services.cycle_engine import CycleInput, evaluate_pig_cycle


def _inp(**kw) -> CycleInput:
    base = dict(
        ratio=7.5, price=16.0, cost=15.0,
        sow_mom_series=[-0.5, -0.4, -0.3],
        ratio_series=[7.0, 7.2, 7.4, 7.5],
    )
    base.update(kw)
    return CycleInput(**base)


class TestPhase:
    def test_overheat_is_prosperity(self):
        out = evaluate_pig_cycle(_inp(ratio=9.6, ratio_series=[9.0, 9.2, 9.4, 9.6]))
        assert out.phase == "prosperity"
        assert out.signal == "卖出"

    def test_deep_loss_is_depression(self):
        out = evaluate_pig_cycle(_inp(price=12.0, cost=15.0, sow_mom_series=[0.5, 0.4]))
        assert out.phase == "depression"
        assert out.signal == "空仓"

    def test_ratio_below_6_is_depression_even_above_cost(self):
        out = evaluate_pig_cycle(_inp(ratio=5.5, sow_mom_series=[]))
        assert out.phase == "depression"

    def test_profit_plus_derating_is_recovery_buy(self):
        out = evaluate_pig_cycle(_inp())
        assert out.phase == "recovery"
        assert out.signal == "买入"

    def test_profit_expanding_is_prosperity_or_recession(self):
        out = evaluate_pig_cycle(_inp(sow_mom_series=[0.3, 0.4, 0.5]))
        assert out.phase in ("prosperity", "recession")
        assert out.signal == "关注"

    def test_ratio_falling_after_high_is_recession(self):
        out = evaluate_pig_cycle(
            _inp(sow_mom_series=[0.3, 0.4], ratio_series=[8.8, 8.6, 8.4, 7.6])
        )
        assert out.phase == "recession"

    def test_missing_everything_defaults_to_depression(self):
        out = evaluate_pig_cycle(CycleInput())
        assert out.phase == "depression"
        assert out.signal == "空仓"

    def test_derating_alone_without_profit_evidence_is_conservative(self):
        # 修复目标：价格/成本/猪粮比全缺失时，仅凭去化不得判复苏发买入
        out = evaluate_pig_cycle(CycleInput(sow_mom_series=[-1.0, -1.0, -1.0]))
        assert out.phase == "depression"
        assert out.signal == "空仓"

    def test_missing_cost_but_ratio_above_6_with_derating_is_recovery(self):
        # 猪粮比 >= 6 是引擎自身的盈亏平衡代理口径，可替代成本口径
        out = evaluate_pig_cycle(_inp(cost=None))
        assert out.phase == "recovery"
        assert out.signal == "买入"


class TestSignals:
    def test_depression_with_derating_is_watch(self):
        out = evaluate_pig_cycle(_inp(price=13.0, cost=15.0))
        assert out.phase == "depression"
        assert out.signal == "关注"

    def test_deep_loss_reason_mentions_level1_warning(self):
        out = evaluate_pig_cycle(_inp(ratio=4.8, price=12.0, cost=15.0))
        assert any("一级预警" in r for r in out.reasons)

    def test_positions_come_from_registry_template(self):
        out = evaluate_pig_cycle(_inp())
        assert [p.name for p in out.positions] == ["核心底仓", "波段仓位", "现金储备"]


class TestHelpers:
    def test_count_consecutive_negative_stops_at_none(self):
        assert ce.count_consecutive_negative([0.1, -0.2, None, -0.3, -0.4]) == 2

    def test_count_consecutive_negative_all_positive(self):
        assert ce.count_consecutive_negative([0.1, 0.2]) == 0

    @pytest.mark.parametrize("value,expected", [
        (4.9, "一级预警"), (5.0, "一级预警"), (5.1, "二级预警"),
        (6.0, "二级预警"), (6.1, "正常"), (9.0, "正常"), (9.01, "过度上涨"),
    ])
    def test_band_label_boundaries(self, value, expected):
        from app.services.industry_registry import PIG_INDUSTRY

        m = PIG_INDUSTRY.metric("hog_corn_ratio")
        assert m.band_label(value) == expected
