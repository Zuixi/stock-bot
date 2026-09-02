"""纯单元测试：P5 标的分析 — 年化出栏纯函数 / 公司列下发 / 导入 stock_id 透传 / L3 码修正。"""

from datetime import date

from app.services.industry_metric_service import (
    _annualize_hogs,
    _company_columns,
    _hogs_ttm_ready,
    _prepare_batch_rows,
)
from app.services.industry_registry import PIG_INDUSTRY


def _month_ends(start: tuple[int, int], n: int) -> list[date]:
    """从 start 月起连续 n 个月的月末日期。"""
    import calendar

    y, m = start
    out = []
    for i in range(n):
        yy, mm = y + (m - 1 + i) // 12, (m - 1 + i) % 12 + 1
        out.append(date(yy, mm, calendar.monthrange(yy, mm)[1]))
    return out


# ── _annualize_hogs / _hogs_ttm_ready ────────────────────────────────


def test_ttm_sum_when_12_distinct_months():
    points = list(zip(_month_ends((2025, 9), 12), [100.0 + i for i in range(12)]))
    assert _annualize_hogs(points) == sum(100.0 + i for i in range(12))
    assert _hogs_ttm_ready(points) is True


def test_ttm_takes_last_12_months_when_history_longer():
    points = list(zip(_month_ends((2025, 8), 13), [float(i) for i in range(13)]))
    # 2025-08..2026-08 共 13 个月，trailing 12M = 后 12 个值（1..12）
    assert _annualize_hogs(points) == float(sum(range(1, 13)))


def test_short_history_latest_month_times_12():
    points = list(zip(_month_ends((2026, 3), 3), [500.0, 600.0, 700.0]))
    assert _annualize_hogs(points) == 700.0 * 12
    assert _hogs_ttm_ready(points) is False  # 落表 extra.annualized=True 的判据


def test_empty_or_nonpositive_returns_none():
    assert _annualize_hogs([]) is None
    assert _annualize_hogs([(date(2026, 1, 31), 0.0)]) is None


def test_same_month_duplicate_takes_latest():
    points = [(date(2026, 1, 10), 100.0), (date(2026, 1, 31), 120.0)]
    assert _annualize_hogs(points) == 120.0 * 12


# ── 列定义（registry 驱动） ──────────────────────────────────────────


def test_columns_fixed_head_plus_registry_company_metrics():
    cols = _company_columns(PIG_INDUSTRY)
    assert [c.key for c in cols[:6]] == [
        "symbol", "name", "latest_price", "total_mv_yi", "pe_ttm", "pb",
    ]
    assert [c.label for c in cols[:6]] == ["代码", "名称", "最新价", "总市值(亿)", "PE(TTM)", "PB"]
    assert cols[0].numeric is False and cols[2].numeric is True

    metric_cols = {c.key: c for c in cols[6:]}
    assert set(metric_cols) == {
        "company.hogs_sold_monthly", "company.cost_complete", "mcap_per_head",
    }
    assert metric_cols["company.hogs_sold_monthly"].unit == "万头"
    assert metric_cols["company.cost_complete"].unit == "元/kg"
    assert metric_cols["mcap_per_head"].unit == "元/头"
    assert metric_cols["mcap_per_head"].tier == "calc"


def test_company_metrics_live_in_company_group_only():
    keys = [m.key for m in PIG_INDUSTRY.metrics if m.group == "company"]
    assert "mcap_per_head" in keys
    # 公司指标不得混入看板分组（strip/quick 由 stock_id==0 的行业级行驱动）
    assert all(
        m.group != "company"
        for m in PIG_INDUSTRY.metrics
        if m.key in ("hog_price", "sow_inventory")
    )


def test_pig_sw_l3_code_is_breeding_not_forestry():
    # 2026-09-03 修正：110301 为林业Ⅲ，生猪养殖 = 110702（docs/references/sw/申万行业分类.md）
    assert "110702" in PIG_INDUSTRY.sw_l3_codes
    assert "110301" not in PIG_INDUSTRY.sw_l3_codes


# ── 导入通道 stock_id 透传 ──────────────────────────────────────────


def test_batch_preserves_company_stock_id():
    rows, unknown, rejected = _prepare_batch_rows(PIG_INDUSTRY, [
        {"metric_key": "company.hogs_sold_monthly", "period": date(2026, 8, 31),
         "value": 600.0, "stock_id": 1},
        {"metric_key": "company.hogs_sold_monthly", "period": date(2026, 8, 31),
         "value": 30.0, "stock_id": 12345},
        {"metric_key": "company.hogs_sold_monthly", "period": date(2026, 8, 31),
         "value": 30.0},  # 未携带 stock_id → 行业级 0（既有语义）
    ])
    assert not unknown and not rejected
    assert [r["stock_id"] for r in rows] == [1, 12345, 0]


def test_batch_company_source_tier_from_registry():
    rows, _, _ = _prepare_batch_rows(PIG_INDUSTRY, [
        {"metric_key": "mcap_per_head", "period": date(2026, 8, 31),
         "value": 2500.0, "stock_id": 1, "source": "manual"},  # 派生指标也走人工通道校验
    ])
    assert rows[0]["source_tier"] == PIG_INDUSTRY.metric("mcap_per_head").tier
