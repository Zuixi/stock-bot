"""§S1.7 **董监高及关联人持股变动**（官方一手，沪深交易所）→ `industry_metrics`。

**本 key 与 `industrial_capital_net_change` 是两个东西**（ADR 0019 硬纪律，
见 `docs/decisions/0019-official-first-source-layer.md`）：

- **主体**：本模块 = 董监高及关联人（内部人）；
  `industrial_capital_net_change` = 5%+ 股东/实控人/机构（产业资本）。
- **频率**：本模块 = 日频（按「证券 × 披露日」聚合）；另一个 = 事件驱动（一行 = 一次公告）。
- **单位**：本模块 = **万股**（沪原生股、深原生万股，已归一）；另一个 = 亿元（待抽取）。
- **产地**：本模块 = 沪深交易所官网**结构化表**；另一个 = 巨潮公告类目 `category_gqbd_szsh`。
- **PIT**：本模块沪有披露日、**深没有**；另一个有 `announcementTime`。

**产地与实测**（2026-10-17，`scripts/spike_official_sources.py --probe holder-change`）：

- **沪**：``query.sse.com.cn/commonQuery.do``（``sqlId=COMMON_SSE_XXPL_CXJL_SSGSGFBDQK_S``，
  ``BEGIN_DATE``/``END_DATE`` 窗口 + ``pageHelp`` 分页）。实测 2026-10-12~16 = **69 行 / 1 页**、
  单日 5 行、全历史 71,512 行 / 716 页。字段含 **``FORM_DATE``（填报=披露日）**、
  ``CHANGE_NUM``（**股**）、``CURRENT_AVG_PRICE``、``CHANGE_REASON``、``DUTY``、``NAME``。
- **深**：``www.szse.cn/api/report/ShowReport/data``（``CATALOGID=1801_cxda`` +
  ``txtStart``/``txtEnd``）。实测同窗口 **73 行 / 4 页**、全历史 124,057 行 / 6203 页。
  字段：``zqdm`` / ``jyrq``（变动日）/ ``bdgs``（**万股**，负数 = 减持）/ ``bdjj`` / ``ggxm`` /
  ``zw`` / ``bdyy`` / ``gxlb``。**没有披露日字段**。

**两条硬约束**：

1. **PIT**：沪的 `published_at` = `FORM_DATE` 当日盘后（21:00，与 S1.7 的 ETF 份额同档）；
   深无披露日 ⇒ `published_at=None` **且** `extra.disclosure_date_unavailable=True`
   （共享 upsert 守卫只放过"显式声明"的 NULL，见 `market_activity_service._upsert_batched`）。
   深市行**不得**进回测/信号链路。
2. **分页只做增量**：两所都按**日期窗口**取（不翻全历史）——AKShare 的同名封装是全历史翻页，
   实测第 176 页即被断连（见 ADR 0019 与 data-source 附.11）。

**⚠️ 实测（2026-10-17）：沪市的 `BEGIN_DATE`/`END_DATE` 过滤的是 ``CHANGE_DATE``（变动日），
不是 ``FORM_DATE``（披露日）** —— 取证：查 2024-03-01~10 窗口能取到一条
「变动日 2024-03-05、填报日 2026-09-20」的补报行，且窗口外的变动日一条都没混进来。
后果：日更按窗口取数**只能**覆盖"变动日落在窗口内"的事件；**变动日远早于窗口的长滞后补报
不会自动进来**（实测间隔两年半）。⇒ 日更窗口取 `INSIDER_LOOKBACK_DAYS`（默认 30 天，
覆盖节假日与常见迟报），长滞后补报靠回补脚本重跑更长区间；这条限制**不可用放宽窗口到"全历史"解决**
（那正是被限流的路径）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from datetime import time as dtime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.stock import Stock
from app.services import market_activity_service
from app.services.industry_registry import (
    INSIDER_CALIBER_VERSION,
    INSIDER_METRIC_KEYS,
    INSIDER_SOURCE,
)

logger = logging.getLogger(__name__)

_CN_TZ = ZoneInfo("Asia/Shanghai")

#: 本模块**实际落库**的 key（与 registry 的登记面逐字相等，导入期断言；收敛器按此登记成一条腿）。
#: 复用 registry 的常量而不是在这里重抄一份 —— 两处抄写必然漂移。
METRIC_KEYS: tuple[str, ...] = INSIDER_METRIC_KEYS
if set(METRIC_KEYS) != set(INSIDER_METRIC_KEYS):  # pragma: no cover - 纯防御
    raise RuntimeError(f"insider_change_service 的 key 集与 registry 不一致：{METRIC_KEYS}")

#: 沪市披露资料的可得时点（盘后填报；与 S1.7 的 ETF 份额同档，偏保守）。
PUBLISH_AT = dtime(21, 0)

SSE_URL = "https://query.sse.com.cn/commonQuery.do"
SSE_SQL_ID = "COMMON_SSE_XXPL_CXJL_SSGSGFBDQK_S"
SSE_REFERER = "https://www.sse.com.cn/"
SZSE_URL = "https://www.szse.cn/api/report/ShowReport/data"
SZSE_CATALOG_ID = "1801_cxda"
SZSE_REFERER = "https://www.szse.cn/disclosure/supervision/change/index.html"

#: 分页参数（沪按 `pageHelp.pageCount`；深交所 `ShowReport` 固定 20 行/页）。
SSE_PAGE_SIZE = 100
SSE_MAX_PAGES = 80  # 实测 2024 全年 74 页；留余量并靠"翻满即响亮失败"兜底
SZSE_PAGE_SIZE = 20
SZSE_MAX_PAGES = 500  # 全历史实测 6203 页 ⇒ 只允许按窗口增量取

#: 交易所原生股数 → **万股**（沪 `CHANGE_NUM` 是股；深 `bdgs` 已经是万股）。
SSE_SHARES_TO_WAN = 1e-4

#: 日更的默认回看天数（沪市窗口按**变动日**过滤，见模块 docstring 的实测限制）。
INSIDER_LOOKBACK_DAYS = 30
SZSE_SHARES_TO_WAN = 1.0

#: 交易所 → `stocks.exchange` 的取值（历史拼写：`Shenzen_Stocks`）。
_EXCHANGE_OF_MARKET = {"SH": "Shanghai_Stocks", "SZ": "Shenzen_Stocks"}


@dataclass(frozen=True)
class InsiderEvent:
    """一条董监高持股变动（已归一：`delta_wan` 为**万股**，买正卖负）。"""

    symbol: str
    market: str
    period: date
    delta_wan: float
    price: float | None
    published_at: datetime | None
    member: str | None
    duty: str | None
    reason: str | None
    change_date: date | None


@dataclass
class InsiderDay:
    """聚合键 = `(symbol, market, period)`：一个披露日里该股的全部变动合成一行。"""

    symbol: str
    market: str
    period: date
    published_at: datetime | None
    delta_wan: float = 0.0
    buy_wan: float = 0.0
    sell_wan: float = 0.0
    amount_wan: float = 0.0
    n_events: int = 0
    events_without_price: int = 0
    members: list[dict[str, Any]] = field(default_factory=list)
    change_dates: list[str] = field(default_factory=list)


def _as_day(raw: Any) -> date | None:
    if not raw:
        return None
    text = str(raw).strip()
    if len(text) >= 10 and text[4] in "-/":
        try:
            return date.fromisoformat(text[:10].replace("/", "-"))
        except ValueError:
            return None
    if len(text) == 8 and text.isdigit():
        try:
            return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
        except ValueError:
            return None
    return None


def _as_float(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return float(str(raw).replace(",", "").strip())
    except ValueError:
        return None


# ── 取数（沪 / 深）────────────────────────────────────────────────────────


async def fetch_sse_events(
    client: httpx.AsyncClient, day_lo: date, day_hi: date
) -> list[InsiderEvent]:
    """沪市董监高持股变动（**按日期窗口增量**，`pageHelp` 分页）。"""
    base = {
        "isPagination": "true",
        "pageHelp.pageSize": str(SSE_PAGE_SIZE),
        "pageHelp.cacheSize": "1",
        "sqlId": SSE_SQL_ID,
        "COMPANY_CODE": "",
        "NAME": "",
        "BEGIN_DATE": day_lo.isoformat(),
        "END_DATE": day_hi.isoformat(),
        "BOARDTYPE": "",
    }
    out: list[InsiderEvent] = []
    page_count = 1
    for page in range(1, SSE_MAX_PAGES + 1):
        params = {
            **base,
            "pageHelp.pageNo": str(page),
            "pageHelp.beginPage": str(page),
            "pageHelp.endPage": str(page),
        }
        resp = await client.get(SSE_URL, params=params, headers={"Referer": SSE_REFERER})
        resp.raise_for_status()
        payload = resp.json()
        if page == 1:
            page_count = int(payload.get("pageHelp", {}).get("pageCount") or 1)
        for row in payload.get("result") or []:
            delta = _as_float(row.get("CHANGE_NUM"))
            if delta is None:
                continue
            form_date = _as_day(row.get("FORM_DATE"))
            change_date = _as_day(row.get("CHANGE_DATE"))
            period = form_date or change_date
            if period is None:
                continue
            out.append(
                InsiderEvent(
                    symbol=str(row.get("COMPANY_CODE") or "").zfill(6),
                    market="SH",
                    period=period,
                    delta_wan=delta * SSE_SHARES_TO_WAN,
                    price=_as_float(row.get("CURRENT_AVG_PRICE")),
                    published_at=(
                        datetime.combine(form_date, PUBLISH_AT, tzinfo=_CN_TZ)
                        if form_date is not None
                        else None
                    ),
                    member=row.get("NAME"),
                    duty=row.get("DUTY"),
                    reason=row.get("CHANGE_REASON"),
                    change_date=change_date,
                )
            )
        if page >= page_count:
            return out
    raise RuntimeError(
        f"沪市董监高持股变动在 {day_lo}~{day_hi} 的页数超过上限 {SSE_MAX_PAGES} —— "
        "拒绝静默截断（分批取更短的窗口）"
    )


async def fetch_szse_events(
    client: httpx.AsyncClient, day_lo: date, day_hi: date
) -> list[InsiderEvent]:
    """深市董监高持股变动（**按日期窗口增量**，`PAGENO` 分页；**无披露日字段**）。"""
    out: list[InsiderEvent] = []
    for page in range(1, SZSE_MAX_PAGES + 1):
        params = {
            "SHOWTYPE": "JSON",
            "CATALOGID": SZSE_CATALOG_ID,
            "TABKEY": "tab1",
            "PAGENO": str(page),
            "random": "0.5",
            "txtStart": day_lo.isoformat(),
            "txtEnd": day_hi.isoformat(),
        }
        resp = await client.get(SZSE_URL, params=params, headers={"Referer": SZSE_REFERER})
        resp.raise_for_status()
        payload = resp.json()
        rows = (payload[0] if payload else {}).get("data") or []
        for row in rows:
            delta = _as_float(row.get("bdgs"))
            period = _as_day(row.get("jyrq"))
            if delta is None or period is None:
                continue
            out.append(
                InsiderEvent(
                    symbol=str(row.get("zqdm") or "").zfill(6),
                    market="SZ",
                    period=period,
                    delta_wan=delta * SZSE_SHARES_TO_WAN,
                    price=_as_float(row.get("bdjj")),
                    # 深交所该表**没有**披露日字段 ⇒ PIT 只能显式标注不可得（不得猜）
                    published_at=None,
                    member=row.get("ggxm") or row.get("gdxm"),
                    duty=row.get("zw"),
                    reason=row.get("bdyy"),
                    change_date=period,
                )
            )
        if len(rows) < SZSE_PAGE_SIZE:
            return out
    raise RuntimeError(
        f"深市董监高持股变动在 {day_lo}~{day_hi} 的页数超过上限 {SZSE_MAX_PAGES} —— "
        "拒绝静默截断（分批取更短的窗口）"
    )


# ── 聚合与行构造（纯函数，便于单测）──────────────────────────────────────


def aggregate_events(events: list[InsiderEvent]) -> list[InsiderDay]:
    """按 `(symbol, market, period)` 聚合：**买正卖负**，金额只在有价时累加。"""
    buckets: dict[tuple[str, str, date], InsiderDay] = {}
    for event in events:
        key = (event.symbol, event.market, event.period)
        day = buckets.get(key)
        if day is None:
            day = InsiderDay(
                symbol=event.symbol,
                market=event.market,
                period=event.period,
                published_at=event.published_at,
            )
            buckets[key] = day
        day.delta_wan += event.delta_wan
        if event.delta_wan >= 0:
            day.buy_wan += event.delta_wan
        else:
            day.sell_wan += event.delta_wan
        if event.price is None:
            day.events_without_price += 1
        else:
            day.amount_wan += event.delta_wan * event.price
        day.n_events += 1
        day.members.append(
            {
                "member": event.member,
                "duty": event.duty,
                "reason": event.reason,
                "delta_wan": round(event.delta_wan, 4),
                "price": event.price,
            }
        )
        if event.change_date is not None:
            day.change_dates.append(event.change_date.isoformat())
    return [buckets[key] for key in sorted(buckets)]


def insider_row(day: InsiderDay, stock_id: int) -> dict[str, Any]:
    """`InsiderDay` → `industry_metrics` 行（与 S1.2 读面同一张表、同一读路）。"""
    extra: dict[str, Any] = {
        "market": day.market,
        "n_events": day.n_events,
        "buy_wan": round(day.buy_wan, 4),
        "sell_wan": round(day.sell_wan, 4),
        "amount_wan": round(day.amount_wan, 4),
        "events_without_price": day.events_without_price,
        "members": day.members,
        "change_dates": sorted(set(day.change_dates)),
    }
    if day.published_at is None:
        # 显式声明"披露日不可得"——共享 upsert 守卫只放过带这个标记的 NULL（绝不静默 NULL）
        extra["disclosure_date_unavailable"] = True
    return {
        "industry_key": "market",
        "stock_id": stock_id,
        "metric_key": "insider_net_change_daily",
        "source": INSIDER_SOURCE,
        "source_tier": "official",
        "freq": "daily",
        "period": day.period,
        "value": round(day.delta_wan, 4),
        "unit": "万股",
        "extra": extra,
        "published_at": day.published_at,
        "caliber_version": INSIDER_CALIBER_VERSION,
    }


async def _resolve_stock_ids(
    db: AsyncSession, events: list[InsiderEvent]
) -> dict[tuple[str, str], int]:
    """`(market, 6 位代码)` → `stocks.id`；解析不到的不猜（返回缺省）。"""
    symbols = sorted({e.symbol for e in events})
    if not symbols:
        return {}
    stmt = select(Stock.symbol, Stock.exchange, Stock.id).where(Stock.symbol.in_(symbols))
    rows = (await db.execute(stmt)).all()
    mapping: dict[tuple[str, str], int] = {}
    for symbol, exchange, stock_id in rows:
        for market, expected in _EXCHANGE_OF_MARKET.items():
            if exchange == expected:
                mapping[(market, str(symbol))] = int(stock_id)
    return mapping


async def ingest_insider_range(
    db: AsyncSession, day_lo: date, day_hi: date, *, client: httpx.AsyncClient | None = None
) -> dict[str, int]:
    """取 `[day_lo, day_hi]` 的两市变动并**幂等**写入 `industry_metrics`（`scope='market'`）。

    返回 `{"events": n, "rows": n, "unresolved_symbols": n, "rows_without_pit": n}`。
    调用方负责 `await db.commit()`。
    """
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=40.0, follow_redirects=True)
    try:
        events = [
            *await fetch_sse_events(http_client, day_lo, day_hi),
            *await fetch_szse_events(http_client, day_lo, day_hi),
        ]
    finally:
        if owns_client:
            await http_client.aclose()

    mapping = await _resolve_stock_ids(db, events)
    rows: list[dict[str, Any]] = []
    unresolved = 0
    for day in aggregate_events(events):
        stock_id = mapping.get((day.market, day.symbol))
        if stock_id is None:
            unresolved += 1
            logger.info("insider: 证券 %s(%s) 在 stocks 里解析不到 —— 跳过", day.symbol, day.market)
            continue
        rows.append(insider_row(day, stock_id))

    written = await market_activity_service.upsert_metric_rows(db, rows) if rows else 0
    return {
        "events": len(events),
        "rows": written,
        "unresolved_symbols": unresolved,
        "rows_without_pit": sum(1 for r in rows if r["published_at"] is None),
    }
