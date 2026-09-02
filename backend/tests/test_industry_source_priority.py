"""纯单元测试：源优先级裁决与 registry 声明。"""

from datetime import date

from app.models.industry_research import IndustryMetric
from app.services.industry_metric_service import PURGE_SOURCES, _pick_latest
from app.services.industry_registry import PIG_INDUSTRY


def _row(metric_key, source, period, freq="daily"):
    return IndustryMetric(
        industry_key="pig", stock_id=0, metric_key=metric_key,
        source=source, freq=freq, period=period, value=1.0,
    )


def test_real_source_beats_mock_regardless_of_recency():
    grouped = {
        "hog_price": [
            _row("hog_price", "mock", date(2026, 9, 2)),
            _row("hog_price", "akshare_100ppi", date(2026, 8, 30)),
        ]
    }
    assert _pick_latest(PIG_INDUSTRY, grouped, "hog_price").source == "akshare_100ppi"


def test_mock_always_last_in_registry_sources():
    for m in PIG_INDUSTRY.metrics:
        if len(m.sources) > 1:
            assert m.sources[-1] == "mock", f"{m.key}: {m.sources}"


def test_lh_future_registers_akshare_sina():
    assert "akshare_sina" in PIG_INDUSTRY.metric("lh_future_main").sources


def test_fallback_prefers_most_recent_period():
    # registry 未登记的 source 兜底时按最新 period 取，行为确定
    grouped = {
        "hog_price": [
            _row("hog_price", "manual", date(2026, 1, 1)),
            _row("hog_price", "other", date(2026, 6, 1)),
        ]
    }
    assert _pick_latest(PIG_INDUSTRY, grouped, "hog_price").source == "other"


def test_purge_sources_cover_mock_and_derived():
    # mock→真实源切换的清除范围必须同时覆盖 derived 行：派生计算只 upsert 不删除，
    # 若只删 mock 行，由 mock 基础行算出的旧 derived 序列会存活并继续喂给周期引擎。
    assert PURGE_SOURCES == {"mock", "derived"}
