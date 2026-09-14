# 连板梯队与市场情绪 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用库内日线 + 交易所口径涨跌停价自算出「连板梯队 / 申万 L3 细分最高板 / 昨日涨停今日表现 / 情绪温度计」，第三方 Web 只作盘中增强，Web 失败不构成功能降级。

**Architecture:** TuShare `stk_limit` 落表成权威限价基准 → 单条窗口 SQL（候选 CTE 先收敛再回查）产出候选股逐日 `is_lu/touched/streak_upto` → 纯函数算梯队/晋级率/板块聚合/情绪 KPI → 服务层组装快照并缓存 → 4 个只读端点。Web（东财涨停池）只在盘中给已匹配 symbol 附加封板时间/封单/炸板次数，失败静默降级为 `null`。

**Tech Stack:** FastAPI · SQLAlchemy 2.0 async · PostgreSQL（gaps-and-islands 窗口函数）· Alembic · APScheduler（`Asia/Shanghai`）· RabbitMQ Worker · Redis 缓存 · React 18 + antd 5 + TanStack Query · Playwright

**Spec:** [docs/design/limit-up-sentiment.md](../../docs/design/limit-up-sentiment.md)（口径、字段名、降级矩阵、实测证据均以 spec 为准）

## Global Constraints

- 价格列一律 `Numeric(12,4)`（与 `daily_quotes.close` 同型），**禁止 `Float`**；`0.005` 容差保留以吸收噪声。
- 限价基准只用 `stock_price_limits`（TuShare `stk_limit` 原值），**禁止**按 ST/代码前缀推算比例，**禁止**用 `LAG(close)` 当 `pre_close`。
- 新迁移 `down_revision` 必须接在当前 head `cf4b8e317fe5` 之后，`uv run alembic heads` 必须只有 1 个 head。
- **读端点不得写库**（不落表）；补数走 worker/scheduler。唯一例外是 `get_snapshot` 的东财涨停池增强（Task 6）：只读、失败必静默、客户端已有 10s 超时，且按 `as_of` 取数（该端点支持历史日期）；不得新增其他外呼。
- **每一步跑真库/真接口后必须回填实测数字**：本计划里所有「实测 X ms / N 行 / 覆盖率」都必须在对应步骤重测一次并就地更新，数字不能被下一层代码静默推翻（限价行数、窗口行数、覆盖率的初值已按 2026-09-14 复测修正）。
- 服务层的时间判断必须 `ZoneInfo("Asia/Shanghai")`；`CronTrigger` 必须显式 `timezone="Asia/Shanghai"`。
- 缓存 key 必须含所有改变结果集的维度（`as_of`、`lookback`）。
- 所有聚合/排序 SQL 必须带确定性 tiebreak（`streak DESC, amount DESC, stock_id ASC`）。
- 候选集合必须 `DISTINCT`；聚合前先证 `count(*) == count(DISTINCT (stock_id, trade_date))`（扇出守卫）。
- 前端缺失值渲染 `--`（绝不 `?? 0`）；金额/百分比格式化复用 `features/market/components/format.ts`。
- 前后端字段映射在 `shared/api/limitUp.ts` 一处完成（snake_case → camelCase）。
- 任务 10 必须更新 `docs/Changelog.md` 与 `docs/references/best-practices.md`，并保持与 `docs/design/limit-up-sentiment.md` 的表名/字段/metric 命名一致。
- 每个任务收尾跑 `bash scripts/self_review.sh`（AGENTS 强制的收尾自检）。本计划不触 Tier-1 纯计算热点（规则引擎/rollup/归一化 mapper），**不需要** `bash scripts/bench.sh`；若后续改动波及这些热点，再加跑基准门禁。

---

## File Structure

**Backend**

| 文件 | 职责 |
|---|---|
| `backend/app/models/market_data.py`（改） | +`StockPriceLimit` / +`MarketSentimentDaily` 两个模型 |
| `backend/app/core/providers/tushare_client.py`（改） | `fetch_stk_limit` 补 `fields`（`pre_close` 是默认不显示的列） |
| `backend/app/migrations/versions/a7c1f0b2d3e4_add_stock_price_limits.py`（新） | 限价表 DDL |
| `backend/app/migrations/versions/b8d2e1c3f4a5_add_market_sentiment_daily.py`（新） | 情绪聚合表 DDL |
| `backend/app/repositories/stock_repo.py`（改） | +`build_ts_code_to_stock_id()`（JSONB 之外的唯一 ts_code→id 映射实现） |
| `backend/app/repositories/limit_up_repo.py`（新） | 全部 SQL：窗口查询 / 当日广度 / 限价 upsert / 交易日序列 / 情绪表 upsert |
| `backend/app/services/limit_up_calculator.py`（新） | **纯函数**：梯队、`N天M板`、晋级率、L3 聚合、情绪 KPI |
| `backend/app/services/limit_up_service.py`（新） | 编排：可用性判定 source、缓存、降级契约、Web 增强注入 |
| `backend/app/services/market_data_service.py`（改） | +`ingest_stock_price_limits()` |
| `backend/app/core/providers/eastmoney_client.py`（改） | +`fetch_limit_up_pool()`（T6） |
| `backend/app/schemas/limit_up.py`（新） | 4 个响应模型 |
| `backend/app/api/v1/market_data.py`（改） | +4 个 GET 路由 |
| `backend/app/schemas/task.py`（改） | `MarketDataJobType` +`"price_limits"` / +`"sentiment_daily"` |
| `backend/app/workers/market_data_worker.py`（改） | +2 个 job 分支 |
| `backend/app/scheduler/jobs.py`（改） | +`price_limits_daily_job` / +`sentiment_daily_job` |
| `backend/app/scheduler/runner.py`（改） | 注册两个 `CronTrigger`（16:50 / 17:15） |

**Backend tests**

| 文件 | 层次 |
|---|---|
| `backend/tests/test_limit_up_calculator.py`（新） | 纯单元（无 DB/网络），含黄金日 + off-by-one + 停牌 policy A |
| `backend/tests/test_limit_up_repo.py`（新） | `-m e2e`：扇出守卫 + 查询计划守卫 |
| `backend/tests/test_limit_up_service.py`（新） | 降级契约（monkeypatch，无 DB） |
| `backend/tests/test_limit_up_web.py`（新） | Web 映射 + 对账（monkeypatch） |

**Frontend**

| 文件 | 职责 |
|---|---|
| `frontend/src/shared/api/limitUp.ts`（新） | 4 个 fetch 函数 + snake→camel 映射 |
| `frontend/src/features/market/components/SentimentHeader.tsx`（新） | 情绪温度计（涨停/跌停/炸板 + 晋级率 + 最高板） |
| `frontend/src/features/market/components/LimitUpLadder.tsx`（新） | 连板梯队 |
| `frontend/src/features/market/components/SwL3LimitUpBoard.tsx`（新） | 申万 L3 最高板表 |
| `frontend/src/features/market/components/YesterdayLimitUp.tsx`（新） | 昨日涨停今日表现表 |
| `frontend/src/features/market/components/index.ts`（改） | barrel 导出 |
| `frontend/src/shared/ui/SectionCard.tsx`（改） | +可选 `asof` 属性（复用既有 `.section-card__asof` 样式） |
| `frontend/src/shared/ui/date.ts`（新） | `formatCnDate` 共享实现（避免 `shared/ui` 反向依赖 `features/`） |
| `frontend/src/features/market/components/format.ts`（改） | `formatCnDate` 改为从 `shared/ui/date.ts` 转发（保留原导出名） |
| `frontend/src/pages/market/index.tsx`（改） | +`sentiment` Tab |
| `frontend/e2e/limitUpSentiment.spec.ts`（新） | e2e |

---

### Task 1: 权威限价表与补漏 ingest

**Files:**
- Modify: `backend/app/core/providers/tushare_client.py`（`fetch_stk_limit` 补 `fields`）
- Modify: `backend/app/services/tushare_ingest.py`（`_build_stock_id_map` 改薄委托）
- Modify: `backend/app/models/market_data.py`（imports + 两个模型中的 `StockPriceLimit`）
- Create: `backend/app/migrations/versions/a7c1f0b2d3e4_add_stock_price_limits.py`
- Modify: `backend/app/repositories/stock_repo.py`（+`build_ts_code_to_stock_id`）
- Modify: `backend/app/repositories/limit_up_repo.py`（新建，Task 1 只放限价相关函数）
- Modify: `backend/app/services/market_data_service.py`（+`ingest_stock_price_limits`）
- Modify: `backend/app/schemas/task.py`、`backend/app/workers/market_data_worker.py`、`backend/app/scheduler/jobs.py`、`backend/app/scheduler/runner.py`
- Test: `backend/tests/test_limit_up_ingest.py`（新建）

**Interfaces:**
- Consumes: `TuShareClient.fetch_stk_limit(trade_date=...)`（**需改，见 Step 3**）、模块内既有 `_get_tushare()`、`stocks` / `daily_quotes` 读路径
- Produces:
  - `StockPriceLimit` 模型（表 `stock_price_limits`，唯一键 `uq_price_limit_date_stock`）
  - `limit_up_repo.upsert_price_limits(db, rows: list[dict]) -> int`
  - `limit_up_repo.list_recent_trade_dates(db, as_of: date, limit: int) -> list[date]`（升序）
  - `limit_up_repo.missing_price_limit_dates(db, dates: list[date]) -> list[date]`
  - `limit_up_repo.has_price_limits(db, as_of: date) -> bool`
  - `market_data_service.ingest_stock_price_limits(db, trade_date=None, window_days=20) -> dict`

- [ ] **Step 1: 写失败的测试**

`backend/tests/test_limit_up_ingest.py`:

```python
"""限价 ingest 的纯映射单测（monkeypatch，不触 DB/网络）。"""

from datetime import date

import pandas as pd

from app.services import market_data_service as mds


def test_map_stk_limit_rows_keeps_exchange_limits_verbatim():
    """限价必须原值落库：不做任何按 ST/前缀的比例重算。"""
    df = pd.DataFrame(
        [
            {"trade_date": "20260908", "ts_code": "000488.SZ", "pre_close": 1.94,
             "up_limit": 2.13, "down_limit": 1.75},
        ]
    )
    rows = mds._map_stk_limit_rows(df, date(2026, 9, 8), {"000488.SZ": 42})
    assert rows == [
        {"trade_date": date(2026, 9, 8), "stock_id": 42, "ts_code": "000488.SZ",
         "pre_close": 1.94, "up_limit": 2.13, "down_limit": 1.75}
    ]


def test_map_stk_limit_rows_skips_unmapped_and_null_limit():
    df = pd.DataFrame(
        [
            {"trade_date": "20260908", "ts_code": "510300.SH", "pre_close": 4.0,
             "up_limit": 4.4, "down_limit": 3.6},          # 基金：不在 stocks 表
            {"trade_date": "20260908", "ts_code": "600000.SH", "pre_close": None,
             "up_limit": None, "down_limit": None},        # 限价缺失
            {"trade_date": "20260908", "ts_code": "000001.SZ", "pre_close": 11.6,
             "up_limit": 12.76, "down_limit": 10.44},
        ]
    )
    rows = mds._map_stk_limit_rows(df, date(2026, 9, 8), {"000001.SZ": 7})
    assert [r["stock_id"] for r in rows] == [7]


def test_fetch_stk_limit_requests_pre_close_field(monkeypatch):
    """`stk_limit` 的 pre_close 是「默认显示=N」字段：不显式传 fields 就不会返回。

    实测 2026-09-08：不传 fields 只回 [trade_date, ts_code, up_limit, down_limit]，
    传 fields 后 5,637/5,637 行 pre_close 非空。漏这一步会让整列 NULL，
    而所有溢价/赚钱效应 KPI 都依赖它。
    """
    import asyncio

    from app.core.providers import tushare_client as tc

    captured: dict[str, str] = {}

    async def _query(api_name: str, fields: str = "", **_kw):
        captured["api"], captured["fields"] = api_name, fields
        return pd.DataFrame()

    client = tc.TuShareClient.__new__(tc.TuShareClient)
    monkeypatch.setattr(client, "_query", _query)
    asyncio.run(client.fetch_stk_limit(trade_date="20260908"))
    assert captured["api"] == "stk_limit"
    assert "pre_close" in captured["fields"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_limit_up_ingest.py -v`
Expected: FAIL —— `AttributeError: module 'app.services.market_data_service' has no attribute '_map_stk_limit_rows'`

- [ ] **Step 3: 让 `fetch_stk_limit` 带回 `pre_close` + 加模型**

先改客户端（**本任务最容易漏的一步**）：`backend/app/core/providers/tushare_client.py` 的 `fetch_stk_limit` 显式传 `fields`。

```python
    async def fetch_stk_limit(
        self,
        trade_date: str = "",
        ts_code: str = "",
        fields: str = "trade_date,ts_code,pre_close,up_limit,down_limit",
    ) -> pd.DataFrame:
        """Fetch daily price limit info (涨跌停价格).

        ``pre_close`` 在 TuShare 文档里是「默认显示 = N」字段：不显式传 fields，
        API 只返回 [trade_date, ts_code, up_limit, down_limit]（2026-09-14 实测）。
        漏传会让 stock_price_limits.pre_close 整列为 NULL，而昨日涨停溢价/赚钱效应
        KPI 全依赖它——静默产出 null 而不是报错。
        """
        kwargs: dict[str, str] = {}
        if trade_date:
            kwargs["trade_date"] = trade_date
        if ts_code:
            kwargs["ts_code"] = ts_code
        return await self._query("stk_limit", fields=fields, **kwargs)
```

实测（2026-09-08）：不传 fields → 列 `[trade_date, ts_code, up_limit, down_limit]`、5,637 行；传 fields → 列含 `pre_close` 且 **5,637/5,637 非空**。

然后 `backend/app/models/market_data.py`：在 import 段补 `Numeric`（`from sqlalchemy import ... Numeric ...`），文件末尾追加：

```python
class StockPriceLimit(Base):
    """交易所口径的每日涨跌停价（TuShare stk_limit），连板判定的唯一权威基准。

    为什么不用「名称含 ST + 代码前缀推比例」：2026-09-08 实测权威 up_limit 判出 75 只涨停，
    名称启发式判出 83 只，10 处分歧里 9 只是 ST 名称股（当日真实限幅 10%，如 ST晨鸣
    up_limit=2.13 / pre_close=1.94）。stocks.name 是当日快照而非历史名称，历史回放必错。
    pre_close 由 stk_limit 原生提供（交易所口径、已含除权调整；**需客户端显式传 fields**），
    禁止用 LAG(close) 现算。注意语义：该行 pre_close 是「本交易日的前收」，
    要算某日涨跌幅就得取**该日行**的 pre_close。

    建键说明：用 stock_id 而非 ts_code——stocks 表没有 ts_code 列（只在 detail JSONB 里），
    JSONB join 需函数索引且每次读都要过 stocks。不额外建索引：唯一键已服务日筛，窗口侧走
    hash join（实测单日 5,499 行 hash 2.6ms / 317kB），加第二个索引是纯写放大。
    """

    __tablename__ = "stock_price_limits"
    __table_args__ = (UniqueConstraint("trade_date", "stock_id", name="uq_price_limit_date_stock"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    stock_id: Mapped[int] = mapped_column(nullable=False)
    ts_code: Mapped[str] = mapped_column(String(16), nullable=False)  # 溯源用
    pre_close: Mapped[float | None] = mapped_column(Numeric(12, 4))
    up_limit: Mapped[float | None] = mapped_column(Numeric(12, 4))
    down_limit: Mapped[float | None] = mapped_column(Numeric(12, 4))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

- [ ] **Step 4: 建迁移**

```bash
cd backend && uv run alembic revision --rev-id a7c1f0b2d3e4 -m "add stock price limits"
```

内容：

```python
"""add stock price limits

Revision ID: a7c1f0b2d3e4
Revises: cf4b8e317fe5
Create Date: 2026-09-14

交易所口径涨跌停价（TuShare stk_limit 原值）。价格列为 Numeric(12,4) 以与
daily_quotes.close 同型——用 Float 会让 "close >= up_limit - 0.005" 引入浮点转换，
而 0.005 容差本就是为吸收噪声而设。唯一键 (trade_date, stock_id) 已服务
「按日筛 + 窗口 hash join」，不再建第二个索引。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c1f0b2d3e4"
down_revision: str | None = "cf4b8e317fe5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stock_price_limits",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("ts_code", sa.String(length=16), nullable=False),
        sa.Column("pre_close", sa.Numeric(12, 4), nullable=True),
        sa.Column("up_limit", sa.Numeric(12, 4), nullable=True),
        sa.Column("down_limit", sa.Numeric(12, 4), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trade_date", "stock_id", name="uq_price_limit_date_stock"),
    )


def downgrade() -> None:
    op.drop_table("stock_price_limits")
```

```bash
cd backend && uv run alembic upgrade head && uv run alembic heads
```
Expected: `cf4b8e317fe5 -> a7c1f0b2d3e4, add stock price limits` 且 `a7c1f0b2d3e4 (head)` —— 只有一个 head。

- [ ] **Step 5: 抽出 ts_code → stock_id 映射（DRY）**

`backend/app/repositories/stock_repo.py` 追加：

```python
_TS_SUFFIX = {"Shanghai_Stocks": ".SH", "Shenzen_Stocks": ".SZ", "Beijing_Stocks": ".BJ"}


async def build_ts_code_to_stock_id(db: AsyncSession) -> dict[str, int]:
    """TuShare ts_code（如 '000001.SZ'）→ stock_id。

    唯一的 ts_code 映射实现：'stocks.detail->>ts_code' 也可用，但那是 JSONB 提取、
    需函数索引才能 join，不能作为读取路径的连接键。
    """
    result = await db.execute(select(Stock.id, Stock.exchange, Stock.symbol))
    return {
        f"{row.symbol}{_TS_SUFFIX.get(row.exchange, '')}": row.id  # type: ignore[misc]
        for row in result
    }
```

并把 `backend/app/services/tushare_ingest.py:751` 的 `_build_stock_id_map` 改为薄委托：

```python
    async def _build_stock_id_map(self, db: AsyncSession) -> dict[str, int]:
        """Build a mapping from TuShare ts_code (e.g. '000001.SZ') to DB stock_id."""
        from app.repositories.stock_repo import build_ts_code_to_stock_id  # noqa: PLC0415

        return await build_ts_code_to_stock_id(db)
```

- [ ] **Step 6: 写 repo 的限价读写**

`backend/app/repositories/limit_up_repo.py`（新建）：

```python
"""连板/情绪数据访问：权威限价、候选窗口、当日广度、情绪聚合。

口径与实测证据见 docs/design/limit-up-sentiment.md。
"""

from __future__ import annotations

from datetime import date
from typing import Any, cast

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market_data import StockPriceLimit
from app.models.quote import DailyQuote


async def list_recent_trade_dates(db: AsyncSession, as_of: date, limit: int) -> list[date]:
    """窗口内交易日（升序）。

    真源是 daily_quotes 实际存在的日期，而不是 trade_cal 推算或 weekday()——库内没有
    持久化交易日历，而"哪些日子真有行情"本身就是最准的可用性判据（节假日后的永久
    缺口也是靠这个口径暴露的）。
    """
    stmt = (
        select(DailyQuote.trade_date)
        .where(DailyQuote.trade_date <= as_of)
        .group_by(DailyQuote.trade_date)
        .order_by(DailyQuote.trade_date.desc())
        .limit(limit)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return sorted(rows)


async def missing_price_limit_dates(db: AsyncSession, dates: list[date]) -> list[date]:
    """给定交易日中，stock_price_limits 尚无任何行的那些（补漏判据）。"""
    if not dates:
        return []
    present = set(
        (await db.execute(
            select(StockPriceLimit.trade_date)
            .where(StockPriceLimit.trade_date.in_(dates))
            .group_by(StockPriceLimit.trade_date)
        )).scalars().all()
    )
    return [d for d in dates if d not in present]


async def has_price_limits(db: AsyncSession, as_of: date) -> bool:
    stmt = select(func.count()).select_from(StockPriceLimit).where(
        StockPriceLimit.trade_date == as_of
    ).limit(1)
    return bool((await db.execute(stmt)).scalar_one())


async def upsert_price_limits(db: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """幂等写入限价；同批内先按唯一键去重（pg 的 ON CONFLICT 不处理同批自冲突）。

    去重键必须是完整的唯一键 (trade_date, stock_id)——只按 stock_id 去重会静默
    丢掉其他日期的行（调用方目前按日分批，错只错在"以后有人合并多日"）。
    """
    if not rows:
        return 0
    deduped = {(r["trade_date"], r["stock_id"]): r for r in rows}
    stmt = pg_insert(StockPriceLimit).values(list(deduped.values())).on_conflict_do_update(
        constraint="uq_price_limit_date_stock",
        set_={
            "pre_close": pg_insert(StockPriceLimit).excluded.pre_close,
            "up_limit": pg_insert(StockPriceLimit).excluded.up_limit,
            "down_limit": pg_insert(StockPriceLimit).excluded.down_limit,
        },
    )
    result = cast("CursorResult[Any]", await db.execute(stmt))
    await db.flush()
    return int(result.rowcount)
```

- [ ] **Step 7: 写 ingest 服务方法**

`backend/app/services/market_data_service.py` 追加：

```python
def _map_stk_limit_rows(
    df: pd.DataFrame, trade_date: date, stock_id_map: dict[str, int]
) -> list[dict[str, Any]]:
    """TuShare stk_limit 行 → 落库 dict（纯映射，同步函数：与模块内其他 `_map_*` 一致）。

    丢弃两类行：映射不到 A 股 stocks 的（基金/B 股，实测单日 5,637 行里 138 行）、
    限价为空的。**原值透传，不做任何比例重算。**
    """
    rows: list[dict[str, Any]] = []
    for row in df.to_dict("records"):
        ts_code = str(row.get("ts_code", "")).strip()
        stock_id = stock_id_map.get(ts_code)
        up_limit = row.get("up_limit")
        if stock_id is None or up_limit is None or pd.isna(up_limit):
            continue
        rows.append(
            {
                "trade_date": trade_date,
                "stock_id": stock_id,
                "ts_code": ts_code,
                "pre_close": None if pd.isna(row.get("pre_close")) else float(row["pre_close"]),
                "up_limit": float(up_limit),
                "down_limit": None
                if pd.isna(row.get("down_limit"))
                else float(row["down_limit"]),
            }
        )
    return rows


async def ingest_stock_price_limits(
    db: AsyncSession, trade_date: date | None = None, window_days: int = 20
) -> dict[str, Any]:
    """拉取缺失交易日的交易所口径涨跌停价（幂等 + 补漏）。

    「补漏」判据是 stock_price_limits 里没有该日任何行，而不是"最新日已存在就跳过"：
    窗口中间的日子缺一个，连板链就会断在那里，且不会报错——只能靠逐日对账发现。
    """
    from app.repositories import limit_up_repo, stock_repo  # noqa: PLC0415

    client = _get_tushare()
    as_of = trade_date or await limit_up_repo.latest_quote_date(db)
    if as_of is None:
        return {"status": "skipped", "reason": "daily_quotes empty"}
    dates = await limit_up_repo.list_recent_trade_dates(db, as_of, window_days)
    todo = await limit_up_repo.missing_price_limit_dates(db, dates)
    if trade_date is not None:
        todo = [trade_date]
    if not todo:
        return {"status": "ok", "as_of": as_of.isoformat(), "fetched": 0, "upserted": 0}

    stock_id_map = await stock_repo.build_ts_code_to_stock_id(db)
    fetched = upserted = 0
    for d in todo:
        df = await client.fetch_stk_limit(trade_date=d.strftime("%Y%m%d"))
        rows = _map_stk_limit_rows(df, d, stock_id_map)
        fetched += len(rows)
        upserted += await limit_up_repo.upsert_price_limits(db, rows)
    return {
        "status": "ok",
        "as_of": as_of.isoformat(),
        "dates": [d.isoformat() for d in todo],
        "fetched": fetched,
        "upserted": upserted,
    }
```

其中 `client = _get_tushare()` 取自该模块既有的 TuShare 客户端获取方式（与其他 `ingest_*` 一致）；限价读写的四个函数（`latest_quote_date` / `list_recent_trade_dates` / `missing_price_limit_dates` / `upsert_price_limits`）与 `list_recent_trade_dates` 同在 `limit_up_repo`（**不是 `market_data_repo`**），Task 2 的窗口查询也复用 `latest_quote_date`：

```python
async def latest_quote_date(db: AsyncSession) -> date | None:
    return (await db.execute(select(func.max(DailyQuote.trade_date)))).scalar_one_or_none()
```

- [ ] **Step 8: 接线 worker + scheduler**

`backend/app/schemas/task.py`：`MarketDataJobType` 追加 `"price_limits"`。
`backend/app/workers/market_data_worker.py`：`_run` 加分支

```python
        elif job == "price_limits":
            result = await market_data_service.ingest_stock_price_limits(
                db, trade_date=_opt_date(params.get("trade_date")),
                window_days=int(params.get("window_days", 20)),
            )
```

`backend/app/scheduler/jobs.py` 追加：

```python
async def price_limits_daily_job() -> None:
    """权威涨跌停价补漏（交易日 16:50，晚于 16:30 的 quotes 回补）。

    按时区显式取上海日期：容器默认 UTC，naive datetime.now() 会取到前一天。
    补漏判据在 service 内（stock_price_limits 无该日行），所以即使某天任务没跑，
    下一次也会自动追平。

    注意窗口上界：daily_quotes 的每日回补拉的是「上一个工作日」（见 `_fetch_yesterday_daily_quotes`），
    所以本任务补到的最新交易日 = 上一个交易日，与情绪快照的 `as_of` 语义一致。
    """
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services import market_data_service  # noqa: PLC0415

    if not _is_workday():
        return
    try:
        async with async_session_factory() as db:
            result = await market_data_service.ingest_stock_price_limits(db)
            await db.commit()
        logger.info("Price limits daily done: %s", result)
    except Exception:
        logger.exception("Price limits daily job failed")
```

`backend/app/scheduler/runner.py` 追加（放在 daily_basic 16:45 之后）：

```python
    # Exchange price limits (authoritative limit-up basis): 16:50 Mon-Fri
    scheduler.add_job(
        price_limits_daily_job,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=50, timezone="Asia/Shanghai"),
        id="price_limits_daily",
        name="Price limits daily",
        replace_existing=True,
    )
```

记得在 runner 的 import 段加入 `price_limits_daily_job`。

- [ ] **Step 9: 跑测试与全量检查**

Run: `cd backend && uv run pytest tests/test_limit_up_ingest.py -v && uv run pytest -q`
Expected: 新测试 3 passed；全量无回归。
Run: `cd backend && uv run --extra dev ruff check . && uv run --extra dev mypy app`
Expected: 无问题。

- [ ] **Step 10: 实机验证补数闭环（真库）**

Run:
```bash
cd backend && uv run python -c "
import asyncio
from app.core.database import async_session_factory
from app.services import market_data_service
async def m():
    async with async_session_factory() as db:
        print(await market_data_service.ingest_stock_price_limits(db))
        await db.commit()
asyncio.run(m())
"
cd backend && uv run python -c "
import asyncio
from app.core.database import async_session_factory
from sqlalchemy import text
async def m():
    async with async_session_factory() as db:
        r = await db.execute(text('SELECT trade_date, count(*) FROM stock_price_limits GROUP BY 1 ORDER BY 1 DESC LIMIT 3'))
        print(r.fetchall())
asyncio.run(m())
"
```
Expected: 约 20 个交易日被补齐；单日实测 5,637 行（其中 5,499 行可映射到 A 股 `stocks`，
与 `daily_quotes` 当日 5,490 行同量级）。同时手工确认 `pre_close` 非空：
```bash
cd backend && uv run python -c "
import asyncio
from app.core.database import async_session_factory
from sqlalchemy import text
async def m():
    async with async_session_factory() as db:
        r = await db.execute(text('SELECT count(*), count(pre_close) FROM stock_price_limits'))
        print(r.fetchall())   # 两数必须相等；不等说明 fetch_stk_limit 漏传 fields
asyncio.run(m())
"
```

- [ ] **Step 11: Commit**

```bash
git add backend/app/models/market_data.py backend/app/migrations/versions/a7c1f0b2d3e4_add_stock_price_limits.py \
  backend/app/repositories/stock_repo.py backend/app/repositories/limit_up_repo.py \
  backend/app/services/market_data_service.py backend/app/services/tushare_ingest.py \
  backend/app/schemas/task.py backend/app/workers/market_data_worker.py \
  backend/app/scheduler/jobs.py backend/app/scheduler/runner.py backend/tests/test_limit_up_ingest.py
git commit -m "feat(limit-up): 交易所口径涨跌停价表与补漏 ingest"
```

---

### Task 2: 候选窗口 SQL 与扇出/计划守卫

**Files:**
- Modify: `backend/app/repositories/limit_up_repo.py`（+2 条 SQL 常量 + 2 个查询函数）
- Test: `backend/tests/test_limit_up_repo.py`（新建，`-m e2e`）

**Interfaces:**
- Consumes: Task 1 的 `stock_price_limits` 表与 `list_recent_trade_dates` / `latest_quote_date`
- Produces:
  - `limit_up_repo.fetch_limit_up_window(db, as_of, as_of_prev, window_start) -> list[dict]`
    每行键：`stock_id, symbol, name, trade_date, close, open, high, amount, pre_close, up_limit, down_limit, is_lu, touched, streak_upto, sw_l1_code, sw_l1_name, sw_l3_code, sw_l3_name`
  - `limit_up_repo.fetch_day_breadth(db, as_of) -> dict`（键 `zt_count, dt_count, zb_count, quoted`）

- [ ] **Step 1: 写失败的测试（扇出守卫 + 计划守卫）**

`backend/tests/test_limit_up_repo.py`:

```python
"""连板窗口 SQL 的两条不变量：无扇出、计划不回退。

`-m e2e`（需要真 Postgres）。运行：uv run pytest tests/test_limit_up_repo.py -v -m e2e
"""

import json
from collections.abc import AsyncGenerator
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text

from app.core.database import async_session_factory, engine
from app.repositories import limit_up_repo

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
async def _dispose_engine_after_test() -> AsyncGenerator[None, None]:
    yield
    await engine.dispose()


async def _window_args(db: Any) -> dict[str, Any]:
    as_of = await limit_up_repo.latest_quote_date(db)
    assert as_of is not None, "daily_quotes 为空，先跑 quotes 回补"
    days = await limit_up_repo.list_recent_trade_dates(db, as_of, 16)
    assert len(days) >= 2
    return {"as_of": as_of, "as_of_prev": days[-2], "window_start": days[0]}


async def test_window_has_no_fanout() -> None:
    """同一 (stock_id, trade_date) 不得出现两次。

    候选集是「今日 ∪ 昨日」涨停并集，两日都涨停的票在 cand 里出现两次会让
    JOIN cand 扇出：实测涨停数从真值 75 变 94，并凭空造出 8 连板。加 DISTINCT 才一致。
    SW 侧同理：sw_industry_members 若一股多行业，LEFT JOIN 也会扇出。
    """
    async with async_session_factory() as db:
        rows = await limit_up_repo.fetch_limit_up_window(db, **await _window_args(db))
    keys = [(r["stock_id"], r["trade_date"]) for r in rows]
    assert len(keys) == len(set(keys)), "窗口查询出现扇出（cand 未 DISTINCT 或成员表一股多行业）"
    assert rows, "窗口为空：stock_price_limits 尚未回补"
    # 窗口必须回整段交易日，而不只是 as_of/as_of_prev 两天：
    # 否则 boards_in_window 恒 ≤ 2、每只票 missing_days 恒 = 窗口天数 - 2。
    assert len({r["trade_date"] for r in rows}) > 2, "窗口被截成两天：N天M板/停牌缺日全错"


async def test_window_rides_stock_date_unique_index() -> None:
    """窗口回查必须走 uq_daily_quotes_stock_date，且不得退化为逐股 probe 的 nested loop。"""
    async with async_session_factory() as db:
        args = await _window_args(db)
        explain = await db.execute(
            text("EXPLAIN (FORMAT JSON) " + limit_up_repo.build_window_sql_for_explain()),
            args,
        )
        plan = explain.scalar_one()
    if isinstance(plan, str):
        plan = json.loads(plan)
    nodes: list[dict[str, Any]] = []
    _walk(plan[0]["Plan"], nodes)
    index_names = {n.get("Index Name") for n in nodes}
    # uq_daily_quotes_stock_date 与 idx_daily_quotes_stock_date 列完全相同，规划器按 OID
    # 任选其一（2026-09-14 实测：本库选 idx_daily_quotes_stock_date；断言 uq_ 会误红，
    # 且 T9 删掉 idx_ 之后才轮到 uq_）。
    assert index_names & {"uq_daily_quotes_stock_date", "idx_daily_quotes_stock_date"}, index_names
    assert not any(n["Node Type"] == "Seq Scan" and n.get("Relation Name") == "daily_quotes"
                   for n in nodes), "daily_quotes 全表扫描"


def _walk(node: dict[str, Any], out: list[dict[str, Any]]) -> None:
    out.append(node)
    for child in node.get("Plans", []):
        _walk(child, out)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_limit_up_repo.py -v -m e2e`
Expected: FAIL —— `AttributeError: ... has no attribute 'fetch_limit_up_window'`

- [ ] **Step 3: 实现窗口 SQL**

`backend/app/repositories/limit_up_repo.py` 追加：

```python
# 查询形状即性能契约（实测，2026-09-14 复测：16 交易日窗口 2026-08-18~09-08，
# 等价索引临时表）：整窗回 2,411 行 / ~48ms；只取末日+前日回 302 行 / ~47ms。
# 计划形状为 Filter/Join + `Bitmap Heap Scan daily_quotes` + `Index Scan
# idx_daily_quotes_stock_date`（uq_ 与 idx_ 同列，规划器按 OID 任选其一，**不要
# 断言具体是哪一个**）。若让 stock_price_limits 驱动、逐股 probe daily_quotes，
# 同一逻辑要 620ms（Nested Loop 5,499 次 probe）。改这条 SQL 前先跑计划守卫测试。
#
# 四条硬性注意：
# 1) cand 必须 DISTINCT —— 两日都涨停的票会出现两次，扇出后涨停数 75→94 并造出假连板。
# 2) is_lu 必须 COALESCE(false) —— LEFT JOIN 到缺失限价的交易日会得到 NULL，
#    而 NULL 会让 gaps-and-islands 的 grp 分组错乱（NULL 与 false 不同组）。
# 3) 停牌 policy A：gaps-and-islands 按「该股实际有行情的行」分段，停牌日既不计数
#    也不截断连板（对齐东财 lbc）。不要加"缺行即断"的守卫——那会与用户对照的口径不符。
# 4) streaked 的 PARTITION 必须带 is_lu 与 grp 两个键。`grp = rn_all - rn_by_val` 只保证
#    **组内常量**，不保证**组间唯一**：一段 false 岛与紧接的 true 岛会撞同一 grp，
#    被合并后 true 行的 streak 继承 false 行的计数。实测本窗口内 4 行 is_lu 虚高
#    （600127 @08-27/09-01 报 3 实为 1；*ST艾艾、002365 @08-20 报 2 实为 1），
#    恰好不落在 09-08/09-07，所以黄金数字会漏过它，但 promotion_rate 读的正是
#    as_of_prev 的 streak——换个日子就静默错。
_WINDOW_SQL = """
WITH limits AS (
    SELECT l.stock_id, l.trade_date, l.pre_close, l.up_limit, l.down_limit
    FROM stock_price_limits l
    WHERE l.trade_date BETWEEN :window_start AND :as_of
),
cand AS (
    SELECT DISTINCT q.stock_id
    FROM daily_quotes q
    JOIN limits l ON l.stock_id = q.stock_id AND l.trade_date = q.trade_date
    WHERE q.trade_date IN (:as_of, :as_of_prev)
      AND q.close >= l.up_limit - 0.005
),
win AS (
    SELECT q.stock_id, q.trade_date, q.close, q.open, q.high, q.amount,
           l.pre_close, l.up_limit, l.down_limit,
           COALESCE(q.close >= l.up_limit - 0.005, false) AS is_lu,
           COALESCE(q.high  >= l.up_limit - 0.005, false) AS touched
    FROM daily_quotes q
    JOIN cand c ON c.stock_id = q.stock_id
    LEFT JOIN limits l ON l.stock_id = q.stock_id AND l.trade_date = q.trade_date
    WHERE q.trade_date BETWEEN :window_start AND :as_of
),
island AS (
    SELECT w.*,
           row_number() OVER (PARTITION BY w.stock_id ORDER BY w.trade_date)
         - row_number() OVER (PARTITION BY w.stock_id, w.is_lu ORDER BY w.trade_date) AS grp
    FROM win w
),
streaked AS (
    SELECT i.*,
           row_number() OVER (
               PARTITION BY i.stock_id, i.is_lu, i.grp ORDER BY i.trade_date
           ) AS streak_upto
    FROM island i
)
SELECT s.stock_id, st.symbol, st.name, s.trade_date, s.close, s.open, s.high, s.amount,
       s.pre_close, s.up_limit, s.down_limit, s.is_lu, s.touched, s.streak_upto,
       c1.industry_code AS sw_l1_code, c1.industry_name AS sw_l1_name,
       c3.industry_code AS sw_l3_code, c3.industry_name AS sw_l3_name
FROM streaked s
JOIN stocks st ON st.id = s.stock_id
LEFT JOIN sw_industry_members m ON m.symbol = st.symbol
LEFT JOIN sw_industry_classes c3 ON c3.industry_code = m.industry_code AND c3.level = 3
LEFT JOIN sw_industry_classes c2 ON c2.industry_code = c3.parent_code
LEFT JOIN sw_industry_classes c1 ON c1.industry_code = c2.parent_code
-- 不过滤 trade_date：整窗都要回（streak 只是窗口副产品），N天M板 / missing_days
-- 还靠窗口其余交易日；只回 as_of 与 as_of_prev 会把 boards_in_window 锁死在 2、
-- 并把每只票的 missing_days 算成「窗口天数 - 2」（实测 16 窗口 → 14）。
ORDER BY s.stock_id, s.trade_date
"""

# 全市场当日广度：口径与候选窗口不同，不能合并——候选集只有"今日或昨日涨停"的票，
# 炸板的票不在其中，实测会把 09-08 的炸板数从 39 算成 12（约 3 倍偏差）。
_BREADTH_SQL = """
SELECT count(*) FILTER (WHERE q.close >= l.up_limit  - 0.005) AS zt_count,
       count(*) FILTER (WHERE q.close <= l.down_limit + 0.005) AS dt_count,
       count(*) FILTER (WHERE q.high >= l.up_limit - 0.005
                          AND q.close < l.up_limit - 0.005) AS zb_count,
       count(*) AS quoted
FROM daily_quotes q
JOIN stock_price_limits l ON l.stock_id = q.stock_id AND l.trade_date = q.trade_date
WHERE q.trade_date = :as_of
"""


def build_window_sql_for_explain() -> str:
    """给计划守卫测试用的同源 SQL（避免测试里复制一份会漂移的 SQL 文本）。"""
    return _WINDOW_SQL


async def fetch_limit_up_window(
    db: AsyncSession, as_of: date, as_of_prev: date, window_start: date
) -> list[dict[str, Any]]:
    """候选股在窗口内的**逐日**行（含 is_lu / touched / streak_upto / 申万 L1+L3）。

    返回整段窗口（16 个交易日约 2,400 行），不只 as_of/as_of_prev 两天：
    `n_day_m_board`（M = 板次数）与 `missing_days`（停牌披露）都要吃窗口里其余交易日。
    调用方（`limit_up_calculator`）自己按日期筛选。
    """
    rows = (
        await db.execute(
            text(_WINDOW_SQL),
            {"as_of": as_of, "as_of_prev": as_of_prev, "window_start": window_start},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def fetch_day_breadth(db: AsyncSession, as_of: date) -> dict[str, int]:
    """当日全市场涨停/跌停/炸板家数与有行情家数。"""
    row = (await db.execute(text(_BREADTH_SQL), {"as_of": as_of})).mappings().one()
    return {k: int(v or 0) for k, v in dict(row).items()}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_limit_up_repo.py -v -m e2e`
Expected: 2 passed。
同时手工核对黄金日数字（记录到 spec 的"实测证据"里）：
```bash
cd backend && uv run python -c "
import asyncio, datetime as dt
from app.core.database import async_session_factory
from app.repositories import limit_up_repo
from collections import Counter
async def m():
    async with async_session_factory() as db:
        as_of, prev, ws = dt.date(2026,9,8), dt.date(2026,9,7), dt.date(2026,8,18)
        rows = await limit_up_repo.fetch_limit_up_window(db, as_of, prev, ws)
        print('ladder', dict(sorted(Counter(r['streak_upto'] for r in rows if r['trade_date']==as_of and r['is_lu']).items(), reverse=True)))
        print('breadth', await limit_up_repo.fetch_day_breadth(db, as_of))
asyncio.run(m())
"
```
Expected（与 spec 记录一致）：`ladder {1: 56, 2: 13, 3: 3, 4: 3}`、
`breadth {'zt_count': 75, 'dt_count': 1, 'zb_count': 39, 'quoted': 5490}`。

> 这两组数字 2026-09-14 已用真 `stk_limit`（显式 fields）+ 真 `daily_quotes` 逐条复现。
> 注意：ladder 能复现**不代表** `streak_upto` 全对（见 `_WINDOW_SQL` 第 4 条，本窗口真实
> 存在 4 行虚高，只是不落在 09-08）；`boards_in_window`/`missing_days` 必须另外手查——
> `missing_days` 应是个位数，若出现 14 说明窗口又被截回两天了。

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/limit_up_repo.py backend/tests/test_limit_up_repo.py
git commit -m "feat(limit-up): 候选窗口 SQL + 扇出与查询计划守卫"
```

---

### Task 3: 策略纯函数（梯队 / N天M板 / 晋级率 / L3 聚合 / 情绪 KPI）

**Files:**
- Create: `backend/app/services/limit_up_calculator.py`
- Test: `backend/tests/test_limit_up_calculator.py`

**Interfaces:**
- Consumes: Task 2 的窗口行 dict（键名见 Task 2 Interfaces）
- Produces（全部纯函数，无 I/O）:
  - `LOOKBACK_TRADE_DAYS = 10`、`PARTIAL_QUOTE_FLOOR = 4000`、`NOISY_PROMOTION_N = 5`
  - `ladder(window_rows, as_of) -> list[dict]`（每项 `streak / label / stocks`）
  - `sector_ladder(window_rows, as_of) -> dict`（`items` + `unclassified_count`）
  - `promotion_rate(window_rows, as_of_prev, as_of, level) -> dict`（`rate / n / noisy`）
  - `yesterday_limit_up(window_rows, as_of_prev, as_of, market_days) -> dict`（`kpis` + `items`）
  - `sentiment_kpis(breadth, yzt, promo_1to2, promo_2to3, ladder_items) -> dict`
  - `is_partial(breadth) -> bool`

- [ ] **Step 1: 写失败的测试**

`backend/tests/test_limit_up_calculator.py`:

```python
"""连板策略纯函数单测（无 DB、无网络）。

用例编号对应 spec §3 的口径；其中 test_streak_is_one_for_first_board 与
test_suspended_days_do_not_break_streak 是两条曾真实踩过的坑。
"""

from datetime import date

from app.services import limit_up_calculator as calc

D1, D2, D3, D4, D5 = (date(2026, 9, d) for d in (1, 2, 3, 4, 5))


def _row(stock_id, d, *, is_lu, streak, symbol="000001", name="测试", close=10.0,
         pre_close=9.0, open_=9.2, amount=1.0, sw_l3="110703", sw_l3_name="生猪养殖",
         sw_l1="110000", sw_l1_name="农林牧渔"):
    return {
        "stock_id": stock_id, "symbol": symbol, "name": name, "trade_date": d,
        "close": close, "open": open_, "high": close, "amount": amount,
        "pre_close": pre_close, "up_limit": 11.0, "down_limit": 9.0,
        "is_lu": is_lu, "touched": is_lu, "streak_upto": streak,
        "sw_l3_code": sw_l3, "sw_l3_name": sw_l3_name,
        "sw_l1_code": sw_l1, "sw_l1_name": sw_l1_name,
    }


def test_streak_is_one_for_first_board():
    """首板必须是 1。

    反例：向量化写法的 (~is_lu).cumsum() + groupby(...).cumcount() 变体会把整档梯队
    错位一格、首板凭空消失（56 只首板显示成二连板）——这是本功能最致命的一种错法。
    """
    rows = [_row(1, D1, is_lu=True, streak=1)]
    assert [b["streak"] for b in calc.ladder(rows, D1)] == [1]
    assert calc.ladder(rows, D1)[0]["stocks"][0]["symbol"] == "000001"


def test_ladder_groups_by_streak_desc_with_high_tier_label():
    rows = [
        _row(1, D1, is_lu=True, streak=1, symbol="000001"),
        _row(2, D1, is_lu=True, streak=2, symbol="000002"),
        _row(3, D1, is_lu=True, streak=6, symbol="000003"),
        _row(4, D1, is_lu=False, streak=0, symbol="000004"),
    ]
    got = [(b["streak"], b["label"], len(b["stocks"])) for b in calc.ladder(rows, D1)]
    assert got == [(6, "6连板", 1), (2, "2连板", 1), (1, "首板", 1)]


def test_n_day_m_board_counts_market_days_not_stock_rows():
    """N天M板：M=涨停次数，N=市场交易日跨度（不是该股有行情的天数）。"""
    market_days = [D1, D2, D3, D4, D5]
    rows = [_row(1, D3, is_lu=True, streak=1), _row(1, D5, is_lu=True, streak=2)]
    span, boards = calc.n_day_m_board(rows, 1, D5, market_days)
    assert (boards, span) == (2, 3)          # 3天2板


def test_suspended_days_do_not_break_streak():
    """policy A：停牌日不计数也不截断（对齐东财 lbc）。

    实测依据：002274 华昌化工 窗口内 12 个交易日只有 7 天有行情；若把缺行当断板，
    本地数字会与用户拿来对照的第三方不一致。missing_days 必须如实下发。
    """
    market_days = [D1, D2, D3, D4, D5]
    rows = [_row(1, D1, is_lu=True, streak=1), _row(1, D4, is_lu=True, streak=2),
            _row(1, D5, is_lu=True, streak=3)]
    assert calc.ladder(rows, D5)[0]["stocks"][0]["streak"] == 3
    assert calc.missing_days(rows, 1, market_days) == 2      # 5 个市场日 - 3 天有行情


def test_promotion_rate_is_intersection_based():
    """1进2 的分子必须是交集：昨日首板 且 今日二板。"""
    rows = [
        _row(1, D4, is_lu=True, streak=1), _row(1, D5, is_lu=True, streak=2),   # 晋级
        _row(2, D4, is_lu=True, streak=1), _row(2, D5, is_lu=False, streak=0),  # 断板
        _row(3, D4, is_lu=True, streak=1), _row(3, D5, is_lu=True, streak=1),   # 断后回封
    ]
    promo = calc.promotion_rate(rows, D4, D5, level=1)
    assert promo == {"rate": 1 / 3, "n": 3, "noisy": True}


def test_promotion_rate_none_when_no_denominator():
    assert calc.promotion_rate([], D4, D5, level=1) == {"rate": None, "n": 0, "noisy": True}


def test_sector_ladder_picks_deterministic_leader_and_buckets_unmapped():
    """同板位龙头按 amount DESC, symbol ASC 裁决；映射不到的进 unclassified。"""
    rows = [
        _row(1, D5, is_lu=True, streak=2, symbol="000002", amount=5.0, sw_l3="110703"),
        _row(2, D5, is_lu=True, streak=2, symbol="000001", amount=5.0, sw_l3="110703"),
        _row(3, D5, is_lu=True, streak=1, symbol="000003", amount=9.0, sw_l3=None,
             sw_l3_name=None, sw_l1=None, sw_l1_name=None),
    ]
    got = calc.sector_ladder(rows, D5)
    assert got["unclassified_count"] == 1
    assert got["items"][0]["l3_name"] == "生猪养殖"
    assert got["items"][0]["leader_symbol"] == "000001"   # 同板同额 → symbol 升序
    assert got["items"][0]["max_streak"] == 2


def test_yesterday_limit_up_computes_today_pct_when_quoted():
    """今日有行情的昨日涨停股：按当日 pre_close 算 today_pct，且不得误判为停牌。"""
    rows = [
        _row(1, D4, is_lu=True, streak=1, pre_close=10.0),
        _row(1, D5, is_lu=False, streak=0, close=11.0, open_=10.5, pre_close=10.0),
    ]
    got = calc.yesterday_limit_up(rows, D4, D5, [D4, D5])
    item = got["items"][0]
    assert item["suspended"] is False
    assert item["today_pct"] == 10.0
    assert item["today_open_premium"] == 5.0
    assert got["kpis"]["yzt_avg_pct"] == 10.0


def test_yesterday_limit_up_reports_suspended_stock_as_null_not_zero():
    """今日停牌的昨日涨停股 today_* 一律 None，且 suspended=True（缺失 ≠ 0%）。

    反例：把无当日行的票默认成 today_pct=0.0，会被当成"平盘"混进均值与榜单且不报错
    （真实 0.00% 与缺失无法区分）；正确口径是排除在 measured 之外。
    注意 fixture **必须不包含该股的 D5 行**，否则 suspended=False、这条测试就成惰性用例。
    """
    rows = [_row(1, D4, is_lu=True, streak=1, pre_close=10.0)]   # D5 停牌：无当日行
    got = calc.yesterday_limit_up(rows, D4, D5, [D4, D5])
    item = got["items"][0]
    assert item["suspended"] is True
    assert item["today_pct"] is None
    assert item["today_open_premium"] is None
    assert item["today_streak"] is None
    assert item["broken"] is False
    assert got["kpis"] == {
        "n": 1, "measured": 0, "yzt_avg_pct": None, "yzt_avg_open_premium": None,
    }


def test_yesterday_pct_uses_today_pre_close_not_prev_row():
    """分母必须是 as_of **当日行**的 pre_close，不是 prev 行。

    取 prev 行 = 「今日 ÷ 前前日」的 2 日收益，静默多算一天。真库实测 2026-09-08：
    当日 pre_close → 2.8225%（95 只），昨日行 pre_close → 13.9488%。
    本用例让两行的 pre_close 不同，使这个错法必然被抓住。
    """
    rows = [
        _row(1, D4, is_lu=True, streak=1, close=10.0, pre_close=9.0),  # 前前日收 9.0
        _row(1, D5, is_lu=False, streak=0, close=10.5, open_=10.2, pre_close=10.0),
    ]
    item = calc.yesterday_limit_up(rows, D4, D5, [D4, D5])["items"][0]
    assert item["today_pct"] == 5.0          # (10.5-10.0)/10.0，而不是 (10.5-9.0)/9.0
    assert item["today_open_premium"] == 2.0
    assert item["broken"] is False            # 每个 item 都必须带 broken 键（停牌股也不例外）


def test_sentiment_kpis_broken_rate_uses_market_breadth():
    breadth = {"zt_count": 75, "dt_count": 1, "zb_count": 39, "quoted": 5490}
    kpis = calc.sentiment_kpis(
        breadth,
        {"yzt_avg_pct": 2.82, "yzt_avg_open_premium": 1.1, "n": 95},
        {"rate": 0.159, "n": 82, "noisy": False},
        {"rate": 0.31, "n": 13, "noisy": False},
        [{"streak": 4}],
    )
    assert kpis["zt_count"] == 75
    assert kpis["broken_rate"] == round(39 / 114, 4)
    assert kpis["max_streak"] == 4
    assert kpis["promo_1to2"] == 0.159


def test_is_partial_uses_quote_floor():
    assert calc.is_partial({"quoted": 5490}) is False
    assert calc.is_partial({"quoted": 12}) is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_limit_up_calculator.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.services.limit_up_calculator'`

- [ ] **Step 3: 实现纯函数**

`backend/app/services/limit_up_calculator.py`（新建）：

```python
"""连板/情绪策略纯函数——无 DB、无网络、无时钟依赖，全部可单测。

口径见 docs/design/limit-up-sentiment.md §3。这里只放"策略"，聚合已在 SQL 里做完
（gaps-and-islands 实测与逐行状态机同结果），因此本模块不重算连板。
"""

from __future__ import annotations

from datetime import date
from typing import Any

LOOKBACK_TRADE_DAYS = 10
PARTIAL_QUOTE_FLOOR = 4000  # 实测正常交易日 5,490 行/日
NOISY_PROMOTION_N = 5


def _today_rows(window_rows: list[dict[str, Any]], as_of: date) -> dict[int, dict[str, Any]]:
    return {r["stock_id"]: r for r in window_rows if r["trade_date"] == as_of}


def _limit_up_rows(window_rows: list[dict[str, Any]], d: date) -> list[dict[str, Any]]:
    return [r for r in window_rows if r["trade_date"] == d and r["is_lu"]]


def missing_days(window_rows: list[dict[str, Any]], stock_id: int, market_days: list[date]) -> int:
    """该股在窗口内缺失的交易日数（停牌/无行情），UI 用来披露停牌股。"""
    have = {r["trade_date"] for r in window_rows if r["stock_id"] == stock_id}
    return len([d for d in market_days if d not in have])


def n_day_m_board(
    window_rows: list[dict[str, Any]],
    stock_id: int,
    as_of: date,
    market_days: list[date],
    lookback: int = LOOKBACK_TRADE_DAYS,
) -> tuple[int, int]:
    """(days_span, boards_in_window)：最近 lookback 个市场交易日内的涨停跨度与次数。

    policy A：只数该股真实有行情的交易日；停牌日不计数也不截断。
    """
    window = [d for d in market_days if d <= as_of][-lookback:]
    allowed = set(window)
    lu_days = [
        r["trade_date"]
        for r in window_rows
        if r["stock_id"] == stock_id and r["is_lu"] and r["trade_date"] in allowed
    ]
    if not lu_days:
        return (0, 0)
    first = min(lu_days)
    span = len([d for d in market_days if first <= d <= as_of])
    return (span, len(lu_days))


def _label(streak: int) -> str:
    return "首板" if streak <= 1 else f"{streak}连板"


def ladder(window_rows: list[dict[str, Any]], as_of: date) -> list[dict[str, Any]]:
    """当日涨停按连板数降序分档（含首板），每档内按成交额降序、代码升序。"""
    rows = _limit_up_rows(window_rows, as_of)
    buckets: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        buckets.setdefault(int(r["streak_upto"]), []).append(r)
    out: list[dict[str, Any]] = []
    for streak in sorted(buckets, reverse=True):
        stocks = sorted(
            buckets[streak], key=lambda r: (-(r["amount"] or 0.0), r["symbol"])
        )
        out.append({"streak": streak, "label": _label(streak), "stocks": stocks})
    return out


def sector_ladder(window_rows: list[dict[str, Any]], as_of: date) -> dict[str, Any]:
    """申万 L3 维度的最高板排行；未映射标的进 unclassified_count 兜底桶。

    龙头裁决必须确定性（streak DESC, amount DESC, symbol ASC），否则两次相同请求
    之间「龙头」会跳变。
    """
    rows = _limit_up_rows(window_rows, as_of)
    groups: dict[str, list[dict[str, Any]]] = {}
    unclassified = 0
    for r in rows:
        code = r["sw_l3_code"]
        if code is None:
            unclassified += 1
            continue
        groups.setdefault(code, []).append(r)

    items: list[dict[str, Any]] = []
    for code, members in groups.items():
        leader = min(members, key=lambda r: (-int(r["streak_upto"]), -(r["amount"] or 0.0), r["symbol"]))
        items.append(
            {
                "l3_code": code,
                "l3_name": members[0]["sw_l3_name"],
                "l1_code": members[0]["sw_l1_code"],
                "l1_name": members[0]["sw_l1_name"],
                "max_streak": int(leader["streak_upto"]),
                "leader_symbol": leader["symbol"],
                "leader_name": leader["name"],
                "leader_streak": int(leader["streak_upto"]),
                "zt_count": len(members),
            }
        )
    items.sort(key=lambda i: (-i["max_streak"], -i["zt_count"], i["l3_code"]))
    return {"items": items, "unclassified_count": unclassified}


def promotion_rate(
    window_rows: list[dict[str, Any]], as_of_prev: date, as_of: date, level: int
) -> dict[str, Any]:
    """level 进 level+1 的晋级率：分子是「昨日 level 板 ∩ 今日 level+1 板」。"""
    prev = {
        r["stock_id"]
        for r in window_rows
        if r["trade_date"] == as_of_prev and r["is_lu"] and int(r["streak_upto"]) == level
    }
    today = _today_rows(window_rows, as_of)
    promoted = sum(
        1
        for s in prev
        if (t := today.get(s)) is not None and t["is_lu"] and int(t["streak_upto"]) == level + 1
    )
    n = len(prev)
    return {
        "rate": (promoted / n) if n else None,
        "n": n,
        "noisy": n < NOISY_PROMOTION_N,
    }


def yesterday_limit_up(
    window_rows: list[dict[str, Any]],
    as_of_prev: date,
    as_of: date,
    market_days: list[date],
) -> dict[str, Any]:
    """昨日涨停股今日表现 + 赚钱效应 KPI。

    涨跌幅基准必须取 **as_of 当日行**的 `pre_close`（= 上一交易日收盘，交易所口径、
    已含除权调整），不是 prev 行的。取 prev 行会变成「今日 ÷ 前前日」的 2 日收益：
    实测 2026-09-08（95 只昨日涨停股）从真值 2.8225% 变成 13.9488%，5 倍偏差且不报错。
    今日停牌的票 today_* 一律 None（缺失 ≠ 0%）。
    """
    prev = _limit_up_rows(window_rows, as_of_prev)
    today = _today_rows(window_rows, as_of)

    items: list[dict[str, Any]] = []
    pcts: list[float] = []
    premiums: list[float] = []
    for p in prev:
        t = today.get(p["stock_id"])
        pre = (
            None
            if t is None or t["pre_close"] is None
            else float(t["pre_close"])
        )
        item: dict[str, Any] = {
            "symbol": p["symbol"],
            "name": p["name"],
            "prev_streak": int(p["streak_upto"]),
            "sw_l3_name": p["sw_l3_name"],
            "missing_days": missing_days(window_rows, p["stock_id"], market_days),
            "today_pct": None,
            "today_open_premium": None,
            "today_streak": None,
            "is_lu": False,
            "touched": False,
            "broken": False,
            "suspended": t is None,
        }
        if t is not None and pre is not None:
            close = float(t["close"]) if t["close"] is not None else None
            open_ = float(t["open"]) if t["open"] is not None else None
            item["today_pct"] = None if close is None else round((close - pre) / pre * 100, 4)
            item["today_open_premium"] = (
                None if open_ is None else round((open_ - pre) / pre * 100, 4)
            )
            item["today_streak"] = int(t["streak_upto"]) if t["is_lu"] else 0
            item["is_lu"] = bool(t["is_lu"])
            item["touched"] = bool(t["touched"])
            item["broken"] = bool(t["touched"]) and not bool(t["is_lu"])
            if item["today_pct"] is not None:
                pcts.append(item["today_pct"])
            if item["today_open_premium"] is not None:
                premiums.append(item["today_open_premium"])
        items.append(item)

    # 缺失值（None / 停牌）必须排在最后：不能写 `or -999`——那会把真实的 0.00% 当成缺失。
    items.sort(
        key=lambda i: (
            -(i["prev_streak"]),
            -(i["today_pct"] if i["today_pct"] is not None else -999.0),
            i["symbol"],
        )
    )
    kpis = {
        "n": len(prev),
        "measured": len(pcts),
        "yzt_avg_pct": round(sum(pcts) / len(pcts), 4) if pcts else None,
        "yzt_avg_open_premium": round(sum(premiums) / len(premiums), 4) if premiums else None,
    }
    return {"kpis": kpis, "items": items}


def sentiment_kpis(
    breadth: dict[str, int],
    yzt: dict[str, Any],
    promo_1to2: dict[str, Any],
    promo_2to3: dict[str, Any],
    ladder_items: list[dict[str, Any]],
) -> dict[str, Any]:
    """情绪温度计：广度三计数 + 赚钱效应 + 晋级率 + 空间板高度。"""
    zt, zb = breadth["zt_count"], breadth["zb_count"]
    return {
        "zt_count": zt,
        "dt_count": breadth["dt_count"],
        "zb_count": zb,
        "broken_rate": round(zb / (zt + zb), 4) if (zt + zb) else None,
        "yzt_avg_pct": yzt["yzt_avg_pct"],
        "yzt_avg_open_premium": yzt["yzt_avg_open_premium"],
        "yzt_n": yzt["n"],
        "promo_1to2": promo_1to2["rate"],
        "promo_1to2_n": promo_1to2["n"],
        "promo_1to2_noisy": promo_1to2["noisy"],
        "promo_2to3": promo_2to3["rate"],
        "promo_2to3_n": promo_2to3["n"],
        "promo_2to3_noisy": promo_2to3["noisy"],
        "max_streak": max((b["streak"] for b in ladder_items), default=0),
    }


def is_partial(breadth: dict[str, int]) -> bool:
    """当日行情行数不足 → 数据不完整（部分 ingest），不得当作完整梯队展示。"""
    return breadth["quoted"] < PARTIAL_QUOTE_FLOOR
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_limit_up_calculator.py -v`
Expected: 12 passed。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/limit_up_calculator.py backend/tests/test_limit_up_calculator.py
git commit -m "feat(limit-up): 梯队/晋级率/板块聚合/情绪 KPI 纯函数与黄金用例"
```

---

### Task 4: 快照编排服务与 3 个只读端点（+ 日历 schema）

**Files:**
- Create: `backend/app/services/limit_up_service.py`、`backend/app/schemas/limit_up.py`
- Modify: `backend/app/api/v1/market_data.py`
- Test: `backend/tests/test_limit_up_service.py`

**Interfaces:**
- Consumes: Task 2 的两个查询函数；Task 3 的全部纯函数
- Produces:
  - `limit_up_service.get_snapshot(cache, as_of=None, lookback=LOOKBACK_TRADE_DAYS) -> dict`
    （键 `as_of, as_of_prev, source, limits_present, is_partial, sw_coverage, lookback, degraded_reason, breadth, kpis, echelons, sectors, yesterday, market_days`）
  - `limit_up_service.get_snapshot(cache, as_of=None, lookback=LOOKBACK_TRADE_DAYS) -> dict`
  - `limit_up_service.sector_payload / yesterday_payload`（2 个投影）
  - `api/v1/market_data.py`：`GET /limit-up-ladder`、`GET /sector-limit-up`、`GET /yesterday-limit-up`（**3 个端点**；`GET /sentiment/calendar` 归 Task 5——它依赖 Task 5 才建的 `market_sentiment_daily` 表与 `list_sentiment_calendar`，在 T4 注册会交付一个必 500 且无测试覆盖的路由）

- [ ] **Step 1: 写失败的测试（降级契约）**

`backend/tests/test_limit_up_service.py`:

```python
"""快照编排的降级契约（monkeypatch，无 DB/网络）。

source 由数据可用性决定，不由偏好决定；限价缺失时返回空 payload + 原因，
绝不输出按比例猜出来的涨停——详见 docs/design/limit-up-sentiment.md §5。
"""

from datetime import date
from typing import Any

import pytest

from app.services import limit_up_service as svc

D5 = date(2026, 9, 8)
D4 = date(2026, 9, 7)


@pytest.fixture
def _patch_sources(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {
        "limits": True,
        "window": [],
        "breadth": {"zt_count": 0, "dt_count": 0, "zb_count": 0, "quoted": 0},
        "trade_dates": [date(2026, 8, 18), D4, D5],
    }

    async def _latest(_db: Any) -> date:
        return D5

    async def _dates(_db: Any, _as_of: date, _limit: int) -> list[date]:
        return state["trade_dates"]

    async def _has(_db: Any, _as_of: date) -> bool:
        return bool(state["limits"])

    async def _window(_db: Any, **_kw: Any) -> list[dict[str, Any]]:
        return list(state["window"])

    async def _breadth(_db: Any, _as_of: date) -> dict[str, int]:
        return dict(state["breadth"])

    monkeypatch.setattr(svc.limit_up_repo, "latest_quote_date", _latest)
    monkeypatch.setattr(svc.limit_up_repo, "list_recent_trade_dates", _dates)
    monkeypatch.setattr(svc.limit_up_repo, "has_price_limits", _has)
    monkeypatch.setattr(svc.limit_up_repo, "fetch_limit_up_window", _window)
    monkeypatch.setattr(svc.limit_up_repo, "fetch_day_breadth", _breadth)
    return state


async def test_missing_price_limits_degrades_without_guessing(_patch_sources: dict[str, Any]) -> None:
    _patch_sources["limits"] = False
    _patch_sources["breadth"] = {"zt_count": 0, "dt_count": 0, "zb_count": 0, "quoted": 5490}
    snap = await svc.get_snapshot(cache=None, as_of=D5)
    assert snap["source"] == "local_calc"
    assert snap["limits_present"] is False
    assert snap["degraded_reason"] == "price_limits_missing"
    assert snap["echelons"] == [] and snap["yesterday"]["items"] == []


async def test_partial_day_is_flagged_not_hidden(_patch_sources: dict[str, Any]) -> None:
    _patch_sources["breadth"] = {"zt_count": 3, "dt_count": 0, "zb_count": 1, "quoted": 12}
    snap = await svc.get_snapshot(cache=None, as_of=D5)
    assert snap["is_partial"] is True
    assert snap["degraded_reason"] == "partial_day"


async def test_complete_day_reports_local_calc(_patch_sources: dict[str, Any]) -> None:
    _patch_sources["breadth"] = {"zt_count": 75, "dt_count": 1, "zb_count": 39, "quoted": 5490}
    snap = await svc.get_snapshot(cache=None, as_of=D5)
    assert snap["source"] == "local_calc"
    assert snap["limits_present"] is True and snap["is_partial"] is False
    assert snap["degraded_reason"] is None
    assert snap["kpis"]["zt_count"] == 75 and snap["kpis"]["zb_count"] == 39


async def test_empty_daily_quotes_returns_empty_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _none(_db: Any) -> None:
        return None

    monkeypatch.setattr(svc.limit_up_repo, "latest_quote_date", _none)
    snap = await svc.get_snapshot(cache=None)
    assert snap["degraded_reason"] == "no_quotes"
    assert snap["as_of"] is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_limit_up_service.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.services.limit_up_service'`

- [ ] **Step 3: 实现服务与 schema**

`backend/app/services/limit_up_service.py`:

```python
"""连板梯队与市场情绪：快照编排（唯一带 try/except 的层）。

source 由**数据可用性**决定，不由偏好决定：有完整限价 + 完整当日行情 → 本地自算（权威、
可回放）；否则退 Web 当日涨停池（仅此时有封板时间/封单）；两者都不可用 → 空 payload +
degraded_reason，**不猜**。读路径只读，绝不外呼写库。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from app.core.database import async_session_factory
from app.repositories import limit_up_repo
from app.services import limit_up_calculator as calc

if TYPE_CHECKING:
    from app.core.redis import CacheClient

logger = logging.getLogger(__name__)

SNAPSHOT_TTL = 300
CALENDAR_TTL = 900
_WINDOW_BUFFER = 6  # gaps-and-islands 需要的前置上下文（lookback 之外多取的交易日）


def window_trade_days(lookback: int) -> int:
    """窗口交易日数必须随 lookback 增长。

    硬编码 16 会让 lookback=30（端点允许）静默截断：streak 被窗口起点截平、
    N天M板数不到 lookback。key 已包含 lookback，所以窗口大小变化不会汄染缓存。
    """
    return lookback + _WINDOW_BUFFER


def _empty(as_of: date | None, reason: str) -> dict[str, Any]:
    return {
        "as_of": as_of,
        "as_of_prev": None,
        "source": "local_calc",
        "limits_present": False,
        "is_partial": False,
        "sw_coverage": None,
        "degraded_reason": reason,
        "market_days": [],
        "breadth": {"zt_count": 0, "dt_count": 0, "zb_count": 0, "quoted": 0},
        "kpis": {},
        "echelons": [],
        "sectors": {"items": [], "unclassified_count": 0},
        "yesterday": {"kpis": {}, "items": []},
    }


async def get_snapshot(
    cache: CacheClient | None, as_of: date | None = None, lookback: int = calc.LOOKBACK_TRADE_DAYS
) -> dict[str, Any]:
    """一次查询 → 一次快照 → 4 个投影（4 个端点共享同一份，保证口径同源）。"""
    async with async_session_factory() as db:
        target = as_of or await limit_up_repo.latest_quote_date(db)
        if target is None:
            return _empty(None, "no_quotes")
        key = f"market:limit-up:snapshot:{target.isoformat()}:{lookback}"
        if cache is not None:
            cached = await cache.get(key)
            if cached:
                return cached
        market_days = await limit_up_repo.list_recent_trade_dates(
            db, target, window_trade_days(lookback)
        )
        if len(market_days) < 2:
            return _empty(target, "insufficient_trade_days")
        breadth = await limit_up_repo.fetch_day_breadth(db, target)
        limits_present = await limit_up_repo.has_price_limits(db, target)
        snap = _empty(target, None)  # type: ignore[arg-type]
        snap["as_of_prev"] = market_days[-2]
        snap["limits_present"] = limits_present
        snap["is_partial"] = calc.is_partial(breadth)
        snap["breadth"] = breadth
        snap["market_days"] = market_days
        if not limits_present:
            snap["degraded_reason"] = "price_limits_missing"
            return snap
        rows = await limit_up_repo.fetch_limit_up_window(
            db, as_of=target, as_of_prev=market_days[-2], window_start=market_days[0]
        )
        if not rows:
            snap["degraded_reason"] = "no_limit_up_rows"
            return snap

    echelons = calc.ladder(rows, target)
    ladder_pcts = [
        {
            "stock_id": s["stock_id"],
            "symbol": s["symbol"],
            "name": s["name"],
            "streak": s["streak_upto"],
            "sw_l3_name": s["sw_l3_name"],
            "sw_l1_name": s["sw_l1_name"],
        }
        for b in echelons
        for s in b["stocks"]
    ]
    for item in ladder_pcts:
        span, boards = calc.n_day_m_board(rows, item["stock_id"], target, market_days, lookback)
        item["days_span"] = span
        item["boards_in_window"] = boards
        item["missing_days"] = calc.missing_days(rows, item["stock_id"], market_days)
        item["seal_time"] = None      # 本地路径不具备（Web 增强在 Task 6 注入）
        item["seal_fund"] = None
        item["break_count"] = None
    yesterday = calc.yesterday_limit_up(rows, market_days[-2], target, market_days)
    by_id = {i["stock_id"]: i for i in ladder_pcts}
    snap = {
        **snap,
        "echelons": [
            {
                "streak": b["streak"],
                "label": b["label"],
                "stocks": [by_id[s["stock_id"]] for s in b["stocks"]],
            }
            for b in echelons
        ],
        "sectors": calc.sector_ladder(rows, target),
        "yesterday": yesterday,
        "kpis": calc.sentiment_kpis(
            breadth,
            yesterday["kpis"],
            calc.promotion_rate(rows, market_days[-2], target, level=1),
            calc.promotion_rate(rows, market_days[-2], target, level=2),
            [{"streak": b["streak"]} for b in echelons],
        ),
    }
    mapped = sum(1 for i in ladder_pcts if i["sw_l3_name"])
    snap["sw_coverage"] = round(mapped / len(ladder_pcts), 4) if ladder_pcts else None
    if snap["is_partial"] and snap["degraded_reason"] is None:
        snap["degraded_reason"] = "partial_day"
    if cache is not None and snap["degraded_reason"] is None:
        await cache.set(key, snap, ttl=SNAPSHOT_TTL)
    return snap
```

（`window_rows` 现在是整窗（Task 2 已改）：`calc.*` 内部自己按 `as_of`/`as_of_prev` 筛，
`n_day_m_board`/`missing_days` 才能拿到 lookback 内的全部交易日。）

`backend/app/schemas/limit_up.py`：4 个 Pydantic 模型（字段名与 spec §5 一致）：

```python
"""连板梯队与市场情绪响应模型（字段名与 docs/design/limit-up-sentiment.md §5 对齐）。

`source` 只允许 `local_calc`：本地口径是唯一权威、可回放的路径（决策 3）。
东财 `hybk` 是**东财板块**口径，填不进申万 L3，所以 Web 不产出完整 payload，
只给封板时间/封单/炸板次数三个增强字段（Task 6）；不要给它编一个 source="web"。
"""

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel


class LadderStockOut(BaseModel):
    symbol: str
    name: str
    streak: int
    days_span: int
    boards_in_window: int
    missing_days: int
    sw_l1_name: str | None = None
    sw_l3_name: str | None = None
    seal_time: str | None = None      # 本地路径恒 None（前端渲染 --）
    seal_fund: float | None = None
    break_count: int | None = None


class EchelonOut(BaseModel):
    streak: int
    label: str
    stocks: list[LadderStockOut]


class SentimentKpisOut(BaseModel):
    zt_count: int
    dt_count: int
    zb_count: int
    broken_rate: float | None = None
    yzt_avg_pct: float | None = None
    yzt_avg_open_premium: float | None = None
    yzt_n: int = 0
    promo_1to2: float | None = None
    promo_1to2_n: int = 0
    promo_1to2_noisy: bool = True
    promo_2to3: float | None = None
    promo_2to3_n: int = 0
    promo_2to3_noisy: bool = True
    max_streak: int = 0


class LimitUpLadderOut(BaseModel):
    as_of: date | None
    as_of_prev: date | None
    source: Literal["local_calc"]
    limits_present: bool
    is_partial: bool
    sw_coverage: float | None = None
    lookback: int
    degraded_reason: str | None = None
    kpis: SentimentKpisOut | None = None
    echelons: list[EchelonOut] = []


class SectorLimitUpItemOut(BaseModel):
    l3_code: str
    l3_name: str | None = None
    l1_code: str | None = None
    l1_name: str | None = None
    max_streak: int
    leader_symbol: str
    leader_name: str | None = None
    leader_streak: int
    zt_count: int


class SectorLimitUpOut(BaseModel):
    as_of: date | None
    source: Literal["local_calc"]
    degraded_reason: str | None = None
    unclassified_count: int = 0
    items: list[SectorLimitUpItemOut] = []


class YesterdayLimitUpItemOut(BaseModel):
    symbol: str
    name: str
    prev_streak: int
    today_pct: float | None = None
    today_open_premium: float | None = None
    today_streak: int | None = None
    is_lu: bool = False
    touched: bool = False
    broken: bool = False
    suspended: bool = False
    missing_days: int = 0
    sw_l3_name: str | None = None


class YesterdayLimitUpOut(BaseModel):
    as_of: date | None
    as_of_prev: date | None
    source: Literal["local_calc"]
    degraded_reason: str | None = None
    kpis: dict[str, Any] = {}
    items: list[YesterdayLimitUpItemOut] = []


class SentimentCalendarPointOut(BaseModel):
    trade_date: date
    zt_count: int
    dt_count: int
    zb_count: int
    broken_rate: float | None = None
    yzt_avg_pct: float | None = None
    promo_1to2: float | None = None
    promo_2to3: float | None = None
    max_streak: int = 0
```

- [ ] **Step 4: 写 3 个端点**

`backend/app/api/v1/market_data.py` 追加：

```python
@router.get("/limit-up-ladder", response_model=LimitUpLadderOut)
async def get_limit_up_ladder(
    cache: CacheDep,
    date_: str | None = Query(default=None, alias="date", description="ISO 日期，缺省=库内最新交易日"),
    lookback: int = Query(default=10, ge=5, le=30),
) -> LimitUpLadderOut:
    """连板梯队 + 情绪温度计（本地 K 线自算为主，见 docs/design/limit-up-sentiment.md）。"""
    try:
        snap = await limit_up_service.get_snapshot(cache, _iso(date_), lookback)
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be ISO format, e.g. 2026-09-08") from None
    return LimitUpLadderOut(
        as_of=snap["as_of"], as_of_prev=snap["as_of_prev"], source=snap["source"],
        limits_present=snap["limits_present"], is_partial=snap["is_partial"],
        sw_coverage=snap["sw_coverage"], lookback=lookback,
        degraded_reason=snap["degraded_reason"],
        kpis=snap["kpis"] or None, echelons=snap["echelons"],
    )


@router.get("/sector-limit-up", response_model=SectorLimitUpOut)
async def get_sector_limit_up(
    cache: CacheDep,
    date_: str | None = Query(default=None, alias="date"),
    sw_l1: str | None = Query(default=None, description="按申万一级代码过滤，如 110000"),
) -> SectorLimitUpOut:
    snap = await limit_up_service.get_snapshot(cache, _iso(date_))
    payload = limit_up_service.sector_payload(snap, sw_l1)
    return SectorLimitUpOut(**payload)


@router.get("/yesterday-limit-up", response_model=YesterdayLimitUpOut)
async def get_yesterday_limit_up(
    cache: CacheDep, date_: str | None = Query(default=None, alias="date")
) -> YesterdayLimitUpOut:
    snap = await limit_up_service.get_snapshot(cache, _iso(date_))
    return YesterdayLimitUpOut(**limit_up_service.yesterday_payload(snap))
```

_iso(...)：本模块新增一个小 helper（**目前不存在**，现有 `get_*_endpoint` 的日期解析在 service 层、
端点只接 `ValueError` 转 400）。在 `market_data.py` 顶部 import 处补 `from datetime import date, datetime`，
并在路由前定义：

```python
def _iso(v: str | None) -> date | None:
    """ISO 日期 → date；解析失败抛 ValueError（由各端点转 400，与 /dragon-tiger 同写法）。"""
    return datetime.fromisoformat(v).date() if v else None
```
`limit_up_service` 再补两个投影函数：

```python
def sector_payload(snap: dict[str, Any], sw_l1: str | None = None) -> dict[str, Any]:
    """申万 L3 最高板投影；`sw_l1` 过滤为客户端维度，缓存不受其影响。"""
    items = snap["sectors"]["items"]
    if sw_l1:
        items = [i for i in items if i["l1_code"] == sw_l1]
    return {
        "as_of": snap["as_of"], "source": snap["source"],
        "degraded_reason": snap["degraded_reason"],
        "unclassified_count": snap["sectors"]["unclassified_count"], "items": items,
    }


def yesterday_payload(snap: dict[str, Any]) -> dict[str, Any]:
    """昨日涨停今日表现投影。"""
    return {
        "as_of": snap["as_of"], "as_of_prev": snap["as_of_prev"], "source": snap["source"],
        "degraded_reason": snap["degraded_reason"],
        "kpis": snap["yesterday"]["kpis"], "items": snap["yesterday"]["items"],
    }
```

> `get_calendar` 与 `GET /sentiment/calendar` **不在本任务**（见 Files 说明：它们归 Task 5，因为依赖 Task 5 才建的 `market_sentiment_daily` 表与 `limit_up_repo.list_sentiment_calendar`）。`SentimentCalendarPointOut` schema 仍在本任务定义（Task 5 直接用它）。

- [ ] **Step 5: 跑测试与全量检查**

Run: `cd backend && uv run pytest tests/test_limit_up_service.py -v && uv run pytest -q`
Expected: 4 passed；全量无回归。
Run: `cd backend && uv run --extra dev ruff check . && uv run --extra dev mypy app`
Expected: 无问题。

- [ ] **Step 6: 实机验证端点（真库）**

```bash
cd backend && uv run python -c "
import asyncio, json
from app.services import limit_up_service
async def m():
    snap = await limit_up_service.get_snapshot(None, __import__('datetime').date(2026,9,8))
    print('source', snap['source'], '| partial', snap['is_partial'], '| kpis', snap['kpis'])
    print('ladder', [(e['streak'], e['label'], len(e['stocks'])) for e in snap['echelons']])
    print('sectors top3', snap['sectors']['items'][:3])
    print('yesterday kpis', snap['yesterday']['kpis'])
    print('sw_coverage', snap['sw_coverage'])   # 实测 70/75 = 0.9333
    e = snap['echelons'][0]['stocks'][0]
    print('spac板', e['symbol'], e['streak'], e['days_span'], e['boards_in_window'], e['missing_days'])
asyncio.run(m())
"
```
Expected：`source local_calc`；梯队含 4 板×3 / 3 板×3 / 2 板×13 / 首板×56；`kpis.zt_count=75 / zb_count=39 / dt_count=1`；
`yesterday kpis ≈ {n:95, yzt_avg_pct:2.82, yzt_avg_open_premium:3.11}`；`sw_coverage ≈ 0.9333`；
空间板 `days_span/boards_in_window` 应为 4/4（若出现 1/1 或 `missing_days=14` → 窗口又被截成两天了）。

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/limit_up_service.py backend/app/schemas/limit_up.py \
  backend/app/api/v1/market_data.py backend/tests/test_limit_up_service.py
git commit -m "feat(limit-up): 快照编排服务与 4 个只读端点（可用性驱动 source + 降级契约）"
```

---

### Task 5: 盘后情绪落库与情绪周期端点

**Files:**
- Modify: `backend/app/models/market_data.py`（+`MarketSentimentDaily`）
- Create: `backend/app/migrations/versions/b8d2e1c3f4a5_add_market_sentiment_daily.py`
- Modify: `backend/app/repositories/limit_up_repo.py`（+`upsert_sentiment_daily` / `list_sentiment_calendar`）
- Modify: `backend/app/services/limit_up_service.py`（+`persist_snapshot`）
- Modify: `backend/app/schemas/task.py`、`workers/market_data_worker.py`、`scheduler/jobs.py`、`scheduler/runner.py`
- Test: `backend/tests/test_limit_up_service.py`（追加 persist 单测）

**Interfaces:**
- Consumes: Task 4 的 `get_snapshot`
- Produces: `limit_up_service.persist_snapshot(db, cache, as_of=None) -> dict`（返回 `{"status","trade_date","zt_count"}`）；`limit_up_service.get_calendar(cache, days=30) -> list[dict]`；`limit_up_repo.list_sentiment_calendar(db, days) -> list[dict]`；端点 `GET /sentiment/calendar`

- [ ] **Step 1: 写失败的测试**

`backend/tests/test_limit_up_service.py` 追加：

```python
async def test_persist_skips_partial_day(_patch_sources: dict[str, Any]) -> None:
    """部分 ingest 的当日不得写入情绪表（否则时序图会永远带着一个假低谷）。"""
    _patch_sources["breadth"] = {"zt_count": 3, "dt_count": 0, "zb_count": 1, "quoted": 12}
    got = await svc.persist_snapshot(db=None, cache=None, as_of=D5)  # type: ignore[arg-type]
    assert got["status"] == "skipped"
    assert got["reason"] == "partial_day"


async def test_persist_skips_empty_candidate_day(
    _patch_sources: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """kpis 为空的降级快照（no_limit_up_rows）必须跳过，不能拿 k["zt_count"] 取 KeyError。"""
    _patch_sources["window"] = []  # 候选集为空 → 限价在、但无涨停行
    _patch_sources["breadth"] = {"zt_count": 0, "dt_count": 0, "zb_count": 0, "quoted": 5490}
    called = False

    async def _boom(*_a: Any, **_kw: Any) -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(svc.limit_up_repo, "upsert_sentiment_daily", _boom)
    got = await svc.persist_snapshot(db=None, cache=None, as_of=D5)  # type: ignore[arg-type]
    assert got == {"status": "skipped", "reason": "no_limit_up_rows"}
    assert called is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_limit_up_service.py::test_persist_skips_partial_day -v`
Expected: FAIL —— `AttributeError: ... has no attribute 'persist_snapshot'`

- [ ] **Step 3: 加模型与迁移**

`backend/app/models/market_data.py` 追加：

```python
class MarketSentimentDaily(Base):
    """短线情绪周期聚合（约 10 个数字/交易日）。

    纯派生缓存：可随时由 daily_quotes + stock_price_limits 重建，绝不是真相来源。
    存在的唯一理由是跨月时序图重算昂贵——逐日明细不落表，因为 daily_quotes 本身就是历史。
    """

    __tablename__ = "market_sentiment_daily"
    __table_args__ = (UniqueConstraint("trade_date", name="uq_sentiment_daily_date"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    zt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    zb_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    broken_rate: Mapped[float | None] = mapped_column(Float)
    yzt_avg_pct: Mapped[float | None] = mapped_column(Float)
    promo_1to2: Mapped[float | None] = mapped_column(Float)
    promo_1to2_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    promo_2to3: Mapped[float | None] = mapped_column(Float)
    promo_2to3_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_streak_symbol: Mapped[str | None] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="local_calc")
```

迁移（`--rev-id b8d2e1c3f4a5`，`down_revision = "a7c1f0b2d3e4"`）按模型逐列 `op.create_table` 写出，`UniqueConstraint("trade_date", name="uq_sentiment_daily_date")`。

- [ ] **Step 4: repo + service + 接线（含日历端点）**

`limit_up_repo.py` 追加：

```python
async def upsert_sentiment_daily(db: AsyncSession, row: dict[str, Any]) -> int:
    stmt = pg_insert(MarketSentimentDaily).values(**row).on_conflict_do_update(
        constraint="uq_sentiment_daily_date",
        set_={k: v for k, v in row.items() if k not in ("trade_date", "source")},
    )
    result = cast("CursorResult[Any]", await db.execute(stmt))
    await db.flush()
    return int(result.rowcount)


async def list_sentiment_calendar(db: AsyncSession, days: int) -> list[dict[str, Any]]:
    """近 N 个交易日情绪时序（升序），供周期图消费。"""
    stmt = (
        select(MarketSentimentDaily)
        .order_by(MarketSentimentDaily.trade_date.desc())
        .limit(days)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return [
        {
            "trade_date": r.trade_date,
            "zt_count": r.zt_count, "dt_count": r.dt_count, "zb_count": r.zb_count,
            "broken_rate": r.broken_rate, "yzt_avg_pct": r.yzt_avg_pct,
            "promo_1to2": r.promo_1to2, "promo_2to3": r.promo_2to3,
            "max_streak": r.max_streak,
        }
        for r in reversed(rows)
    ]
```

`limit_up_service.py` 追加：

```python
async def persist_snapshot(
    db: AsyncSession | None, cache: CacheClient | None, as_of: date | None = None
) -> dict[str, Any]:
    """盘后把当日情绪聚合写入派生表（幂等，可重复跑）。

    is_partial / 限价缺失 / 空候选 / 无行情一律不写：时序图里塞进一个假低谷比缺一天更糟；
    `no_limit_up_rows`（快照 kpis 为空）漏在名单外会直接 `k["zt_count"]` KeyError。
    """
    snap = await get_snapshot(cache, as_of)
    if snap["degraded_reason"] in (
        "price_limits_missing",
        "partial_day",
        "no_quotes",
        "insufficient_trade_days",
        "no_limit_up_rows",
    ):
        return {"status": "skipped", "reason": snap["degraded_reason"]}
    k = snap["kpis"]
    leaders = snap["echelons"][0]["stocks"] if snap["echelons"] else []
    # 缓存命中时 snap 是 Redis JSON 回读的，as_of 已变成 str；直接落库会抛
    # asyncpg `'str' object has no attribute 'toordinal'`（真库复现过）。
    trade_date = date.fromisoformat(str(snap["as_of"]))
    row = {
        "trade_date": trade_date, "zt_count": k["zt_count"], "dt_count": k["dt_count"],
        "zb_count": k["zb_count"], "broken_rate": k["broken_rate"],
        "yzt_avg_pct": k["yzt_avg_pct"], "promo_1to2": k["promo_1to2"],
        "promo_1to2_n": k["promo_1to2_n"], "promo_2to3": k["promo_2to3"],
        "promo_2to3_n": k["promo_2to3_n"], "max_streak": k["max_streak"],
        "max_streak_symbol": leaders[0]["symbol"] if leaders else None,
        "source": snap["source"],
    }
    if db is None:
        async with async_session_factory() as session:
            await limit_up_repo.upsert_sentiment_daily(session, row)
            await session.commit()
    else:
        await limit_up_repo.upsert_sentiment_daily(db, row)
    return {"status": "ok", "trade_date": trade_date, "zt_count": k["zt_count"]}
```

接线：`MarketDataJobType` 加 `"sentiment_daily"`；worker 加分支（**传 `cache=None`**：盘后任务不需要
缓存，且可避开上一条的 str/date 转换）；`scheduler/jobs.py` 加 `sentiment_daily_job`（17:15，晚于 16:50 的限价补漏）。
日历端点（`get_calendar` + `GET /sentiment/calendar`）在**本任务**实现（T4 只交付 3 个端点、只定义 `SentimentCalendarPointOut` schema）：它依赖本任务才建的 `market_sentiment_daily` 表与 `list_sentiment_calendar`，放在 T4 会交付一个必 500 且无覆盖的路由。

```python
    # Short-term sentiment snapshot (limit-up ladder cycle): 17:15 Mon-Fri
    scheduler.add_job(
        sentiment_daily_job,
        CronTrigger(day_of_week="mon-fri", hour=17, minute=15, timezone="Asia/Shanghai"),
        id="sentiment_daily",
        name="Market sentiment daily",
        replace_existing=True,
    )
```

- [ ] **Step 5: 跑测试与实机验证**

Run: `cd backend && uv run pytest tests/test_limit_up_service.py -v`
Expected: 6 passed。
Run: `cd backend && uv run alembic upgrade head && uv run alembic heads`
Expected: `b8d2e1c3f4a5 (head)`，单 head。
Run:
```bash
cd backend && uv run python -c "
import asyncio
from app.core.database import async_session_factory
from app.services import limit_up_service
async def m():
    async with async_session_factory() as db:
        print(await limit_up_service.persist_snapshot(db, None))
        await db.commit()
asyncio.run(m())
"
cd backend && uv run python -c "
import asyncio
from app.services import limit_up_service
async def m():
    print(await limit_up_service.get_calendar(None, 5))
asyncio.run(m())
"
```
Expected: `{'status': 'ok', 'trade_date': ..., 'zt_count': 75}`；日历返回该日一行。

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/market_data.py backend/app/migrations/versions/b8d2e1c3f4a5_add_market_sentiment_daily.py \
  backend/app/repositories/limit_up_repo.py backend/app/services/limit_up_service.py \
  backend/app/schemas/task.py backend/app/workers/market_data_worker.py \
  backend/app/scheduler/jobs.py backend/app/scheduler/runner.py backend/tests/test_limit_up_service.py
git commit -m "feat(limit-up): 情绪周期落库与 sentiment/calendar 端点"
```

---

### Task 6: 东财 Web 增强（封板时间/封单/炸板次数）与对账

**Files:**
- Modify: `backend/app/core/providers/eastmoney_client.py`（+`fetch_limit_up_pool`）
- Modify: `backend/app/services/limit_up_service.py`（+`enrich_from_web`，接入 `get_snapshot`）
- Test: `backend/tests/test_limit_up_web.py`

**Interfaces:**
- Consumes: `EastmoneyClient._get_json`（既有，含节流与一次传输层重试）
- Produces:
  - `EastmoneyClient.fetch_limit_up_pool(trade_date: str) -> list[dict]`（`trade_date` **必填**）
    每项键：`symbol, name, streak, days, boards, seal_time, seal_fund, break_count, board_name, amount`
    （`board_name` = 东财 `hybk`，是**东财板块**口径，不能当申万 L3 用；不映射换手率 `hs`）
  - `limit_up_service.enrich_from_web(snapshot, trade_date) -> dict`（就地为匹配 symbol 注入三字段；任何异常只 log）

- [ ] **Step 1: 写失败的测试**

`backend/tests/test_limit_up_web.py`:

```python
"""东财涨停池映射与对账（monkeypatch，无网络）。

Web 只作增强/兜底：它给得出封板时间与封单，给不出权威连板链（lbc 是东财自己的口径）。
本地自算与 Web 的 streak 允许存在口径差，但差异必须可测、可观测，不能静默。
"""

from app.core.providers.eastmoney_client import EastmoneyClient

RAW = {
    "data": {
        "tc": 2,
        "pool": [
            {"c": "000981", "m": 0, "n": "山子高科", "zdp": 10.038, "amount": 773003856,
             "lbc": 1, "fbt": 92500, "lbt": 92500, "fund": 454003010, "zbc": 0,
             "hybk": "汽车零部", "zttj": {"days": 1, "ct": 1}},
            {"c": "600354", "m": 1, "n": "敦煌种业", "zdp": 9.98, "amount": 12345678,
             "lbc": 4, "fbt": 100312, "lbt": 144500, "fund": 81200000, "zbc": 2,
             "hybk": "种植业", "zttj": {"days": 4, "ct": 4}},
        ],
    }
}


def test_map_zt_pool_row_normalizes_price_scale_and_times():
    """东财 p/fbt/lbt 是定点整数：价格 ×1000、时间 HHMMSS 需零填充。"""
    rows = [EastmoneyClient._map_zt_pool_row(d) for d in RAW["data"]["pool"]]
    assert rows[0] == {
        "symbol": "000981", "name": "山子高科", "streak": 1, "days": 1, "boards": 1,
        "seal_time": "09:25:00", "seal_fund": 454003010.0, "break_count": 0,
        "board_name": "汽车零部", "amount": 773003856.0,
    }
    assert rows[1]["seal_time"] == "10:03:12"
    assert rows[1]["streak"] == 4 and rows[1]["break_count"] == 2


def test_map_zt_pool_row_tolerates_missing_zttj():
    row = {"c": "000001", "m": 0, "n": "X", "zdp": 1.0, "amount": 1, "lbc": 2,
           "fbt": 93000, "fund": 0, "zbc": 1}
    got = EastmoneyClient._map_zt_pool_row(row)
    assert got["days"] is None and got["boards"] is None


async def test_enrich_injects_seal_fields_only_for_matched_symbols():
    from app.services import limit_up_service as svc

    snapshot = {"echelons": [{"streak": 1, "label": "首板", "stocks": [
        {"symbol": "000981", "seal_time": None, "seal_fund": None, "break_count": None},
        {"symbol": "999999", "seal_time": None, "seal_fund": None, "break_count": None},
    ]}]}
    web = [{"symbol": "000981", "seal_time": "09:25:00", "seal_fund": 1.0, "break_count": 0}]
    out = svc.apply_web_enrichment(snapshot, web)
    assert out["echelons"][0]["stocks"][0]["seal_time"] == "09:25:00"
    assert out["echelons"][0]["stocks"][1]["seal_time"] is None   # 未匹配 → 保持 None


async def test_web_failure_never_raises(monkeypatch):
    from app.services import limit_up_service as svc

    called = False

    async def _boom(*_a, **_kw):
        nonlocal called
        called = True
        raise RuntimeError("eastmoney down")

    monkeypatch.setattr(svc, "_fetch_web_pool", _boom)
    # echelons 必须非空：空快照会走早退分支，`_boom` 根本不被调用，测不到异常路径。
    stock = {"symbol": "000981", "seal_time": None, "seal_fund": None, "break_count": None}
    snapshot = {"echelons": [{"streak": 1, "label": "首板", "stocks": [stock]}],
                "degraded_reason": None}
    got = await svc.enrich_from_web(snapshot, "20260908")
    assert called is True
    assert got["degraded_reason"] is None    # 增强失败不改变主路径状态
    assert got["echelons"][0]["stocks"][0]["seal_time"] is None   # 三字段保持 null → 前端 `--`


async def test_enrich_skipped_when_snapshot_degraded(monkeypatch):
    """降级快照不得触发外呼：本来就没数据，再去请求 Web 是白花钱 + 白堵请求。"""
    from app.services import limit_up_service as svc

    called = False

    async def _boom(*_a, **_kw):
        nonlocal called
        called = True
        raise RuntimeError("should not be called")

    monkeypatch.setattr(svc, "_fetch_web_pool", _boom)
    snapshot = {"echelons": [{"streak": 1, "label": "首板", "stocks": [{"symbol": "1"}]}],
                "degraded_reason": "partial_day"}
    got = await svc.enrich_from_web(snapshot, "20260908")
    assert got["degraded_reason"] == "partial_day"
    assert called is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_limit_up_web.py -v`
Expected: FAIL —— `AttributeError: type object 'EastmoneyClient' has no attribute '_map_zt_pool_row'`

- [ ] **Step 3: 实现客户端方法**

`eastmoney_client.py` 追加（端点、`date` 过滤、零填充与 `rc` 行为均 2026-09-14 实机 curl 复验）：

实测事实（写进代码注释，避免后人"优化"掉）：
- `date=YYYYMMDD` **必需**：不带 `date` 时返回 `{"rc":102,"data":null}`，而 `_get_json` 会把
  `rc not in (0, None)` 当错误抛 `RuntimeError`；把参数设成必填就不靠调用方自觉。
- 历史日期可查（实测 20260907/20260908/20260914 → `tc` 93/73/55，集合不同）。
- **但只限最近约 20 个交易日**：实测 20260825 有行，而 20260820/20260814/20260807/20260707 均 `rc=0 / pool=[]`。因此对更早的 `as_of`，增强会静默返回空、`seal_*` 保持 `null`——这是**预期行为**（该端点不是历史源），不是故障；不要为此加重试或把空池当错。
- 响应里的 `qdate` **恒为当天**、与请求的 `date` 无关，不要拿它做校验。
- 该路径只在 `push2ex` 可用；`push2delay`（本模块其他端点的域）对 `/getTopicZTPool` 返回**空**。
  （与其他 push2 端点"push2delay 优先"的注释相反，这是可接受的例外，需注明。）

```python
    ZT_POOL_PATH = "/getTopicZTPool"
    ZT_POOL_BASE = "https://push2ex.eastmoney.com"

    @staticmethod
    def _map_zt_pool_row(d: dict[str, Any]) -> dict[str, Any]:
        """东财涨停池原始行 → 归一字段。

        定点整数：p=价格×1000、fbt/lbt=HHMMSS（需零填充）、fund/amount 已是元。
        zttj{days,ct} 是可缺的统计块（个别票缺"N天M板"），缺则置 None 而不是造 0。
        """
        def _t(v: Any) -> str | None:
            if v in (None, "", 0):
                return None
            s = str(int(v)).zfill(6)
            return f"{s[:2]}:{s[2:4]}:{s[4:6]}"

        zttj = d.get("zttj") or {}
        return {
            "symbol": str(d.get("c")),
            "name": d.get("n"),
            "streak": _num(d.get("lbc")),
            "days": zttj.get("days"),
            "boards": zttj.get("ct"),
            "seal_time": _t(d.get("fbt")),
            "seal_fund": _num(d.get("fund")),
            "break_count": _num(d.get("zbc")),
            "board_name": d.get("hybk"),
            "amount": _num(d.get("amount")),
        }

    async def fetch_limit_up_pool(self, trade_date: str) -> list[dict[str, Any]]:
        """东财涨停股池（含封板时间/封单资金/炸板次数）——本地日线给不出的三字段。

        `trade_date` 必填（YYYYMMDD）：不带 date 服务端返回 rc=102/data=null。
        仅在增强路径调用；失败由调用方兜住，不参与主路径。
        """
        params: dict[str, Any] = {
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "dpt": "wz.ztzt",
            "Pageindex": 0,
            "pagesize": 5000,
            "sort": "fbt:asc",
            "date": trade_date,
        }
        data = await self._get_json(self.ZT_POOL_BASE, self.ZT_POOL_PATH, params)
        pool = ((data.get("data") or {}) or {}).get("pool") or []
        return [self._map_zt_pool_row(d) for d in pool]
```

- [ ] **Step 4: 实现增强接入**

`limit_up_service.py` 追加：

```python
async def _fetch_web_pool(trade_date: str) -> list[dict[str, Any]]:
    from app.core.providers.eastmoney_client import get_eastmoney_client  # noqa: PLC0415

    return await get_eastmoney_client().fetch_limit_up_pool(trade_date)


def apply_web_enrichment(snapshot: dict[str, Any], web_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """把 Web 的封板时间/封单/炸板次数注入匹配 symbol；未匹配保持 None。"""
    by_symbol = {r["symbol"]: r for r in web_rows}
    for echelon in snapshot.get("echelons", []):
        for stock in echelon["stocks"]:
            web = by_symbol.get(stock["symbol"])
            if web is None:
                continue
            stock["seal_time"] = web["seal_time"]
            stock["seal_fund"] = web["seal_fund"]
            stock["break_count"] = web["break_count"]
    return snapshot


async def enrich_from_web(snapshot: dict[str, Any], trade_date: str) -> dict[str, Any]:
    """附加封板信息（增强字段只能来自 Web）；任何失败仅 log，主路径状态不变。

    取数按 **as_of**（不是"今天"）：daily_quotes 只回补到上一个工作日，
    `latest_quote_date` 永远不是今天，所以 `target == 今日 && 盘中` 的触发条件恒为假（死代码）；
    且该端点支持历史日期，按 as_of 取数既正确又不需要交易时段判断。
    """
    if not snapshot.get("echelons") or snapshot.get("degraded_reason"):
        return snapshot
    try:
        web_rows = await _fetch_web_pool(trade_date)
    except Exception:  # noqa: BLE001 —— 增强失败不得影响主路径
        logger.warning("limit-up web enrichment failed; seal fields stay null", exc_info=True)
        return snapshot
    return apply_web_enrichment(snapshot, web_rows)
```

在 `get_snapshot` 的**缓存写入之前**接一行：

```python
    if snap["degraded_reason"] is None:
        snap = await enrich_from_web(snap, target.strftime("%Y%m%d"))
```

缓存 key **不需要** `:intraday` 后缀：`as_of` 一定是已收盘的交易日（quotes 只到上一个工作日），
Web 对该日的封板数据是终态，不存在"盘中结果污染收盘读数"。同一天多次请求共享同一份缓存（TTL 300s），
代价是每个未命中缓存的请求会多一次被节流的 push2ex 调用（~0.3s，失败静默回退）。

- [ ] **Step 5: 跑测试与对账（真库 + 真网络）**

Run: `cd backend && uv run pytest tests/test_limit_up_web.py -v`
Expected: 5 passed。
对账（人工核对，把结论写进 Changelog 的"验证"段；**两集合差异必须记录**）：
```bash
cd backend && uv run python -c "
import asyncio, datetime as dt
from app.core.database import async_session_factory
from app.core.providers.eastmoney_client import get_eastmoney_client
from app.repositories import limit_up_repo
async def m():
    web = await get_eastmoney_client().fetch_limit_up_pool('20260908')
    async with async_session_factory() as db:
        rows = await limit_up_repo.fetch_limit_up_window(db, dt.date(2026,9,8), dt.date(2026,9,7), dt.date(2026,8,18))
    local = {r['symbol'] for r in rows if r['trade_date']==dt.date(2026,9,8) and r['is_lu']}
    wsym = {w['symbol'] for w in web}
    print('local', len(local), 'web', len(wsym), 'only_local', sorted(local-wsym)[:10], 'only_web', sorted(wsym-local)[:10])
asyncio.run(m())
"
```
Expected: 两集合高度一致（差异只应来自数据源停更/口径时间差）；**差异必须记录**，不得静默忽略。

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/providers/eastmoney_client.py backend/app/services/limit_up_service.py backend/tests/test_limit_up_web.py
git commit -m "feat(limit-up): 东财涨停池盘中增强（封板时间/封单/炸板次数）"
```

---

### Task 7: 前端 API 层与 4 个组件 + 情绪 Tab

**Files:**
- Create: `frontend/src/shared/api/limitUp.ts`
- Create: `frontend/src/features/market/components/{SentimentHeader,LimitUpLadder,SwL3LimitUpBoard,YesterdayLimitUp}.tsx`
- Modify: `frontend/src/features/market/components/index.ts`、`frontend/src/pages/market/index.tsx`
- Modify: `frontend/src/shared/ui/SectionCard.tsx`（+ 可选 `asof` 属性）、`frontend/src/shared/ui/date.ts`（新建，`formatCnDate` 共享实现）
- Modify: `frontend/src/features/market/components/format.ts`（`formatCnDate` 改为从 `shared/ui/date.ts` 转发，保留既有导出名）

**Interfaces:**
- Consumes: Task 4/5/6 的 4 个端点
- Produces:
  - `fetchLimitUpLadder(date?: string, lookback?: number): Promise<LimitUpLadder>`
  - `fetchSectorLimitUp(date?: string, swL1?: string): Promise<SectorLimitUp>`
  - `fetchYesterdayLimitUp(date?: string): Promise<YesterdayLimitUp>`
  - `fetchSentimentCalendar(days?: number): Promise<SentimentCalendarPoint[]>`
  - 组件：`SentimentHeader`（props `kpis`）、`LimitUpLadder`（`echelons`）、`SwL3LimitUpBoard`（`data`）、`YesterdayLimitUp`（`data`）

- [ ] **Step 1: 写 API 层**

`frontend/src/shared/api/limitUp.ts`:

```ts
import { apiGet } from "./client";

// ---- 后端原始 payload（snake_case，只在 mapper 里解包） ----

interface BackendLadderStock {
  symbol: string;
  name: string;
  streak: number;
  days_span: number;
  boards_in_window: number;
  missing_days: number;
  sw_l1_name: string | null;
  sw_l3_name: string | null;
  seal_time: string | null;
  seal_fund: number | null;
  break_count: number | null;
}

interface BackendKpis {
  zt_count: number;
  dt_count: number;
  zb_count: number;
  broken_rate: number | null;
  yzt_avg_pct: number | null;
  yzt_avg_open_premium: number | null;
  yzt_n: number;
  promo_1to2: number | null;
  promo_1to2_n: number;
  promo_1to2_noisy: boolean;
  promo_2to3: number | null;
  promo_2to3_n: number;
  promo_2to3_noisy: boolean;
  max_streak: number;
}

interface BackendLadder {
  as_of: string | null;
  as_of_prev: string | null;
  source: "local_calc";
  limits_present: boolean;
  is_partial: boolean;
  sw_coverage: number | null;
  degraded_reason: string | null;
  kpis: BackendKpis | null;
  echelons: Array<{ streak: number; label: string; stocks: BackendLadderStock[] }>;
}

export interface LadderStock {
  symbol: string;
  name: string;
  streak: number;
  daysSpan: number;
  boardsInWindow: number;
  missingDays: number;
  swL1Name: string | null;
  swL3Name: string | null;
  sealTime: string | null;
  sealFund: number | null;
  breakCount: number | null;
}

export interface SentimentKpis {
  ztCount: number;
  dtCount: number;
  zbCount: number;
  brokenRate: number | null;
  yztAvgPct: number | null;
  yztAvgOpenPremium: number | null;
  yztN: number;
  promo1to2: number | null;
  promo1to2N: number;
  promo1to2Noisy: boolean;
  promo2to3: number | null;
  promo2to3N: number;
  promo2to3Noisy: boolean;
  maxStreak: number;
}

export interface LimitUpLadder {
  asOf: string | null;
  asOfPrev: string | null;
  source: "local_calc";
  limitsPresent: boolean;
  isPartial: boolean;
  swCoverage: number | null;
  degradedReason: string | null;
  kpis: SentimentKpis | null;
  echelons: Array<{ streak: number; label: string; stocks: LadderStock[] }>;
}

const mapStock = (s: BackendLadderStock): LadderStock => ({
  symbol: s.symbol,
  name: s.name,
  streak: s.streak,
  daysSpan: s.days_span,
  boardsInWindow: s.boards_in_window,
  missingDays: s.missing_days,
  swL1Name: s.sw_l1_name,
  swL3Name: s.sw_l3_name,
  sealTime: s.seal_time,
  sealFund: s.seal_fund,
  breakCount: s.break_count,
});

const mapKpis = (k: BackendKpis): SentimentKpis => ({
  ztCount: k.zt_count,
  dtCount: k.dt_count,
  zbCount: k.zb_count,
  brokenRate: k.broken_rate,
  yztAvgPct: k.yzt_avg_pct,
  yztAvgOpenPremium: k.yzt_avg_open_premium,
  yztN: k.yzt_n,
  promo1to2: k.promo_1to2,
  promo1to2N: k.promo_1to2_n,
  promo1to2Noisy: k.promo_1to2_noisy,
  promo2to3: k.promo_2to3,
  promo2to3N: k.promo_2to3_n,
  promo2to3Noisy: k.promo_2to3_noisy,
  maxStreak: k.max_streak,
});

export function fetchLimitUpLadder(date?: string, lookback = 10): Promise<LimitUpLadder> {
  return apiGet<BackendLadder>("/api/v1/market/limit-up-ladder", { date, lookback }).then((b) => ({
    asOf: b.as_of,
    asOfPrev: b.as_of_prev,
    source: b.source,
    limitsPresent: b.limits_present,
    isPartial: b.is_partial,
    swCoverage: b.sw_coverage,
    degradedReason: b.degraded_reason,
    kpis: b.kpis ? mapKpis(b.kpis) : null,
    echelons: b.echelons.map((e) => ({ streak: e.streak, label: e.label, stocks: e.stocks.map(mapStock) })),
  }));
}
// fetchSectorLimitUp / fetchYesterdayLimitUp / fetchSentimentCalendar 同构，按上面的
// BackendXxx / mapXxx 成对写出，字段一一对应（不做任何语义重算）。
```

- [ ] **Step 2: 写 4 个组件**

`SentimentHeader.tsx`：`SectionCard` + `DataRow` 组合，展示 涨停/跌停/炸板、炸板率、昨日涨停均值、1进2、2进3、最高板。
**小样本抑制**：`promo1to2Noisy` 为真时数值后加 `（n=小）` 并弱化配色。
**缺失值**：`brokenRate == null` 渲染 `--`，**绝不 `?? 0`**。

`LimitUpLadder.tsx`：按 `echelons` 渲染分档块，档位标题 `label`，档内 `Tag` 显示 `{daysSpan}天{boardsInWindow}板`（仅当 `boardsInWindow !== streak` 时显示，避免冗余）、`缺少 N 个交易日`（`missingDays > 0` 时）、`sealTime ?? "--"`。

`SwL3LimitUpBoard.tsx`：antd `Table`，列 = 细分行业 / 最高板 / 龙头 / 涨停家数 / 一级行业；`rowKey="l3Code"`；`onRow` 点击跳个股页（**已核实的现有路由：`/stock/:symbol`，单数、不带 exchange**）。用 `useNavigate()` + `navigate(\`/stock/${leaderSymbol}\`)`，与 `StockTable`/`DragonTigerTable` 一致；**没有** symbol→exchange 推导工具，不要新写一个（路由也不需要）。
顶部 `Segmented` 按一级行业过滤（客户端过滤 `l1Code`，数据来自已加载的 `items`）。

`SentimentHeader.tsx` 的 props 类型：`kpis: SentimentKpis | null | undefined`（`ladder.data?.kpis` 可为 null），
内部对 null 渲染裸 `--`，不得 fallback 到 0。

三个组件根元素必须带 e2e 依赖的 class（Task 8 Step 3 不再"按失败信息补"）：
`SentimentHeader` → `sentiment-header`，`LimitUpLadder` → `sentiment-ladder`，`SwL3LimitUpBoard` → `sector-limit-up`。
样式复用既有 token/class，不新增 CSS 变量。

`YesterdayLimitUp.tsx`：`Table`，列 = 代码/名称/细分行业/昨日连板/今日今开溢价/今日涨幅/状态；状态用 `Tag`：`晋级`（`isLu`）、`炸板`（`broken`）、`停牌`（`suspended`）、`震荡`；`todayPct == null` → `--`；`rowKey="symbol"`。

- [ ] **Step 3: 接到 market 页**

`frontend/src/features/market/components/index.ts` 追加 4 个导出；`frontend/src/pages/market/index.tsx` 在 `items` 里加：

```tsx
    {
      key: "sentiment",
      label: "短线情绪",
      children: <SentimentTab />,
    },
```

`SectionCard` 目前**没有 `asof` 属性**（`SectionCardProps` 只有 `id/title/moreHref/moreText/children`；`section-card__asof` 的样式已存在，但由 `RankingMatrix` 自己手写）。先在 `frontend/src/shared/ui/SectionCard.tsx` 补一个可选属性，避免到处手写：

```tsx
  /** 「数据截至 …」行（复用既有 .section-card__asof 样式，不新增 class） */
  asof?: string | null;
```

并在标题行渲染 `{asof ? <div className="section-card__asof">数据截至 {formatCnDate(asof)}</div> : null}`。

格式化 helper 走共享实现，**不要**让 `shared/ui` 依赖 `features/`（反向依赖），也**不要**在 SectionCard 里再写一份：

```ts
// frontend/src/shared/ui/date.ts（新建）
/** ISO 日期 → 「9月9日」（与 features/market/components/format.ts 既有实现同形）。 */
export function formatCnDate(iso: string): string {
  const d = new Date(iso);
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}
```

`features/market/components/format.ts` 的既有 `formatCnDate` 改为 `export { formatCnDate } from "@/shared/ui/date";`（保留原导出名，`RankingMatrix` 等既有消费方零改动），并同步把 `shared/ui/index.ts` 的 barrel 补上 `export { formatCnDate } from "./date";`。

`SentimentTab`（同文件内小组件）用**一次** `useQuery(["limit-up-ladder"], fetchLimitUpLadder)` 拿梯队与 KPI，另外两个端点各自 `useQuery`（三块各自降级，单点失败不牵连邻区）。注意补 import（该文件现状只导入 react/antd）：

```tsx
import { useQuery } from "@tanstack/react-query";
import { SectionCard } from "@/shared/ui";
import { fetchLimitUpLadder, fetchSectorLimitUp, fetchYesterdayLimitUp } from "@/shared/api/limitUp";
import { LimitUpLadder, SentimentHeader, SwL3LimitUpBoard, YesterdayLimitUp } from "@/features/market/components";
```

```tsx
function SentimentTab() {
  const ladder = useQuery({ queryKey: ["limit-up-ladder"], queryFn: () => fetchLimitUpLadder(), staleTime: 60_000 });
  const sectors = useQuery({ queryKey: ["sector-limit-up"], queryFn: () => fetchSectorLimitUp(), staleTime: 60_000 });
  const yesterday = useQuery({ queryKey: ["yesterday-limit-up"], queryFn: () => fetchYesterdayLimitUp(), staleTime: 60_000 });
  const degraded = ladder.data?.degradedReason;
  return (
    <Row gutter={[16, 16]}>
      <Col span={24}>
        <SectionCard title="情绪温度计">
          {degraded ? <DegradedNotice reason={degraded} /> : <SentimentHeader kpis={ladder.data?.kpis} />}
        </SectionCard>
      </Col>
      <Col span={24}>
        <SectionCard title="连板梯队" asof={ladder.data?.asOf}>
          <LimitUpLadder echelons={ladder.data?.echelons ?? []} />
        </SectionCard>
      </Col>
      <Col xs={24} xl={12}>
        <SectionCard title="申万三级最高板" asof={sectors.data?.asOf}>
          <SwL3LimitUpBoard data={sectors.data} />
        </SectionCard>
      </Col>
      <Col xs={24} xl={12}>
        <SectionCard title="昨日涨停今日表现" asof={yesterday.data?.asOfPrev}>
          <YesterdayLimitUp data={yesterday.data} />
        </SectionCard>
      </Col>
    </Row>
  );
}
```

`DegradedNotice`：把 `degraded_reason` 映射成中文文案（`price_limits_missing` → 「涨跌停价尚未回补，连板梯队暂不可用（每个交易日 16:50 自动补齐）」；`partial_day` → 「当日行情未回补完整，暂不展示梯队」；`no_quotes` → 「库内暂无行情数据」；`no_limit_up_rows` → 「当日无涨停股（候选为空）」；`insufficient_trade_days` → 「交易日不足 2 天」），**不同原因不同文案**，不写"暂无数据"；未知原因回退到 `degraded_reason` 原串，不静默吞掉。

- [ ] **Step 4: 类型检查与 lint**

Run: `cd frontend && npx tsc -b && npm run lint && npm run check:design`
Expected: 无错误（设计 token 门禁只校验 `theme.css`/`theme.ts` 的涨跌色对比度，新组件不新增 CSS 变量即天然通过）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/shared/api/limitUp.ts frontend/src/features/market/components/ \
  frontend/src/pages/market/index.tsx
git commit -m "feat(limit-up): 前端情绪 Tab——温度计/连板梯队/申万L3最高板/昨日涨停表现"
```

---

### Task 8: 前端 e2e

**Files:**
- Create: `frontend/e2e/limitUpSentiment.spec.ts`

**Interfaces:**
- Consumes: Task 7 的 Tab 与文案

- [ ] **Step 1: 写 e2e（先红）**

```ts
import { expect, test } from "@playwright/test";

/**
 * 短线情绪 Tab E2E。契约（docs/design/limit-up-sentiment.md §5）：
 * - 本地自算路径下 封板时间 渲染 `--`（缺失 ≠ 0）
 * - 降级原因必须显示对应文案，不得显示"暂无数据"
 * - 三块各自降级：abort 一个端点，邻区仍正常
 */
const LADDER = {
  as_of: "2026-09-08", as_of_prev: "2026-09-07", source: "local_calc",
  limits_present: true, is_partial: false, sw_coverage: 0.9333, degraded_reason: null,
  kpis: { zt_count: 75, dt_count: 1, zb_count: 39, broken_rate: 0.3421, yzt_avg_pct: 2.82,
          yzt_avg_open_premium: 1.1, yzt_n: 95, promo_1to2: 0.1585, promo_1to2_n: 82,
          promo_1to2_noisy: false, promo_2to3: 0.3077, promo_2to3_n: 13,
          promo_2to3_noisy: false, max_streak: 4 },
  echelons: [
    { streak: 4, label: "4连板", stocks: [
      { symbol: "600354", name: "敦煌种业", streak: 4, days_span: 4, boards_in_window: 4,
        missing_days: 0, sw_l1_name: "农林牧渔", sw_l3_name: "种植业",
        seal_time: null, seal_fund: null, break_count: null } ] },
    { streak: 1, label: "首板", stocks: [
      { symbol: "002274", name: "华昌化工", streak: 1, days_span: 5, boards_in_window: 5,
        missing_days: 5, sw_l1_name: "基础化工", sw_l3_name: "化学原料",
        seal_time: null, seal_fund: null, break_count: null } ] },
  ],
};

test("情绪 Tab 展示梯队并遵守缺失值契约", async ({ page }) => {
  await page.route("**/market/limit-up-ladder*", (r) => r.fulfill({ json: LADDER }));
  await page.route("**/market/sector-limit-up*", (r) => r.fulfill({ json: {
    as_of: "2026-09-08", source: "local_calc", degraded_reason: null,
    unclassified_count: 2,
    items: [{ l3_code: "110703", l3_name: "生猪养殖", l1_code: "110000", l1_name: "农林牧渔",
              max_streak: 3, leader_symbol: "002714", leader_name: "牧原股份",
              leader_streak: 3, zt_count: 4 }] } }));
  await page.route("**/market/yesterday-limit-up*", (r) => r.fulfill({ json: {
    as_of: "2026-09-08", as_of_prev: "2026-09-07", source: "local_calc", degraded_reason: null,
    kpis: { n: 2, measured: 1, yzt_avg_pct: 10.0, yzt_avg_open_premium: 5.0 },
    items: [
      { symbol: "000001", name: "平安银行", prev_streak: 1, today_pct: 10.0,
        today_open_premium: 5.0, today_streak: 2, is_lu: true, touched: true, broken: false,
        suspended: false, missing_days: 0, sw_l3_name: "银行" },
      { symbol: "000002", name: "万科A", prev_streak: 1, today_pct: null,
        today_open_premium: null, today_streak: null, is_lu: false, touched: false,
        broken: false, suspended: true, missing_days: 1, sw_l3_name: "房地产" },
    ] } }));

  await page.goto("/market");
  await page.getByRole("tab", { name: "短线情绪" }).click();

  // 梯队
  await expect(page.locator(".sentiment-ladder")).toContainText("4连板");
  await expect(page.locator(".sentiment-ladder")).toContainText("敦煌种业");
  // N天M板：boards_in_window(5) ≠ streak(1) → 必须渲染「5天5板」（窗口被截成两天时会变成 2 天以下）
  await expect(page.locator(".sentiment-ladder")).toContainText("5天5板");
  // 本地路径无封板时间 → `--`
  await expect(page.locator(".sentiment-ladder")).toContainText("--");
  // 停牌披露
  await expect(page.locator(".sentiment-ladder")).toContainText("缺少");
  // 温度计
  await expect(page.locator(".sentiment-header")).toContainText("75");
  await expect(page.locator(".sentiment-header")).toContainText("39");
  // 昨日表现：停牌票不得渲染 0.00%
  const suspendedRow = page.locator("tr", { hasText: "万科A" });
  await expect(suspendedRow).toContainText("停牌");
  await expect(suspendedRow).not.toContainText("0.00%");
  // 申万 L3
  await expect(page.locator(".sector-limit-up")).toContainText("生猪养殖");
  await expect(page.locator(".sector-limit-up")).toContainText("牧原股份");
});

test("限价缺失时显示原因而非空态", async ({ page }) => {
  await page.route("**/market/limit-up-ladder*", (r) => r.fulfill({ json: {
    ...LADDER, limits_present: false, degraded_reason: "price_limits_missing",
    kpis: null, echelons: [] } }));
  await page.route("**/market/sector-limit-up*", (r) => r.fulfill({ json: {
    as_of: null, source: "local_calc", degraded_reason: "price_limits_missing",
    unclassified_count: 0, items: [] } }));
  await page.route("**/market/yesterday-limit-up*", (r) => r.fulfill({ json: {
    as_of: null, as_of_prev: null, source: "local_calc",
    degraded_reason: "price_limits_missing", kpis: {}, items: [] } }));

  await page.goto("/market");
  await page.getByRole("tab", { name: "短线情绪" }).click();
  await expect(page.getByText(/涨跌停价尚未回补/)).toBeVisible();
  await expect(page.getByText("暂无数据")).toHaveCount(0);
});

test("单端点失败不牵连邻区", async ({ page }) => {
  await page.route("**/market/sector-limit-up*", (r) => r.abort());
  await page.route("**/market/limit-up-ladder*", (r) => r.fulfill({ json: LADDER }));
  await page.route("**/market/yesterday-limit-up*", (r) => r.fulfill({ json: {
    as_of: "2026-09-08", as_of_prev: "2026-09-07", source: "local_calc",
    degraded_reason: null, kpis: { n: 0, measured: 0 }, items: [] } }));
  await page.goto("/market");
  await page.getByRole("tab", { name: "短线情绪" }).click();
  await expect(page.locator(".sentiment-ladder")).toContainText("敦煌种业");
  await expect(page.locator(".sector-limit-up")).toBeVisible();
});
```

- [ ] **Step 2: 跑确认先红**

Run: `cd frontend && npx playwright test e2e/limitUpSentiment.spec.ts`
Expected: FAIL —— 找不到「短线情绪」Tab（若 Task 7 未完成）或文案不符。

- [ ] **Step 3: 修到绿**

Task 7 已规定根元素 class（`sentiment-ladder` / `sentiment-header` / `sector-limit-up`），本步只修文案/渲染细节，直到通过。**不许**改断言去迁就实现。

- [ ] **Step 4: 跑全量前端门禁**

Run: `cd frontend && npx playwright test && npx tsc -b && npm run lint && npm run check:design`
Expected: 全绿（既有用例无回归）。

- [ ] **Step 5: Commit**

```bash
git add frontend/e2e/limitUpSentiment.spec.ts frontend/src/features/market/components
git commit -m "test(limit-up): 短线情绪 Tab e2e（缺失值/降级原因/单点失败隔离）"
```

---

### Task 9: 清理 daily_quotes 冗余索引（可独立跳过）

**Files:**
- Create: `backend/app/migrations/versions/c9e3f2d4a5b6_drop_redundant_daily_quotes_index.py`
- Modify: `backend/app/models/quote.py`

**Interfaces:**
- Produces: 无（纯清理）

- [ ] **Step 1: 确认事实（先测再改）**

Run:
```bash
cd backend && uv run python -c "
import asyncio
from app.core.database import async_session_factory
from sqlalchemy import text
async def m():
    async with async_session_factory() as db:
        r = await db.execute(text(\"SELECT indexname, indexdef FROM pg_indexes WHERE tablename='daily_quotes'\"))
        for x in r.fetchall(): print(x[1])
asyncio.run(m())
"
```
Expected：`uq_daily_quotes_stock_date` 与 `idx_daily_quotes_stock_date` **列完全相同**（`(stock_id, trade_date)`）；
另有 `ix_daily_quotes_stock_id`（模型 `index=True` 产生，与唯一约束首列重复，一并清）。
**实测（2026-09-14）窗口/连板查询走的是 `idx_daily_quotes_stock_date`，不是唯一约束那个**
——两者同列时规划器按 OID 任选，所以 T2 的计划守卫只能断言"二者之一"（已改）。
本任务删掉 idx_ 之后，同一查询会自动改走 uq_。

额外核实项（写进迁移注释）：本库 `daily_quotes` 实测 `relkind='r'`、`pg_inherits` 计数 0，
是**普通表**（模型里"partitioned by trade_date"的注释是陈旧的），所以 `DROP INDEX CONCURRENTLY` 可用；
若某环境真的做了分区，分区索引不支持 CONCURRENTLY，需改用普通 DROP（短暂锁）。

- [ ] **Step 2: 写迁移（CONCURRENTLY，避免锁表）**

```python
"""drop redundant daily_quotes index

Revision ID: c9e3f2d4a5b6
Revises: b8d2e1c3f4a5
Create Date: 2026-09-14

uq_daily_quotes_stock_date 与 idx_daily_quotes_stock_date 列完全相同；实测（2026-09-14）
窗口/连板查询走的是 **idx_daily_quotes_stock_date**（同列索引规划器按 OID 任选），
删掉它之后同一查询自动改走 uq_。另删 ix_daily_quotes_stock_id（与唯一约束首列重复）。
4.33M 行表每次写三个索引中两个是纯写放大。用 CONCURRENTLY 避免 ACCESS EXCLUSIVE
（DROP INDEX CONCURRENTLY 不能在事务内，故显式 autocommit_block）；本库实测 daily_quotes
为普通表（relkind='r'，非分区），CONCURRENTLY 可用。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c9e3f2d4a5b6"
down_revision: str | None = "b8d2e1c3f4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_daily_quotes_stock_date",
            table_name="daily_quotes",
            postgresql_concurrently=True,
        )
        op.drop_index(
            "ix_daily_quotes_stock_id",
            table_name="daily_quotes",
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    op.create_index(
        "idx_daily_quotes_stock_date", "daily_quotes", ["stock_id", "trade_date"]
    )
    op.create_index("ix_daily_quotes_stock_id", "daily_quotes", ["stock_id"])
```

- [ ] **Step 3: 同步模型，避免 autogenerate 复活**

`backend/app/models/quote.py` 的 `__table_args__` 删掉 `Index("idx_daily_quotes_stock_date", "stock_id", "trade_date"),` 一行，并保留 `uq_daily_quotes_stock_date`；同时把 `stock_id: Mapped[int] = mapped_column(nullable=False, index=True)` 的 `index=True` 去掉（对应 Step 2 里删掉的 `ix_daily_quotes_stock_id`）。两处模型改动必须与迁移同步，否则下次 `--autogenerate` 会把索引又加回来。

- [ ] **Step 4: 跑迁移与全部门禁**

Run: `cd backend && uv run alembic upgrade head && uv run alembic heads && uv run pytest -q && uv run pytest tests/test_limit_up_repo.py tests/test_rankings_index.py -v -m e2e`
Expected: 单 head；全量无回归；删了 idx_ 之后计划守卫应改走 `uq_daily_quotes_stock_date`
（守卫已接受二者之一，所以删前删后都是绿的；关键是 `daily_quotes` 不得出现 Seq Scan）。
另外跑一次 `bash scripts/self_review.sh`（AGENTS 强制的收尾自检，本计划每个任务都跑）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/migrations/versions/c9e3f2d4a5b6_drop_redundant_daily_quotes_index.py backend/app/models/quote.py
git commit -m "perf(db): 清理 daily_quotes 与唯一约束重复的索引"
```

---

### Task 10: 文档沉淀与交叉引用对齐

**Files:**
- Modify: `docs/Changelog.md`、`docs/references/best-practices.md`、`docs/design/data-source.md`（§七 追加 `stk_limit` 与东财涨停池实测记录）

- [ ] **Step 1: Changelog**

在 `docs/Changelog.md` **顶部**加一节，格式对齐既有条目（含「验证」与「涉及模块」两段）：

```markdown
## 2026-09-14 - 连板梯队与市场情绪：本地 K 线自算主干 + 限价权威表 + 降级契约
- **权威限价**：新增 `stock_price_limits`（TuShare `stk_limit` 原值，`Numeric(12,4)`，唯一键 `(trade_date, stock_id)`）+ 按交易日补漏 ingest（16:50）。**`fetch_stk_limit` 必须显式传 `fields`**：`pre_close` 是 TuShare「默认显示=N」字段，不传只回 `[trade_date, ts_code, up_limit, down_limit]`（2026-09-14 实测），整列 NULL 且不报错；传 `fields` 后 5,637/5,637 非空。**实测否决了名称启发式**：2026-09-08 权威判 75 只涨停、名称 ST/前缀启发式判 83 只，10 处分歧里 9 只是 ST 名称股真实限幅为 10%（`ST晨鸣` up_limit=2.13 / pre_close=1.94）；前收一律用 `stk_limit.pre_close`（交易所口径、含除权），用 LAG 当日前收会把涨停数算成 142
- **本地自算主干**：`limit_up_repo` 单条窗口 SQL（候选 CTE 先收敛再回查，实测 16 交易日整窗 2,411 行 / ~48ms；限价表驱动的自然 join 顺序 620ms）+ `limit_up_calculator` 纯函数（梯队 / `N天M板` / 交集式晋级率 / 申万 L3 最高板 / 情绪 KPI）。候选集必须 `DISTINCT`——两日并集重复会让涨停数 75→94 并造出假 8 连板。三条已锁死的坑：①候选集必须 `DISTINCT`——两日并集重复会让涨停数 75→94 并造出假 8 连板（新增扇出守卫测试）；②窗口必须回整段交易日（只回 as_of/as_of_prev 会把 `boards_in_window` 锁在 2、`missing_days` 恒为 14）；③`streaked` 必须 `PARTITION BY stock_id, is_lu, grp`（`grp = rn_all - rn_by_val` 只保证组内常量、不保证组间唯一，实测窗口内 4 行 is_lu 的 streak 被虚高）
- **涨跌幅分母**：昨日涨停股的今日涨幅一律取 **as_of 当日行**的 `pre_close`（= 昨收）。取 as_of_prev 行的 `pre_close` 会变成「今日 ÷ 前前日」的 2 日收益：实测 2026-09-08 从真值 2.8225% 变成 13.9488%（95 只），5 倍偏差且不报错
- **申万 L3 细分最高板**：复用既有 L3→L2→L1 `parent_code` 两跳链，龙头按 `streak DESC, amount DESC, symbol ASC` 确定性裁决，未映射标的进 `unclassified_count` 兜底桶 + 回传 `sw_coverage`（实测黄金日 70/75 = 0.9333）
- **降级契约**：`source` **只用** `local_calc`（东财 `hybk` 是东财板块口径、填不进申万 L3，Web 不产出完整 payload）；限价缺失/当日行数 < 4000（正常 5,490）时返回空 payload + `degraded_reason`（`price_limits_missing` / `partial_day` / `no_quotes` / `no_limit_up_rows` / `insufficient_trade_days`），**不猜比例**；读端点不写库，唯一外呼是东财增强（失败静默）
- **Web 增强**：东财涨停池（`push2ex/getTopicZTPool`，`date` 必填、客户端 10s 超时）按 `as_of` 取数注入封板时间/封单资金/炸板次数，失败只 log 不改主路径状态；本地路径这三字段为 `null`，前端渲染 `--`。`push2delay` 对该端点返回空，故硬编码 `push2ex`
- **情绪周期**：`market_sentiment_daily`（纯派生缓存，可重建）盘后 17:15 落库 + `GET /market/sentiment/calendar`
- **验证**：TDD 先红后绿（纯函数 12 例含 off-by-one、停牌 policy A 与 pre_close 分母；repo 扇出/窗口完整性/计划守卫 3 例 e2e；降级契约 6 例；Web 映射/失败隔离/降级不外呼 5 例）；黄金日 2026-09-08 全链路口径一致（梯队 4板×3/3板×3/2板×13/首板×56，涨停 75 / 跌停 1 / 炸板 39，昨日涨停池 95 只今日均值 +2.8225% / 今开溢价 +3.11%，`sw_coverage` 0.9333）；`uv run pytest -q`、`mypy app`、`npx tsc -b`、`npm run check:design`、`playwright test`、`bash scripts/self_review.sh` 全绿
- 涉及模块：backend/app/{models/market_data.py,repositories/{limit_up_repo,stock_repo}.py,services/{limit_up_calculator,limit_up_service,market_data_service,tushare_ingest}.py,core/providers/eastmoney_client.py,schemas/{limit_up,task}.py,api/v1/market_data.py,workers/market_data_worker.py,scheduler/{jobs,runner}.py,migrations/versions/a7c1f0b2d3e4_*}, backend/tests/test_limit_up_{ingest,repo,calculator,service,web}.py, frontend/src/{shared/api/limitUp.ts,features/market/components/{SentimentHeader,LimitUpLadder,SwL3LimitUpBoard,YesterdayLimitUp}.tsx,pages/market/index.tsx}, frontend/e2e/limitUpSentiment.spec.ts, docs/design/limit-up-sentiment.md
```

- [ ] **Step 2: best-practices 一句话沉淀**

在 `docs/references/best-practices.md` 的「一、数据源与采集」末尾追加（一句话，与既有条目同风格）：

```markdown
- 依赖第三方实时接口的功能必须先把口径落成自有数据再算（限价这类**有权威原值**的字段要用官方接口原值落表，别用名称/代码前缀推比例）：实测 2026-09-08 名称启发式把 75 只涨停误判成 83 只（9 只 ST 名称股真实限幅 10%）、而用 `LAG(close)` 当前收会把涨停数算成 142——外呼失败只降级"增强字段"（封板时间/封单），绝不降级主口径。**且要核到字段级**：TuShare 文档里"默认显示=N"的列（如 `stk_limit.pre_close`）不显式传 `fields` 就不返回，落库整列 NULL 而不报错；同理涨跌幅分母必须取**当日行**的 `pre_close`（取前一日行 = 静默多算一天，实测 2.82% → 13.95%）。附三条同源教训：候选集合取两日并集必须 `DISTINCT`（扇出会 75→94 并造出假连板）；gaps-and-islands 的 `grp = rn_all - rn_by_val` 只保证组内常量、**不保证组间唯一**，`PARTITION BY` 必须带上分组列本体（否则不同值的岛会合并且 streak 静默虚高）；以及"缺失 ≠ 零"在停牌股上必须显式回传 `missing_days` 而不是折算成 0%。
```

- [ ] **Step 3: data-source.md 追加实测记录**

在 `docs/design/data-source.md` §七 后追加：

```markdown
### 涨跌停价与涨停池（2026-09-14 实测）

| 源 | 接口 | 内容 | 结论 |
|---|---|---|---|
| TuShare | `stk_limit`（`fields=trade_date,ts_code,pre_close,up_limit,down_limit`） | 全市场（含基金/B股，单日 5,637 行）交易所口径涨跌停价 + 原生 `pre_close` | **权威基准**，单交易日一次调用；5,499 行可映射到 A 股 `stocks`。不传 `fields` 时 `pre_close` 不返回 |
| 东财 push2ex | `/getTopicZTPool`（`ut=7eea3edcaed734bea9cbfc24409ed989`） | 当日涨停股池：`lbc` 连板数、`fbt/lbt` 首/末封板时间、`fund` 封单、`zbc` 炸板次数、`zttj{days,ct}` | 仅作**增强**（本地日线给不出封板时间/封单）。`date=YYYYMMDD` **必填**（不带 `date` 返回 `rc=102`）；`push2delay` 同路径返回空，只能用 `push2ex`；响应 `qdate` 恒为当天、不可当校验用 |
| 东财 push2ex | `/getYesterdayZTPool`、`/getTopicZBPool` | 昨日涨停今日表现 / 炸板池 | 备选对账源，不入主路径 |

> 本地化计算不依赖 `trade_cal`：窗口交易日取自 `daily_quotes` 实际存在的日期（库内无持久化交易日历，且"真有行情"本身就是最准的可用性判据）。
```

- [ ] **Step 4: 交叉引用一致性检查**

Run:
```bash
grep -rn "stock_price_limits\|market_sentiment_daily\|limit-up-ladder\|degraded_reason" docs/ plans/2026-09-14-limit-up-sentiment.md | wc -l
grep -c "price_limits_missing\|partial_day" docs/design/limit-up-sentiment.md backend/app/services/limit_up_service.py
grep -rn 'source="web"\|Literal\["local_calc", "web"\]\|"local_calc" | "web"' docs/ plans/2026-09-14-limit-up-sentiment.md | grep -v Changelog
# 前端 TS union 也要查：`"local_calc" | "web"` 这种放宽写法 grep 不到上面的 Python 模式
```
Expected: 表名/路由/降级原因在三处（spec / plan / Changelog）命名完全一致，无 `limit_up_limits`、`sentiment_daily`、`/market/limit-up` 之类的别名残留。

- [ ] **Step 5: Commit**

```bash
git add docs/Changelog.md docs/references/best-practices.md docs/design/data-source.md
git commit -m "docs: 连板梯队与市场情绪的 Changelog 与最佳实践沉淀"
```

---

## Self-Review

**1. Spec coverage**

| spec 章节 | 覆盖任务 |
|---|---|
| §2 决策 1（`stk_limit` 为限价基准） | T1（表+ingest+**显式 `fields`**）、T2（SQL 用它判定 `is_lu`） |
| §2 决策 2（`pre_close` 用原生值） | T1（落 `pre_close`）、T3（溢价率，**分母取当日行**） |
| §2 决策 3（自算先于 Web） | T2/T3/T4 全部不依赖 Web；T6 才引入 |
| §2 决策 4（不猜比例） | T4（`degraded_reason`）、T3（不写启发式函数） |
| §3.1 涨停/跌停/炸板 | T2（`_BREADTH_SQL`）、T3（`sentiment_kpis`） |
| §3.2 连板数 + 停牌 policy A | T2（gaps-and-islands）、T3（`missing_days`）、单测钉死 |
| §3.3 `N天M板` | T3（`n_day_m_board`） |
| §3.4 晋级率交集式 + 小样本 | T3（`promotion_rate`）、T7（前端 noisy 标注） |
| §3.5 申万 L3 最高板 + 兜底桶 | T2（JOIN）、T3（`sector_ladder`）、T4（`sw_coverage`） |
| §3.6 情绪 KPI（全市场口径） | T2（独立的 breadth SQL）、T3 |
| §4 两张表 | T1、T5 |
| §5 4 个端点 + 降级矩阵 | T4、T5、T6、T7（降级矩阵已收窄为 `source` 只有 `local_calc`：东财 `hybk` ≠ 申万 L3，Web 只供增强字段） |
| §6 SQL 性能与扇出 | T2（计划守卫 + 扇出守卫） |

无缺口。spec 的「非目标」在计划中也没有误加（无分笔、无逐日明细表、无 Web-first、无启发式）。

**2. Placeholder scan**

已消除：所有代码步骤给了可直接落地的实现；T7 的另外三个 fetch 函数以"同构 + 字段一一对应"说明并以 `BackendXxx/mapXxx` 成对模板给出（属同一文件内的机械重复，已在 T7 Step 1 给出完整模板与一条完整实现），不是 TBD；T5 的迁移只给"按模型逐列写出"因为 T1 的迁移已给出逐列模板且 T2 起复用同一写法。

**3. Type consistency**

- `fetch_limit_up_window` 返回行键在 T2 定义，T3 全部消费同一批键（`streak_upto` / `is_lu` / `touched` / `sw_l3_code` / `sw_l3_name` / `sw_l1_code` / `sw_l1_name` / `pre_close`）——T3 的测试 fixture `_row()` 与之一致。
- `promotion_rate` / `sector_ladder` / `yesterday_limit_up` 返回结构在 T3 定义，T4 的 `sentiment_kpis` 入参与之对齐（`{"rate","n","noisy"}`）。
- `snap` 的键在 T4 定义（`echelons/sectors/yesterday/kpis/breadth/market_days/...`），T5 `persist_snapshot`、T6 `enrich_from_web`、T7 前端映射三处引用同名键。
- 前端 camelCase 字段与后端 snake_case 字段在 T7 的 mapper 里一一对应（`days_span→daysSpan`、`boards_in_window→boardsInWindow`、`promo_1to2→promo1to2`、`promo_1to2_n→promo1to2N`）。
- 端点路径 `/api/v1/market/limit-up-ladder|sector-limit-up|yesterday-limit-up|sentiment/calendar` 在 T4、T7、T8、T10 四处一致。

**4. 与代码现状对齐（2026-09-14 复测）**

计划里所有“已存在”、“无需改动”、“复用既有”的断言均已对庰代码/真库确认，下列是审计后修正过的：

- `alembic heads` 单头 = `cf4b8e317fe5` ✔（T1/T5/T9 的 `down_revision` 链正确）
- `TuShareClient.fetch_stk_limit` **需改**（补 `fields`）；`daily_quotes.pre_close` 与它无关
- `market_data_repo` **没有** `latest_quote_date` 等四个函数，它们全在 `limit_up_repo`
- `api/v1/market_data.py` **没有** `_iso()`，需新增；日期解析现状在 service 层
- `SectionCard` **没有** `asof` 属性（需补）；个股页路由是 `/stock/:symbol`（不是 `/stocks/{exchange}/{symbol}`）
- 真库 `daily_quotes` 实测 `relkind='r'`（普通表、非分区），`DROP INDEX CONCURRENTLY` 可用
- 窗口查询实测走 `idx_daily_quotes_stock_date`（非 uq_），两者同列任选

**已修正的自身问题（审计单）**：①`yesterday_limit_up` 取错 `pre_close` 行（2 日收益，5 倍偏差）；②`fetch_stk_limit` 漏传 `fields`（`pre_close` 整列 NULL）；③窗口 SQL 末尾多了一句只回两天的 `WHERE`（`boards_in_window`/`missing_days` 全错）；④gaps-and-islands 的 `PARTITION BY` 缺 `is_lu`（streak 静默虚高）；⑤T2 计划守卫断言了具体索引名（必红）；⑥T1 测试与实现异步性不一致、T1 Step 7 写错 repo 模块名；⑦`source="web"` 全链未实现却出现在契约与降级矩阵；⑧T6 增强触发条件 `target == 今日` 死代码 + `:intraday` 缓存 key 多举；⑨ `_WINDOW_TRADE_DAYS` 硬编码与 `lookback≤ 30` 冲突；⑩ `persist_snapshot` 的 str/date 与 `no_limit_up_rows` 两个崩溃点；⑪T4 双重调用 `yesterday_limit_up`；⑫T9 漏删 `ix_daily_quotes_stock_id` 且 `downgrade` 不完整。
