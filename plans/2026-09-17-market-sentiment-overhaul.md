# 市场数据可信度与实时化改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让行情中心的数据不再被单条脏行/缺列打坏、交易时段能看到盘中情绪、热门板块可下钻，且每张卡都能自证"数据截至何时、什么口径"。

**Architecture:** 后端新增一个**日级完整性判据**（`market_day_service`）作为"最新交易日"的唯一来源，8 个按日聚合端点全部改走它并返回 `as_of_quality`；当日全市场派生数据只从**一次 Redis 快照**加载（消灭 5 处重复 597ms SQL）；情绪面引入**盘中（东财）/收盘（本地自算）双口径**并配 5 分钟分时快照表；热门板块换成东财真实板块体系并支持成分股下钻；前端用统一的 `useMarketPolling` + as_of/口径徽标收口刷新与标注。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 (async) + PostgreSQL + Redis + APScheduler + Alembic（backend，uv 管理）；React 18 + TypeScript + Vite + Ant Design 5 + TanStack Query（frontend，npm 管理）；Playwright（e2e）。

**Spec:** `docs/design/market-data-trust-and-realtime.md`

## Global Constraints

- 后端命令一律在 `backend/` 下用 `uv run`；lint/类型检查：`uv run --extra dev ruff check app/ tests/`、`uv run --extra dev ruff format --check app/ tests/`、`uv run --extra dev mypy app`。CI 的 lint 范围是 `app/ tests/`（不是仓库根）。
- 前端命令在 `frontend/` 下用 `npm`；类型门禁是 `npx tsc --noEmit` + `npm run check:design`（`npm run lint` 在本仓库**不可用**——eslint 配置不在 main 上）。
- 测试默认排除 e2e 与 bench（`pyproject.toml` 的 addopts）；本地跑测试必须按 CI 口径清空凭证：`TUSHARE_TOKEN= uv run pytest`。
- 单测不得依赖真实外网/真实凭证：外部接口全部用夹具或 seam 替换（参考 `backend/tests/test_daily_ingest_pct_chg.py`、`test_universe_lag_and_quote_date.py`）。
- 外部数据源：**行情快照/板块/涨停池用东方财富**（`app/core/providers/eastmoney_client.py`，push2delay 域）；北向资金上游 TuShare `moneyflow_hsgt` 已停更，**只标注不改源**。
- 每个任务必须：先写失败测试 → 确认失败 → 最小实现 → 测试通过 → **提交**。提交信息用中文，说明"做了什么 / 为什么 / 涉及模块"。
- 每个任务收尾必须同步：`docs/Changelog.md` 加一条（一句话结论）+ `docs/references/best-practices.md` 在对应分类追加一句（若该任务产生可复用教训）。
- 改动涉及路由/表名/`metric_key` 时，必须同步核对 `docs/build.md`、`docs/ARCHITECTURE.md`、`docs/design/*` 与相关 `AGENTS.md` 的交叉引用一致。
- 不新增平行端点（改既有端点语义）；不引入新的第三方依赖；不重构任务范围外的代码。
- 阶段门禁：每个 Phase 结束后必须跑 `bash scripts/self_review.sh --full` 并**全绿**才能进入下一个 Phase。

---

## 文件结构（决定任务分解）

| 文件 | 责任 |
|---|---|
| `backend/app/services/market_day_service.py` 🆕 | **日级完整性判据**唯一来源：`resolve_latest_complete_day` / `is_day_complete` / `MarketDay` |
| `backend/app/services/job_alert_service.py` 🆕 | 任务失败告警记录与读取（Redis） |
| `backend/app/services/market_snapshot_service.py` 🆕 | 当日全市场派生快照（`(stock_id, pct_chg, close, amount)`）的加载与缓存 |
| `backend/app/services/intraday_sentiment_service.py` 🆕 | 东财涨停池 → 与本地口径同构的盘中 snapshot；5 分钟分时快照采集 |
| `backend/app/services/market_service.py` ✏️ | 8 个读端点接入判据、改读快照、包对象返回 |
| `backend/app/services/market_data_service.py` ✏️ | 板块资金流回落、北向标注、share_float 分片 |
| `backend/app/services/limit_up_service.py` ✏️ | `mode=intraday` 分支、`as_of_label` |
| `backend/app/services/data_init.py` ✏️ | 覆盖任务窗口上界改上一交易日 |
| `backend/app/core/providers/eastmoney_client.py` ✏️ | 新增板块列表/成分股取数（复用既有 clist） |
| `backend/app/api/v1/market.py` / `market_data.py` ✏️ | 端点签名与响应模型 |
| `backend/app/schemas/market.py` / `reconciliation.py` ✏️ | `as_of_quality` / `failed_jobs` |
| `backend/app/migrations/versions/*` 🆕 | `market_sentiment_intraday` 表 |
| `backend/app/workers/market_data_worker.py` / `scheduler/jobs.py` ✏️ | 新采集任务 + 失败告警接线 |
| `backend/tests/test_market_day_service.py` 🆕 | 完整性判据单测 |
| `backend/tests/test_market_snapshot.py` 🆕 | 快照加载单测 |
| `backend/tests/test_intraday_sentiment.py` 🆕 | 盘中口径夹具测试 |
| `backend/tests/test_job_alerts.py` 🆕 | 失败告警单测 |
| `backend/tests/fixtures/eastmoney/*.json` 🆕 | 录制的东财响应夹具 |
| `frontend/src/features/market/hooks/useMarketPolling.ts` 🆕 | 市场状态驱动的共享轮询 |
| `frontend/src/features/market/hooks/marketStatus.ts` 🆕 | 交易日 + 交易时段判定（上海时区） |
| `frontend/src/features/market/components/BoardDrilldownDrawer.tsx` 🆕 | 板块成分股抽屉 |
| `frontend/src/features/market/components/SentimentIntradayChart.tsx` 🆕 | 分时情绪曲线 |
| `frontend/src/shared/api/market.ts` / `marketData.ts` / `limitUp.ts` ✏️ | 新响应形状与新端点 |
| `frontend/src/pages/market/index.tsx`、`market-hot-sectors/index.tsx` ✏️ | 双口径切换、卡片接线 |
| `frontend/e2e/marketDataFace.spec.ts` / `limitUpSentiment.spec.ts` ✏️ | 新增断言 |
| `frontend/e2e/marketTrust.spec.ts` 🆕 | 标注入/下钻/键盘可达/暗色 |

---

# Phase 0 · 数据可信度（P0）

> 目标：任何按日聚合的读路径不再被单条脏行/缺列打坏；失败不再静默；读路径不再"漏一天空一天"。

### Task 1: 日级完整性判据 `market_day_service`

**Files:**
- Create: `backend/app/services/market_day_service.py`
- Test: `backend/tests/test_market_day_service.py`

**Interfaces:**
- Produces（后续任务全部依赖这些名字）：
  - `MarketDay`（frozen dataclass）：`day: date`、`quality: Literal["complete","partial","fallback"]`、`reason: str | None`、`rows: int`、`universe: int`、`pct_chg_ratio: float`、`limits_present: bool`
  - `def is_day_complete(rows: int, universe: int, pct_chg_ratio: float, limits_present: bool) -> bool`
  - `async def resolve_latest_complete_day(db, *, cache=None) -> MarketDay | None`（无任何行情时返回 `None`）
  - 常量：`MIN_ROW_RATIO = 0.9`、`MIN_PCT_CHG_RATIO = 0.99`、`FALLBACK_LOOKBACK_DAYS = 5`、`CACHE_KEY = "market:day:latest_complete"`、`CACHE_TTL = 60`
- Consumes: `app.repositories.quote_repo`（`get_latest_trade_date`、交易日列表）、`app.repositories.stock_repo`（在市数量）、`limit_up_repo.has_price_limits`

- [ ] **Step 1: 写失败测试**

```python
"""日级完整性判据：行数 + pct_chg 非空率 + 涨跌停价存在。"""
from datetime import date

from app.services import market_day_service as mds


def test_complete_only_when_all_three_conditions_hold() -> None:
    assert mds.is_day_complete(5485, 5513, 1.0, True) is True
    # 行数够但列全 NULL（09-14/15 的真实形态）→ 不完整
    assert mds.is_day_complete(5485, 5513, 0.0002, True) is False
    # 行数够、列满，但缺该日涨跌停价 → 不完整
    assert mds.is_day_complete(5485, 5513, 1.0, False) is False
    # 行数不足（脏行日：1 行）→ 不完整
    assert mds.is_day_complete(1, 5513, 1.0, False) is False
    # 阈值边界：0.9×5513 = 4961.7 → 4962 通过、4961 不通过
    assert mds.is_day_complete(4962, 5513, 0.99, True) is True
    assert mds.is_day_complete(4961, 5513, 0.99, True) is False


def test_universe_zero_is_not_complete() -> None:
    """空 stocks 表时不能把"0/0"判成完整。"""
    assert mds.is_day_complete(0, 0, 1.0, True) is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && TUSHARE_TOKEN= uv run pytest tests/test_market_day_service.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.market_day_service'`

- [ ] **Step 3: 最小实现**

```python
"""日级完整性判据 —— "最新交易日"决策的唯一来源。

行数够 ≠ 数据可用：实测 2026-09-14/15/16 的 daily_quotes 行数满 5485，但
pct_chg 只有 1 行非空（历史 NULL），下游所有按日聚合的端点都在用 LATERAL
逐股回看前收重算——判据看不出列坏了，于是永远不会被重拉。
同理，一条当日脏行（data_init 覆盖任务写出的未收盘数据）会把 max(trade_date)
顶到今天，让 8 个端点集体降级成 1 行，所以"最新日"必须带完整性门禁并允许回落。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from sqlalchemy import func, select

from app.core.database import async_session_factory
from app.models.quote import DailyQuote
from app.models.stock import Stock

logger = logging.getLogger(__name__)

MarketQuality = Literal["complete", "partial", "fallback"]

MIN_ROW_RATIO = 0.9
MIN_PCT_CHG_RATIO = 0.99
FALLBACK_LOOKBACK_DAYS = 5
CACHE_KEY = "market:day:latest_complete"
CACHE_TTL = 60
_INCOMPLETE_REASON = "latest_day_incomplete"


@dataclass(frozen=True)
class MarketDay:
    day: date
    quality: MarketQuality
    reason: str | None
    rows: int
    universe: int
    pct_chg_ratio: float
    limits_present: bool


def is_day_complete(rows: int, universe: int, pct_chg_ratio: float, limits_present: bool) -> bool:
    """三元组判据：行数达阈值 + pct_chg 非空率达标 + 该日涨跌停价存在。

    `universe <= 0`（stocks 表为空）时一律判不完整——避免把 0/0 的真空判成完整。
    """
    if universe <= 0:
        return False
    if rows < MIN_ROW_RATIO * universe:
        return False
    if pct_chg_ratio < MIN_PCT_CHG_RATIO:
        return False
    return bool(limits_present)
```

（同文件补齐 `_day_stats(db, day) -> tuple[int, float, bool]`、`_universe_count(db) -> int`、`resolve_latest_complete_day(...)`；`resolve` 先查缓存（命中即返回 dict→`MarketDay`），再取 `max(trade_date)` 与候选日列表，逐个判定，命中不完整则向前回看最多 `FALLBACK_LOOKBACK_DAYS` 个交易日；把结果写入缓存。缓存命中时要处理"缓存里是旧格式 dict"的情况：用 `MarketDay(**payload)` 重建，键缺失即缓存失效。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && TUSHARE_TOKEN= uv run pytest tests/test_market_day_service.py -q --no-cov`
Expected: PASS（2 passed）

- [ ] **Step 5: 用真实库验证判据对当前数据的结论**

Run:
```bash
cd backend && uv run python - <<'EOF'
import asyncio
from app.core.database import async_session_factory
from app.services import market_day_service as mds
async def main():
    async with async_session_factory() as db:
        for d in ("2026-09-17", "2026-09-16", "2026-09-15", "2026-09-14"):
            day = __import__("datetime").date.fromisoformat(d)
            rows, ratio, limits = await mds._day_stats(db, day)
            uni = await mds._universe_count(db)
            print(d, rows, round(ratio, 4), limits, "→", mds.is_day_complete(rows, uni, ratio, limits))
        print("resolved:", await mds.resolve_latest_complete_day(db))
asyncio.run(main())
EOF
```
Expected: `2026-09-17 → False`（1 行脏行）、`2026-09-16 → False`（pct_chg_ratio 极低）、其余同理；`resolved` 落到 `2026-09-11`（09-11 是最近一个 pct_chg 满覆盖且有限价的日子）。

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/market_day_service.py backend/tests/test_market_day_service.py
git commit -m "feat(market): 日级完整性判据（行数+pct_chg 非空率+限价存在）作为最新日唯一来源"
```

---

### Task 2: 8 个读端点接入判据 + `as_of_quality` + 列表端点包对象

**Files:**
- Modify: `backend/app/services/market_service.py`（`get_distribution:252`、`get_sectors:324`、`get_capital_flow:384`、`get_hot_boards:437`、`get_rankings:581`、`get_sw_performance`、`_latest_trade_date:77`、`get_latest_trade_date:118`）
- Modify: `backend/app/api/v1/market.py`（`:31 distribution`、`:47 sectors`、`:52 capital-flow`、`:57 hot-boards`、`:36 rankings`、`:103 sw-industry/performance`）
- Modify: `backend/app/schemas/market.py`（新增 `AsOfQuality` 混入 / `MarketListOut`）
- Modify: `backend/tests/test_market_contract.py`、`backend/tests/fixtures/rankings.sample.json`、`backend/tests/fixtures/swPerformance.sample.json`
- Test: `backend/tests/test_market_asof_quality.py` 🆕

**Interfaces:**
- Consumes: Task 1 的 `resolve_latest_complete_day` → `MarketDay`
- Produces:
  - 响应模型 `MarketListOut`：`{as_of: date | None, as_of_quality: str, items: list[dict]}`
  - `as_of_quality` 取值：`complete | partial | fallback`；`fallback` 时另带 `as_of_reason: str | None`
  - 端点返回形状（**破坏性变更**，前端同任务内适配）：`/market/distribution`、`/market/sectors`、`/market/capital-flow`、`/market/hot-boards` 由裸数组改为上述对象；`/market/rankings`、`/market/sw-industry/performance` 保持对象并新增 `as_of_quality`/`as_of_reason`

- [ ] **Step 1: 写失败测试**（服务层用 seam 替换判据，不打库）

```python
"""按日聚合端点必须带 as_of 质量标注，且 as_of 取自完整性判据而非 max(trade_date)。"""
from datetime import date
from typing import Any

import pytest

from app.services import market_day_service as mds
from app.services import market_service

D16 = date(2026, 9, 16)


@pytest.fixture
def _day(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _resolve(_db: Any, *, cache: Any = None) -> mds.MarketDay:
        return mds.MarketDay(D16, "fallback", "latest_day_incomplete", 5485, 5513, 1.0, True)

    monkeypatch.setattr(market_service.market_day_service, "resolve_latest_complete_day", _resolve)


async def test_distribution_carries_as_of_quality(_day: None, monkeypatch) -> None:
    async def _rows(_db: Any, _day_: date) -> list[dict]:
        return [{"range": "0~1%", "count": 3}]

    monkeypatch.setattr(market_service, "_distribution_rows", _rows)
    out = await market_service.get_distribution(None)
    assert out["as_of"] == "2026-09-16"
    assert out["as_of_quality"] == "fallback"
    assert out["as_of_reason"] == "latest_day_incomplete"
    assert out["items"] == [{"range": "0~1%", "count": 3}]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && TUSHARE_TOKEN= uv run pytest tests/test_market_asof_quality.py -q --no-cov`
Expected: FAIL — `KeyError: 'as_of'` 或 `AttributeError: module ... has no attribute 'market_day_service'`

- [ ] **Step 3: 实现**

要点（照此改，不要另起炉灶）：
1. `market_service.py` 顶部 `from app.services import market_day_service`；`_latest_trade_date(db)` 保留为薄封装但**改为 `resolve_latest_complete_day` 的 delegate**（返回 `MarketDay.day`），保证"最新日"只有一个判据来源。
2. 各 `get_*` 的返回类型由 `list[dict]` 改为 `dict[str, Any]`：`{"as_of": md.day.isoformat(), "as_of_quality": md.quality, "as_of_reason": md.reason, "items": rows}`。
3. 空数据路径（无任何行情）返回 `as_of=None, as_of_quality="partial", items=[]`，**不抛异常**。
4. `schemas/market.py` 加：

```python
class MarketListOut(BaseModel):
    """按日聚合端点的统一信封：as_of 与质量标注 + items。"""
    as_of: date | None = None
    as_of_quality: Literal["complete", "partial", "fallback"] = "partial"
    as_of_reason: str | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)
```
5. `RankingResponseOut`、`SwPerformanceResponseOut` 各加 `as_of_quality: Literal[...] = "complete"` 与 `as_of_reason: str | None = None`。
6. 更新 `backend/tests/fixtures/rankings.sample.json`、`swPerformance.sample.json` 增加 `as_of_quality` 键（`test_market_contract.py` 用 `set(body) == set(fixture)` 断言键集，夹具不同步会红）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && TUSHARE_TOKEN= uv run pytest tests/test_market_asof_quality.py -q --no-cov && TUSHARE_TOKEN= uv run pytest -q --no-cov -m "not e2e and not bench" | tail -3`
Expected: 新测试 PASS，且全量仍 PASS（274+N）

- [ ] **Step 5: 活体验证（当前脏行仍在库，正好是回归场景）**

Run:
```bash
curl -s "http://localhost/api/v1/market/distribution" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['as_of'], d['as_of_quality'], len(d['items']))"
curl -s "http://localhost/api/v1/market/hot-boards?category=industry" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['as_of'], d['as_of_quality'], len(d['items']))"
```
Expected: `as_of` 是完整日而非 09-17，`as_of_quality=fallback`，`items` 数量是完整市场量级（≠1）。
（注：容器内是旧镜像，此步在 Phase 0 门禁里用重建镜像验证；此处若 404/旧形状属预期，记录到 ledger。）

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/market_service.py backend/app/api/v1/market.py backend/app/schemas/market.py backend/tests/
git commit -m "feat(market): 按日聚合端点接入完整性判据并返回 as_of_quality（列表端点包对象）"
```

---

### Task 3: 断掉脏行来源 + 清理存量脏行

**Files:**
- Modify: `backend/app/services/data_init.py:120-135`（`_ensure_trailing_three_year_daily_quotes` 的 `end_date`）
- Create: `backend/scripts/repair_market_day.py`（一次性修复 CLI：删脏行 + 回填指定日 `pct_chg`）
- Test: `backend/tests/test_data_init_window.py` 🆕

**Interfaces:**
- Produces: `def last_completed_trading_day(today: date | None = None) -> date`（在 `data_init.py`，返回今天之前的最近一个工作日；单股覆盖任务的窗口上界）
- CLI：`uv run python scripts/repair_market_day.py --purge-incomplete-today --backfill 2026-09-14 2026-09-15`（幂等、先打印将影响的行数）

- [ ] **Step 1: 写失败测试**

```python
"""单股覆盖任务的窗口上界必须是"上一个交易日"，否则会写入当日未收盘的脏行。"""
from datetime import date

from app.services.data_init import last_completed_trading_day


def test_window_upper_bound_is_previous_weekday() -> None:
    assert last_completed_trading_day(date(2026, 9, 17)) == date(2026, 9, 16)  # 周四 → 周三
    assert last_completed_trading_day(date(2026, 9, 14)) == date(2026, 9, 11)  # 周一 → 上周五
    assert last_completed_trading_day(date(2026, 9, 13)) == date(2026, 9, 11)  # 周日 → 上周五
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && TUSHARE_TOKEN= uv run pytest tests/test_data_init_window.py -q --no-cov`
Expected: FAIL — `ImportError: cannot import name 'last_completed_trading_day'`

- [ ] **Step 3: 实现**

1. `data_init.py` 新增 `last_completed_trading_day(today=None)`（复用 `market_service.last_weekday`，再退一天）。
2. `_ensure_trailing_three_year_daily_quotes` 与 `_ensure_trailing_one_year_daily_basic` 的 `end_date` 改为它；`asof_date=today` 同步改为该值（否则 `expected_start > asof_date` 的比较会把"今天"算进期望窗口）。
3. `backend/scripts/repair_market_day.py`：`--purge-incomplete-today` 删除 `trade_date = 当日 且 行数 < 0.9×universe` 的行（打印行数并要求 `--yes` 才执行）；`--backfill D...` 对每个日期调用既有 `TuShareIngestService().ingest_daily_quotes(db, D)`（幂等）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && TUSHARE_TOKEN= uv run pytest tests/test_data_init_window.py -q --no-cov`
Expected: PASS

- [ ] **Step 5: 执行数据修复（已授权；先看影响面）**

```bash
cd backend && uv run python scripts/repair_market_day.py --purge-incomplete-today            # 仅打印
cd backend && uv run python scripts/repair_market_day.py --purge-incomplete-today --yes      # 删 09-17 那 1 行
cd backend && uv run python scripts/repair_market_day.py --backfill 2026-09-14 2026-09-15    # 回填 pct_chg
```
Expected: 09-17 行数 1 → 0；09-14/15 的 `count(pct_chg)` 从 1 → 5487。

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/data_init.py backend/scripts/repair_market_day.py backend/tests/test_data_init_window.py docs/Changelog.md
git commit -m "fix(data-init): 单股覆盖窗口上界改上一交易日（断脏行来源）+ 一次性修复 CLI"
```

---

### Task 4: `share_float` 分片 + 任务失败可观测

**Files:**
- Create: `backend/app/services/job_alert_service.py`
- Modify: `backend/app/repositories/market_data_repo.py:267`（`upsert_share_floats` 分片）、`backend/app/services/market_data_service.py:761`（`ingest_share_floats`）
- Modify: `backend/app/scheduler/jobs.py`（所有 job 的 `except Exception` 追加告警）
- Modify: `backend/app/schemas/reconciliation.py`、`backend/app/api/v1/market_data.py`（freshness 暴露 `failed_jobs`）
- Test: `backend/tests/test_job_alerts.py` 🆕、`backend/tests/test_share_float_batching.py` 🆕

**Interfaces:**
- Produces:
  - `async def record_job_failure(job_id: str, error: str, *, cache=None) -> None`
  - `async def list_job_failures(limit: int = 20) -> list[dict[str, Any]]` → `[{"job_id", "error", "at"}]`
  - Redis：`HSET job:failures <job_id> json` + `EXPIRE job:failures 604800`
  - `DataFreshnessOut.failed_jobs: list[JobFailureOut]`
  - `UPSERT_CHUNK = 500`（`market_data_repo`）

- [ ] **Step 1: 写失败测试**

```python
"""批量 upsert 必须分片：asyncpg 单语句 bind 参数上限 32767（实测解禁任务因此静默崩了 13 天）。"""
from app.repositories.market_data_repo import UPSERT_CHUNK, chunk_rows


def test_chunk_rows_splits_large_batches() -> None:
    rows = [{"a": i} for i in range(1200)]
    chunks = list(chunk_rows(rows))
    assert [len(c) for c in chunks] == [500, 500, 200]


def test_chunk_rows_keeps_small_batch_whole() -> None:
    assert [len(c) for c in chunk_rows([{"a": 1}])] == [1]
    assert list(chunk_rows([])) == []


async def test_record_and_list_job_failure() -> None:
    from app.services import job_alert_service as jas

    class _Redis:
        def __init__(self) -> None:
            self.h: dict[str, str] = {}
        async def hset(self, key, field, value):  # noqa: ANN001
            self.h[field] = value
            return 1
        async def expire(self, key, ttl):  # noqa: ANN001
            return True
        async def hgetall(self, key):  # noqa: ANN001
            return dict(self.h)

    r = _Redis()
    await jas.record_job_failure("share_float_daily", "boom", cache=r)
    out = await jas.list_job_failures(cache=r)
    assert out and out[0]["job_id"] == "share_float_daily" and "boom" in out[0]["error"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && TUSHARE_TOKEN= uv run pytest tests/test_job_alerts.py tests/test_share_float_batching.py -q --no-cov`
Expected: FAIL — `ImportError: cannot import name 'chunk_rows'`

- [ ] **Step 3: 实现**

1. `market_data_repo`：`UPSERT_CHUNK = 500` + `def chunk_rows(rows, size=UPSERT_CHUNK)`；`upsert_share_floats` 内部对每个 chunk 单独 `execute` 并累加 rowcount（其余批量 upsert 同样核对是否已分片——`upsert_quotes` 是调用方分批，保持现状）。
2. `job_alert_service`：Redis hash `job:failures`，`record` 用 `json.dumps({"job_id","error","at"})`（`at` 为上海时区 ISO），`list` 反序列化并按 `at` 降序；Redis 不可用/异常一律静默返回（告警不能反过来打断 job）。
3. `scheduler/jobs.py`：把 `except Exception: logger.exception("... failed")` 改为同时 `await record_job_failure("<job_id>", repr(exc))`（每个 job 用其 scheduler id 字符串，如 `"share_float_daily"`）。
4. `DataFreshnessOut` + `JobFailureOut(job_id, error, at)`；`market_data.py` 的 `/data-freshness` handler 里 `result["failed_jobs"] = await job_alert_service.list_job_failures()`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && TUSHARE_TOKEN= uv run pytest tests/test_job_alerts.py tests/test_share_float_batching.py -q --no-cov`
Expected: PASS（4 passed）

- [ ] **Step 5: 活体验证分片修复**

```bash
cd backend && uv run python - <<'EOF'
import asyncio
from app.core.database import async_session_factory
from app.services import market_data_service as mds
async def main():
    async with async_session_factory() as db:
        print(await mds.ingest_share_floats(db, days=7))
        await db.commit()
asyncio.run(main())
EOF
```
Expected: 不再抛 `InterfaceError`；`SELECT max(float_date) FROM share_floats` 前进到最近交易日。

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/job_alert_service.py backend/app/repositories/market_data_repo.py backend/app/services/market_data_service.py backend/app/scheduler/jobs.py backend/app/schemas/reconciliation.py backend/app/api/v1/market_data.py backend/tests/
git commit -m "fix(market-data): 解禁批量 upsert 分片（修 13 天静默崩溃）+ 任务失败告警可观测"
```

---

### Task 5: 读路径"最近可用快照"回落 + 北向停更标注

**Files:**
- Modify: `backend/app/services/market_data_service.py`（`get_sector_moneyflow`、`get_market_moneyflow`、`get_northbound_series`）
- Modify: `backend/app/services/market_service.py`（`get_rankings` 的 `is_latest_trading_day` 语义）
- Test: `backend/tests/test_read_fallback.py` 🆕

**Interfaces:**
- Produces：
  - `async def latest_snapshot_day(db, table_day_col) -> date | None`（返回某表最近有数据的日期）
  - `/market/sector-moneyflow` 响应新增 `as_of`（该快照日）与 `stale_days: int`（今天 - as_of，交易日近似用自然日）
  - `/market/northbound` 响应新增 `as_of`、`source_status: Literal["live","stale","discontinued"]`（`stale_days > 5` → `"discontinued"`）
  - `/market/rankings` 的 `is_latest_trading_day` 改为"该 as_of == 当前自然日/最近交易日"的显式判据（不再恒真）

- [ ] **Step 1: 写失败测试**

```python
"""读路径不得因"表里今天没数据"就返回空——应回落到最近可用日并标注。"""
from datetime import date

import pytest

from app.services import market_data_service as mds


async def test_sector_moneyflow_falls_back_to_latest_snapshot(monkeypatch) -> None:
    async def _repo(db, day, dimension, limit):  # noqa: ANN001
        return [] if day == date(2026, 9, 17) else [type("S", (), {
            "board_code": "BK1211", "board_name": "汽车", "pct_change": 1.35,
            "main_net_inflow": 3.7e9, "super_large_net": 2.7e9, "large_net": 9.3e8,
            "up_count": 235, "down_count": 87, "main_net_ratio": 6.46,
        })()]

    monkeypatch.setattr(mds.market_data_repo, "list_sector_moneyflow", _repo)
    monkeypatch.setattr(mds, "_latest_snapshot_day", lambda *a, **k: _async(date(2026, 9, 8)))
    monkeypatch.setattr(mds, "_today_sh", lambda: date(2026, 9, 17))
    out = await mds.get_sector_moneyflow(None, "industry", 15)
    assert out["as_of"] == "2026-09-08"
    assert out["stale_days"] == 9
    assert out["items"][0]["board_code"] == "BK1211"
```

- [ ] **Step 2: 跑测试确认失败** → FAIL（返回 `list` 而非 dict）
- [ ] **Step 3: 实现**：三个读路径统一改为"先取最近可用日 → 再查该日 → 返回 `{as_of, stale_days, items}`"；北向按 `stale_days` 判 `source_status`；`get_rankings` 用判据日与 `_today_sh()` 比较显式算出 `is_latest_trading_day`。
- [ ] **Step 4: 跑测试确认通过** + 全量 pytest
- [ ] **Step 5: 活体验证**：`curl /market/sector-moneyflow?dimension=industry` 应返回 09-08 的 15 行 + `stale_days=9`（不再是 `[]`）
- [ ] **Step 6: 提交**

```bash
git commit -m "fix(market-data): 板块资金流/北向读路径回落最近可用快照并标注陈旧度"
```

---

### ⛔ Phase 0 门禁（必须全绿才进入 Phase 1）

- [ ] `bash scripts/self_review.sh --full` → 全绿
- [ ] **重建后端镜像并重启**，用活栈验证脏行场景已消除：
```bash
docker compose build api && docker compose up -d api scheduler worker
curl -s http://localhost/api/v1/market/limit-up-ladder | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['as_of'], d.get('degraded_reason'), len(d['echelons']))"
curl -s http://localhost/api/v1/market/data-freshness | python3 -c "import sys,json;d=json.load(sys.stdin);print('failed_jobs:', d.get('failed_jobs'))"
```
Expected：`limit-up-ladder` 返回完整日的 5 档梯队；`data-freshness` 能列出失败 job。
- [ ] `docs/Changelog.md` 有 Phase 0 条目；`best-practices` 追加"判据三元组/读路径回落/失败告警"三条

---

# Phase 1 · 性能与一致性

### Task 6: 当日全市场派生快照（一次加载，多处复用）

**Files:**
- Create: `backend/app/services/market_snapshot_service.py`
- Test: `backend/tests/test_market_snapshot.py` 🆕

**Interfaces:**
- Produces：
  - `async def load_day_rows(db, day: date, *, cache=None) -> list[dict[str, Any]]`——元素 `{"stock_id", "symbol", "name", "csrc_desc", "province", "close", "pct_chg", "amount", "total_mv", "circ_mv", "turnover_rate"}`，按 `stock_id` 升序
  - Redis 键 `market:day:rows:{day}`，TTL `300`
  - `def group_by(rows, key: str) -> dict[str, list[dict]]`（纯函数，供各端点分组）
  - `def summarize_group(items) -> dict`（`{total, up_count, flat_count, down_count, avg_chg}`，纯函数）

- [ ] **Step 1: 写失败测试**（分组/汇总纯函数 + 缓存命中不打库）
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**：SQL 改为**读 `dq.pct_chg` 存储列**（不再 LATERAL 回看前收），一次 join `stocks` + 最新 `daily_basic`（市值/换手）；`cache` 命中直接反序列化返回。
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: 性能实测**（对比改造前 597ms）

```bash
cd backend && uv run python - <<'EOF'
import asyncio, time
from app.core.database import async_session_factory
from app.services import market_snapshot_service as mss
async def main():
    async with async_session_factory() as db:
        t=time.perf_counter(); rows = await mss.load_day_rows(db, __import__("datetime").date(2026,9,16), cache=None)
        print(len(rows), f"{1000*(time.perf_counter()-t):.1f} ms")
asyncio.run(main())
EOF
```
Expected: < 100 ms（改造前同数据量 597 ms）

- [ ] **Step 6: 提交**

```bash
git commit -m "perf(market): 当日派生快照单次加载（改读 pct_chg 列，删逐股 LATERAL 回看）"
```

---

### Task 7: 5 个聚合端点改从快照计算

**Files:**
- Modify: `backend/app/services/market_service.py`（`get_distribution`、`get_sectors`、`get_capital_flow`、`get_hot_boards`、`get_sw_performance`）
- Test: `backend/tests/test_market_aggregations.py` 🆕

**Interfaces:**
- Consumes: Task 6 的 `load_day_rows` / `group_by` / `summarize_group`
- Produces: 各端点内部不再含 `LEFT JOIN LATERAL`；输出形状与 Task 2 的信封一致（`items` 内容键不变）

- [ ] **Step 1: 写失败测试**：断言 `get_distribution/get_sectors/get_capital_flow` 在 monkeypatch 掉 `load_day_rows` 后能产出正确分桶/分组结果（纯计算），且**不再调用** `db.execute`
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**：删除 5 处 LATERAL SQL，改用快照 + 纯函数分组；`get_hot_boards` 先落 `csrc_desc`/`province` 分组（Phase 3 再换东财体系，此任务只把数据来源换成快照）
- [ ] **Step 4: 跑测试确认通过** + 全量 pytest 绿
- [ ] **Step 5: 活体验证同页多次请求只算一次**
```bash
for u in distribution sectors capital-flow; do curl -s -o /dev/null -w "$u %{time_total}s\n" "http://localhost/api/v1/market/$u"; done
```
Expected: 首次（冷缓存）< 150 ms，后续 < 30 ms
- [ ] **Step 6: 提交**

### Task 8: 缓存键带 as_of + TTL 对齐

**Files:** `backend/app/services/market_service.py`、`market_snapshot_service.py`、`market_data_service.py`
- [ ] 所有按日缓存键插入 `{as_of}`（如 `market:sectors:{day}`）；`_MARKET_CACHE_TTL` 保持 300 但**与前端 staleTime 对齐规则**写入注释；单测断言键含日期、跨日不复用。
- [ ] 提交：`fix(market): 按日缓存键带 as_of，修"补数后看不到新值"`

### Task 9: 前端共享轮询 + 统一 query key + as_of/口径徽标

**Files:**
- Create: `frontend/src/features/market/hooks/marketStatus.ts`、`frontend/src/features/market/hooks/useMarketPolling.ts`
- Modify: `frontend/src/features/market/components/*.tsx`（全部卡片）、`frontend/src/shared/ui/SectionCard.tsx`、`frontend/src/shared/api/market.ts`
- Modify: `frontend/e2e/marketDataFace.spec.ts`
- Test: e2e（无前端单测框架，见 Global Constraints）

**Interfaces:**
- Produces：
  - `export function marketStatus(now?: Date): "open" | "closed" | "preopen"`（上海时区，工作日 09:30–15:00 为 `open`）
  - `export function useMarketPolling(): { refetchInterval: number | false }`——`open` → 30_000，其余 `false`
  - `SectionCard` 新增 `quality?: string`（渲染口径徽标：`complete`→"收盘"、`fallback`→"回落至 {asof}"、`stale`→"数据源停更"）
- query key 规范：`["market","distribution", asOf]`、`["market","sectors", asOf]`、`["market","hot-boards", category, asOf]`（同一端点全局只有一个 key 前缀）

- [ ] **Step 1: 先写 e2e 断言（红）**：在 `marketDataFace.spec.ts` 加"每张卡都有『数据截至』与口径徽标"用例，预期失败
- [ ] **Step 2: 跑 e2e 确认失败**：`E2E_BASE_URL=http://localhost npx playwright test e2e/marketDataFace.spec.ts`（需先 `docker cp` 最新 dist；见 Phase 门禁说明）
- [ ] **Step 3: 实现** hooks + 卡片接线 + key 统一 + 适配 Task 2 的新响应形状（`items` 包裹）
- [ ] **Step 4: `npx tsc --noEmit` + 重跑 e2e 确认通过**
- [ ] **Step 5: 提交**

### ⛔ Phase 1 门禁
- [ ] `self_review.sh --full` 全绿；`bash scripts/bench.sh` 绿；前端 `tsc`/`check:design`/`build` 绿
- [ ] 活栈：同一页面同端点 Network 只有 1 次请求；卡片显示 as_of
- [ ] Changelog + best-practices 同步（"快照单一来源""缓存键含 as_of""共享轮询"）

---

# Phase 2 · 盘中实时情绪（双口径）

### Task 10: 东财涨停池 → 盘中 snapshot（纯函数 + 夹具）

**Files:**
- Create: `backend/app/services/intraday_sentiment_service.py`
- Create: `backend/tests/fixtures/eastmoney/zt_pool_20260917.json`
- Test: `backend/tests/test_intraday_sentiment.py` 🆕

**Interfaces:**
- Produces：
  - `def build_intraday_snapshot(pool_rows: list[dict], *, as_of: date, captured_at: datetime) -> dict[str, Any]`——输出与 `limit_up_service.get_snapshot` **同构**（`echelons/kpis/sectors/as_of/as_of_prev/source/degraded_reason/as_of_label`），`source="eastmoney_intraday"`，`as_of_label=f"盘中 {captured_at:%H:%M}"`
  - `async def fetch_intraday_pool(trade_date: str) -> list[dict]`（薄封装 `eastmoney_client.fetch_limit_up_pool`）
- 夹具：录制真实响应（47 行，20260917），测试只读夹具

- [ ] **Step 1: 写失败测试**（用夹具断言：档位=streak 分组、`zt_count == len(pool)`、`max_streak == 3`、`as_of_label` 前缀"盘中"、`seal_fund/break_count` 与夹具一致）
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**（纯函数，不做 IO；板块聚合按 `board_name`）
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: 提交**

### Task 11: `mode=intraday` 端点接线（不污染收盘序列）

**Files:** `backend/app/services/limit_up_service.py`、`backend/app/api/v1/market_data.py`、`backend/app/schemas/market_data.py`
- [ ] 单测：`mode=intraday` 走盘中分支且 `as_of_label` 含"盘中"；东财失败 → 回落收盘口径并在 `as_of_label` 标注 `"盘中不可用，已回落收盘"`；**断言 `persist_snapshot` 未被调用**（盘中不写 `market_sentiment_daily`）
- [ ] 实现：`get_snapshot(cache, as_of, lookback, mode="close")`；`mode=intraday` 时要求 `as_of` 为空或今日（历史日期无盘中意义，返 400）
- [ ] 提交

### Task 12: 分时快照表 + 5 分钟采集 + 分时序列端点

**Files:**
- Create: `backend/app/migrations/versions/<hash>_add_market_sentiment_intraday.py`
- Modify: `backend/app/models/market_data.py`、`backend/app/repositories/market_data_repo.py`、`backend/app/scheduler/jobs.py`、`backend/app/scheduler/runner.py`（注册 `intraday_sentiment_poll`，交易日 09:30–15:00 每 5 分钟）、`backend/app/api/v1/market_data.py`（`GET /market/sentiment/intraday?date=`）
- Test: `backend/tests/test_intraday_snapshot_table.py` 🆕
- [ ] 单测：迁移 up/down 可跑（用 `alembic upgrade head` + `downgrade -1`）；采集任务把池行数写进表且 `unique(trade_date, captured_at)` 幂等；端点按 `captured_at` 升序返回点序列
- [ ] 实现 + `uv run alembic upgrade head`（活库执行，已授权）
- [ ] 提交

### Task 13: 前端盘中/收盘切换 + 分时曲线 + 30s 轮询

**Files:** `frontend/src/features/market/components/SentimentIntradayChart.tsx` 🆕、`frontend/src/pages/market/index.tsx`、`frontend/src/shared/api/limitUp.ts`、`frontend/e2e/limitUpSentiment.spec.ts`
- [ ] 先写 e2e（红）：情绪 Tab 存在"盘中/收盘"切换、切到盘中时卡头出现"盘中 HH:MM"、分时曲线 svg 存在
- [ ] 实现（分时曲线复用 `SentimentThermometer` 的纯 SVG 风格；`useMarketPolling` 驱动 30s 刷新）
- [ ] `tsc` + e2e 绿 → 提交

### ⛔ Phase 2 门禁
- [ ] `self_review.sh --full` + 活栈验证：`mode=intraday` 与实际池一致；`market_sentiment_daily` 无盘中行；分时曲线有点
- [ ] Changelog + best-practices（"双口径并存必须标 source 与 as_of_label""盘中数据不得写入收盘权威序列"）

---

# Phase 3 · 热门板块重做 + 体验

### Task 14: 东财板块端点（三套 + 真实 code）+ 成分股下钻

**Files:**
- Modify: `backend/app/core/providers/eastmoney_client.py`（`fetch_board_list(category)`、`fetch_board_stocks(board_code, limit)`）
- Modify: `backend/app/services/market_service.py`（`get_hot_boards` 换东财体系）、`backend/app/api/v1/market.py`（新增 `/market/boards/{board_code}/stocks`）
- Test: `backend/tests/test_board_endpoints.py` 🆕、`backend/tests/fixtures/eastmoney/board_list_industry.json` 🆕
- [ ] 单测（夹具）：`hot-boards` 返回真实 `code`（`BK` 前缀）、`leaders` 非空、`mainNetInflow/amount` 存在；`concept` **不再返回 `[]`**；`/boards/{code}/stocks` 返回成分股；东财失败 → 回落本地分组实现并标注
- [ ] 实现 + 提交

### Task 15: 前端热门板块卡 + 列表页 + 成分股抽屉

**Files:** `frontend/src/features/market/components/BoardDrilldownDrawer.tsx` 🆕、`HotSectors.tsx`、`frontend/src/pages/market-hot-sectors/index.tsx`、`frontend/src/shared/api/market.ts`、`frontend/e2e/marketTrust.spec.ts` 🆕
- [ ] 先写 e2e（红）：点板块条目 → 抽屉出现成分股；列表页排序/搜索可用；切分类保留选中
- [ ] 实现（排序维度：涨跌幅/成交额/主力净额/家数；`code` 真实后 `?board=` 高亮生效）
- [ ] `tsc` + e2e 绿 → 提交

### Task 16: a11y + 暗色 + 北向标注 + 数据版图降级

**Files:** `HotSectors.tsx`/`IndustryClassification.tsx`（改真 `<Link>`）、`frontend/src/pages/market/market.css`（暗色白底根因）、`NorthboundCard.tsx`、`DataCoverageMatrix.tsx`、`frontend/e2e/marketTrust.spec.ts`
- [ ] 先写 e2e（红）：可点击条目是 `role=link`/`<a href>`（键盘可达）；`/market` 暗色无 `rgb(255,255,255)` 面板；北向卡显示"数据源已停更（最新 09-07）"；数据版图北向一行标注"规划中"
- [ ] 实现 → `tsc` + e2e 绿 → 提交

### ⛔ Phase 3 门禁 + 总门禁
- [ ] `self_review.sh --full`、`bench.sh`、后端全量 pytest、前端 `tsc`/`check:design`/`build`
- [ ] **重建镜像 + 全量 E2E（经 gateway）**：
```bash
docker compose build api && docker compose up -d api scheduler worker
cd frontend && npm run build && docker cp dist/. frontend:/usr/share/nginx/html/
E2E_BASE_URL=http://localhost npx playwright test 2>&1 | tail -20
```
  失败项必须与干净 main 基线对账后才可放行（记录到 ledger）
- [ ] 文档：Changelog/best-practices/`docs/design/market-data-trust-and-realtime.md`/`plans/*` 交叉引用一致；`docs/build.md` 若涉及新任务则同步

---

## 自查（写计划时已完成）

- **Spec 覆盖**：§4.1→T1/T2、§4.2→T4、§4.3→T10/T11/T12、§4.4→T14/T15、§4.5→T9/T13/T16、§5 修复→T3/T4、§6 验收→各 Phase 门禁、§7 测试策略→每任务测试步骤、§8 风险→T14 回落分支。
- **占位符扫描**：无 TBD/TODO；关键任务给了真实代码或明确的"照此改"清单。
- **类型一致性**：`MarketDay`/`resolve_latest_complete_day`/`load_day_rows`/`build_intraday_snapshot`/`record_job_failure`/`useMarketPolling` 在各任务中名称一致。
- **跨任务共享文件**：`market_service.py`（T2 信封 → T7 换数据源 → T14 换板块体系）、`limit_up_service.py`（T11 → T12）、前端 market 组件（T9 → T13 → T15 → T16）均为**顺序依赖**，不并行派发。
