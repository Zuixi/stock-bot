"""纯单元测试：batch 导入行准备（白名单/未知指标/source_tier）。"""

from datetime import date

from app.services.industry_metric_service import IMPORT_ALLOWED_SOURCES, _prepare_batch_rows
from app.services.industry_registry import PIG_INDUSTRY


def test_batch_accepts_manual_and_rejects_provider_sources():
    items = [
        {"metric_key": "industry_cost_avg", "period": date(2026, 8, 31), "value": 13.5,
         "source": "manual"},
        {"metric_key": "industry_cost_avg", "period": date(2026, 8, 31), "value": 13.5,
         "source": "akshare_100ppi"},  # 采集适配器专属 source，人工通道不得伪造
        {"metric_key": "sow_inventory", "period": date(2026, 6, 30), "value": 4038.0,
         "source": "stats_gov"},  # 统计局 CSV 导入通道
        {"metric_key": "nope", "period": date(2026, 8, 31), "value": 1.0},
    ]
    rows, unknown, rejected = _prepare_batch_rows(PIG_INDUSTRY, items)
    assert [r["source"] for r in rows] == ["manual", "stats_gov"]
    assert unknown == ["nope"]
    assert rejected == ["industry_cost_avg:akshare_100ppi"]


def test_batch_source_tier_always_from_registry():
    rows, _, _ = _prepare_batch_rows(PIG_INDUSTRY, [
        {"metric_key": "sow_inventory", "period": date(2026, 6, 30), "value": 4038.0,
         "source": "stats_gov"},
    ])
    assert rows[0]["source_tier"] == PIG_INDUSTRY.metric("sow_inventory").tier


def test_import_allowed_sources_constant():
    assert IMPORT_ALLOWED_SOURCES == {"manual", "stats_gov"}
