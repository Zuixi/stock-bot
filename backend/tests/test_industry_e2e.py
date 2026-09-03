"""E2E: 行业投研工作台全链路（pytest -m e2e）。

需要 docker compose 栈在运行（api + worker + rabbitmq + postgres + redis）：
    API_BASE_URL=http://localhost:8000 uv run pytest tests/test_industry_e2e.py -m e2e
离线环境用 `-m "not e2e"` 跳过本文件。

覆盖：任务生命周期（先发后提交竞态回归）、latest 频率裁决、dashboard 契约、
history limit/频率覆写（月末双频共存）、batch 导入白名单、ingest 幂等、
知识库 seed 聚合（P6）、泛化验证（broiler 第二行业，P6 收尾）、前端烟雾。
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
    assert d["data_quality"]["status"] in {"healthy", "degraded", "unavailable", "demo"}
    assert isinstance(d["signal_is_stale"], bool)
    assert "signal_events" in d
    assert "verification_summary" in d
    if d["data_quality"]["signal_ready"]:
        assert d["cycle"]["phase"] in PHASES
        assert len(d["cycle"]["phases"]) == 4
        assert d["signal"]["signal_type"] in SIGNALS
        assert sum(p["pct"] for p in d["signal"]["positions"]) == 100
        assert any(s["effective_date"] for s in d["signal_history"])
    else:
        assert d["signal_is_stale"] is True
        assert d["cycle"] is None
        assert d["signal"] is None

    price_vs_cost = d["trends"]["price_vs_cost"]
    # 真实源（搜猪网当年序列）月度窗口短于 mock 37 个月，取 ≥6 保证趋势图仍有实质历史
    assert len(price_vs_cost["periods"]) >= 6
    assert set(price_vs_cost["series"]) >= {"生猪均价", "行业平均完全成本"}

    ref = d["trends"]["sow_inventory"]["reference"]
    assert ref is not None and ref["value"] > 0
    assert ref["effective_from"] <= date.today().isoformat()  # 政策锚点按生效日期切换

    assert d["strip"], "综合指标带不得为空"
    for event in d["signal_events"]:
        for evaluation in event["evaluations"]:
            assert {"horizon_days", "status", "target_date", "score", "criteria_results"} <= set(
                evaluation
            )


async def test_signal_events_endpoint_is_read_only_ordered_and_validates_limit(
    client: AsyncClient,
):
    before = await client.get("/api/v1/industries/pig/signal-events?limit=20")
    assert before.status_code == 200
    events = before.json()
    event_dates = [item["event_date"] for item in events]
    assert event_dates == sorted(event_dates, reverse=True)

    repeated = await client.get("/api/v1/industries/pig/signal-events?limit=20")
    assert repeated.status_code == 200
    assert repeated.json() == events

    assert (await client.get("/api/v1/industries/nope/signal-events")).status_code == 404
    assert (await client.get("/api/v1/industries/pig/signal-events?limit=0")).status_code == 422
    assert (await client.get("/api/v1/industries/pig/signal-events?limit=101")).status_code == 422


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


# ── 标的分析（P5）：成分股对比 + 头均市值派生 ────────────────────────

async def _pig_member_stocks(client: AsyncClient) -> list[dict]:
    """动态解析生猪养殖 L3 成分股（sw_l3_codes → tree 定位路径 → enriched 含 id/total_mv）。"""
    industries = (await client.get("/api/v1/industries")).json()
    l3_codes = set(next(i["sw_l3_codes"] for i in industries if i["key"] == "pig"))
    tree = (await client.get("/api/v1/market/sw-industry/tree")).json()

    def find_path(nodes):
        for l1 in nodes:
            for l2 in l1.get("children") or []:
                for l3 in l2.get("children") or []:
                    if l3["code"] in l3_codes:
                        return l1["code"], l2["code"], l3["code"]
        return None

    path = find_path(tree)
    assert path is not None, f"tree 中未找到 {l3_codes}（registry sw_l3_codes 与库内分类不一致？）"
    l1, l2, l3 = path
    stocks = (
        await client.get(f"/api/v1/market/sw-industry/{l1}/{l2}/{l3}/stocks/enriched")
    ).json()
    assert stocks, "生猪养殖成分股不得为空"
    return stocks


async def test_companies_endpoint_with_company_metrics(client: AsyncClient):
    """导入两只真实猪股公司数据 → companies 表含头均市值派生 + registry 列下发。"""
    stocks = await _pig_member_stocks(client)
    picks = sorted(
        (s for s in stocks if s.get("total_mv")),  # daily_basic 有市值才可派生头均市值
        key=lambda s: s["total_mv"], reverse=True,
    )[:2]
    assert len(picks) == 2, "需要 ≥2 只有 daily_basic 市值的成分股（牧原/温氏等）"

    # 每股 6 个月出栏量 + 1 条完全成本（人工通道携带 stock_id=stocks.id）
    hog_periods = _last_n_month_ends(6)
    cost_period = next(
        (d for d in reversed(_last_n_month_ends(5)) if d.month in (3, 6, 9, 12)),
        hog_periods[0],  # 任意连续 ≥4 个月必含一个季末月，兜底仅为防御
    )
    items = []
    for s in picks:
        for period in hog_periods:
            items.append({
                "metric_key": "company.hogs_sold_monthly",
                "period": period.isoformat(),
                "value": 500.0,
                "stock_id": s["id"],
                "source": "manual",
            })
        items.append({
            "metric_key": "company.cost_complete",
            "period": cost_period.isoformat(),
            "value": 13.2,
            "stock_id": s["id"],
            "source": "manual",
        })
    resp = await client.post(
        "/api/v1/industries/pig/metrics/batch",
        json={"items": items, "recompute_derived": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["upserted"] >= 14  # 2×(6+1)
    assert body["skipped_unknown_metric"] == []
    assert body["derived_upserted"] >= 1  # 头均市值派生行已落表

    resp = await client.get("/api/v1/industries/pig/companies")
    assert resp.status_code == 200
    payload = resp.json()
    labels = [c["label"] for c in payload["columns"]]
    assert labels[:6] == ["代码", "名称", "最新价", "总市值(亿)", "PE(TTM)", "PB"]
    assert {"完全成本", "头均市值", "月度出栏量"} <= set(labels)

    rows = {r["symbol"]: r for r in payload["rows"]}
    assert len(rows) >= 2
    for s in picks:
        row = rows[s["symbol"]]
        assert row["name"] == s["name"]
        assert row["has_company_data"] is True
        # 6 个月历史 → 年化=500×12=6000 万头；头均市值 = total_mv(万)/6000 > 0（单位相消为元/头）
        assert row["metrics"]["mcap_per_head"] > 0
        assert row["metrics"]["company.cost_complete"] == 13.2
        assert row["total_mv_yi"] is not None and row["total_mv_yi"] > 0


async def test_companies_unknown_industry_404(client: AsyncClient):
    resp = await client.get("/api/v1/industries/nope/companies")
    assert resp.status_code == 404


def _last_n_month_ends(n: int, today: date | None = None) -> list[date]:
    """最近 n 个**已完整**自然月的月末（升序，不含当月）。"""
    import calendar

    today = today or date.today()
    y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    ends = []
    for i in range(n - 1, -1, -1):
        yy, mm = y + (m - 1 - i) // 12, (m - 1 - i) % 12 + 1
        ends.append(date(yy, mm, calendar.monthrange(yy, mm)[1]))
    return ends


# ── 行情面（P5）：ETF/可转债日线管道 ────────────────────────────────

async def _trigger_securities_fetch(client: AsyncClient) -> dict:
    """POST fetch-securities → 轮询到终态 → 返回任务 dict（TuShare 实拉）。"""
    resp = await client.post("/api/v1/tasks/fetch-securities", json={"industry_key": "pig"})
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
        f"securities task {task_id} stuck at {task['status'] if task else 'no-response'}"
    )
    return task


async def test_securities_fetch_and_series(client: AsyncClient):
    """fetch-securities 任务（TuShare fund/cb daily）→ securities 端点按代码分组出序列。"""
    task = await _trigger_securities_fetch(client)
    result = task["result"]
    assert result["etf_codes"], "registry etf_codes 不得为空"
    assert result["etf_upserted"] > 0

    resp = await client.get("/api/v1/industries/pig/securities?type=etf&limit=90")
    assert resp.status_code == 200, resp.text
    etf = resp.json()
    assert etf["type"] == "etf"
    with_data = [c for c in etf["codes"] if c["latest"]]
    assert with_data, "ETF 序列不得为空"
    code = with_data[0]
    assert len(code["series"]) >= 30  # 一年回补 ≥ 30 个交易日
    assert code["latest"]["close"] > 0
    # 涨跌幅 = close vs pre_close（后端算好，供表格直接渲染）
    if code["latest"]["pre_close"]:
        expected = (
            (code["latest"]["close"] - code["latest"]["pre_close"])
            / code["latest"]["pre_close"]
            * 100
        )
        assert abs(code["change_pct"] - round(expected, 2)) < 0.01

    # 可转债：registry 有在市转债则断言序列；无则断言空 codes 形状（源无关分支）
    from app.services.industry_registry import PIG_INDUSTRY

    resp = await client.get("/api/v1/industries/pig/securities?type=cb&limit=90")
    assert resp.status_code == 200
    cb = resp.json()
    if PIG_INDUSTRY.cb_codes:
        cb_with_data = [c for c in cb["codes"] if c["latest"]]
        assert cb_with_data and len(cb_with_data[0]["series"]) >= 30
    else:
        assert cb["codes"] == []


async def test_securities_unknown_industry_404_and_bad_type_422(client: AsyncClient):
    assert (await client.get("/api/v1/industries/nope/securities?type=etf")).status_code == 404
    assert (await client.get("/api/v1/industries/pig/securities?type=bond")).status_code == 422


# ── 知识库（P6）：机构图谱 / 权威性原则 / 思维导图 ────────────────────


async def test_knowledge_endpoint_seed_content(client: AsyncClient):
    """迁移内 seed → knowledge 聚合：org ≥12 覆盖四分组、原则 ≥4 条、思维导图 ≥4 分支。"""
    resp = await client.get("/api/v1/industries/pig/knowledge")
    assert resp.status_code == 200
    payload = resp.json()

    orgs = payload["org"]
    assert len(orgs) >= 12
    assert {o["group"] for o in orgs} == {"官方", "协会", "数据平台", "期货"}
    assert len({o["name"] for o in orgs}) == len(orgs)  # 机构名唯一
    assert all(o["tier"] in ("official", "highfreq", "calc", "manual") for o in orgs)

    principle = payload["principle"]
    assert principle is not None
    assert principle["title"] == "数据权威性使用原则"
    assert len(principle["items"]) >= 4

    mindmap = payload["mindmap"]
    assert mindmap and mindmap["name"]
    assert len(mindmap["children"]) >= 4  # 供给/需求/成本/政策/金融


async def test_knowledge_unknown_industry_404(client: AsyncClient):
    assert (await client.get("/api/v1/industries/nope/knowledge")).status_code == 404


# ── 泛化验证（P6 收尾）：broiler 第二行业零新页面端到端 ────────────────


async def _l3_stocks(client: AsyncClient, industry_key: str) -> list[dict]:
    """按 registry sw_l3_codes 动态解析行业成分股（tree 定位 → enriched 端点）。"""
    industries = (await client.get("/api/v1/industries")).json()
    l3_codes = set(next(i["sw_l3_codes"] for i in industries if i["key"] == industry_key))
    tree = (await client.get("/api/v1/market/sw-industry/tree")).json()

    def find_path(nodes):
        for l1 in nodes:
            for l2 in l1.get("children") or []:
                for l3 in l2.get("children") or []:
                    if l3["code"] in l3_codes:
                        return l1["code"], l2["code"], l3["code"]
        return None

    path = find_path(tree)
    assert path is not None, f"tree 中未找到 {l3_codes}"
    l1, l2, l3 = path
    stocks = (
        await client.get(f"/api/v1/market/sw-industry/{l1}/{l2}/{l3}/stocks/enriched")
    ).json()
    assert stocks, f"{industry_key} 成分股不得为空"
    return stocks


async def test_broiler_mock_ingest_generalization(client: AsyncClient):
    """fetch-industry-metrics {broiler} → completed；列表双行业 + 信号字段 + dashboard 可读。"""
    task = await _trigger_industry_ingest(
        client, {"industry_key": "broiler", "source": "mock"}
    )
    result = task["result"]
    assert result["upserted"] >= 90  # 2 指标 × 45 天日度序列

    industries = (await client.get("/api/v1/industries")).json()
    by_key = {i["key"]: i for i in industries}
    assert set(by_key) >= {"pig", "broiler"}

    broiler = by_key["broiler"]
    assert broiler["metric_with_data"] >= 2
    assert broiler["phase"] in PHASES  # ingest 后信号已评估落表
    assert broiler["signal_type"] in SIGNALS
    assert broiler["signal_date"] is not None

    pig = by_key["pig"]
    pig_dashboard = (await client.get("/api/v1/industries/pig/dashboard")).json()
    if pig_dashboard["data_quality"]["signal_ready"]:
        assert pig["phase"] in PHASES and pig["signal_type"] in SIGNALS
    else:
        assert pig["phase"] is None and pig["signal_type"] is None
        assert pig_dashboard["signal_is_stale"] is True

    resp = await client.get("/api/v1/industries/broiler/dashboard")
    assert resp.status_code == 200
    d = resp.json()
    assert d["cycle"]["phase"] in PHASES
    assert d["signal"]["signal_type"] in SIGNALS
    assert any(m["metric_key"] == "chick_price" and m["value"] for m in d["strip"])
    assert any(m["metric_key"] == "broiler_price" and m["value"] for m in d["strip"])


async def test_broiler_sw_codes_disjoint_pig_companies_unaffected(client: AsyncClient):
    """broiler 申万代码与 pig 不相交：pig companies 端点不受第二行业污染。"""
    pig_syms = {s["symbol"] for s in await _l3_stocks(client, "pig")}
    broiler_syms = {s["symbol"] for s in await _l3_stocks(client, "broiler")}
    assert pig_syms and broiler_syms
    assert pig_syms.isdisjoint(broiler_syms), "两行业 sw_l3_codes 必须不相交"

    rows = (await client.get("/api/v1/industries/pig/companies")).json()["rows"]
    row_syms = {r["symbol"] for r in rows}
    assert pig_syms <= row_syms
    assert row_syms.isdisjoint(broiler_syms)


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
