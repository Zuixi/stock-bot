---
status: active
scope: 玄田数据通道接入（XuantianClient + 产能四指标 + 2009→now 全历史回补）与 ingest 增量门控（三层：调度门/内容哈希门/行级 diff），消灭每次 ingest 全量重写与下游全表重算
touches: backend/app/core/providers/xuantian_client.py, backend/app/models/industry_research.py, backend/app/migrations/versions/, backend/app/repositories/industry_metric_repo.py, backend/app/services/industry_metric_service.py, backend/app/services/industry_registry.py, backend/app/scheduler/jobs.py, backend/app/workers/industry_metrics_worker.py, backend/app/config.py, backend/tests/
updated: 2026-09-29
next-action: 实施完成待合并——Task 1-7 全部落地并通过 final review（修正 MF-1 空写轮状态武装 / MF-2 计划登记 / MF-3 L1 谓词测试），合并后状态改 completed
---

# 玄田产能数据直连 + ingest 增量门控 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已实测验证的玄田数据 API（`xt.yangzhu.vip`，一次调用返回 2009→now 能繁母猪存栏等产能指标全历史）直连落库，并为 `ingest_industry_metrics` 建立三层增量门控（调度门 → 内容哈希门 → 行级 diff），使稳定态 ingest 变成近零成本操作。

**Architecture:** 新建 `XuantianClient`（httpx 直连，仿 CAAA client 约定：UA + 超时 + 失败返回空不抛穿），在 service 层新增 `_fetch_xuantian_capacity_rows` 与现有 fetcher 并列；增量门控以新表 `industry_ingest_state` 为锚点（每 industry×source 一行，存内容哈希/水位/下次到期），由纯函数 `compute_ingest_actions` 执行行级 diff（new/changed/unchanged 三分类），ingest 主流程在 fetch 后依次过 L1/L2/L3 门。上游修订（官方数据回修是常态）通过 changed 行的 old→new 留痕捕捉，而非跳过历史。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Alembic + httpx（已有依赖，零新增）+ PostgreSQL JSONB。测试为 pytest 纯单测（不触网：client 注入假对象、fixture 复刻实机验证的响应形状）。

**Spec:** 本文件即 spec（含三轮对话沉淀的设计决策）；数据源格式依据 2026-09-29 实机探测（见 Task 2 格式表与 `docs/design/data-source.md`）。

## Global Constraints

- 分层铁律：`app/api/v1` → `app/services` → `app/repositories` → `app/models`；worker/scheduler 只调 service
- 双轨不变量：APScheduler 与 RabbitMQ Worker 共用同一 service ingest 方法，**不得**各自实现抓取逻辑
- registry 是单一事实源：新指标的 key/freq/tier/sources 只写在 `industry_registry.py`，阈值调优不进 engine
- metric_key 命名与 `docs/design/data-source.md` 对齐，两处同步修改
- `industry_metrics` 唯一约束 `(industry_key, stock_id, metric_key, source, freq, period)` 是幂等基石，不得改动
- mock 永远垫底：任何 MetricDef 的 sources 列表末位必须是 `"mock"`（`test_mock_always_last_in_registry_sources` 锁定）
- 上游数据语义：官方统计会修订历史，**禁止**用"period > 库内最大值"做增量判断；0 是哨兵值不是观测
- ruff line-length 100、mypy（pydantic plugin）；测试 `uv run pytest`（默认排除 e2e/bench）
- 每完成一个 Task：`git status --porcelain` + `bash scripts/slop_scan.sh` + `bash scripts/self_review.sh` + `docs/Changelog.md` 补记

---

### Task 1: IndustryIngestState 模型 + Alembic 迁移 + 仓库层

**Files:**
- Modify: `backend/app/models/industry_research.py`（文件末尾追加模型，约 40 行）
- Create: `backend/app/migrations/versions/a9b3c7d1e5f2_add_industry_ingest_state.py`
- Modify: `backend/app/repositories/industry_metric_repo.py`（追加 3 个函数）
- Test: `backend/tests/test_industry_ingest_state.py`

**Interfaces:**
- Consumes: `app.core.database.Base`（现有 ORM 基类）
- Produces:
  - `IndustryIngestState` ORM：字段见 Step 3
  - `async def get_ingest_state(db, industry_key: str, source: str) -> IndustryIngestState | None`
  - `async def upsert_ingest_state(db, row: dict) -> IndustryIngestState`
  - `async def touch_ingest_state(db, industry_key: str, source: str, *, last_checked_at: datetime) -> None`（内容未变时只更新检查时间，不动哈希/水位）

**测试策略（ponytail 裁定）：** repo 三函数是薄 SQL 封装，不用真 DB 测——sqlite 内存库方案**废弃**：`aiosqlite` 不在依赖里，且 `Base.metadata.create_all` 会因 JSONB 列直接炸（PG 方言专属类型；仓内 sqlite 测试先例 `test_concept_agg_sql.py` 刻意只跑纯 SQL 字符串，不建 ORM 表）。本 Task 的单测只锁**语义可纯测的部分**（get missing → None 的接口形状用 MagicMock AsyncSession 打桩）；真库读写验证一次性放在 Task 7 的 `alembic upgrade head` + 真实 API 全链路里。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_industry_ingest_state.py
"""industry_ingest_state 仓库层语义（mock AsyncSession，不触任何 DB）.

repo 三函数是薄 SQL 封装；真库行为由 Task 7 的 alembic + 真实 API 链路验证
（JSONB/PG 方言不进 sqlite，见 test_concept_agg_sql.py 先例的同类取舍）。
本文件锁接口形状与 touch 的最小语义：miss → None、upsert 走 get-then-set。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.models.industry_research import IndustryIngestState
from app.repositories import industry_metric_repo as repo


def _db_returning(result):
    db = MagicMock()
    execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=result)))
    db.execute = execute
    return db


async def test_get_missing_returns_none():
    db = _db_returning(None)
    assert await repo.get_ingest_state(db, "pig", "xuantian") is None


async def test_get_returns_state_row():
    row = IndustryIngestState(industry_key="pig", source="xuantian", content_hash="abc")
    db = _db_returning(row)
    assert (await repo.get_ingest_state(db, "pig", "xuantian")) is row


async def test_upsert_existing_sets_fields_and_flushes():
    row = IndustryIngestState(industry_key="pig", source="xuantian", content_hash="old")
    db = _db_returning(row)
    db.flush = AsyncMock()
    out = await repo.upsert_ingest_state(db, {"industry_key": "pig", "source": "xuantian", "content_hash": "new"})
    assert out is row and row.content_hash == "new"
    db.flush.assert_awaited_once()


async def test_touch_only_sets_checked_at():
    row = IndustryIngestState(industry_key="pig", source="xuantian", content_hash="keep")
    db = _db_returning(row)
    db.flush = AsyncMock()
    from datetime import UTC, datetime
    await repo.touch_ingest_state(db, "pig", "xuantian", last_checked_at=datetime(2026, 9, 30, 8, 0, tzinfo=UTC))
    assert row.content_hash == "keep"  # 哈希水位不动
    assert row.last_checked_at is not None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_industry_ingest_state.py -v`
Expected: FAIL（`ImportError: IndustryIngestState`）

- [ ] **Step 3: 写模型 + 迁移 + 仓库实现**

模型追加到 `backend/app/models/industry_research.py` 末尾（沿用文件头的表注释风格）：

```python
class IndustryIngestState(Base):
    """Per (industry_key, source) ingest 增量门控状态.

    三层门控的锚点：L1 调度门读 next_due_at，L2 内容门读 content_hash，
    L3 行级 diff 的修订留痕落 stats。官方统计会修订历史（如 2023 畜禽监测
    样本轮换），因此禁止以 last_period 水位跳过历史——last_period 仅作审计。
    """

    __tablename__ = "industry_ingest_state"
    __table_args__ = (
        UniqueConstraint("industry_key", "source", name="uq_ingest_state_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    industry_key: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_period: Mapped[date | None] = mapped_column(Date)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stats: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

迁移文件（head 为 `2614ed9a9ab4`，已 AST 解析确认，非猜测）：

```python
"""add industry_ingest_state

Revision ID: a9b3c7d1e5f2
Revises: 2614ed9a9ab4
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "a9b3c7d1e5f2"
down_revision: str | None = "2614ed9a9ab4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "industry_ingest_state",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("industry_key", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_period", sa.Date(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stats", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("industry_key", "source", name="uq_ingest_state_source"),
    )


def downgrade() -> None:
    op.drop_table("industry_ingest_state")
```

仓库层追加到 `industry_metric_repo.py`（沿用现有 async 函数风格）：

```python
async def get_ingest_state(
    db: AsyncSession, industry_key: str, source: str
) -> IndustryIngestState | None:
    stmt = select(IndustryIngestState).where(
        IndustryIngestState.industry_key == industry_key,
        IndustryIngestState.source == source,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def upsert_ingest_state(db: AsyncSession, row: dict) -> IndustryIngestState:
    """按 (industry_key, source) 整行覆写（唯一写者是 _gated_xuantian_fetch，每次全字段）."""
    state = await get_ingest_state(db, row["industry_key"], row["source"])
    if state is None:
        state = IndustryIngestState(industry_key=row["industry_key"], source=row["source"])
        db.add(state)
    for k, v in row.items():
        if k not in ("industry_key", "source"):
            setattr(state, k, v)
    await db.flush()
    return state


async def touch_ingest_state(
    db: AsyncSession, industry_key: str, source: str, *, last_checked_at: datetime
) -> None:
    state = await get_ingest_state(db, industry_key, source)
    if state is not None:
        state.last_checked_at = last_checked_at
        await db.flush()
```

模型文件头部 import 处需要补 `UniqueConstraint`（若未导入）。repo 文件 import 处补 `IndustryIngestState` 与 `datetime`。

时间戳语义钉死（写进模型 docstring）：`last_success_at` = 最近一次**有写入**的成功抓取；`last_checked_at` = 最近一次抓取（含 L2 短路"查了没变"）；`created_at`/`updated_at` = 行级审计。三者用途不同，排障时不可互换。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_industry_ingest_state.py -v`
Expected: 4 passed

- [ ] **Step 5: 静态检查 + 提交**

Run: `cd backend && uv run --extra dev ruff check . && uv run --extra dev mypy app`

```bash
git add backend/app/models/industry_research.py backend/app/migrations/versions/a9b3c7d1e5f2_add_industry_ingest_state.py backend/app/repositories/industry_metric_repo.py backend/tests/test_industry_ingest_state.py
git commit -m "feat(industry): ingest state table for incremental gating (L1/L2/L3 anchor)"
```

---

### Task 2: XuantianClient + 周期解析纯函数

**Files:**
- Create: `backend/app/core/providers/xuantian_client.py`
- Test: `backend/tests/test_xuantian_client.py`

**Interfaces:**
- Consumes: httpx（已有依赖）
- Produces:
  - `def parse_capacity_rows(data: list[list[str | float]]) -> list[dict]` — 纯函数：玄田原始 5 列行 → 标准行 dict；无法解析的周期串抛 `ValueError`
  - `class XuantianClient`：`async def fetch_capacity(self) -> list[dict] | None`（失败 log warning 返回 None，不抛穿）
  - `def get_xuantian_client() -> XuantianClient`（模块级单例）

**玄田 API 格式（2026-09-29 实机全量验证，22 行）：**

```
POST https://xt.yangzhu.vip/data/getmapdata?ptype=7&areano=-1
Headers: Referer: https://zhujia.zhuwang.com.cn/  Origin: 同  User-Agent: Mozilla/5.0
响应: {"code":200,"msg":"获取成功","data":[[周期,能繁母猪存栏,猪肉产量,生猪存栏,生猪出栏],...]}
周期格式三种:
  "2009"                 → yearly,  period = YYYY-12-31
  "2025年二季度（末）"    → quarterly, period = Q1→03-31 / Q2→06-30 / Q3→09-30 / Q4→12-31
  "2025年7月"            → monthly,  period = 当月月末
哨兵: 月度行的产量/存栏/出栏为 "0"（推算数据未覆盖），必须跳过
```

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_xuantian_client.py
"""XuantianClient 纯函数层：5 列原始行 → 标准行；周期解析/哨兵剔除/列映射。不触网。"""

from __future__ import annotations

from datetime import date

import pytest

from app.core.providers.xuantian_client import parse_capacity_rows

CAPACITY_COLUMNS = ("sow_inventory", "pork_output", "hog_inventory", "hog_slaughter_quarterly")


def test_yearly_row_parses_to_dec31():
    rows = parse_capacity_rows([["2009", "4910", "4932.85", "47177.21", "64465"]])
    assert rows[0]["period"] == date(2009, 12, 31)
    assert rows[0]["freq"] == "yearly"
    assert rows[0]["values"]["sow_inventory"] == 4910.0
    assert rows[0]["values"]["pork_output"] == 4932.85
    assert rows[0]["values"]["hog_inventory"] == 47177.21
    assert rows[0]["values"]["hog_slaughter_quarterly"] == 64465.0


def test_quarter_row_parses_to_quarter_end():
    rows = parse_capacity_rows([["2025年二季度（末）", "4043", "1418", "42447", "17143"]])
    assert rows[0]["period"] == date(2025, 6, 30)
    assert rows[0]["freq"] == "quarterly"


def test_monthly_row_keeps_only_sow():
    # 月度行产量/存栏/出栏为哨兵 0：只保留能繁列
    rows = parse_capacity_rows([["2025年7月", "4042", "0", "0", "0"]])
    assert rows[0]["period"] == date(2025, 7, 31)
    assert rows[0]["freq"] == "monthly"
    assert rows[0]["values"] == {"sow_inventory": 4042.0}


def test_yearly_zero_value_column_skipped():
    rows = parse_capacity_rows([["2019", "3080", "0", "31041", "54419"]])
    assert "pork_output" not in rows[0]["values"]


def test_unparseable_period_raises():
    with pytest.raises(ValueError, match="unparseable period"):
        parse_capacity_rows([["2025年十三月", "1", "2", "3", "4"]])


def test_all_zero_row_dropped():
    # 能繁也为 0 的行整行丢弃
    rows = parse_capacity_rows([["2025年8月", "0", "0", "0", "0"]])
    assert rows == []
```

- [ ] **Step 1b: 真实响应 fixture 快照测试（防上游改版静默漂移）**

```python
# 追加到 backend/tests/test_xuantian_client.py
import json
import pathlib

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "xuantian_capacity.json"


def test_fixture_snapshot_shape():
    """2026-09-29 实机快照：22 行、列结构、覆盖范围——上游改版时此测试先红。"""
    payload = json.loads(FIXTURE.read_text())
    rows = parse_capacity_rows(payload["data"])
    sow_periods = [r["period"] for r in rows if "sow_inventory" in r["values"]]
    assert len(sow_periods) == 22          # 16 年度 + 3 季度 + 3 月度
    assert sow_periods[0] == date(2009, 12, 31)
    assert sow_periods[-1] == date(2025, 10, 31)
    # ASF 崩塌锚点：2018→2019 能繁下滑（数值变化即上游修订，测试红 = 人工复核信号）
    by_period = {r["period"]: r["values"].get("sow_inventory") for r in rows}
    assert by_period[date(2018, 12, 31)] == 3189.0
    assert by_period[date(2019, 12, 31)] == 3080.0
```

fixture 文件：`backend/tests/fixtures/xuantian_capacity.json` = `/tmp/zt_capacity.json` 的完整副本（本次调研已落盘；若执行时 /tmp 已清，用下方 curl 重拉）：

```bash
mkdir -p backend/tests/fixtures
curl -s --max-time 30 -X POST "https://xt.yangzhu.vip/data/getmapdata?ptype=7&areano=-1" \
  -H "Referer: https://zhujia.zhuwang.com.cn/" -H "Origin: https://zhujia.zhuwang.com.cn" \
  -H "User-Agent: Mozilla/5.0" -o backend/tests/fixtures/xuantian_capacity.json
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_xuantian_client.py -v`
Expected: FAIL（`ModuleNotFoundError: xuantian_client`）

- [ ] **Step 3: 实现 client**

```python
# backend/app/core/providers/xuantian_client.py
"""玄田数据（中国养猪网）产能数据客户端 — xt.yangzhu.vip 直连.

2026-09-29 实机验证：POST getmapdata?ptype=7 返回 2009→now 产能四指标
（能繁母猪存栏/猪肉产量/生猪存栏/生猪出栏）。数据本体为统计局口径（年度/季度
绝对数），月度行为"季度基数+农业农村部环比推算"。零新增依赖（httpx）。

Usage::

    from app.core.providers.xuantian_client import get_xuantian_client
    rows = await get_xuantian_client().fetch_capacity()
"""

from __future__ import annotations

import calendar
import logging
import re

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://xt.yangzhu.vip/data/getmapdata"
_HEADERS = {
    "Referer": "https://zhujia.zhuwang.com.cn/",
    "Origin": "https://zhujia.zhuwang.com.cn",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}
_TIMEOUT = 15.0

# 列序固定：[周期, 能繁母猪存栏, 猪肉产量, 生猪存栏, 生猪出栏]（实机验证）
CAPACITY_COLUMNS = ("sow_inventory", "pork_output", "hog_inventory", "hog_slaughter_quarterly")

_YEAR_RE = re.compile(r"^(\d{4})$")
_QUARTER_RE = re.compile(r"^(\d{4})年([一二三四1-4])季度")
_MONTH_RE = re.compile(r"^(\d{4})年(\d{1,2})月")
_QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}


def _parse_period(raw: str) -> tuple[date, str]:
    """周期串 → (period 截止日, freq)；无法解析抛 ValueError."""
    s = raw.strip()
    if m := _YEAR_RE.match(s):
        return date(int(m.group(1)), 12, 31), "yearly"
    if m := _QUARTER_RE.match(s):
        q = m.group(2)
        qn = {"一": 1, "二": 2, "三": 3, "四": 4}.get(q, q)
        month, day = _QUARTER_END[int(qn)]
        return date(int(m.group(1)), month, day), "quarterly"
    if m := _MONTH_RE.match(s):
        y, mo = int(m.group(1)), int(m.group(2))
        return date(y, mo, calendar.monthrange(y, mo)[1]), "monthly"
    raise ValueError(f"unparseable period: {raw!r}")


def parse_capacity_rows(data: list) -> list[dict]:
    """原始 5 列行 → [{period, freq, values:{metric_key: 非零值}}]；整行无效则丢弃."""
    out: list[dict] = []
    for row in data:
        try:
            period, freq = _parse_period(str(row[0]))
        except ValueError:
            logger.warning("xuantian unparseable period %r (row skipped)", row[0])
            continue
        values: dict[str, float] = {}
        for i, key in enumerate(CAPACITY_COLUMNS, start=1):
            v = float(row[i])
            if v > 0:  # 0 = 哨兵（月度行未覆盖列），不是观测
                values[key] = v
        if values:
            out.append({"period": period, "freq": freq, "values": values, "raw_period": str(row[0])})
    return out


class XuantianClient:
    """httpx 直连（UA + 15s 超时）；任何失败 log warning 返回 None（不抛穿，CAAA 同约定）."""

    async def fetch_capacity(self) -> list[dict] | None:
        try:
            async with httpx.AsyncClient(headers=_HEADERS, timeout=_TIMEOUT) as client:
                resp = await client.post(_BASE, params={"ptype": "7", "areano": "-1"})
                resp.raise_for_status()
                payload = resp.json()
            if payload.get("code") != 200:
                logger.warning("xuantian non-200 code %s (skipped)", payload.get("code"))
                return None
            return parse_capacity_rows(payload["data"])
        except Exception as exc:
            logger.warning("xuantian capacity fetch failed (skipped): %s", exc)
            return None


_default_client: XuantianClient | None = None


def get_xuantian_client() -> XuantianClient:
    global _default_client
    if _default_client is None:
        _default_client = XuantianClient()
    return _default_client
```

注意两个测试期望的对齐点：`test_unparseable_period_raises` 期望 `parse_capacity_rows` 对无法解析行抛 ValueError，但上面实现是 log+skip。**采用 skip 语义**（与"单指标失败不牵连其他行"的既有约定一致），把该测试改为：

```python
def test_unparseable_period_skipped_not_raised():
    rows = parse_capacity_rows([["2025年十三月", "1", "2", "3", "4"]])
    assert rows == []
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_xuantian_client.py -v`
Expected: 7 passed

- [ ] **Step 5: 静态检查 + 提交**

Run: `cd backend && uv run --extra dev ruff check . && uv run --extra dev mypy app`

```bash
git add backend/app/core/providers/xuantian_client.py backend/tests/test_xuantian_client.py backend/tests/fixtures/xuantian_capacity.json
git commit -m "feat(industry): xuantian capacity client with period parsing + live snapshot fixture"
```

---

### Task 3: registry 扩展 — 3 个新 MetricDef + xuantian source 注册

**Files:**
- Modify: `backend/app/services/industry_registry.py`（sow_inventory 定义处 + 新增 3 个 MetricDef，约 45 行）
- Test: `backend/tests/test_industry_source_priority.py`（追加 2 个测试）

**Interfaces:**
- Consumes: `MetricDef`、`TIER_OFFICIAL`（现有）
- Produces: registry 中存在 `pork_output` / `hog_inventory` / `hog_slaughter_quarterly` 三个指标定义；`sow_inventory.sources == ["stats_gov", "caaa", "xuantian", "mock"]`

- [ ] **Step 1: 写失败测试**

```python
# 追加到 backend/tests/test_industry_source_priority.py
def test_sow_inventory_registers_xuantian_source():
    # 玄田 = 统计局口径的门户镜像，排在部委发布渠道 caaa 之后；mock 垫底
    assert PIG_INDUSTRY.metric("sow_inventory").sources == [
        "stats_gov", "caaa", "xuantian", "mock",
    ]


def test_capacity_metrics_registered_with_official_tier():
    for key in ("pork_output", "hog_inventory", "hog_slaughter_quarterly"):
        m = PIG_INDUSTRY.metric(key)
        assert m is not None, f"{key} not registered"
        assert m.tier == "official"
        assert "xuantian" in m.sources
        assert m.sources[-1] == "mock"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_industry_source_priority.py -v`
Expected: 新增 2 个 FAIL（`pork_output` 等 metric 为 None）

- [ ] **Step 3: 修改 registry**

`sow_inventory` 的 sources 行改为：

```python
        sources=["stats_gov", "caaa", "xuantian", "mock"],
```

（`stats_gov` 保留占位：EasyQuery API 直连在大陆网络环境仍可用，后续接入时优先级天然正确，无害。）

在其后插入三个新 MetricDef（放在 supply 分组，紧跟 sow_inventory_mom 之前，保持分组聚拢）：

```python
    MetricDef(
        key="pork_output",
        name="猪肉产量",
        unit="万吨",
        freq="quarterly",
        tier=TIER_OFFICIAL,
        sources=["xuantian", "mock"],
        group="supply",
        description="统计局季度/年度猪肉产量，供给端总量基准（玄田镜像）",
    ),
    MetricDef(
        key="hog_inventory",
        name="生猪存栏",
        unit="万头",
        freq="quarterly",
        tier=TIER_OFFICIAL,
        sources=["xuantian", "mock"],
        group="supply",
        description="统计局季度末生猪存栏绝对数，产能存量基准（玄田镜像）",
    ),
    MetricDef(
        key="hog_slaughter_quarterly",
        name="生猪出栏量",
        unit="万头",
        freq="quarterly",
        tier=TIER_OFFICIAL,
        sources=["xuantian", "mock"],
        group="supply",
        description="统计局季度生猪出栏量，供给流量指标（玄田镜像）",
    ),
```

注意 freq 语义：玄田同时提供 yearly 和 quarterly 行，MetricDef.freq 注册为 `quarterly`（查询端 `_pick_row` 的"注册频率优先"保证 latest 展示取季度行，yearly 行作为长历史背景共存——这正是零迁移设计的前提）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_industry_source_priority.py tests/test_industry_mock_data.py -v`
Expected: 全部 passed（含既有的 `test_mock_always_last_in_registry_sources`——新指标 mock 垫底）

- [ ] **Step 5: 检查 mock builder 兼容 + 提交**

`industry_mock_data.py` 的 generic builder 只为配置了 `mock_base` 的指标生成序列（未配置则跳过）——三个新指标不配 mock_base，mock 模式下无行，无害。跑全量确认无回归：

Run: `cd backend && uv run pytest -x -q`

```bash
git add backend/app/services/industry_registry.py backend/tests/test_industry_source_priority.py
git commit -m "feat(industry): register pork_output/hog_inventory/hog_slaughter_quarterly + xuantian source"
```

---

### Task 4: 行级 diff 纯函数 compute_ingest_actions

**Files:**
- Create: `backend/app/services/industry_ingest_diff.py`
- Test: `backend/tests/test_industry_ingest_diff.py`

**Interfaces:**
- Consumes: 无（纯函数，标准行 dict 格式）
- Produces:
  - `def compute_ingest_actions(fetched: list[dict], existing: list[dict]) -> dict`：返回 `{"new": [...], "changed": [...], "unchanged_count": int, "revisions": [{"metric_key", "freq", "period", "old", "new"}]}`
  - 行 dict 键：`industry_key, stock_id, metric_key, source, source_tier, freq, period, value, unit, extra`
  - `def canonical_content_hash(rows: list[dict]) -> str`：规范化（排序 + 定点化）后 sha256，前 16 hex
  - `def next_due(source: str, now: datetime) -> datetime`：源发布节奏推算（节奏表 `_SOURCE_CADENCE_DAYS` 也住本模块——纯函数住纯模块，service 顶部 import config 会让单测 import 链拉起整个 settings）

**next_due 节奏表（随 compute_ingest_actions 一起实现、一起测试）：**

```python
_SOURCE_CADENCE_DAYS = {
    "xuantian": 30,       # 能繁月度行月更
    "caaa": 32,           # 次月 20-27 日发布，保守 +2 天
    "akshare_soozhu": 1,  # 日价
    "akshare_sina": 1,    # LH 期货交易日
}


def next_due(source: str, now: datetime) -> datetime:
    """各源发布节奏 → 下次应查时间（未知源默认日更，宁多查不漏数据）."""
    return now + timedelta(days=_SOURCE_CADENCE_DAYS.get(source, 1))
```

对应测试（追加进 Task 4 的测试文件）：

```python
from datetime import UTC, datetime, timedelta

from app.services.industry_ingest_diff import next_due


def test_next_due_cadence_per_source():
    now = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)
    assert next_due("xuantian", now) == now + timedelta(days=30)
    assert next_due("caaa", now) == now + timedelta(days=32)
    assert next_due("akshare_soozhu", now) == now + timedelta(days=1)
    assert next_due("akshare_sina", now) == now + timedelta(days=1)
    assert next_due("unknown_source", now) == now + timedelta(days=1)  # 未知源默认日更
```

**设计依据（来自对话定稿）：** diff 键 = `(metric_key, freq, period)`（source 已在调用侧固定——每个 source 独立过门）；value 变化 = changed（带 old→new 修订留痕）；库有响应无 = 保留 + 记入 `orphaned_count`（官方数据语义下不镜像上游删除）；value 精度比较先 float 再容差（Numeric(18,4) 落库回读有 Decimal 化，统一 `float()` 后 `abs(a-b) < 1e-6`）。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_industry_ingest_diff.py
"""行级 diff 纯函数：new/changed/unchanged 三分类 + 修订留痕 + 哈希稳定性。"""

from __future__ import annotations

import hashlib
import json
from datetime import date

from app.services.industry_ingest_diff import canonical_content_hash, compute_ingest_actions


def _row(period: date, value: float, freq: str = "monthly", metric_key: str = "sow_inventory"):
    return {
        "industry_key": "pig", "stock_id": 0, "metric_key": metric_key,
        "source": "xuantian", "source_tier": "official", "freq": freq,
        "period": period, "value": value, "unit": "万头", "extra": None,
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
            "metric_key": "sow_inventory", "freq": "quarterly",
            "period": date(2025, 6, 30), "old": 4043.0, "new": 4038.0,
        }
    ]


def test_decimalized_existing_compared_by_tolerance():
    # Numeric(18,4) 落库回读 Decimal("4043.0000")，与 float 4043.0 视为相同
    existing = [_row(date(2025, 6, 30), 4043.0000123, "quarterly")]
    fetched = [_row(date(2025, 6, 30), 4043.0, "quarterly")]
    out = compute_ingest_actions(fetched, existing)
    assert out["unchanged_count"] == 1  # 差 1.23e-5... 需 < 1e-6 容差
    # 修正：4043.0000123 与 4043.0 差 1.23e-7？不：0.0000123 = 1.23e-5 > 1e-6 → changed
    # 此测试用真容差内案例：
```

上面最后一个测试的数值自相矛盾（0.0000123 = 1.23e-5 > 1e-6），修正为：

```python
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
        _row(date(2024, 12, 31), 4078.0, "yearly"),        # unchanged
        _row(date(2025, 6, 30), 4041.0, "quarterly"),      # changed（修订）
        _row(date(2025, 9, 30), 4035.0, "quarterly"),      # new
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_industry_ingest_diff.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**

```python
# backend/app/services/industry_ingest_diff.py
"""ingest 行级 diff + 内容哈希（纯函数，离线单测锁定）.

三层增量门控的 L2/L3 计算核心：官方统计会修订历史，diff 必须全量比对而非
水位跳过；修订 old→new 留痕本身就是投研信号（统计局回修幅度可分析）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date

_VALUE_TOL = 1e-6

_ROW_KEYS = ("industry_key", "stock_id", "metric_key", "source", "source_tier",
             "freq", "period", "value", "unit", "extra")


def _diff_key(r: dict) -> tuple[str, str, date]:
    return (r["metric_key"], r["freq"], r["period"])


def canonical_content_hash(rows: list[dict]) -> str:
    """行集合 → 16 hex sha256；排序消除顺序敏感，定点化消除 float 表示漂移."""
    canon = sorted(
        (
            {
                "metric_key": r["metric_key"], "freq": r["freq"],
                "period": r["period"].isoformat(), "value": round(float(r["value"]), 4),
            }
            for r in rows
        ),
        key=lambda x: (x["metric_key"], x["freq"], x["period"]),
    )
    return hashlib.sha256(json.dumps(canon, ensure_ascii=False).encode()).hexdigest()[:16]


def compute_ingest_actions(fetched: list[dict], existing: list[dict]) -> dict:
    """fetched vs existing（同 source）→ new/changed/unchanged/orphaned + 修订留痕.

    existing 为空（首抓）→ 全部 new。orphaned（库有响应无）只计数不删除：
    官方数据语义下不镜像上游删除，异常走人工核查。
    """
    by_key = {_diff_key(r): r for r in existing}
    fetched_keys = set()
    new: list[dict] = []
    changed: list[dict] = []
    revisions: list[dict] = []
    unchanged = 0
    for r in fetched:
        k = _diff_key(r)
        fetched_keys.add(k)
        cur = by_key.get(k)
        if cur is None:
            new.append(r)
        elif abs(float(r["value"]) - float(cur["value"])) > _VALUE_TOL:
            changed.append(r)
            revisions.append(
                {
                    "metric_key": k[0], "freq": k[1], "period": k[2],
                    "old": float(cur["value"]), "new": float(r["value"]),
                }
            )
        else:
            unchanged += 1
    return {
        "new": new,
        "changed": changed,
        "unchanged_count": unchanged,
        "orphaned_count": len([k for k in by_key if k not in fetched_keys]),
        "revisions": revisions,
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_industry_ingest_diff.py -v`
Expected: 9 passed

- [ ] **Step 5: 静态检查 + 提交**

Run: `cd backend && uv run --extra dev ruff check . && uv run --extra dev mypy app`

```bash
git add backend/app/services/industry_ingest_diff.py backend/tests/test_industry_ingest_diff.py
git commit -m "feat(industry): row-level ingest diff with revision trace + canonical hash"
```

---

### Task 5: fetcher 接线 — _fetch_xuantian_capacity_rows + ingest 主流程并轨

**Files:**
- Modify: `backend/app/services/industry_metric_service.py`（新增 fetcher 函数 + `ingest_industry_metrics` 的 akshare 分支并入，约 55 行）
- Test: `backend/tests/test_industry_fetchers.py`（追加测试）

**Interfaces:**
- Consumes: `get_xuantian_client()`（Task 2）、`parse_capacity_rows` 输出行格式、registry 三新指标（Task 3）
- Produces: `async def _fetch_xuantian_capacity_rows(cfg: IndustryConfig, client: XuantianClient | None = None) -> list[dict]`：玄田解析行 → 标准 metric 行（每指标一行展开，`extra={"channel": "xuantian", "raw_period": ...}`）；在 `source == "akshare"` 分支与 caaa 行同样"upsert 前并入"

- [ ] **Step 1: 写失败测试**

```python
# 追加到 backend/tests/test_industry_fetchers.py
from datetime import date

from app.services.industry_metric_service import _fetch_xuantian_capacity_rows


class _FakeXuantianClient:
    def __init__(self, rows):
        self._rows = rows

    async def fetch_capacity(self):
        return self._rows


async def test_xuantian_rows_expand_to_metric_rows():
    client = _FakeXuantianClient(
        [
            {"period": date(2025, 6, 30), "freq": "quarterly",
             "values": {"sow_inventory": 4043.0, "pork_output": 1418.0,
                        "hog_inventory": 42447.0, "hog_slaughter_quarterly": 17143.0},
             "raw_period": "2025年二季度（末）"},
            {"period": date(2025, 7, 31), "freq": "monthly",
             "values": {"sow_inventory": 4042.0}, "raw_period": "2025年7月"},
        ]
    )
    rows = await _fetch_xuantian_capacity_rows(PIG_INDUSTRY, client=client)
    assert len(rows) == 5  # 季度行 4 指标 + 月度行 1 指标
    by_key = {(r["metric_key"], r["period"].isoformat()): r for r in rows}
    sow_q = by_key[("sow_inventory", "2025-06-30")]
    assert sow_q["source"] == "xuantian"
    assert sow_q["source_tier"] == "official"
    assert sow_q["freq"] == "quarterly"
    assert sow_q["value"] == 4043.0
    assert sow_q["extra"]["raw_period"] == "2025年二季度（末）"
    sow_m = by_key[("sow_inventory", "2025-07-31")]
    assert sow_m["freq"] == "monthly"


async def test_xuantian_fetch_failure_returns_empty():
    class _Boom:
        async def fetch_capacity(self):
            raise RuntimeError("network down")

    rows = await _fetch_xuantian_capacity_rows(PIG_INDUSTRY, client=_Boom())
    assert rows == []


async def test_xuantian_unknown_metric_skipped():
    # registry 未定义的指标键静默跳过（跨行业复用防线）
    client = _FakeXuantianClient(
        [{"period": date(2025, 7, 31), "freq": "monthly",
          "values": {"nonexistent_metric": 1.0}, "raw_period": "2025年7月"}]
    )
    rows = await _fetch_xuantian_capacity_rows(PIG_INDUSTRY, client=client)
    assert rows == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_industry_fetchers.py -v -k xuantian`
Expected: FAIL（`_fetch_xuantian_capacity_rows` 不存在）

- [ ] **Step 3: 实现 fetcher + 并轨**

`industry_metric_service.py` 在 `_fetch_caaa_sow_row` 之后新增：

```python
async def _fetch_xuantian_capacity_rows(
    cfg: IndustryConfig, client: "XuantianClient | None" = None
) -> list[dict]:
    """玄田产能四指标 → 标准 metric 行（每指标展开一行）.

    客户端已做周期解析与哨兵剔除；本层只做 registry 对照展开。任何失败
    返回空列表（不抛穿），未命中 registry 的指标键静默跳过。
    """
    if client is None:
        from app.core.providers.xuantian_client import get_xuantian_client  # noqa: PLC0415

        client = get_xuantian_client()

    try:
        parsed = await client.fetch_capacity()
    except Exception as exc:  # 双保险：client 自身已兜底，此处防注入实现抛穿
        logger.warning("Xuantian capacity fetch raised (skipped): %s", exc)
        return []
    if not parsed:
        return []

    rows: list[dict] = []
    for item in parsed:
        for metric_key, value in item["values"].items():
            m = cfg.metric(metric_key)
            if m is None:
                continue
            rows.append(
                {
                    "industry_key": cfg.key,
                    "stock_id": 0,
                    "metric_key": m.key,
                    "source": "xuantian",
                    "source_tier": m.tier,
                    "freq": item["freq"],
                    "period": item["period"],
                    "value": value,
                    "unit": m.unit or None,
                    "extra": {"channel": "xuantian", "raw_period": item["raw_period"]},
                }
            )
    return rows
```

TYPE_CHECKING 块补 `from app.core.providers.xuantian_client import XuantianClient`。

`ingest_industry_metrics` 的 akshare 分支改为：

```python
    elif source == "akshare":
        # caaa + xuantian 行在 upsert 前并入：进入 covered_metrics → mock purge 覆盖
        rows = await _fetch_akshare_rows(cfg, months=months)
        rows += await _fetch_caaa_sow_row(cfg)
        rows += await _fetch_xuantian_capacity_rows(cfg)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_industry_fetchers.py -v`
Expected: 全部 passed（含既有 AKShare fetcher 测试）

- [ ] **Step 5: 静态检查 + 全量回归 + 提交**

Run: `cd backend && uv run pytest -x -q && uv run --extra dev ruff check . && uv run --extra dev mypy app`

```bash
git add backend/app/services/industry_metric_service.py backend/tests/test_industry_fetchers.py
git commit -m "feat(industry): wire xuantian capacity fetcher into ingest (2009-now backfill)"
```

---

### Task 6: 三层门控接入 ingest 主流程

**Files:**
- Modify: `backend/app/services/industry_metric_service.py`（`ingest_industry_metrics` 重构为门控版，约 80 行改动）
- Modify: `backend/app/scheduler/jobs.py:155-172`（`industry_metrics_refresh_job` 传 `force=False`）
- Modify: `backend/app/workers/industry_metrics_worker.py:45`（传 `force=True`）
- Test: `backend/tests/test_industry_ingest_gating.py`

**Interfaces:**
- Consumes: `get_ingest_state`/`upsert_ingest_state`/`touch_ingest_state`（Task 1）、`canonical_content_hash`/`compute_ingest_actions`（Task 4）
- Produces:
  - `ingest_industry_metrics(db, industry_key, source=None, months=37, *, force: bool = False)` — 新增 keyword-only 参数
  - 返回 dict 追加键：`"gating": "not_due" | "unchanged" | "full" | "incremental"`、`"stats": {"new": int, "changed": int, "unchanged": int, "orphaned": int}`、`"revisions": [...]`
  - `def next_due(source: str, now: datetime) -> datetime` — 各源发布节奏推算（见 Step 3 表）；**放在 `industry_ingest_diff.py` 纯函数模块**（ponytail 裁定：service 顶部 import config，单测 import 链会拉起整个 settings；纯函数进纯模块，service 只 re-export 使用）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_industry_ingest_gating.py
"""三层门控：L1 调度门 / L2 内容门 / L3 行级 diff 的编排逻辑（纯逻辑 + 内存 DB 状态表）。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.services.industry_ingest_diff import canonical_content_hash, compute_ingest_actions, next_due


def test_l1_not_due_skips_fetch():
    # next_due_at 在未来 → 调度门直接短路（不发请求）
    now = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)
    assert now < next_due("xuantian", now)  # 节奏推算：上次+30d，门内比较 now < state.next_due_at
    # next_due 的节奏表逐源断言在 Task 4 测试文件（纯函数住纯模块）


def test_l3_incremental_writes_only_delta():
    rows_v1 = [
        {"metric_key": "sow_inventory", "freq": "quarterly", "period": date(2025, 6, 30), "value": 4043.0},
        {"metric_key": "sow_inventory", "freq": "quarterly", "period": date(2025, 9, 30), "value": 4035.0},
    ]
    rows_v2 = [  # Q2 被修订 4043→4041，新增 Q4
        {"metric_key": "sow_inventory", "freq": "quarterly", "period": date(2025, 6, 30), "value": 4041.0},
        {"metric_key": "sow_inventory", "freq": "quarterly", "period": date(2025, 9, 30), "value": 4035.0},
        {"metric_key": "sow_inventory", "freq": "quarterly", "period": date(2025, 12, 31), "value": 3990.0},
    ]
    out = compute_ingest_actions(_pad(rows_v2), _pad(rows_v1))
    assert len(out["new"]) == 1 and len(out["changed"]) == 1
    assert out["revisions"][0]["old"] == 4043.0 and out["revisions"][0]["new"] == 4041.0
    assert canonical_content_hash(_pad(rows_v1)) != canonical_content_hash(_pad(rows_v2))


def _pad(rows):
    return [
        {**r, "industry_key": "pig", "stock_id": 0, "source": "xuantian",
         "source_tier": "official", "unit": "万头", "extra": None}
        for r in rows
    ]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_industry_ingest_gating.py -v`
Expected: FAIL（`next_due` 不存在）

- [ ] **Step 3: 实现门控编排**

`industry_metric_service.py` 顶部补 import（`datetime`, `UTC`, `canonical_content_hash`, `compute_ingest_actions`, 状态表 repo 函数，`next_due` 从 diff 模块 re-export）+ 新增：

```python
# ── 增量门控（三层）──────────────────────────────────────────────────
#
# L1 调度门：next_due_at 未到 → 不发请求（scheduled 轨道；worker force=True 绕过）
# L2 内容门：响应哈希与库内一致 → 零写入零重算，只 touch 检查时间
# L3 行级 diff：只写 new/changed 行；修订 old→new 留痕（官方回修是常态，非异常）
#
# 依据：官方统计会修订历史（2023 畜禽监测样本轮换即实例），因此任何层都
# 不以 last_period 水位跳过历史——水位仅审计用。

# 节奏表与 next_due() 定义在 industry_ingest_diff.py（纯函数模块），
# 此处 from app.services.industry_ingest_diff import next_due  # noqa: F401
```

`ingest_industry_metrics` 重构（保持函数签名向后兼容，新增 keyword-only `force`）：

```python
async def ingest_industry_metrics(
    db: AsyncSession,
    industry_key: str = "pig",
    source: str | None = None,
    months: int = 37,
    *,
    force: bool = False,
) -> dict:
    """Fetch → [L1/L2/L3 门控] → upsert → purge → derive → signal（幂等）.

    force=True（worker 手动触发）绕过 L1 调度门——修订场景的人工兜底；
    scheduled 轨道 force=False 吃满三层短路。
    """
    cfg = _require_industry(industry_key)
    source = source or settings.industry_data_source

    if source == "mock":
        rows = build_industry_mock_points(cfg, months=months)
        gating = "full"
    elif source == "akshare":
        # 玄田通道独立过门（唯一带全量历史响应的源）；日价源照常滚动抓取：
        xt_rows, gating, xt_stats, xt_revisions = await _gated_xuantian_fetch(db, cfg, force=force)
        rows = await _fetch_akshare_rows(cfg, months=months)
        rows += await _fetch_caaa_sow_row(cfg)
        rows += xt_rows
    else:
        raise ValueError(f"Unknown industry data source: {source}")

    await _ensure_reference_points(db, cfg)
    upserted = await repo.upsert_metrics(db, rows)

    covered = {r["metric_key"] for r in rows}
    purge_keys = _covered_purge_keys(covered)
    purged = 0
    if source != "mock" and upserted > 0 and purge_keys:
        purged = await repo.delete_rows_by_source(
            db, cfg.key, ["mock", "derived"], metric_keys=sorted(purge_keys)
        )

    # 短路条件看"本轮是否有任何写入"，不只看玄田 gating——玄田 unchanged 但
    # soozhu 日价有新行时（常态），派生（猪粮比/sow_mom）必须照常重算
    # （ponytail review 修正：原"gating != unchanged"会停掉日价源的派生链）
    wrote_anything = upserted > 0
    derived_count = 0
    signal = None
    if wrote_anything:
        derived_count = await _compute_derived_metrics(db, cfg)
        signal = await evaluate_and_store_signal(db, cfg)
    else:
        signal = await repo.latest_signal(db, cfg.key)

    result = {
        "source": source,
        "gating": gating,
        "upserted": upserted,
        "derived_upserted": derived_count,
        "purged": purged,
        "covered_metrics": sorted(covered),
        "signal": signal.signal_type if signal else None,
    }
    if gating in ("full", "incremental", "unchanged"):
        result["stats"] = xt_stats  # 仅 akshare 分支有 xt_stats；mock 分支此键缺省
        result["revisions"] = xt_revisions
    return result
```

`_gated_xuantian_fetch`（新增私有函数，编排三层门）：

```python
async def _gated_xuantian_fetch(
    db: AsyncSession, cfg: IndustryConfig, *, force: bool
) -> tuple[list[dict], str, dict, list[dict]]:
    """玄田通道三层门控：返回 (待写行, gating 状态, stats, revisions) 并维护状态表.

    gating: "not_due"（L1 短路，返回空行）| "unchanged"（L2 短路）|
            "full"（首抓全量）| "incremental"（有 new/changed）
    短路时 stats/revisions 返回空 dict/list，主流程直接透传。
    """
    now = datetime.now(UTC)
    state = await repo.get_ingest_state(db, cfg.key, "xuantian")

    # L1：调度门（仅非 force；首抓无 state 时直接放行）
    if not force and state and state.next_due_at and now < state.next_due_at:
        return [], "not_due", {}, []

    parsed = await _fetch_xuantian_capacity_rows(cfg)
    if not parsed:
        return [], "not_due", {}, []  # 抓取失败视为本轮跳过（client 已 log）

    new_hash = canonical_content_hash(parsed)

    # L2：内容门（哈希一致 → 零下游）
    if state and state.content_hash == new_hash:
        await repo.touch_ingest_state(db, cfg.key, "xuantian", last_checked_at=now)
        return [], "unchanged", {}, []

    # L3：行级 diff（首抓 existing 为空 → 全部 new）
    existing = await repo.list_metric_rows(
        db, cfg.key, source="xuantian"
    )
    actions = compute_ingest_actions(parsed, _as_row_dicts(existing))
    to_write = actions["new"] + actions["changed"]
    xt_stats = {
        "new": len(actions["new"]),
        "changed": len(actions["changed"]),
        "unchanged": actions["unchanged_count"],
        "orphaned": actions["orphaned_count"],
    }

    if to_write:
        max_period = max(r["period"] for r in parsed)
        await repo.upsert_ingest_state(
            db,
            {
                "industry_key": cfg.key,
                "source": "xuantian",
                "last_success_at": now,
                "last_period": max_period,
                "content_hash": new_hash,
                "next_due_at": next_due("xuantian", now),
                "stats": xt_stats,
            },
        )
    return to_write, ("full" if state is None else "incremental"), xt_stats, actions["revisions"]
```

同时需要两个配套件（本 Task 一并实现并测试）：

```python
# repo 追加（industry_metric_repo.py）——按 industry+source 拉全量行，供 L3 diff 比对
async def list_metric_rows(
    db: AsyncSession, industry_key: str, source: str
) -> list[IndustryMetric]:
    stmt = (
        select(IndustryMetric)
        .where(
            IndustryMetric.industry_key == industry_key,
            IndustryMetric.source == source,
        )
        .order_by(IndustryMetric.period.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


# service 侧 helper——ORM 行 → diff 输入 dict（键与 fetcher 产出的标准行对齐）
def _as_row_dicts(rows: list[IndustryMetric]) -> list[dict]:
    return [
        {
            "industry_key": r.industry_key,
            "stock_id": r.stock_id,
            "metric_key": r.metric_key,
            "source": r.source,
            "source_tier": r.source_tier,
            "freq": r.freq,
            "period": r.period,
            "value": float(r.value) if r.value is not None else None,
            "unit": r.unit,
            "extra": r.extra,
        }
        for r in rows
    ]
```

`_gated_xuantian_fetch` 返回 `(rows, gating, stats, revisions)` 四元组，主流程解包后透传进结果 dict（上面伪码已按此写）。

scheduler 与 worker 调用点各加一行：

```python
# app/scheduler/jobs.py:165
result = await industry_metric_service.ingest_industry_metrics(db, "pig", force=False)
# app/workers/industry_metrics_worker.py:45
result = await industry_metric_service.ingest_industry_metrics(
    db, industry_key=industry_key, source=source, months=months, force=True
)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_industry_ingest_gating.py tests/test_industry_ingest_diff.py tests/test_industry_ingest_state.py -v`
Expected: 全部 passed

- [ ] **Step 5: 全量回归 + 静态检查 + 提交**

Run: `cd backend && uv run pytest -x -q && uv run --extra dev ruff check . && uv run --extra dev mypy app`

```bash
git add backend/app/services/industry_metric_service.py backend/app/scheduler/jobs.py backend/app/workers/industry_metrics_worker.py backend/app/repositories/industry_metric_repo.py backend/tests/test_industry_ingest_gating.py
git commit -m "feat(industry): three-layer incremental gating for ingest (schedule/content-hash/row-diff)"
```

---

### Task 7: 端到端验证（真实 API 全链路）+ 文档同步

**Files:**
- Modify: `docs/design/data-source.md`（§六 参考链接补玄田；§五 历史回补工作量的 stats_gov CSV 行改为已完成语义）
- Modify: `plans/industry-research-workbench.md:90`（勾选 CSV 回补项，注明实际通道为玄田直连）
- Modify: `docs/Changelog.md`
- Test: 手动验证脚本（不入库，运维操作记录于此）

**Interfaces:**
- Consumes: 全部前序 Task
- Produces: 生产可验证的完整链路；文档交叉引用一致

- [ ] **Step 1: 起库并跑真实全链路**

```bash
# 确保 docker PG 起着（127.0.0.1:5433）
docker compose up -d postgres
cd backend && uv run alembic upgrade head
# 手动触发一次 worker 语义的 ingest（force=True，走真实 API）
uv run python -c "
import asyncio
from app.core.database import async_session_factory
from app.services.industry_metric_service import ingest_industry_metrics

async def main():
    async with async_session_factory() as db:
        r = await ingest_industry_metrics(db, 'pig', source='akshare', force=True)
        await db.commit()
        print(r)

asyncio.run(main())
"
```

Expected（关键断言）：
- `gating == "full"`（首抓）
- `covered_metrics` 含 `sow_inventory, pork_output, hog_inventory, hog_slaughter_quarterly`
- `stats.new >= 79`（能繁 22 + 产量/存栏/出栏各 19）
- 再跑一遍同命令：`gating == "unchanged"`、`upserted == 0`、`stats.unchanged == 上轮 new 数`

- [ ] **Step 2: DB 抽查数据正确性**

凭据以 compose env 为准（实测 `docker-compose.yml:48-50`：用户 `stock_user`、库 `stock_bot`；宿主机映射 `127.0.0.1:5433`）：

```bash
docker compose exec postgres psql -U stock_user -d stock_bot -c "
SELECT metric_key, freq, count(*), min(period), max(period)
FROM industry_metrics WHERE source = 'xuantian'
GROUP BY metric_key, freq ORDER BY metric_key, freq;"
```

Expected：4 个 metric_key；sow_inventory 三种 freq（16 yearly + 3 quarterly + 3 monthly）；其余三个 = 16 yearly + 3 quarterly；min(period) = 2009-12-31；ASF 锚点抽查 `SELECT value FROM industry_metrics WHERE source='xuantian' AND metric_key='sow_inventory' AND period='2019-12-31'` = 3080。

- [ ] **Step 3: 文档同步**

`docs/design/data-source.md` §六 参考链接追加：

```markdown
- [玄田数据 · 产能数据（中国养猪网，2009→now 年/季/月产能四指标）](https://zhujia.zhuwang.com.cn)（API：`POST https://xt.yangzhu.vip/data/getmapdata?ptype=7&areano=-1`，Referer 必带；2026-09-29 实测可用，月度行产量/存栏/出栏为 0 哨兵）
```

§二 L2 表中 `sow_inventory` 行的"渠道"列追加玄田镜像说明；`hog_inventory` / `hog_slaughter_quarterly` / 新增 `pork_output` 行标注"已接入：xuantian 源"。

`plans/industry-research-workbench.md:90` 的未勾选项改为：

```markdown
- [x] 产能历史回补 2009 至今（stats_gov CSV 通道由玄田直连 API 取代：一次调用返回全历史年/季/月序列，2026-09-29 实测；月度环比历史仍需 CAAA 文章通道逐月回补，见下）
```

- [ ] **Step 4: 门禁与收尾**

```bash
cd /Users/wqz/Developer/stock-bot
git status --porcelain
bash scripts/slop_scan.sh
bash scripts/self_review.sh
```

`docs/Changelog.md` 追加（当日日期段）：

```markdown
- 行业投研：接入玄田数据产能通道（`XuantianClient` 直连 xt.yangzhu.vip，2009→now 能繁/猪肉产量/生猪存栏/出栏全历史落库，registry 新增 `pork_output`/`hog_inventory`/`hog_slaughter_quarterly` 三指标 + `xuantian` source）；ingest 增量门控三层（调度门/内容哈希/行级 diff，新表 `industry_ingest_state`，修订 old→new 留痕），稳定态 ingest 近零成本
```

- [ ] **Step 5: 提交**

```bash
git add docs/design/data-source.md plans/industry-research-workbench.md docs/Changelog.md
git commit -m "docs(industry): xuantian channel + incremental gating; mark capacity backfill done"
```

---

## 验收清单（全 Task 完成后）

> 2026-09-29 回访勾选（依据：Task 7 真库真 API 验证记录 + final review）。

- [x] `uv run pytest`（backend，全量）绿（632 passed；final-fix 后含新增门控测试复跑全绿）
- [x] `uv run --extra dev ruff check .` / `uv run --extra dev mypy app` 绿（Task 7 门禁记录 + final-fix 复跑）
- [x] `bash scripts/self_review.sh` 绿（含 doc_gate）（Task 7 Step 4：15 项 doc_gate 通过）
- [x] DB 内 `source='xuantian'` 行数 ≥ 79，period 覆盖 2009-12-31 → 最近月（实测 79 行，2009-12-31 → 2025-10-31）
- [x] 重复 ingest：`gating=unchanged`、零写入、零派生重算（玄田通道零写入 79→79；日价源照常滚动为裁定内行为）
- [x] 未到期 ingest（scheduled 轨道）：`gating=not_due`、零请求
- [x] 手动改一行 DB value 后 ingest：`gating=incremental`、该行 changed、revisions 留痕 old→new（old=9999.0 → new=3080.0 自愈）
- [ ] 前端工作台：supply 分组出现猪肉产量/生猪存栏/出栏三卡片（registry 驱动，零前端改动验证）——**未实际目视验证**（Task 7 与 final-fix 均未起前端核对本项，合并前或回访时补）
- [ ] `plans/index.md` 本计划状态改 completed（依据：验收清单全勾）——已登记为 active（实施完成待合并），合并后改 completed

## 已知限制与后续（不在本计划范围）

1. **月度环比历史（2021-05→2024）**：玄田月度行只有近几个月，完整月度序列仍需 CAAA 文章逐月回补——独立计划，通道已在（`CaaaClient` + 文章 URL 模式）
2. **派生收窄**：`_compute_derived_metrics` 仍全量重算；L2 短路已消除稳定态成本，受影响 period 精确重算留给回测引擎计划一起做（需要 period 区间失效语义设计）
3. **stats_gov 直连**：大陆网络环境可后续接 EasyQuery API（指标代码 A0207），registry 占位已留，优先级天然正确
4. **多行业推广**：`_gated_xuantian_fetch` 的三层门是 source 级通用件，broiler 等行业接入新源时直接复用
