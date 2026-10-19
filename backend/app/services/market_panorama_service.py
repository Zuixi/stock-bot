"""A股全景读面：广度 × 结构 × 资金，一次请求 <100ms。

数据源（全部日级、已落库）：
- `market_breadth_daily` / `market_breadth_bucket_daily`（2026-10-19 新增，B 类可回算）；
- `market_industry_daily`（申万一级 31 行/日，C 类归属标注）；
- `sector_moneyflow_snapshots`（A 类，只认 `eod_full`；历史不可回补）。

设计约束：
1. **不 N+1**：60 天广度 1 条、60 天行业 1 条（≈1,860 行）、11 桶 1 条、资金流 2 条；
2. **不现算历史**：趋势直接读日表，避免 5,500×60 的行情扫描；
3. **缺失不补 0**：资金流无完整日 → `flows=null`；广度/行业无行 → 空数组；
4. Redis 120s；响应本身带 `as_of/expected_as_of/stale`，前端不得把旧数据读成今天。
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from app.core.database import async_session_factory
from app.repositories import panorama_repo
from app.services import market_day_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.redis import CacheClient

PANORAMA_TTL = 120
PANORAMA_CACHE_PREFIX = "market:panorama"

#: 市场结构状态（启发式，描述性标签；改阈值必须同步 docs）。
STRUCTURE_LABEL: dict[str, str] = {
    "broad_rally": "普涨",
    "structured": "结构",
    "broad_decline": "普跌",
    "concentration": "抱团",
}
STRUCTURE_TONE: dict[str, str] = {
    "broad_rally": "hot",
    "structured": "neutral",
    "broad_decline": "cold",
    "concentration": "extreme",
}


def structure_state(
    *, up_ratio: float | None, top3_share: float | None, median_pct: float | None
) -> str:
    """广度 + 集中度 + 中位涨跌幅 → 四态（阈值写死、可复核；不是交易信号）。

    为什么普涨/普跌还要看中位数：2026-10-16 上涨占比 37.8%、但中位数 +0.47%，
    只看广度会把"指数涨、个股分化"误读成普跌。中位数是"典型个股"的方向，必须同看。
    """
    if up_ratio is None or top3_share is None:
        return "structured"
    if up_ratio >= 0.6 and top3_share < 0.35 and (median_pct or 0) > 0:
        return "broad_rally"
    if up_ratio <= 0.4 and top3_share < 0.35 and (median_pct or 0) < 0:
        return "broad_decline"
    if top3_share >= 0.45:
        return "concentration"
    return "structured"


def _ratio(up: int, down: int, flat: int) -> float | None:
    total = up + down + flat
    return None if total <= 0 else round(up / total, 6)


def _breadth_point(row: Any) -> dict[str, Any]:
    return {
        "trade_date": row.trade_date,
        "up_count": row.up_count,
        "down_count": row.down_count,
        "flat_count": row.flat_count,
        "limit_up_count": row.limit_up_count,
        "limit_down_count": row.limit_down_count,
        "median_pct": row.median_pct,
        "mean_pct": row.mean_pct,
        "amount": float(row.amount) if row.amount is not None else None,
        "up_ratio": _ratio(row.up_count, row.down_count, row.flat_count),
    }


def _structure_point(
    *,
    trade_date: date,
    up_ratio: float | None,
    median_pct: float | None,
    row: Any | None,
) -> dict[str, Any]:
    """单日结构：Top3 成交额占比 / HHI / 行业收益离散度（SQL 已聚合）。"""
    top3 = None if row is None or row.top3_share is None else float(row.top3_share)
    hhi = None if row is None or row.hhi is None else float(row.hhi)
    dispersion = None if row is None or row.dispersion is None else float(row.dispersion)
    state = structure_state(up_ratio=up_ratio, top3_share=top3, median_pct=median_pct)
    return {
        "trade_date": trade_date,
        "up_ratio": up_ratio,
        "top3_share": top3,
        "hhi": hhi,
        "dispersion": dispersion,
        "state": state,
        "label": STRUCTURE_LABEL[state],
        "tone": STRUCTURE_TONE[state],
    }


def _flow_item(row: Any) -> dict[str, Any]:
    inflow = float(row.main_net_inflow) if row.main_net_inflow is not None else None
    amount = float(row.amount) if row.amount is not None else None
    return {
        "board_code": row.board_code,
        "board_name": row.board_name,
        "pct_change": row.pct_change,
        "main_net_inflow": inflow,
        "net_ratio": None if inflow is None or not amount else round(inflow / amount, 6),
    }


def build_panorama_payload(
    *,
    breadth_rows: list[Any],
    bucket_rows: list[Any],
    structure_rows: list[Any],
    industry_rows: list[Any],
    flow_rows: list[Any],
    flow_as_of: date | None,
    flow_available_from: date | None,
    expected_as_of: date | None,
    quality: str,
    days: int,
) -> dict[str, Any]:
    """纯函数：组装全景响应（无 IO；缓存/查询在 :func:`get_panorama`）。"""
    breadth = [_breadth_point(r) for r in breadth_rows]
    structure_by_date = {row.trade_date: row for row in structure_rows}

    structure_series = [
        _structure_point(
            trade_date=point["trade_date"],
            up_ratio=point["up_ratio"],
            median_pct=point["median_pct"],
            row=structure_by_date.get(point["trade_date"]),
        )
        for point in breadth
    ]

    latest_industries: list[dict[str, Any]] = []
    if breadth:
        last_day = breadth[-1]["trade_date"]
        prev_day = breadth[-2]["trade_date"] if len(breadth) > 1 else None
        last_rows = [r for r in industry_rows if r.trade_date == last_day]
        prev_rows = [r for r in industry_rows if r.trade_date == prev_day]
        prev_rank = {
            r.industry_code: i + 1
            for i, r in enumerate(
                sorted(
                    [r for r in prev_rows if r.pct_chg is not None],
                    key=lambda r: (-(r.pct_chg or 0), r.industry_code),
                )
            )
        }
        ranked = sorted(
            [r for r in last_rows if r.pct_chg is not None],
            key=lambda r: (-(r.pct_chg or 0), r.industry_code),
        )
        for i, row in enumerate(ranked):
            rank = i + 1
            prev = prev_rank.get(row.industry_code)
            latest_industries.append(
                {
                    "industry_code": row.industry_code,
                    "industry_name": row.industry_name,
                    "pct_chg": row.pct_chg,
                    "up_count": row.up_count,
                    "down_count": row.down_count,
                    "flat_count": row.flat_count,
                    "limit_up_count": row.limit_up_count,
                    "amount": float(row.amount) if row.amount is not None else None,
                    "breadth": row.breadth,
                    "rank": rank,
                    "rank_delta": None if prev is None else prev - rank,
                    "attribution": row.attribution,
                }
            )

    latest_flow_day = flow_as_of
    top_in = []
    top_out = []
    if flow_rows and latest_flow_day is not None:
        ordered = [r for r in flow_rows if r.main_net_inflow is not None]
        top_in = [_flow_item(r) for r in ordered[:3]]
        top_out = [_flow_item(r) for r in list(reversed(ordered))[:3]]

    as_of = breadth[-1]["trade_date"] if breadth else None
    current_structure = structure_series[-1] if structure_series else None
    return {
        "as_of": as_of,
        "expected_as_of": expected_as_of,
        "as_of_quality": quality,
        "stale": (None if as_of is None or expected_as_of is None else as_of < expected_as_of),
        "lag_days": (
            None if as_of is None or expected_as_of is None else (expected_as_of - as_of).days
        ),
        "window": {
            "days": days,
            "from": breadth[0]["trade_date"] if breadth else None,
            "to": as_of,
            "samples": len(breadth),
        },
        "breadth": {
            "latest": breadth[-1] if breadth else None,
            "previous": breadth[-2] if len(breadth) > 1 else None,
            "series": breadth,
        },
        "buckets": [{"bucket": r.bucket, "count": r.count} for r in bucket_rows],
        "industries": latest_industries,
        "structure": {
            "current": current_structure,
            "series": structure_series,
        },
        "flows": None
        if latest_flow_day is None
        else {
            "as_of": latest_flow_day,
            "available_from": flow_available_from,
            "top_in": top_in,
            "top_out": top_out,
        },
        "freshness": {
            "breadth_as_of": as_of,
            "industry_as_of": as_of if latest_industries else None,
            "flow_as_of": latest_flow_day,
            "flow_available_from": flow_available_from,
        },
    }


async def get_panorama(cache: CacheClient | None, days: int = 60) -> dict[str, Any]:
    """`GET /market/panorama` 编排（读日表 + Redis 120s）。"""
    key = f"{PANORAMA_CACHE_PREFIX}:{days}"
    if cache is not None:
        cached: dict[str, Any] | None = await cache.get(key)
        if cached:
            return cached

    db: AsyncSession
    async with async_session_factory() as db:
        breadth_rows = await panorama_repo.list_breadth_days(db, days)
        latest_breadth = breadth_rows[-1].trade_date if breadth_rows else None
        bucket_rows = await panorama_repo.list_buckets(db, latest_breadth) if latest_breadth else []
        structure_rows = await panorama_repo.list_structure_days(db, days)
        industry_rows = await panorama_repo.list_recent_industries(db, 2)
        flow_day = await panorama_repo.latest_eod_flow_day(db, "industry")
        flow_from = await panorama_repo.earliest_eod_flow_day(db, "industry")
        flow_rows = (
            await panorama_repo.list_eod_flow_rows(db, flow_day, "industry") if flow_day else []
        )
        market = await market_day_service.resolve_latest_complete_day(db)

    quality = market.quality if market is not None else "partial"
    payload = build_panorama_payload(
        breadth_rows=breadth_rows,
        bucket_rows=bucket_rows,
        structure_rows=structure_rows,
        industry_rows=industry_rows,
        flow_rows=flow_rows,
        flow_as_of=flow_day,
        flow_available_from=flow_from,
        expected_as_of=market.day if market is not None else None,
        quality=quality,
        days=days,
    )
    if cache is not None and payload["window"]["samples"]:
        await cache.set(key, payload, ttl=PANORAMA_TTL)
    return payload
