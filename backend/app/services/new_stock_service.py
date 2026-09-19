"""次新股板块读路径（T10）：东财概念板块 `BK0501` 成分 × 本地行情 × 涨停梯队。

口径只此一处声明（plans/2026-09-18-concept-boards-and-new-stocks.md §2.2/§2.3）。三条不可退让
的约束：

1. **次新股口径来自板块成分，不由本地时间窗推导**：东财 `BK0501` 是按其"上市 ≤1 年滚动"
   规则维护的概念板块（2026-09-18 实测 162 只成分全部为 2025-09-19…2026-09-17 上市），故
   `NEW_STOCK_BOARD_CODE` 是唯一口径来源。本地再用 `stocks.list_date` 做一次"上市满 1 年"
   过滤只会在板块刷新滞后时与东财分叉，**不做**。
2. **涨停/连板只取梯队快照**：`limit_up_service.get_snapshot` 的 echelons 就是全市场涨停
   计算结果（与短线情绪 tab 同源），故 `streak = ladder_map.get(symbol)`、
   `is_lu = streak is not None and streak >= 1`。绝不为次新股卡另跑一次涨停查询——两套口径
   必然在 ST/20cm/北交所边界上分叉，用户会同时看到两个"涨停家数"。
3. **缺失 ≠ 0**：`never_broken=None`（任一行限价缺失 → 不可判）、`first_open=None`（无首日
   行情）、`pct_chg=None`（停牌/无行情）、`streak=None`（不在梯队里）全部原样留空，前端渲染
   `--`；任何 0 填充都会被下游读成真实值（"零净流入"/"未涨停"）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.repositories import concept_repo
from app.services import limit_up_service, market_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.redis import CacheClient

# 东财"次新股"概念板块码：口径真身（见模块 docstring 第 1 条）。改名/换板 = 改口径，必须评审。
NEW_STOCK_BOARD_CODE = "BK0501"
# 名录行缺失时的兜底名（正常情况下取自 concept_boards.board_name，此处只保证响应形状完整）。
NEW_STOCK_BOARD_NAME = "次新股"
# 与概念列表/详情同款：组件级缓存键 + 300s（行情快照本身按 trade_date 收敛）。
NEW_STOCK_CACHE_KEY = "market:concept:new-stocks"
NEW_STOCK_TTL = 300


def _streak_map(echelons: list[dict[str, Any]]) -> dict[str, int]:
    """梯队 echelons 拍平成 `{symbol: streak}`；`streak` 缺失的行不进表（= 后续判为缺失）。

    `streak=0` 是**合法值**（今天没涨停但仍在梯队投影里的票），必须与"缺失"区分：前者进表
    后 `is_lu=False`、`streak=0`，后者 `streak=None`。
    """
    streaks: dict[str, int] = {}
    for echelon in echelons:
        for stock in echelon.get("stocks", []):
            streak = stock.get("streak")
            if streak is None:
                continue
            streaks[str(stock["symbol"])] = int(streak)
    return streaks


def _build_items(rows: list[dict[str, Any]], streaks: dict[str, int]) -> list[dict[str, Any]]:
    """enriched 行（`StockEnrichedOut.model_dump()`）+ 梯队表 → 次新股 item 基础字段。

    `pct_chg`/`close` 直接取 `change_percent`/`latest_price`：行情缺失时它们就是 `None`，
    此处**不做 `or 0` 兜底**（缺失 ≠ 0）。`never_broken`/`first_open`/`above_first_open`/
    `listed_trade_days` 由 `build_kpis` 从 `stats` 补齐——它们来自 `member_history_stats`，
    与行情是两条数据源，放在同一个函数里合并会让"缺哪一边"不可见。
    """
    items: list[dict[str, Any]] = []
    for row in rows:
        symbol = str(row["symbol"])
        streak = streaks.get(symbol)
        items.append(
            {
                "symbol": symbol,
                "name": row.get("name"),
                "exchange": row.get("exchange"),
                "list_date": row.get("list_date"),
                "pct_chg": row.get("change_percent"),
                "close": row.get("latest_price"),
                "turnover_rate": row.get("turnover_rate"),
                "circ_mv": row.get("circ_mv"),
                "amount": row.get("amount"),
                "streak": streak,
                "is_lu": streak is not None and streak >= 1,
            }
        )
    return items


def build_kpis(items: list[dict[str, Any]], stats: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """纯函数：把 `member_history_stats` 合并进 items 并聚合 8 项 KPI（无 IO、无 DB）。

    **就地补齐 item 的派生字段**（`listed_trade_days`/`never_broken`/`first_open`/
    `above_first_open`）：它们在响应里与 KPI 同源，分开算会让"KPI 说 3 家未开板、表格却渲染
    4 行"这类漂移无从排查。同一份 `items` 既供 KPI 也供表格是刻意的（§2.3：KPI 家数必须与
    表格行数一致）。

    - `never_broken` 原样取 `stats`：`None`（限价缺失 → 不可判）**不计入** `unbroken_count`，
      也不等于"已开板"。
    - `above_first_open = close >= first_open`，任一侧为 `None` 时留 `None`（替代破发口径）。
    - `avg_pct` 的分母是**非空** `pct_chg` 家数；全空 → `None`（不是 0）。
    """
    up = flat = down = unpriced = limit_up = unbroken = above = 0
    pcts: list[float] = []
    for item in items:
        stat = stats.get(str(item["symbol"]), {})
        first_open = stat.get("first_open")
        close = item.get("close")
        item["listed_trade_days"] = int(stat.get("listed_trade_days") or 0)
        item["never_broken"] = stat.get("never_broken")
        item["first_open"] = first_open
        item["above_first_open"] = (
            close >= first_open if close is not None and first_open is not None else None
        )

        pct = item.get("pct_chg")
        if pct is None:
            unpriced += 1
        else:
            pcts.append(float(pct))
            if pct > 0:
                up += 1
            elif pct < 0:
                down += 1
            else:
                flat += 1
        if item.get("is_lu"):
            limit_up += 1
        if item["never_broken"] is True:
            unbroken += 1
        if item["above_first_open"] is True:
            above += 1

    return {
        "up_count": up,
        "flat_count": flat,
        "down_count": down,
        "unpriced_count": unpriced,
        "limit_up_count": limit_up,
        "unbroken_count": unbroken,
        "above_first_open_count": above,
        "avg_pct": (sum(pcts) / len(pcts)) if pcts else None,
    }


def _degraded_reason(has_members: bool, snapshot_reason: Any) -> str | None:
    """降级词表：板内无成分 → `no_members`（本端点的一级空态，优先）；否则**原样透传**快照
    自己的词（`no_quotes` / `price_limits_missing` / `partial_day` / `insufficient_trade_days`），
    唯一例外是 `no_limit_up_rows`——市场级"今天没有涨停"是合法空态，KPI 诚实报 0。

    与 T8 `get_board_detail` 的裁决逐字一致；T8 里这段内联在 service 中、没有可复用 helper，
    而本次改动范围不含 `concept_service.py`，故此处保留同一映射（若将来抽公共 helper，两处
    必须同时替换）。
    """
    if not has_members:
        return "no_members"
    if snapshot_reason and snapshot_reason != "no_limit_up_rows":
        return str(snapshot_reason)
    return None


def _item_sort_key(item: dict[str, Any]) -> tuple[bool, float, str]:
    """`pct_chg DESC NULLS LAST, symbol ASC`——与 `/concepts/{code}/stocks` 同款确定性顺序。

    计划未规定次新股 items 顺序，但响应必须**可复现**（前端 T15 自己再按涨跌幅排序，后端
    顺序不影响 UI）；缺行情的票排最后，同值按 symbol 升序兜底。
    """
    pct = item.get("pct_chg")
    return (pct is None, -(float(pct) if pct is not None else 0.0), str(item["symbol"]))


async def get_new_stock_board(db: AsyncSession, cache: CacheClient | None) -> dict[str, Any]:
    """次新股板块信封：成分 × 本地行情 + 梯队 streak/is_lu + 上市统计 KPI（Redis 300s）。

    数据源三路（互不替代）：`concept_members`（口径真身）/ `market_service` 本地行情 /
    `limit_up_service.get_snapshot`（streak 与 `is_lu`）。成员解析不到 `stocks` 名录（`stock_id`
    为 NULL）的票**不在** items 里——它们是 `unresolved_count` 的暴露面，KPI 与表格同为"可统计
    成分"口径，故四档家数之和恒等于 `len(items)`。

    缓存**只在 items 非空时写**（"数据不完整时宁可不缓存"）：降级/空响应若被缓存，上游数据
    补齐后还要再等一个 TTL 才可见。
    """
    if cache is not None:
        cached: dict[str, Any] | None = await cache.get(NEW_STOCK_CACHE_KEY)
        if cached:
            return cached

    members = await concept_repo.list_member_symbols(db, NEW_STOCK_BOARD_CODE)
    stats = await concept_repo.member_history_stats(db, NEW_STOCK_BOARD_CODE)
    board_meta = await concept_repo.find_board(db, NEW_STOCK_BOARD_CODE)
    membership_as_of = await concept_repo.board_membership_date(db, NEW_STOCK_BOARD_CODE)

    enriched = await market_service.get_stocks_enriched_by_symbols(
        db, [symbol for symbol, _ in members]
    )
    snap = await limit_up_service.get_snapshot(cache)
    items = _build_items(
        [row.model_dump() for row in enriched],
        _streak_map(snap.get("echelons") or []),
    )
    kpis = build_kpis(items, stats)
    items.sort(key=_item_sort_key)

    as_of = snap.get("as_of")
    body: dict[str, Any] = {
        "as_of": as_of.isoformat() if as_of is not None else None,
        "membership_as_of": (
            membership_as_of.isoformat() if membership_as_of is not None else None
        ),
        "board_code": NEW_STOCK_BOARD_CODE,
        "board_name": board_meta["board_name"] if board_meta else NEW_STOCK_BOARD_NAME,
        "source": "em_clist",
        "degraded_reason": _degraded_reason(bool(members), snap.get("degraded_reason")),
        "kpis": kpis,
        "items": items,
    }
    if cache is not None and items:
        await cache.set(NEW_STOCK_CACHE_KEY, body, ttl=NEW_STOCK_TTL)
    return body
