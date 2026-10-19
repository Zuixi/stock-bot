"""§S1.7 **陆股通持仓（季度）**：`hk_hold` 逐交易所取数 → `industry_metrics`。

**为什么不直接用港交所页面**（ADR 0019 的两条判据都不过）：

- **判据 ②（可按时间窗增量）**：港交所 CCASS 的 `mutualmarket.aspx` 是 ASP.NET 表单，
  只能**按单个日期**查；且实测**更早的季度末已返回 0 行**（2025-12-31 / 2025-06-30 全空，
  见 `spike_official_sources.py --probe hkex-holdings`）⇒ 它是"当期快照"，不能当历史序列。
- **判据 ③（披露时点确定）**：页面只给"季末后第 5 个北向交易日发布"的机制文本，
  实测该日之后页面仍在返回**上一季度**的个股数（2026-10-16 查 2026-09-30 只回 2039 行，
  与 `hk_hold` 的 2026-06-30 SH 行数相同）⇒ **对齐未证实**，已登记为未验证余项。

⇒ 本指标的历史序列走主源 `hk_hold`（L2），**逐交易所取**以绕开 4200 行上限
（实测：不带 `exchange` 时 SH 2089 + SZ 1097 + HK 1014 = 4200 会被截断；分开取
SH 2089 + SZ 2320 = 4409 才完整）。港交所页面保留为**校验/当期快照**通道（未接落库）。

**PIT**：披露规则是**季末后第 5 个北向交易日**（沪深股通交易日 = A 股交易日），
故 `published_at` = 该日 21:00（上海时区），由库内 `daily_quotes` 的交易日集合算出 ——
**规则可复算**，不是拍脑袋的固定偏移（也不许用 `period` 兜底，ADR 0011）。

**口径禁令（Ruling 19）**：香港中央結算（HKSCC）是**名义持有人汇总账户**，
一律写「**陆股通持仓**」，**不得**称"外资"。
"""

from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timedelta
from datetime import time as dtime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.industry_research import IndustryMetric
from app.models.stock import Stock
from app.services import market_activity_service
from app.services.industry_registry import (
    NORTHBOUND_HOLDING_CALIBER_VERSION,
    NORTHBOUND_HOLDING_SOURCE,
)

logger = logging.getLogger(__name__)

_CN_TZ = ZoneInfo("Asia/Shanghai")

#: `hk_hold` 的单次行数上限（实测）。撞上即说明被截断，**必须**分批取。
HK_HOLD_ROW_CAP = 4200

#: 只看这两个交易所（`HK` 行是港股通，不是陆股通）。
MAINLAND_EXCHANGES = ("SH", "SZ")

#: 披露规则：季末后**第 5 个北向交易日**收盘后发布（沪深港通交易日 = A 股交易日）。
PUBLISH_TRADING_DAYS_AFTER_QUARTER_END = 5

#: 发布时点（盘后）。
PUBLISH_AT = dtime(21, 0)

#: `vol` 原生单位 = 股 ⇒ 万股。
SHARES_TO_WAN = 1e-4

_EXCHANGE_OF = {"SH": "Shanghai_Stocks", "SZ": "Shenzen_Stocks"}


def quarter_end(day: date) -> date:
    """`day` **之前（含当天）最近**的一个季末（3/31 · 6/30 · 9/30 · 12/31）。

    语义是"已结束的季度"：2026-10-17 → 2026-09-30（不是 12-31），因为本指标只落**已披露**的季末。
    """
    for month in (12, 9, 6, 3):
        end_of_month = date(day.year, month, calendar.monthrange(day.year, month)[1])
        if end_of_month <= day:
            return end_of_month
    return date(day.year - 1, 12, 31)


def recent_quarter_ends(today: date, count: int) -> list[date]:
    """`today` 之前（含当天所在季度若已过季末）最近的 `count` 个季末，**升序**。"""
    ends: list[date] = []
    cursor = quarter_end(today)
    for _ in range(count):
        ends.append(cursor)
        cursor = quarter_end(cursor - timedelta(days=1))
    return sorted(ends)


def publish_day(quarter_last_day: date, trading_days: list[date]) -> date:
    """季末后第 5 个交易日（`trading_days` 必须**升序**且来自库内真实交易日）。"""
    after = [d for d in trading_days if d > quarter_last_day]
    if len(after) < PUBLISH_TRADING_DAYS_AFTER_QUARTER_END:
        raise RuntimeError(
            f"{quarter_last_day} 之后只有 {len(after)} 个交易日（< "
            f"{PUBLISH_TRADING_DAYS_AFTER_QUARTER_END}）—— 行情底座没到发布日，"
            "不得猜一个 `published_at`"
        )
    return after[PUBLISH_TRADING_DAYS_AFTER_QUARTER_END - 1]


async def _trading_days(db: AsyncSession, day_from: date, day_to: date) -> list[date]:
    rows = (
        await db.execute(
            text(
                "SELECT DISTINCT trade_date FROM daily_quotes "
                "WHERE trade_date >= :a AND trade_date <= :b ORDER BY trade_date"
            ),
            {"a": day_from, "b": day_to},
        )
    ).scalars()
    return [d for d in rows]


async def fetch_quarter_holdings(client: Any, quarter_last_day: date) -> dict[str, dict[str, Any]]:
    """`{"000001.SZ": {"vol": 股, "ratio": %, "name": ..., "exchange": ...}}`（逐交易所取）。"""
    stamp = quarter_last_day.strftime("%Y%m%d")
    out: dict[str, dict[str, Any]] = {}
    for exchange in MAINLAND_EXCHANGES:
        df = await client.fetch_hk_hold(trade_date=stamp, exchange=exchange)
        if len(df) >= HK_HOLD_ROW_CAP:
            raise RuntimeError(
                f"hk_hold({stamp}, exchange={exchange}) 回了 {len(df)} 行 = 上限 "
                f"{HK_HOLD_ROW_CAP} ⇒ 被截断，不得当完整集合落库"
            )
        for row in df.itertuples():
            ts_code = str(getattr(row, "ts_code", ""))
            vol = getattr(row, "vol", None)
            if not ts_code or vol is None or pd.isna(vol):
                continue
            out[ts_code] = {
                "vol": float(vol),
                "ratio": None if getattr(row, "ratio", None) is None else float(row.ratio),
                "name": getattr(row, "name", None),
                "exchange": exchange,
            }
    return out


async def _resolve_stock_ids(
    db: AsyncSession, holdings: dict[str, dict[str, Any]]
) -> dict[str, int]:
    """`ts_code` → `stocks.id`（`stocks` 无 `ts_code` 列，用 6 位代码 + 交易所解析）。"""
    symbols = sorted({code.split(".")[0] for code in holdings})
    if not symbols:
        return {}
    stmt = select(Stock.symbol, Stock.exchange, Stock.id).where(Stock.symbol.in_(symbols))
    mapping: dict[tuple[str, str], int] = {}
    for symbol, exchange, stock_id in (await db.execute(stmt)).all():
        mapping[(str(exchange), str(symbol))] = int(stock_id)
    out: dict[str, int] = {}
    for ts_code, meta in holdings.items():
        symbol, _, market = ts_code.partition(".")
        expected = _EXCHANGE_OF.get(market)
        if expected is None:
            continue
        stock_id = mapping.get((expected, symbol))
        if stock_id is not None:
            out[ts_code] = stock_id
    return out


def holding_row(
    ts_code: str, stock_id: int, meta: dict[str, Any], quarter_last_day: date, published: datetime
) -> dict[str, Any]:
    """一行季度持股（`value` = **万股**，原生 `vol` 是股）。"""
    return {
        "industry_key": "market",
        "stock_id": stock_id,
        "metric_key": "northbound_holding_quarterly",
        "source": NORTHBOUND_HOLDING_SOURCE,
        "source_tier": "official",
        "freq": "quarterly",
        "period": quarter_last_day,
        "value": round(meta["vol"] * SHARES_TO_WAN, 4),
        "unit": "万股",
        "extra": {
            "ts_code": ts_code,
            "exchange": meta["exchange"],
            "ratio_pct": meta["ratio"],
            "name": meta["name"],
            # 口径：CCASS participants **汇总**（名义持有人），不得读作"外资"（Ruling 19）
            "holder_nature": "ccass_participants_aggregate",
        },
        "published_at": published,
        "caliber_version": NORTHBOUND_HOLDING_CALIBER_VERSION,
    }


async def ingest_quarter(
    db: AsyncSession, quarter_last_day: date, *, client: Any | None = None
) -> dict[str, int]:
    """取某个季末的持股并**幂等**落库；`published_at` = 季末后第 5 个交易日 21:00。

    返回 `{"holdings": n, "rows": n, "unresolved": n}`。调用方负责 commit。
    """
    if quarter_last_day != quarter_end(quarter_last_day):
        raise ValueError(f"{quarter_last_day} 不是季末日 —— 本指标按季度披露，不得用中间日期")

    from app.core.providers.tushare_client import get_tushare_client  # noqa: PLC0415

    ts_client = client or get_tushare_client()
    holdings = await fetch_quarter_holdings(ts_client, quarter_last_day)

    days = await _trading_days(db, quarter_last_day, quarter_last_day + timedelta(days=40))
    published = datetime.combine(publish_day(quarter_last_day, days), PUBLISH_AT, tzinfo=_CN_TZ)

    mapping = await _resolve_stock_ids(db, holdings)
    rows: list[dict[str, Any]] = []
    non_stock = 0
    unresolved = 0
    for ts_code, meta in holdings.items():
        symbol = ts_code.partition(".")[0]
        stock_id = mapping.get(ts_code)
        if stock_id is not None:
            rows.append(holding_row(ts_code, stock_id, meta, quarter_last_day, published))
        elif not (len(symbol) == 6 and symbol.isdigit()):
            # `hk_hold` 会把 **ETF 等非股票标的**也用伪代码返回（实测 `ETF300`/`ETF301`…）——
            # 它们不是股票，本指标不落（也不该算成"映射失败"）。
            non_stock += 1
        else:
            unresolved += 1
            logger.info("northbound: 股票 %s 在 stocks 里解析不到 —— 跳过", ts_code)

    written = await market_activity_service.upsert_metric_rows(db, rows) if rows else 0
    return {
        "holdings": len(holdings),
        "rows": written,
        "non_stock": non_stock,
        "unresolved": unresolved,
    }


async def quarter_is_ingested(
    db: AsyncSession, quarter_last_day: date, *, min_rows: int = 3000
) -> bool:
    """该季是否已灌够（收敛器据此避免每轮都打上游；也用于手工判断是否需要回补）。

    阈值 3000 的依据：实测三个季末的落库行数 3750 / 3932 / 3952（全量 A 股约 4400 只，
    去掉 ETF 伪代码与解析不到的代码）；显著低于 3000 视为"没灌完"。
    """
    count = (
        await db.execute(
            select(func.count(IndustryMetric.id)).where(
                IndustryMetric.metric_key == "northbound_holding_quarterly",
                IndustryMetric.period == quarter_last_day,
            )
        )
    ).scalar_one()
    return int(count) >= min_rows


async def coverage(db: AsyncSession) -> dict[str, Any]:
    """季度覆盖对账：`{季末: 行数}` + 最新季末。"""
    rows = (
        await db.execute(
            select(IndustryMetric.period, IndustryMetric.id).where(
                IndustryMetric.metric_key == "northbound_holding_quarterly"
            )
        )
    ).all()
    per_quarter: dict[date, int] = {}
    for period, _ in rows:
        per_quarter[period] = per_quarter.get(period, 0) + 1
    return {"per_quarter": dict(sorted(per_quarter.items())), "rows": len(rows)}
