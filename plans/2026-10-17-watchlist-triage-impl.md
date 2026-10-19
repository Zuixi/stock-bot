---
status:      completed
scope:       实现「自选体检」M0–M3：8 个事实型探测器 + 读时算 API + `/me` 今日体检 Tab + e2e；零新表、零新 cron
touches:     backend/app/services/, backend/app/api/v1/, backend/app/schemas/, backend/scripts/, backend/tests/, frontend/src/pages/me/, frontend/src/shared/api/, frontend/e2e/, docs/
updated:     2026-10-17
next-action: -
---

# 自选体检（Watchlist Triage）实现计划

> **For agentic workers:** 本计划逐任务执行，每任务自带测试周期。步骤用 `- [ ]` 跟踪。
> 契约：[docs/specs/S8-watchlist-triage.md](../docs/specs/S8-watchlist-triage.md)；证据：[signal-rule-calibration.md](../docs/design/signal-rule-calibration.md)。

**Goal:** 用户打开「我的 → 今日体检」，看到自己自选里**哪几只有事、是什么事、点哪复核**；事实型、零新表。

**Architecture:** 纯函数探测器（`triage_detectors.py`）吃一个预取的 `TriageContext`（5 条固定查询：行情窗口/涨停价/公告/龙虎榜/板块），
组合层（`triage_service.py`）做**单股合并 + severity 排序 + ≤8 上限**，API（`GET /api/v1/watchlists/triage`）一次返回；
前端 `/me` 新增首个 Tab，默认选中。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Pydantic v2 + pytest；React 18 + TS + AntD 5 + React Query + Playwright。

**Spec:** [docs/specs/S8-watchlist-triage.md](../docs/specs/S8-watchlist-triage.md)

## Global Constraints

- **零新表、零新 cron**：不写 Alembic 迁移；不新增 `QUEUES` 项；读时算。
- **只读自选**（≤50 只），**不扫全市场**；`as_of` 用"最近完整日"（`resolve_latest_complete_day` 的 quality 判据）。
- **口径禁令**：禁 `turnover_rate_f`（全 NULL）；禁 `market_limit_up_stock_daily`（仅 2 天）；禁个股融资余额（不可得）；
  涨跌幅一律 `close/pre_close - 1`（禁 `adj_factor` 判除权）。
- **分级只有四枚举**：`risk` / `event` / `attention` / `background`（顺序即排序优先级）。
- **禁用词**（≥20，清单在 `triage_compliance.py`）：文案不得出现 买入/卖出/推荐/看好/建议关注/目标价/机会/布局 等。
- **反 N+1**：一次请求的 DB 查询数 **≤6** 且不随自选数增长（有断言计数的测试）。
- 后端行宽 100（ruff）；文件头条注释写清"为什么"；测试中文 docstring 写明防的是什么。

---

### Task 1: 探测器（纯函数 + registry 常量）

**Files:**
- Create: `backend/app/services/triage_detectors.py`
- Test: `backend/tests/test_triage_detectors.py`

**Interfaces:**
- Consumes: `app.services.industry_registry`（只读，不加常量到别处）
- Produces:
  - `@dataclass(frozen=True) DetectorSpec(key,label,severity,thresholds,required_fields,verify)`
  - `TRIAGE_DETECTORS: tuple[DetectorSpec, ...]`（8 条，顺序 = 展示优先级）
  - `@dataclass TriageContext(as_of, symbols, quotes: dict[str, list[QuoteRow]], limits: dict[str, LimitRow], announcements: dict[str, list[AnnRow]], dragon_tiger: dict[str, DtRow], sector: dict[str, SectorRow], sectors_of: dict[str, str])`
  - `@dataclass TriageItem(symbol, name, detector, severity, headline, facts: list[Fact], as_of, source, verify)`
  - `def detect(spec, ctx, symbol) -> TriageItem | None`
  - `def run_detectors(ctx) -> list[TriageItem]`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_triage_detectors.py
"""探测器契约：分级枚举、required_fields、次新排除、口径禁令（不得引用 turnover_rate_f）. """
from __future__ import annotations

from datetime import date

import pytest

from app.services import triage_detectors as td


def test_detector_specs_are_locked() -> None:
    assert len(td.TRIAGE_DETECTORS) == 8
    keys = [s.key for s in td.TRIAGE_DETECTORS]
    assert len(set(keys)) == 8
    assert {s.severity for s in td.TRIAGE_DETECTORS} <= td.SEVERITIES


def test_severity_order_is_fixed() -> None:
    assert td.SEVERITIES == ("risk", "event", "attention", "background")


def test_new_high_low_skips_newly_listed() -> None:
    """上市 <60 交易日的股票不得命中新高低（否则次新恒真）。"""
    ctx = td.make_context_for_test(
        as_of=date(2026, 10, 16),
        quotes={"300389": [(date(2026, 10, 1), 10.0, 1000.0, 10.0)] * 20},
        names={"300389": "艾比森"},
    )
    assert td.detect(td.SPEC_NEW_HIGH_LOW, ctx, "300389") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && UV_CACHE_DIR=.uv-cache uv run --extra dev pytest tests/test_triage_detectors.py -v`
Expected: FAIL with `ModuleNotFoundError: app.services.triage_detectors`

- [ ] **Step 3: Write minimal implementation**

```python
"""自选体检的 8 个**事实型**探测器（纯函数，无 IO）.

为什么是"事实"而不是"信号"：2026-10-17 的规则标定把预测型规则证伪（放量新高 −0.92%、首板 −0.28%），
见 docs/design/signal-rule-calibration.md。本模块只陈述**已发生的事**，不做打分、不预测、不承诺。

口径禁令（实机标定得出，改前先看标定报告）：
- 换手率只能用 `turnover_rate`：`turnover_rate_f` 全列 NULL（0/133,308）；
- 涨停判定只能走 `stock_price_limits.up_limit`：`market_limit_up_stock_daily` 实测只有 2 个交易日；
- 个股融资余额不可得（库里只有市场级 margin）⇒ 不得出现"个股杠杆"条件；
- 涨跌幅一律 `close/pre_close - 1`（`adj_factor` 仅 1.3% 非空，不能用于除权判断）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

SEVERITIES: tuple[str, ...] = ("risk", "event", "attention", "background")
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}

#: 上市不足这么多交易日不参与 `new_high_low`（次新恒创新高，属假事实）
MIN_HISTORY_DAYS = 60


@dataclass(frozen=True)
class Fact:
    label: str
    value: float | str
    unit: str = ""


@dataclass(frozen=True)
class DetectorSpec:
    key: str
    label: str
    severity: str
    thresholds: dict[str, float]
    required_fields: tuple[str, ...]
    verify: str


@dataclass(frozen=True)
class QuoteRow:
    trade_date: date
    close: float
    amount: float
    pre_close: float


@dataclass(frozen=True)
class LimitRow:
    up_limit: float
    high: float
    close: float


@dataclass(frozen=True)
class AnnRow:
    announcement_id: str
    title: str
    category: str
    announcement_time: Any


@dataclass(frozen=True)
class DtRow:
    trade_date: date
    net_amount: float


@dataclass(frozen=True)
class SectorRow:
    industry_code: str
    pct_chg: float | None


@dataclass
class TriageContext:
    as_of: date
    names: dict[str, str]
    quotes: dict[str, list[QuoteRow]] = field(default_factory=dict)
    limits: dict[str, LimitRow] = field(default_factory=dict)
    announcements: dict[str, list[AnnRow]] = field(default_factory=dict)
    dragon_tiger: dict[str, list[DtRow]] = field(default_factory=dict)
    sector_pct: dict[str, float] = field(default_factory=dict)
    sector_of: dict[str, str] = field(default_factory=dict)
    unavailable: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class TriageItem:
    symbol: str
    name: str
    detector: str
    severity: str
    headline: str
    facts: tuple[Fact, ...]
    as_of: date
    source: str
    verify: str


def _f(rows: list[QuoteRow], n: int) -> list[QuoteRow]:
    return rows[-n:] if n > 0 else rows


def _pct(close: float, base: float) -> float:
    return (close / base - 1.0) * 100.0 if base else 0.0


SPEC_BREAK_MA20 = DetectorSpec(
    key="break_ma20",
    label="跌破 20 日线",
    severity="risk",
    thresholds={"ma_days": 20},
    required_fields=("daily_quotes.close",),
    verify="kline",
)
SPEC_DROP_SHARP = DetectorSpec(
    key="drop_sharp",
    label="跌得急",
    severity="risk",
    thresholds={"day_pct": -7.0, "five_day_pct": -12.0},
    required_fields=("daily_quotes.close", "daily_quotes.pre_close"),
    verify="kline",
)
SPEC_VOLUME_SPIKE = DetectorSpec(
    key="volume_spike",
    label="放量异动",
    severity="attention",
    thresholds={"amount_ratio": 2.0, "abs_pct": 5.0},
    required_fields=("daily_quotes.amount",),
    verify="kline",
)
SPEC_LIMIT_UP_STATE = DetectorSpec(
    key="limit_up_state",
    label="涨停 / 炸板",
    severity="attention",
    thresholds={"touch_tolerance": 0.005},
    required_fields=("stock_price_limits.up_limit", "daily_quotes.high"),
    verify="kline",
)
SPEC_NEW_ANNOUNCEMENT = DetectorSpec(
    key="new_announcement",
    label="有新公告",
    severity="event",
    thresholds={"days": 3},
    required_fields=("announcements.title", "announcements.announcement_time"),
    verify="announcement",
)
SPEC_DRAGON_TIGER = DetectorSpec(
    key="dragon_tiger",
    label="上了龙虎榜",
    severity="event",
    thresholds={"days": 3},
    required_fields=("dragon_tiger_entries.net_amount",),
    verify="dragon_tiger",
)
SPEC_SECTOR_MOVE = DetectorSpec(
    key="sector_move",
    label="所属板块在动",
    severity="background",
    thresholds={"top_pct": 10.0},
    required_fields=("industry_metrics.pct_chg",),
    verify="sector",
)
SPEC_NEW_HIGH_LOW = DetectorSpec(
    key="new_high_low",
    label="创 60 日新高 / 新低",
    severity="attention",
    thresholds={"days": 60},
    required_fields=("daily_quotes.close",),
    verify="kline",
)

TRIAGE_DETECTORS: tuple[DetectorSpec, ...] = (
    SPEC_BREAK_MA20,
    SPEC_DROP_SHARP,
    SPEC_LIMIT_UP_STATE,
    SPEC_NEW_ANNOUNCEMENT,
    SPEC_DRAGON_TIGER,
    SPEC_VOLUME_SPIKE,
    SPEC_NEW_HIGH_LOW,
    SPEC_SECTOR_MOVE,
)


def _assert_detectors() -> None:
    if len(TRIAGE_DETECTORS) != 8:
        raise RuntimeError(f"探测器必须是 8 个，实际 {len(TRIAGE_DETECTORS)}")
    keys = [s.key for s in TRIAGE_DETECTORS]
    if len(set(keys)) != len(keys):
        raise RuntimeError(f"探测器 key 重复：{keys}")
    for s in TRIAGE_DETECTORS:
        if s.severity not in SEVERITY_RANK:
            raise RuntimeError(f"{s.key} 的 severity 非法：{s.severity}")
        if not s.required_fields:
            raise RuntimeError(f"{s.key} 必须声明 required_fields")
        if "turnover_rate_f" in " ".join(s.required_fields):
            raise RuntimeError("禁止用 turnover_rate_f：实测全列 NULL")


_assert_detectors()


def make_context_for_test(**kw: Any) -> TriageContext:
    """构造最小上下文（测试用；生产走 triage_service.load_context）。"""
    ctx = TriageContext(as_of=kw.pop("time", None) or kw.pop("as_of"), names=kw.pop("names", {}))
    raw = kw.pop("quotes", {})
    ctx.quotes = {s: [QuoteRow(*r) for r in rows] for s, rows in raw.items()}
    return ctx


def _detect_break_ma20(spec: DetectorSpec, ctx: TriageContext, symbol: str) -> TriageItem | None:
    rows = ctx.quotes.get(symbol) or []
    n = int(spec.thresholds["ma_days"]) + 1
    if len(rows) < n:
        return None
    window = _f(rows, n)
    ma20 = sum(r.close for r in window[:-1]) / (n - 1)
    today, prev = window[-1], window[-2]
    prev_ma = sum(r.close for r in _f(rows, n + 1)[:-2]) / (n - 1) if len(rows) >= n + 1 else ma20
    if today.close < ma20 and prev.close >= prev_ma:
        return TriageItem(
            symbol=symbol,
            name=ctx.names.get(symbol, symbol),
            detector=spec.key,
            severity=spec.severity,
            headline=f"跌破 {spec.thresholds['ma_days']:.0f} 日线",
            facts=(Fact("收盘", today.close, "元"), Fact("MA20", round(ma20, 2), "元")),
            as_of=ctx.as_of,
            source="daily_quotes",
            verify="kline",
        )
    return None


def _detect_drop_sharp(spec: DetectorSpec, ctx: TriageContext, symbol: str) -> TriageItem | None:
    rows = ctx.quotes.get(symbol) or []
    if len(rows) < 6:
        return None
    day = _pct(rows[-1].close, rows[-1].pre_close)
    five = _pct(rows[-1].close, rows[-6].close)
    thr = spec.thresholds
    if day <= thr["day_pct"] or five <= thr["five_day_pct"]:
        return TriageItem(
            symbol=symbol,
            name=ctx.names.get(symbol, symbol),
            detector=spec.key,
            severity=spec.severity,
            headline="今天跌得急" if day <= thr["day_pct"] else "5 日跌幅偏大",
            facts=(Fact("当日涨跌", round(day, 2), "%"), Fact("5 日涨跌", round(five, 2), "%")),
            as_of=ctx.as_of,
            source="daily_quotes",
            verify="kline",
        )
    return None


def _detect_limit_up_state(spec: DetectorSpec, ctx: TriageContext, symbol: str) -> TriageItem | None:
    lim = ctx.limits.get(symbol)
    if lim is None or lim.up_limit <= 0:
        return None
    tol = spec.thresholds["touch_tolerance"]
    if lim.close >= lim.up_limit - tol:
        head, sev = "涨停", "attention"
    elif lim.high >= lim.up_limit - tol:
        head, sev = "炸板（盘中触及涨停，收盘在板下）", "risk"
    else:
        return None
    return TriageItem(
        symbol=symbol,
        name=ctx.names.get(symbol, symbol),
        detector=spec.key,
        severity=sev,
        headline=head,
        facts=(Fact("涨停价", lim.up_limit, "元"), Fact("收盘", lim.close, "元")),
        as_of=ctx.as_of,
        source="stock_price_limits",
        verify="kline",
    )


def _detect_new_announcement(spec: DetectorSpec, ctx: TriageContext, symbol: str) -> TriageItem | None:
    rows = ctx.announcements.get(symbol) or []
    if not rows:
        return None
    top = rows[0]
    priority = any(r.category == "equity_change" for r in rows)
    return TriageItem(
        symbol=symbol,
        name=ctx.names.get(symbol, symbol),
        detector=spec.key,
        severity="event",
        headline=("权益变动公告：" if priority else "新公告：") + top.title[:60],
        facts=(Fact("公告数", len(rows), "条"),),
        as_of=ctx.as_of,
        source="announcements",
        verify="announcement",
    )


def _detect_dragon_tiger(spec: DetectorSpec, ctx: TriageContext, symbol: str) -> TriageItem | None:
    rows = ctx.dragon_tiger.get(symbol) or []
    if not rows:
        return None
    net = sum(r.net_amount for r in rows)
    return TriageItem(
        symbol=symbol,
        name=ctx.names.get(symbol, symbol),
        detector=spec.key,
        severity="event",
        headline=f"近 {len(rows)} 次龙虎榜，净额 {net / 1e4:.0f} 万",
        facts=(Fact("上榜次数", len(rows), "次"),),
        as_of=ctx.as_of,
        source="dragon_tiger_entries",
        verify="dragon_tiger",
    )


def _detect_volume_spike(spec: DetectorSpec, ctx: TriageContext, symbol: str) -> TriageItem | None:
    rows = ctx.quotes.get(symbol) or []
    if len(rows) < 21:
        return None
    avg20 = sum(r.amount for r in rows[-21:-1]) / 20
    today = rows[-1]
    if avg20 <= 0:
        return None
    ratio = today.amount / avg20
    pct = _pct(today.close, today.pre_close)
    thr = spec.thresholds
    if ratio >= thr["amount_ratio"] and abs(pct) >= thr["abs_pct"]:
        return TriageItem(
            symbol=symbol,
            name=ctx.names.get(symbol, symbol),
            detector=spec.key,
            severity=spec.severity,
            headline="放量异动",
            facts=(Fact("量比(20日)", round(ratio, 2), "倍"), Fact("当日涨跌", round(pct, 2), "%")),
            as_of=ctx.as_of,
            source="daily_quotes",
            verify="kline",
        )
    return None


def _detect_new_high_low(spec: DetectorSpec, ctx: TriageContext, symbol: str) -> TriageItem | None:
    rows = ctx.quotes.get(symbol) or []
    days = int(spec.thresholds["days"])
    if len(rows) < MIN_HISTORY_DAYS + 1:
        return None
    window = _f(rows, days + 1)
    today = window[-1]
    hi = max(r.close for r in window[:-1])
    lo = min(r.close for r in window[:-1])
    if today.close > hi:
        head, sev, facts = f"创 {days} 日新高", "attention", (Fact("收盘", today.close, "元"),)
    elif today.close < lo:
        head, sev, facts = f"创 {days} 日新低", "risk", (Fact("收盘", today.close, "元"),)
    else:
        return None
    return TriageItem(
        symbol=symbol,
        name=ctx.names.get(symbol, symbol),
        detector=spec.key,
        severity=sev,
        headline=head,
        facts=facts,
        as_of=ctx.as_of,
        source="daily_quotes",
        verify="kline",
    )


def _detect_sector_move(spec: DetectorSpec, ctx: TriageContext, symbol: str) -> TriageItem | None:
    code = ctx.sector_of.get(symbol)
    if not code:
        return None
    pct = ctx.sector_pct.get(code)
    if pct is None:
        return None
    thr = spec.thresholds["top_pct"]
    if abs(pct) >= thr:
        return TriageItem(
            symbol=symbol,
            name=ctx.names.get(symbol, symbol),
            detector=spec.key,
            severity="background",
            headline=f"所属板块当日 {pct:+.2f}%",
            facts=(Fact("板块涨跌", round(pct, 2), "%"),),
            as_of=ctx.as_of,
            source="industry_metrics",
            verify="sector",
        )
    return None


_RUNNERS = {
    "break_ma20": _detect_break_ma20,
    "drop_sharp": _detect_drop_sharp,
    "limit_up_state": _detect_limit_up_state,
    "new_announcement": _detect_new_announcement,
    "dragon_tiger": _detect_dragon_tiger,
    "volume_spike": _detect_volume_spike,
    "new_high_low": _detect_new_high_low,
    "sector_move": _detect_sector_move,
}
if set(_RUNNERS) != {s.key for s in TRIAGE_DETECTORS}:  # 纯防御：加 spec 必须加实现
    raise RuntimeError("TRIAGE_DETECTORS 与探测器实现不一致")


def detect(spec: DetectorSpec, ctx: TriageContext, symbol: str) -> TriageItem | None:
    return _RUNNERS[spec.key](spec, ctx, symbol)


def run_detectors(ctx: TriageContext) -> list[TriageItem]:
    items: list[TriageItem] = []
    for symbol in ctx.symbols_order if hasattr(ctx, "symbols_order") else sorted(ctx.quotes):
        for spec in TRIAGE_DETECTORS:
            item = _RUNNERS[spec.key](spec, ctx, symbol)
            if item is not None:
                items.append(item)
    return items
```

> 注：`TriageContext` 需要 `symbols_order: list[str]` 字段（按自选顺序），在 Step 3 一并加上；
> `make_context_for_test` 是**测试辅助**，生产路径用 `triage_service.load_context`。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && UV_CACHE_DIR=.uv-cache uv run --extra dev pytest tests/test_triage_detectors.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/triage_detectors.py backend/tests/test_triage_detectors.py
git commit -m "feat(triage): 8 个事实型探测器（纯函数 + 分级枚举 + 口径禁令）"
```

---

### Task 2: 上下文装载 + 组合（合并 / 排序 / ≤8）

**Files:**
- Create: `backend/app/services/triage_service.py`
- Test: `backend/tests/test_triage_service.py`

**Interfaces:**
- Consumes: `triage_detectors.{TriageContext, TriageItem, run_detectors, SEVERITY_RANK}`
- Produces:
  - `async def load_context(db, symbols: list[str], as_of: date) -> TriageContext`（**固定 5 条查询**）
  - `def compose(items: list[TriageItem], limit: int = 8) -> tuple[list[TriageItem], int]`（items 已合并/排序，第 2 值 = 被折叠数）
  - `async def get_triage(db, symbols: list[str]) -> TriageResult`

- [ ] **Step 1: Write the failing test**

```python
"""组合层：单股合并取最高 severity、排序固定、≤8 折叠、unavailable 透传."""
from datetime import date

from app.services import triage_detectors as td
from app.services import triage_service as svc


def _item(sym: str, sev: str, det: str = "drop_sharp") -> td.TriageItem:
    return td.TriageItem(sym, sym, det, sev, "x", (), date(2026, 10, 16), "daily_quotes", "kline")


def test_compose_merges_same_symbol_and_keeps_highest_severity() -> None:
    items = [_item("600000", "attention", "volume_spike"), _item("600000", "risk"), _item("000001", "event")]
    merged, folded = svc.compose(items)
    assert folded == 0
    assert [i.symbol for i in merged] == ["600000", "000001"]
    assert merged[0].severity == "risk"


def test_compose_sorts_by_severity_then_abs_pct() -> None:
    items = [_item("a", "background"), _item("b", "risk"), _item("c", "event")]
    merged, _ = svc.compose(items)
    assert [i.severity for i in merged] == ["risk", "event", "background"]


def test_compose_folds_beyond_limit() -> None:
    items = [_item(f"{i:06d}", "attention") for i in range(12)]
    merged, folded = svc.compose(items, limit=8)
    assert len(merged) == 8
    assert folded == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && UV_CACHE_DIR=.uv-cache uv run --extra dev pytest tests/test_triage_service.py -v`
Expected: FAIL（`ImportError`）

- [ ] **Step 3: Write minimal implementation**

```python
"""自选体检：批量取数（固定 5 条查询）+ 组合（合并 / 排序 / ≤8）.

**为什么读时算而不是落表**：自选 ≤50 只、窗口 ≤60 交易日，5 条固定查询即可；
落表要新增表 + cron + 回填（见 spec S8.3 的"零新表"约束）。
**反 N+1**：所有查询按 `symbols` 批量取，循环里不查库；测试断言查询数 ≤6。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market_data import Announcement, DragonTigerEntry, StockPriceLimit
from app.models.quote import DailyQuote
from app.models.securities import Stock
from app.services import triage_detectors as td
from app.services.market_day_service import resolve_latest_complete_day

MAX_SYMBOLS = 50
WINDOW_DAYS = 60
LIMIT = 8


@dataclass
class TriageResult:
    as_of: date
    items: list[td.TriageItem]
    quiet_count: int
    folded: int
    unavailable: list[str]
    names: dict[str, str]


async def load_context(db: AsyncSession, symbols: list[str], as_of: date) -> td.TriageContext:
    """固定 5 条查询：行情窗口 / 涨停价 / 公告 / 龙虎榜 / 板块涨幅。"""
    ctx = td.TriageContext(as_of=as_of, names={}, symbols_order=list(symbols))
    if not symbols:
        return ctx

    stocks = (await db.execute(select(Stock.id, Stock.symbol, Stock.name).where(Stock.symbol.in_(symbols)))).all()
    id_of = {row.symbol: row.id for row in stocks}
    ctx.names = {row.symbol: row.name for row in stocks}

    # ① 行情窗口：一次取全部自选股的最近 60 个交易日
    rows = (
        await db.execute(
            select(DailyQuote.stock_id, DailyQuote.trade_date, DailyQuote.close, DailyQuote.pre_close,
                   DailyQuote.amount)
            .where(DailyQuote.stock_id.in_(list(id_of.values())), DailyQuote.trade_date <= as_of)
            .order_by(DailyQuote.stock_id, DailyQuote.trade_date)
        )
    ).all()
    sym_of = {v: k for k, v in id_of.items()}
    for sid, d, close, pre, amount in rows:
        if close is None:
            continue
        ctx.quotes.setdefault(sym_of[sid], []).append(
            td.QuoteRow(trade_date=d, close=float(close), amount=float(amount or 0), pre_close=float(pre or close))
        )
    for s in ctx.quotes:
        ctx.quotes[s] = ctx.quotes[s][-WINDOW_DAYS:]

    # ② 涨停价 + 当日最高（炸板需要 high）
    lim_rows = (
        await db.execute(
            select(StockPriceLimit.stock_id, StockPriceLimit.up_limit, DailyQuote.high, DailyQuote.close)
            .join(DailyQuote, (DailyQuote.stock_id == StockPriceLimit.stock_id)
                  & (DailyQuote.trade_date == StockPriceLimit.trade_date))
            .where(StockPriceLimit.stock_id.in_(list(id_of.values())),
                   StockPriceLimit.trade_date == as_of)
        )
    ).all()
    for sid, up, high, close in lim_rows:
        ctx.limits[sym_of[sid]] = td.LimitRow(float(up), float(high or 0), float(close or 0))

    # ③ 公告（近 3 个自然日）
    ann_rows = (
        await db.execute(
            select(Announcement.sec_code, Announcement.announcement_id, Announcement.title,
                   func.coalesce(Announcement.category, ""))
            .where(Announcement.sec_code.in_(symbols),
                   Announcement.announce_time >= _days_before(as_of, 3))
            .order_by(Announcement.announce_time.desc())
        )
    ).all()
    for code, aid, title, cat in ann_rows:
        ctx.announcements.setdefault(code, []).append(
            td.AnnRow(str(aid), title or "", cat or "", None)
        )

    # ④ 龙虎榜（近 3 个交易日）
    dt_rows = (
        await db.execute(
            select(DragonTigerEntry.ts_code, DragonTigerEntry.trade_date, DragonTigerEntry.net_amount)
            .where(DragonTigerEntry.ts_code.like(_ts_like(symbols)),
                   DragonTigerEntry.trade_date >= _trading_days_before(db, as_of, 3))
        )
    ).all()
    for ts_code, d, net in dt_rows:
        code = str(ts_code).split(".")[0]
        ctx.dragon_tiger.setdefault(code, []).append(td.DtRow(d, float(net or 0)))

    return ctx


def compose(items: list[td.TriageItem], limit: int = LIMIT) -> tuple[list[td.TriageItem], int]:
    """单股合并（取最高 severity）+ 排序 + 折叠；返回 (展示条目, 被折叠数)."""
    best: dict[str, td.TriageItem] = {}
    for it in items:
        cur = best.get(it.symbol)
        if cur is None or td.SEVERITY_RANK[it.severity] < td.SEVERITY_RANK[cur.severity]:
            best[it.symbol] = it
    ordered = sorted(best.values(), key=lambda i: (td.SEVERITY_RANK[i.severity], i.symbol))
    return ordered[:limit], max(0, len(ordered) - limit)


async def get_triage(db: AsyncSession, symbols: list[str]) -> TriageResult:
    day = await resolve_latest_complete_day(db)
    if day is None:
        return TriageResult(as_of=date.today(), items=[], quiet_count=0, folded=0, unavailable=[], names={})
    ctx = await load_context(db, symbols, day.trade_date if hasattr(day, "trade_date") else day)
    found = td.run_detectors(ctx) if symbols else []
    items, folded = compose(found)
    return TriageResult(
        as_of=ctx.as_of,
        items=items,
        quiet_count=max(0, len(symbols) - len({i.symbol for i in items})),
        folded=folded,
        unavailable=sorted(ctx.unavailable),
        names=ctx.names,
    )
```

> 占位函数 `_days_before` / `_trading_days_before` / `_ts_like` 在同文件实现为 6 行小工具
> （前两个用 `timedelta` + `trade_cal` 查询；`_ts_like` 返回 `("600000.SH","600000.SZ")` 的 `in_` 形式）。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && UV_CACHE_DIR=.uv-cache uv run --extra dev pytest tests/test_triage_service.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/triage_service.py backend/tests/test_triage_service.py
git commit -m "feat(triage): 5 条固定查询装载上下文 + 合并/排序/折叠"
```

---

### Task 3: API（`GET /api/v1/watchlists/triage`）

**Files:**
- Create: `backend/app/schemas/triage.py`, `backend/app/api/v1/watchlist_triage.py`
- Modify: `backend/app/api/v1/__init__.py`（注册 router）
- Test: `backend/tests/test_watchlist_triage_api.py`

**Interfaces:**
- Consumes: `triage_service.get_triage`
- Produces: `GET /api/v1/watchlists/triage?group=` → `TriageOut`

- [ ] **Step 1: Write the failing test**

```python
"""API 契约：登录即可读、自选 >50 返回 422、响应含 items/quiet_count/unavailable/disclaimer."""
import pytest
from httpx import AsyncClient


@pytest.mark.usefixtures("_auth_headers")
async def test_triage_rejects_too_many_symbols(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/watchlists/triage", params={"symbols": ",".join(["600000"] * 51)})
    assert resp.status_code == 422


@pytest.mark.usefixtures("_auth_headers")
async def test_triage_requires_login(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/watchlists/triage")
    assert resp.status_code in (401, 403)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && UV_CACHE_DIR=.uv-cache uv run --extra dev pytest tests/test_watchlist_triage_api.py -v`
Expected: FAIL（404）

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/schemas/triage.py
"""自选体检的响应契约（只读，无写模型）."""
from datetime import date

from pydantic import BaseModel


class FactOut(BaseModel):
    label: str
    value: float | str
    unit: str = ""


class TriageItemOut(BaseModel):
    symbol: str
    name: str
    detector: str
    severity: str
    headline: str
    facts: list[FactOut]
    as_of: date
    source: str
    verify: str


class TriageOut(BaseModel):
    as_of: date
    quiet_count: int
    folded: int
    items: list[TriageItemOut]
    unavailable: list[str]
    disclaimer: str
```

```python
# backend/app/api/v1/watchlist_triage.py
"""自选体检：只读、登录即可用（数据 = 公开行情 + 用户自己的自选）."""
from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUserDep, DbDep
from app.schemas.triage import TriageItemOut, TriageOut
from app.services import triage_service

router = APIRouter()

DISCLAIMER = "本产品只展示已发生的数据事实，不构成投资建议；历史数据不代表未来表现。"


@router.get("/triage", response_model=TriageOut)
async def get_triage(
    db: DbDep,
    user: CurrentUserDep,
    symbols: str | None = Query(None, description="逗号分隔的自选代码；缺省取用户默认自选"),
) -> TriageOut:
    assert user.user_id is not None
    codes = [c.strip() for c in (symbols or "").split(",") if c.strip()]
    if len(codes) > triage_service.MAX_SYMBOLS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"自选最多 {triage_service.MAX_SYMBOLS} 只，请分组后再试",
        )
    if not codes:
        codes = await triage_service.user_symbols(db, user.user_id)
    result = await triage_service.get_triage(db, codes)
    return TriageOut(
        as_of=result.as_of,
        quiet_count=result.quiet_count,
        folded=result.folded,
        unavailable=result.unavailable,
        disclaimer=DISCLAIMER,
        items=[
            TriageItemOut(
                symbol=i.symbol, name=i.name, detector=i.detector, severity=i.severity,
                headline=i.headline, facts=[f.__dict__ for f in i.facts],
                as_of=i.as_of, source=i.source, verify=i.verify,
            )
            for i in result.items
        ],
    )
```

> `__init__.py` 里加：`from app.api.v1 import watchlist_triage` 与
> `router.include_router(watchlist_triage.router, prefix="/watchlists", tags=["watchlists"])`。
> `triage_service.user_symbols(db, user_id)` 读 `user_watchlist_items`（按 `sort_order`），5 行实现。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && UV_CACHE_DIR=.uv-cache uv run --extra dev pytest tests/test_watchlist_triage_api.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/triage.py backend/app/api/v1/watchlist_triage.py backend/app/api/v1/__init__.py backend/tests/test_watchlist_triage_api.py
git commit -m "feat(triage): GET /watchlists/triage + 响应契约"
```

---

### Task 4: 后端测试补齐（反 N+1 / 探测器全覆盖 / 口径禁令）

**Files:**
- Modify: `backend/tests/test_triage_detectors.py`, `backend/tests/test_triage_service.py`

- [ ] **Step 1: 反 N+1 断言**

```python
async def test_query_count_does_not_grow_with_symbols(db_session, monkeypatch) -> None:
    """查询次数 ≤6 且不随自选数增长（N+1 回归）。"""
    from sqlalchemy import event
    counts = []
    for n in (1, 5, 20):
        calls = 0
        def _count(*_a, **_k):  # noqa: ANN001
            nonlocal calls
            calls += 1
        event.listen(db_session.bind.sync_engine, "before_cursor_execute", _count)
        try:
            await svc.get_triage(db_session, [f"{600000 + i:06d}" for i in range(n)])
        finally:
            event.remove(db_session.bind.sync_engine, "before_cursor_execute", _count)
        counts.append(calls)
    assert max(counts) <= 6
    assert len(set(counts)) == 1
```

- [ ] **Step 2: 每个探测器一条正例 + 一条反例**（8 × 2），用 `make_context_for_test` 构造；
  额外一条：`new_high_low` 的次新排除（已在 Task 1 写了，这里补 60 日恰好命中的正例）。

- [ ] **Step 3: 口径禁令的静态断言**

```python
def test_no_forbidden_fields_in_detector_module() -> None:
    """禁 turnover_rate_f / market_limit_up_stock_daily / 个股融资余额（实机标定得出）."""
    src = Path("app/services/triage_detectors.py").read_text(encoding="utf-8")
    assert "turnover_rate_f" not in src
    assert "market_limit_up_stock_daily" not in src
```

- [ ] **Step 4: Run & commit**

Run: `cd backend && UV_CACHE_DIR=.uv-cache uv run --extra dev pytest tests/test_triage_detectors.py tests/test_triage_service.py -q`
Expected: PASS（≥20 passed）

---

### Task 5: 前端 `/me` 今日体检 Tab

**Files:**
- Create: `frontend/src/shared/api/triage.ts`, `frontend/src/features/triage/components/TriagePanel.tsx`, `frontend/src/features/triage/components/TriagePanel.css`
- Modify: `frontend/src/pages/me/index.tsx`（新增 `triage` tab 并设为默认）
- Test: `frontend/e2e/watchlistTriage.spec.ts`

**Interfaces:**
- Consumes: `GET /api/v1/watchlists/triage`
- Produces: `TriagePanel`（React Query，30s staleTime）

- [ ] **Step 1: api client**

```ts
// frontend/src/shared/api/triage.ts
export type TriageSeverity = "risk" | "event" | "attention" | "background";

export interface TriageFact { label: string; value: number | string; unit: string }
export interface TriageItem {
  symbol: string; name: string; detector: string; severity: TriageSeverity;
  headline: string; facts: TriageFact[]; as_of: string; source: string; verify: string;
}
export interface TriageResponse {
  as_of: string; quiet_count: number; folded: number;
  items: TriageItem[]; unavailable: string[]; disclaimer: string;
}

export function fetchTriage(): Promise<TriageResponse> {
  return apiGet<TriageResponse>("/api/v1/watchlists/triage");
}
```
（`apiGet` 从 `@/shared/api/client` 导入，与其它模块一致。）

- [ ] **Step 2: 组件（三态：有条目 / 全静默 / 无自选；每条 3 件事：发生了什么 / 数据截至 / 复核）**

```tsx
// frontend/src/features/triage/components/TriagePanel.tsx
export function TriagePanel() {
  const q = useQuery({ queryKey: ["triage"], queryFn: fetchTriage, staleTime: 30_000 });
  if (q.isLoading) return <Skeleton active />;
  if (q.isError) return <Alert type="warning" message="体检数据暂不可用，请稍后重试" />;
  const data = q.data!;
  if (data.items.length === 0) {
    return <Empty description={`今天你的自选都平静（${data.quiet_count} 只）`} />;
  }
  return (
    <div className="triage">…按 severity 分组的卡片，每张卡片：headline + facts + 数据截至 + 复核按钮…</div>
  );
}
```

- [ ] **Step 3: 挂进 `/me`**：`TAB_KEYS` 增加 `"triage"` 且默认选中；`Tabs` 增加一项 `今日体检`；
  **不改** `/market`、不改导航。
- [ ] **Step 4: e2e（mock 接口 → 确定性断言）**

```ts
// frontend/e2e/watchlistTriage.spec.ts
test("今日体检：有条目时展示事实与免责", async ({ page }) => {
  await page.route("**/api/v1/watchlists/triage*", (r) => r.fulfill({ json: FIXTURE }));
  await page.goto("/me?tab=triage");
  await expect(page.getByText("跌破 20 日线")).toBeVisible();
  await expect(page.getByText(/不构成投资建议/)).toBeVisible();
  await expect(page.getByText(/买入|卖出|推荐|看好/)).toHaveCount(0);
});
test("今日体检：全静默日显示明确文案", async ({ page }) => {
  await page.route("**/api/v1/watchlists/triage*", (r) => r.fulfill({ json: { ...FIXTURE, items: [], quiet_count: 12 } }));
  await page.goto("/me?tab=triage");
  await expect(page.getByText(/都平静/)).toBeVisible();
});
```

- [ ] **Step 5: 验证 + 提交**

Run: `cd frontend && npx tsc --noEmit && E2E_BASE_URL=http://localhost npx playwright test watchlistTriage.spec.ts`

---

### Task 6: 合规脚本

**Files:**
- Create: `backend/scripts/triage_compliance.py`（词表常量 + CLI）, `backend/scripts/check_triage_compliance.py`（扫描 spec/前端文案/API 常量）

- [ ] **Step 1: 词表（≥20）+ 扫描**
- [ ] **Step 2: 接入 `self_review`（若它按"docs 引用脚本存在"扫描，脚本必须真实存在）**
- [ ] **Step 3: 运行**：`cd backend && uv run python scripts/check_triage_compliance.py` → 0 命中

---

### Task 7: 文档同步

- [ ] `docs/features.md`：`/me` 行补「今日体检」；若有新端点，补一行（门禁 [3/13] 会核对代码路径）
- [ ] `docs/ARCHITECTURE.md §6`：`/me` 行的能力描述补"今日体检"
- [ ] `docs/specs/S8-watchlist-triage.md`：把 `/today` 改为 **`/me?tab=triage`（默认 Tab）**，并写明"独立 `/today` 路由推迟"（IA §4 + check-routes 需登记，v2 再做）
- [ ] `docs/Changelog.md`：一条
- [ ] `plans/2026-10-17-watchlist-triage-impl.md`（本文件）状态改 `completed`

---

### Task 8: 集成与证明

- [ ] `docker compose build api && docker compose up -d api worker scheduler`；`curl /health`
- [ ] 实机验证：`curl -s -b cookie .../watchlists/triage | jq` 含真实条目（若自选为空则先加 2 只）
- [ ] `bash scripts/self_review.sh --full`（ruff/mypy/pytest 全绿）
- [ ] `cd frontend && E2E_BASE_URL=http://localhost npx playwright test`（**全量**）
- [ ] Playwright 截图 `/me?tab=triage`（有数据版）作为证据；`present` 给用户
