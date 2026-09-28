# backend/tests/test_industry_ingest_gating.py
"""三层门控：L1 调度门 / L2 内容门 / L3 行级 diff 的编排逻辑.

L1 判定 `is_due` 是纯函数（住 diff 模块）直接断言四态；编排层
`_gated_xuantian_fetch` 用 mock repo/db + 可注入 fetcher 驱动（同
test_industry_ingest_state.py 的打桩约定，不触网不触库）。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.services import industry_metric_service as svc
from app.services.industry_ingest_diff import (
    canonical_content_hash,
    compute_ingest_actions,
    is_due,
    next_due,
)
from app.services.industry_metric_service import _gated_xuantian_fetch
from app.services.industry_registry import PIG_INDUSTRY

NOW = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)


def test_l1_is_due_predicate_four_cases():
    # (a) next_due_at 在未来 + force=False → 门拦下（不发请求）
    assert is_due(NOW + timedelta(days=30), NOW) is False
    # (b) 同一状态 + force=True（worker 手动兜底）→ 绕过放行
    assert is_due(NOW + timedelta(days=30), NOW, force=True) is True
    # (c) 无状态（首抓）→ 放行
    assert is_due(None, NOW) is True
    # (d) next_due_at 已过期 → 放行
    assert is_due(NOW - timedelta(seconds=1), NOW) is True


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


# ── 编排层 `_gated_xuantian_fetch`（mock repo/db + 注入 fetcher）──────────


def _orm_row(r: dict) -> SimpleNamespace:
    """diff 输入行 → 伪 ORM 行（`_as_row_dicts` 只读这九个属性）."""
    return SimpleNamespace(
        industry_key=r["industry_key"],
        stock_id=r["stock_id"],
        metric_key=r["metric_key"],
        source=r["source"],
        source_tier=r["source_tier"],
        freq=r["freq"],
        period=r["period"],
        value=r["value"],
        unit=r["unit"],
        extra=r["extra"],
    )


def _state(
    content_hash: str,
    next_due_at: datetime | None,
    last_success_at: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        content_hash=content_hash,
        next_due_at=next_due_at,
        last_success_at=last_success_at or datetime.now(UTC) - timedelta(days=40),
        last_period=date(2025, 9, 30),
    )


def _mock_db() -> MagicMock:
    return MagicMock()


async def test_l1_not_due_skips_fetch(monkeypatch):
    # next_due_at 在未来 + force=False → 编排层短路：零请求零状态写入
    fetcher = AsyncMock(return_value=_pad([_row(date(2025, 9, 30), 4035.0)]))
    monkeypatch.setattr(svc, "_fetch_xuantian_capacity_rows", fetcher)
    monkeypatch.setattr(
        svc.repo,
        "get_ingest_state",
        AsyncMock(return_value=_state("h1", datetime.now(UTC) + timedelta(days=30))),
    )
    out = await _gated_xuantian_fetch(_mock_db(), PIG_INDUSTRY, force=False)
    assert out == ([], "not_due", {}, [])
    fetcher.assert_not_called()  # 门内连请求都不发


async def test_l1_force_bypasses_schedule_gate(monkeypatch):
    # 同一未来 next_due_at + force=True → 放行抓取（此处进 L2 内容门短路 unchanged）
    parsed = _pad([_row(date(2025, 9, 30), 4035.0)])
    fetcher = AsyncMock(return_value=parsed)
    monkeypatch.setattr(svc, "_fetch_xuantian_capacity_rows", fetcher)
    state = _state(canonical_content_hash(parsed), datetime.now(UTC) + timedelta(days=30))
    monkeypatch.setattr(svc.repo, "get_ingest_state", AsyncMock(return_value=state))
    touch = AsyncMock()
    monkeypatch.setattr(svc.repo, "touch_ingest_state", touch)
    out = await _gated_xuantian_fetch(_mock_db(), PIG_INDUSTRY, force=True)
    assert out == ([], "unchanged", {}, [])
    fetcher.assert_called_once()  # force 绕过了 L1，请求确实发出
    touch.assert_awaited_once()


async def test_empty_diff_with_changed_hash_still_arms_state(monkeypatch):
    # MF-1 回归：L2 哈希不匹配但 L3 零行可写（DB restore / 哈希被清 / 仅 orphaned
    # 响应）→ 状态仍须武装（新哈希 + next_due_at），否则调度门永不生效、天天重抓
    parsed = _pad(
        [
            _row(date(2025, 6, 30), 4043.0),
            _row(date(2025, 9, 30), 4035.0),
        ]
    )
    new_hash = canonical_content_hash(parsed)
    assert new_hash != "stale-hash"  # 前置：L2 必然失配（进 L3）

    prior_success = datetime.now(UTC) - timedelta(days=40)
    monkeypatch.setattr(svc, "_fetch_xuantian_capacity_rows", AsyncMock(return_value=parsed))
    # next_due_at 已过期（相对真实 now）→ L1 放行；库内行与响应完全一致 → to_write 为空
    expired = datetime.now(UTC) - timedelta(seconds=1)
    monkeypatch.setattr(
        svc.repo,
        "get_ingest_state",
        AsyncMock(return_value=_state("stale-hash", expired, prior_success)),
    )
    existing = [_orm_row(r) for r in parsed]
    monkeypatch.setattr(svc.repo, "list_metric_rows", AsyncMock(return_value=existing))
    upsert = AsyncMock()
    monkeypatch.setattr(svc.repo, "upsert_ingest_state", upsert)

    t0 = datetime.now(UTC)
    out = await _gated_xuantian_fetch(_mock_db(), PIG_INDUSTRY, force=False)
    t1 = datetime.now(UTC)
    rows, gating, stats, revisions = out

    assert rows == [] and gating == "incremental" and revisions == []
    assert stats == {"new": 0, "changed": 0, "unchanged": 2, "orphaned": 0}

    # 状态被武装：新哈希落库 + next_due_at 按节奏表推进（30 天内不再重抓）
    upsert.assert_awaited_once()
    payload = upsert.await_args.args[1]
    assert payload["content_hash"] == new_hash
    assert next_due("xuantian", t0) <= payload["next_due_at"] <= next_due("xuantian", t1)
    # 语义保持：last_success_at = "最近一次有写入的成功抓取"，空写轮保留原值
    assert payload["last_success_at"] == prior_success
    assert payload["last_period"] == date(2025, 9, 30)
