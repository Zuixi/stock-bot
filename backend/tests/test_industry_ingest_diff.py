# backend/tests/test_industry_ingest_diff.py
"""行级 diff 纯函数：new/changed/unchanged 三分类 + 修订留痕 + 哈希稳定性 + 源节奏表."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.services.industry_ingest_diff import (
    canonical_content_hash,
    compute_ingest_actions,
    next_due,
)


def _row(period: date, value: float, freq: str = "monthly", metric_key: str = "sow_inventory"):
    return {
        "industry_key": "pig",
        "stock_id": 0,
        "metric_key": metric_key,
        "source": "xuantian",
        "source_tier": "official",
        "freq": freq,
        "period": period,
        "value": value,
        "unit": "万头",
        "extra": None,
    }


def test_first_ingest_all_new():
    fetched = [_row(date(2025, 6, 30), 4043.0, "quarterly")]
    out = compute_ingest_actions(fetched, existing=[])
    assert len(out["new"]) == 1
    assert out["unchanged_count"] == 0
    assert out["revisions"] == []
    assert out["orphaned_count"] == 0


def test_unchanged_rows_not_returned_for_write():
    existing = [_row(date(2025, 6, 30), 4043.0, "quarterly")]
    fetched = [_row(date(2025, 6, 30), 4043.0, "quarterly")]
    out = compute_ingest_actions(fetched, existing)
    assert out["new"] == []
    assert out["changed"] == []
    assert out["unchanged_count"] == 1


def test_value_change_is_changed_with_revision_trace():
    # 官方修订场景：2025Q2 能繁 4043 → 4038（上游定稿回修）
    existing = [_row(date(2025, 6, 30), 4043.0, "quarterly")]
    fetched = [_row(date(2025, 6, 30), 4038.0, "quarterly")]
    out = compute_ingest_actions(fetched, existing)
    assert len(out["changed"]) == 1
    assert out["changed"][0]["value"] == 4038.0
    assert out["revisions"] == [
        {
            "metric_key": "sow_inventory",
            "freq": "quarterly",
            "period": date(2025, 6, 30),
            "old": 4043.0,
            "new": 4038.0,
        }
    ]


def test_decimalized_existing_compared_by_tolerance():
    existing = [_row(date(2025, 6, 30), 4043.0000001, "quarterly")]  # 差 1e-7 < 1e-6
    fetched = [_row(date(2025, 6, 30), 4043.0, "quarterly")]
    out = compute_ingest_actions(fetched, existing)
    assert out["unchanged_count"] == 1


def test_upstream_dropped_row_counted_not_deleted():
    # 库有响应无 → orphaned：保留（官方语义不镜像上游删除），只计数
    existing = [_row(date(2024, 12, 31), 4078.0, "yearly")]
    out = compute_ingest_actions(fetched=[], existing=existing)
    assert out["orphaned_count"] == 1
    assert out["new"] == [] and out["changed"] == []


def test_mixed_classification():
    existing = [
        _row(date(2024, 12, 31), 4078.0, "yearly"),
        _row(date(2025, 6, 30), 4043.0, "quarterly"),
    ]
    fetched = [
        _row(date(2024, 12, 31), 4078.0, "yearly"),  # unchanged
        _row(date(2025, 6, 30), 4041.0, "quarterly"),  # changed（修订）
        _row(date(2025, 9, 30), 4035.0, "quarterly"),  # new
    ]
    out = compute_ingest_actions(fetched, existing)
    assert len(out["new"]) == 1
    assert len(out["changed"]) == 1
    assert out["unchanged_count"] == 1
    assert out["revisions"][0]["old"] == 4043.0


def test_hash_stable_regardless_of_row_order():
    a = [_row(date(2025, 6, 30), 4043.0), _row(date(2024, 12, 31), 4078.0, "yearly")]
    b = list(reversed(a))
    assert canonical_content_hash(a) == canonical_content_hash(b)


def test_hash_changes_when_value_changes():
    a = [_row(date(2025, 6, 30), 4043.0)]
    b = [_row(date(2025, 6, 30), 4044.0)]
    assert canonical_content_hash(a) != canonical_content_hash(b)


def test_hash_format_16_hex():
    h = canonical_content_hash([_row(date(2025, 6, 30), 4043.0)])
    assert len(h) == 16 and int(h, 16) >= 0  # 16 位 hex


def test_next_due_cadence_per_source():
    now = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)
    assert next_due("xuantian", now) == now + timedelta(days=30)
    assert next_due("caaa", now) == now + timedelta(days=32)
    assert next_due("akshare_soozhu", now) == now + timedelta(days=1)
    assert next_due("akshare_sina", now) == now + timedelta(days=1)
    assert next_due("unknown_source", now) == now + timedelta(days=1)  # 未知源默认日更
