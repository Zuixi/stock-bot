"""E2E: 行业投研工作台全链路（pytest -m e2e）。

需要 docker compose 栈在运行（api + worker + rabbitmq + postgres + redis）：
    API_BASE_URL=http://localhost:8000 uv run pytest tests/test_industry_e2e.py -m e2e
离线环境用 `-m "not e2e"` 跳过本文件。

覆盖：任务生命周期（先发后提交竞态回归）、latest 频率裁决、dashboard 契约、
history limit/频率覆写（月末双频共存）、batch 导入白名单、ingest 幂等、前端烟雾。
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import date

import httpx
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.e2e

FRONTEND_BASE_URL = os.environ.get(
    "FRONTEND_BASE_URL", "http://localhost:3000"
)
TASK_POLL_TIMEOUT = 30.0
PHASES = {"prosperity", "recession", "depression", "recovery"}
SIGNALS = {"买入", "卖出", "关注", "空仓"}


async def _trigger_industry_ingest(client: AsyncClient, payload: dict) -> dict:
    """POST 触发 → 轮询到终态 → 返回 completed 任务。"""
    resp = await client.post("/api/v1/tasks/fetch-industry-metrics", json=payload)
    assert resp.status_code == 202, resp.text
    task_id = resp.json()["id"]

    deadline = time.monotonic() + TASK_POLL_TIMEOUT
    task = None
    while time.monotonic() < deadline:
        task = (await client.get(f"/api/v1/tasks/{task_id}")).json()
        if task["status"] in ("completed", "failed", "cancelled"):
            break
        await asyncio.sleep(0.5)
    assert task is not None and task["status"] == "completed", (
        f"ingest task {task_id} stuck at {task['status'] if task else 'no-response'} — "
        "先发后提交竞态回归或 worker 未消费"
    )
    return task


# ── 任务生命周期（竞态回归） ─────────────────────────────────────────

async def test_ingest_task_lifecycle_completes(client: AsyncClient):
    """单次触发必须走完 pending → running → completed，不得卡在 pending。"""
    task = await _trigger_industry_ingest(
        client, {"industry_key": "pig", "source": "mock"}
    )
    assert task["finished_at"] is not None
    result = task["result"]
    assert result["source"] == "mock"
    assert result["upserted"] > 0
    assert "derived_upserted" in result and "purged" in result


async def test_back_to_back_triggers_all_complete(client: AsyncClient):
    """连续触发 3 次（无间隔）都必须 completed —— 竞态在即时派发下最易复现。"""
    resp_ids = []
    for _ in range(3):
        r = await client.post(
            "/api/v1/tasks/fetch-industry-metrics",
            json={"industry_key": "pig", "source": "mock", "months": 6},
        )
        assert r.status_code == 202, r.text
        resp_ids.append(r.json()["id"])

    for task_id in resp_ids:
        deadline = time.monotonic() + TASK_POLL_TIMEOUT
        status = None
        while time.monotonic() < deadline:
            status = (await client.get(f"/api/v1/tasks/{task_id}")).json()["status"]
            if status in ("completed", "failed", "cancelled"):
                break
            await asyncio.sleep(0.5)
        assert status == "completed", f"task {task_id} ended as {status}"


# ── 读端点契约 ───────────────────────────────────────────────────────

async def test_latest_prefers_registry_freq_and_recent_period(client: AsyncClient):
    resp = await client.get("/api/v1/industries/pig/metrics/latest")
    assert resp.status_code == 200
    by_key = {m["metric_key"]: m for m in resp.json()}

    hog = by_key["hog_price"]
    assert hog["freq"] == "daily"          # 注册频率获胜，未来月末的 monthly 行不得胜出
    assert hog["period"] <= date.today().isoformat()
    assert isinstance(hog["value"], float)
    assert hog["delta"] is not None and hog["delta"]["label"] == "日环比"

    sow = by_key["sow_inventory"]
    assert sow["freq"] == "monthly" and sow["delta"]["label"] == "月环比"

    ratio = by_key["hog_corn_ratio"]
    assert ratio["warn"] in ("一级预警", "二级预警", "正常", "过度上涨")


async def test_dashboard_contract(client: AsyncClient):
    resp = await client.get("/api/v1/industries/pig/dashboard")
    assert resp.status_code == 200
    d = resp.json()

    assert d["data_source"] in ("mock", "akshare")
    assert d["cycle"]["phase"] in PHASES
    assert len(d["cycle"]["phases"]) == 4
    assert d["signal"]["signal_type"] in SIGNALS
    assert sum(p["pct"] for p in d["signal"]["positions"]) == 100

    price_vs_cost = d["trends"]["price_vs_cost"]
    assert len(price_vs_cost["periods"]) >= 12
    assert set(price_vs_cost["series"]) >= {"生猪均价", "行业平均完全成本"}

    ref = d["trends"]["sow_inventory"]["reference"]
    assert ref is not None and ref["value"] > 0
    assert ref["effective_from"] <= date.today().isoformat()  # 政策锚点按生效日期切换

    assert d["strip"], "综合指标带不得为空"
    assert any(s["effective_date"] for s in d["signal_history"]), "信号历史不得为空"


async def test_history_limit_and_month_end_dual_freq(client: AsyncClient):
    resp = await client.get(
        "/api/v1/industries/pig/metrics/hog_price/history?limit=5"
    )
    assert resp.status_code == 200
    body = resp.json()
    points = body["points"]
    assert len(points) == 5
    assert [p["period"] for p in points] == sorted(p["period"] for p in points)

    # 频率覆写：日度指标存在月度 rollup 行（月末双频共存 = freq 约束修复的直接证据）
    monthly = (
        await client.get(
            "/api/v1/industries/pig/metrics/hog_price/history?limit=500&freq=monthly"
        )
    ).json()["points"]
    assert len(monthly) >= 12
    assert all(p["freq"] == "monthly" for p in monthly)
    assert all(p["period"][8:10] >= "28" for p in monthly)  # period 均为月末


# ── 导入白名单 ───────────────────────────────────────────────────────

async def test_batch_import_whitelist(client: AsyncClient):
    period = date.today().replace(day=1).isoformat()  # 本月 1 号，避开月末双频样本
    resp = await client.post(
        "/api/v1/industries/pig/metrics/batch",
        json={
            "items": [
                {
                    "metric_key": "industry_cost_avg",
                    "period": period,
                    "value": 13.45,
                    "source": "manual",
                },
                {
                    "metric_key": "hog_price",
                    "period": period,
                    "value": 16.0,
                    "source": "akshare_soozhu",  # 伪造采集源，必须拒绝
                },
            ],
            "recompute_derived": False,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["upserted"] >= 1
    assert body["skipped_invalid_source"] == ["hog_price:akshare_soozhu"]
    assert "derived_upserted" in body


# ── ingest 幂等 ─────────────────────────────────────────────────────

async def test_ingest_idempotent(client: AsyncClient):
    async def _daily_count() -> int:
        points = (
            await client.get(
                "/api/v1/industries/pig/metrics/hog_price/history?limit=1000&freq=daily"
            )
        ).json()["points"]
        return len(points)

    before = await _daily_count()
    await _trigger_industry_ingest(
        client, {"industry_key": "pig", "source": "mock"}
    )
    after = await _daily_count()
    assert after == before, "重跑 ingest 不得增删数据点"


# ── 前端烟雾（栈未含 frontend 时跳过） ──────────────────────────────

async def test_frontend_serves_and_proxies_api():
    try:
        async with AsyncClient(base_url=FRONTEND_BASE_URL, timeout=10.0) as fe:
            page = await fe.get("/")
            api = await fe.get("/api/v1/industries")
    except httpx.ConnectError:
        pytest.skip(f"frontend not reachable at {FRONTEND_BASE_URL}")
    assert page.status_code == 200
    assert api.status_code == 200 and isinstance(api.json(), list)
