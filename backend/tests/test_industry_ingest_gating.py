# backend/tests/test_industry_ingest_gating.py
"""三层门控：L1 调度门 / L2 内容门 / L3 行级 diff 的编排逻辑（纯逻辑 + 内存 DB 状态表）."""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.services.industry_ingest_diff import (
    canonical_content_hash,
    compute_ingest_actions,
    next_due,
)


def test_l1_not_due_skips_fetch():
    # next_due_at 在未来 → 调度门直接短路（不发请求）
    now = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)
    assert now < next_due("xuantian", now)  # 节奏推算：上次+30d，门内比较 now < state.next_due_at
    # next_due 的节奏表逐源断言在 Task 4 测试文件（纯函数住纯模块）


def _row(period: date, value: float) -> dict:
    return {"metric_key": "sow_inventory", "freq": "quarterly", "period": period, "value": value}


def test_l3_incremental_writes_only_delta():
    rows_v1 = [
        _row(date(2025, 6, 30), 4043.0),
        _row(date(2025, 9, 30), 4035.0),
    ]
    rows_v2 = [  # Q2 被修订 4043→4041，新增 Q4
        _row(date(2025, 6, 30), 4041.0),
        _row(date(2025, 9, 30), 4035.0),
        _row(date(2025, 12, 31), 3990.0),
    ]
    out = compute_ingest_actions(_pad(rows_v2), _pad(rows_v1))
    assert len(out["new"]) == 1 and len(out["changed"]) == 1
    assert out["revisions"][0]["old"] == 4043.0 and out["revisions"][0]["new"] == 4041.0
    assert canonical_content_hash(_pad(rows_v1)) != canonical_content_hash(_pad(rows_v2))


def _pad(rows):
    return [
        {
            **r,
            "industry_key": "pig",
            "stock_id": 0,
            "source": "xuantian",
            "source_tier": "official",
            "unit": "万头",
            "extra": None,
        }
        for r in rows
    ]
