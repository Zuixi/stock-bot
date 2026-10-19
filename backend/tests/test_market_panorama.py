"""A股全景读面：结构状态 / 排名变化 / 缺失不补 0 / 端点参数。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_cache
from app.main import app
from app.services import market_panorama_service as mps


def _breadth(d: date, up: int, down: int, flat: int, **kw):
    return SimpleNamespace(
        trade_date=d,
        up_count=up,
        down_count=down,
        flat_count=flat,
        limit_up_count=kw.get("limit_up", 10),
        limit_down_count=kw.get("limit_down", 2),
        median_pct=kw.get("median", 0.2),
        mean_pct=kw.get("mean", 0.3),
        amount=kw.get("amount", 1000.0),
    )


def _industry(d: date, code: str, name: str, pct: float, amount: float, up=0, down=0):
    return SimpleNamespace(
        trade_date=d,
        industry_code=code,
        industry_name=name,
        pct_chg=pct,
        up_count=up,
        down_count=down,
        flat_count=0,
        limit_up_count=0,
        amount=amount,
        breadth=0.5,
        attribution="current_sw_snapshot",
    )


def _structure(d: date, top3: float, hhi: float, disp: float):
    return SimpleNamespace(trade_date=d, top3_share=top3, hhi=hhi, dispersion=disp)


@pytest.mark.parametrize(
    ("up_ratio", "top3", "median", "expected"),
    [
        (0.7, 0.3, 1.0, "broad_rally"),
        (0.7, 0.3, -1.0, "structured"),  # 广度够但中位数为负 → 不是普涨
        (0.7, 0.5, 1.0, "concentration"),
        (0.3, 0.3, -1.0, "broad_decline"),
        (0.3, 0.3, 0.5, "structured"),  # 上涨占比低但中位数为正 → 分化
        (0.5, 0.3, 0.0, "structured"),
        (None, 0.3, 0.0, "structured"),
        (0.7, None, 0.0, "structured"),
    ],
)
def test_structure_state_boundaries(up_ratio, top3, median, expected):
    assert mps.structure_state(up_ratio=up_ratio, top3_share=top3, median_pct=median) == expected


def test_payload_breadth_rank_and_flow() -> None:
    d1, d2 = date(2026, 10, 15), date(2026, 10, 16)
    breadth = [
        _breadth(d1, 1000, 2000, 500),
        _breadth(d2, 2105, 2125, 1333),
    ]
    industries = [
        # 昨日：A 第一、B 第二
        _industry(d1, "A", "行业A", 3.0, 1000.0),
        _industry(d1, "B", "行业B", 2.0, 900.0),
        # 今日：B 第一、A 第二 ⇒ A rank_delta=-1
        _industry(d2, "A", "行业A", 1.5, 800.0),
        _industry(d2, "B", "行业B", 3.0, 1000.0),
    ]
    payload = mps.build_panorama_payload(
        breadth_rows=breadth,
        bucket_rows=[SimpleNamespace(bucket="涨停", count=63)],
        structure_rows=[_structure(d2, 0.34, 0.07, 1.08)],
        industry_rows=industries,
        flow_rows=[
            SimpleNamespace(
                board_code="BK1",
                board_name="印制电路板",
                pct_change=6.0,
                main_net_inflow=2_000.0,
                amount=100_000.0,
            ),
            SimpleNamespace(
                board_code="BK2",
                board_name="银行",
                pct_change=-1.0,
                main_net_inflow=-500.0,
                amount=50_000.0,
            ),
        ],
        flow_as_of=d1,
        flow_available_from=d1,
        expected_as_of=d2,
        quality="complete",
        days=60,
    )
    assert payload["as_of"] == d2
    assert payload["stale"] is False
    assert payload["breadth"]["latest"]["up_count"] == 2105
    assert payload["breadth"]["latest"]["up_ratio"] == pytest.approx(0.3784, abs=1e-4)
    assert payload["structure"]["current"]["state"] == "structured"
    ranks = {i["industry_code"]: i["rank_delta"] for i in payload["industries"]}
    assert ranks == {"B": 1, "A": -1}
    assert payload["flows"]["top_in"][0]["board_code"] == "BK1"
    assert payload["flows"]["top_out"][0]["board_code"] == "BK2"


def test_payload_without_flow_is_null_not_zero() -> None:
    payload = mps.build_panorama_payload(
        breadth_rows=[_breadth(date(2026, 10, 16), 1, 1, 1)],
        bucket_rows=[],
        structure_rows=[],
        industry_rows=[],
        flow_rows=[],
        flow_as_of=None,
        flow_available_from=None,
        expected_as_of=date(2026, 10, 16),
        quality="complete",
        days=20,
    )
    assert payload["flows"] is None
    assert payload["industries"] == []
    assert payload["structure"]["current"]["state"] == "structured"


def test_payload_industries_are_latest_only_not_full_window() -> None:
    """响应只带最新 31 行行业，不是 31×days —— 这是 <100ms 与 99KB 上限的结构保证。"""
    d1, d2 = date(2026, 10, 15), date(2026, 10, 16)
    rows = []
    for d, pct in ((d1, 1.0), (d2, 2.0)):
        for i in range(31):
            rows.append(_industry(d, f"{i:02d}", f"行业{i}", pct, 1000.0 + i))
    payload = mps.build_panorama_payload(
        breadth_rows=[_breadth(d1, 1, 1, 1), _breadth(d2, 2, 2, 2)],
        bucket_rows=[],
        structure_rows=[_structure(d2, 0.3, 0.05, 1.0)],
        industry_rows=rows,
        flow_rows=[],
        flow_as_of=None,
        flow_available_from=None,
        expected_as_of=d2,
        quality="complete",
        days=250,
    )
    assert len(payload["industries"]) == 31
    assert all(i["rank"] >= 1 for i in payload["industries"])


@pytest.mark.asyncio
async def test_panorama_endpoint_ok_and_validation(monkeypatch) -> None:
    async def fake_get(cache, days):
        return mps.build_panorama_payload(
            breadth_rows=[_breadth(date(2026, 10, 16), 10, 20, 5)],
            bucket_rows=[],
            structure_rows=[],
            industry_rows=[],
            flow_rows=[],
            flow_as_of=None,
            flow_available_from=None,
            expected_as_of=date(2026, 10, 16),
            quality="complete",
            days=days,
        )

    monkeypatch.setattr(mps, "get_panorama", fake_get)
    app.dependency_overrides[get_cache] = lambda: None
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            ok = await client.get("/api/v1/market/panorama?days=60")
            assert ok.status_code == 200
            body = ok.json()
            assert body["window"]["days"] == 60
            assert body["window"]["from"] == "2026-10-16"
            assert body["breadth"]["latest"]["up_count"] == 10
            bad = await client.get("/api/v1/market/panorama?days=4")
            assert bad.status_code == 422
    finally:
        app.dependency_overrides.clear()
