"""泛化验证单元测试（P6 收尾，纯离线，无 DB/网络）。

锁定三件事：
1. registry 注册双行业且 sw_l3_codes 不相交（列表/companies 隔离的前提）；
2. broiler 演示行业全部指标 mock-only 且带 mock_base 基准值；
3. 通用 mock builder 的输出形状：无重复冲突键、确定性、末点等于基准值。

**2026-10-12 更新（spec §S1.8 落地）**：`INDUSTRIES` 不再只装行业 scope ——
`industry_key` 已泛化为通用 scope 键，`market`（全市场情绪）作为**第一个非行业
scope** 进了同一张字典。故第 1 条从"INDUSTRIES 恰好两个 key"改成
"**行业** scope 恰好两个 key + `market` 是登记在册的非行业 scope"，
并新增一条"非行业 scope 必须 abstain 行业专属字段"的不变量（避免以后有人给
`market` 填 `sw_l3_codes`/`phases`，让"按 scope 取行业信息"的消费方悄悄读错）。
"""

from __future__ import annotations

from datetime import date

from app.services import industry_registry as reg
from app.services.cycle_engine import CycleInput, evaluate_pig_cycle
from app.services.industry_mock_data import (
    build_generic_mock_points,
    build_industry_mock_points,
)

#: 真正的**行业** scope（挂申万分类、有周期相位与仓位模板）。
INDUSTRY_SCOPES = {"pig", "broiler"}
#: 非行业 scope（全市场/宏观类，只借表与读面，不借行业语义）。
NON_INDUSTRY_SCOPES = {"market"}


class TestRegistryGeneralization:
    def test_registry_has_two_industries_with_disjoint_sw_codes(self):
        # 行业 scope 仍是恰好两个，且申万Ⅲ级码不相交
        assert INDUSTRY_SCOPES <= set(reg.INDUSTRIES)
        assert NON_INDUSTRY_SCOPES <= set(reg.INDUSTRIES)
        pig, broiler = reg.INDUSTRIES["pig"], reg.INDUSTRIES["broiler"]
        assert set(pig.sw_l3_codes).isdisjoint(broiler.sw_l3_codes)

    def test_non_industry_scopes_abstain_from_industry_only_fields(self):
        """非行业 scope 不得携带行业专属字段（申万码 / 周期相位 / 仓位模板）。

        `IndustryConfig` 是行业与非行业共用的容器（见 registry 模块注释）；这条
        不变量把"market 只有 metrics / reference_points 有意义"钉住，防止以后
        有人顺手给它填一套相位，让 `sw_l3_codes` / 相位的消费方读到一个假行业。
        """
        for key in NON_INDUSTRY_SCOPES:
            cfg = reg.INDUSTRIES[key]
            assert cfg.sw_l3_codes == [], key
            assert cfg.phases == [], key
            assert cfg.position_templates == {}, key
            assert cfg.metrics, f"{key} 必须有自己的指标定义"
        for key in INDUSTRY_SCOPES:
            cfg = reg.INDUSTRIES[key]
            assert cfg.sw_l3_codes, key  # 行业 scope 必须有申万码
            assert cfg.phases, key

    def test_broiler_metrics_are_mock_only_with_base(self):
        broiler = reg.INDUSTRIES["broiler"]
        assert len(broiler.metrics) >= 2
        keys = {m.key for m in broiler.metrics}
        assert {"chick_price", "broiler_price"} <= keys
        for m in broiler.metrics:
            assert m.sources == ["mock"], f"{m.key} 演示行业不得声明真实源"
            assert m.mock_base is not None and m.mock_base > 0

    def test_broiler_reuses_canonical_phase_keys_and_templates(self):
        pig, broiler = reg.INDUSTRIES["pig"], reg.INDUSTRIES["broiler"]
        assert [p.key for p in broiler.phases] == [p.key for p in pig.phases]
        for signal, slices in broiler.position_templates.items():
            assert sum(s.pct for s in slices) == 100, f"{signal} 仓位模板需配平"


class TestGenericMockBuilder:
    def test_shape_and_no_duplicate_conflict_keys(self):
        rows = build_generic_mock_points(reg.BROILER_INDUSTRY)
        assert rows, "配置 mock_base 的指标必须出序列"
        today = date.today()
        seen: set[tuple[str, str, str, date]] = set()
        for r in rows:
            key = (r["metric_key"], r["source"], r["freq"], r["period"])
            assert key not in seen, f"批内重复冲突键：{key}"
            seen.add(key)
            assert r["industry_key"] == "broiler"
            assert r["source"] == "mock" and r["stock_id"] == 0
            assert r["period"] <= today
            assert r["value"] > 0
            assert r["unit"] in ("元/羽", "元/kg")

    def test_daily_series_window_and_last_point_equals_base(self):
        rows = build_generic_mock_points(reg.BROILER_INDUSTRY)
        chick = [r for r in rows if r["metric_key"] == "chick_price"]
        assert len(chick) == 45  # 与 pig 日度窗口同宽
        base = reg.INDUSTRIES["broiler"].metric("chick_price").mock_base
        assert chick[-1]["value"] == base  # 末点精确等于基准值（日/月口径一致性约定）

    def test_deterministic_rerun(self):
        assert build_generic_mock_points(reg.BROILER_INDUSTRY) == build_generic_mock_points(
            reg.BROILER_INDUSTRY
        )

    def test_pig_not_affected_by_generic_builder(self):
        # pig 指标不带 mock_base：通用 builder 产出为空 → dispatch 到专用序列是必要的
        assert build_generic_mock_points(reg.PIG_INDUSTRY) == []
        pig_rows = build_industry_mock_points(reg.PIG_INDUSTRY, months=12)
        assert any(r["metric_key"] == "sow_inventory" for r in pig_rows)

    def test_dispatch_routes_broiler_to_generic(self):
        rows = build_industry_mock_points(reg.BROILER_INDUSTRY)
        assert {r["metric_key"] for r in rows} == {"chick_price", "broiler_price"}


class TestCycleEngineConfigDriven:
    def test_engine_accepts_broiler_config_positions(self):
        # 关键指标缺失 → 保守萧条/空仓；仓位模板来自传入 cfg（而非写死 pig）
        out = evaluate_pig_cycle(CycleInput(), reg.INDUSTRIES["broiler"])
        assert out.phase == "depression"
        assert out.signal == "空仓"
        assert [p.name for p in out.positions] == ["核心底仓", "波段仓位", "现金储备"]
        assert sum(p.pct for p in out.positions) == 100

    def test_engine_default_still_pig(self):
        out = evaluate_pig_cycle(CycleInput())
        assert out.phase == "depression"  # 与既有行为一致（缺省配置回退 PIG_INDUSTRY）
