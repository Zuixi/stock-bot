"""连板梯队与市场情绪：快照编排（唯一带 try/except 的层）。

source 由**数据可用性**决定，不由偏好决定：有完整限价 + 完整当日行情 → 本地自算（权威、
可回放）；否则返回空 payload + degraded_reason，**不猜**。读路径只读，绝不外呼写库。

`mode=intraday`（Task 11）：盘中口径（今日），源=东财涨停池，**不写** `market_sentiment_daily`。
`mode=close`（默认）：原有本地口径，行为与今天字节级一致。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Literal

from app.core.database import async_session_factory
from app.repositories import limit_up_repo
from app.services import limit_up_calculator as calc
from app.services import market_day_service
from app.services.market_data_service import _today_sh

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.redis import CacheClient

logger = logging.getLogger(__name__)

SNAPSHOT_TTL = 300
CALENDAR_TTL = 900
INTRADAY_TTL = 60  # 盘中缓存：与 30s 轮询半衰期对齐（Task 11）
# 派生表（market_sentiment_daily）写入后须失效的读缓存键模式：失效内聚在
# persist_snapshot 内，任何调用方（定时/手动/对账）都不依赖"记得清缓存"。
_CALENDAR_CACHE_PATTERN = "market:limit-up:calendar:*"
_WINDOW_BUFFER = 6  # gaps-and-islands 需要的前置上下文（lookback 之外多取的交易日）

# 模式（Task 11）。`close` = 既有本地口径；`intraday` = 盘中口径（今日东财池）。
# 由路由层 `mode: Literal["close", "intraday"] = "close"` 透传过来；历史日期 + 盘中 = 400。
SnapshotMode = Literal["close", "intraday"]


def window_trade_days(lookback: int) -> int:
    """窗口交易日数必须随 lookback 增长。

    硬编码 16 会让 lookback=30（端点允许）静默截断：streak 被窗口起点截平、
    N天M板数不到 lookback。key 已包含 lookback，所以窗口大小变化不会污染缓存。
    """
    return lookback + _WINDOW_BUFFER


def _intraday_cache_key(trade_date: date) -> str:
    """盘中缓存键（Task 11）——**仅**含 trade_date，**不**含 lookback。

    与 close 路径 `market:limit-up:snapshot:{date}:{lookback}` 完全独立，不串味。
    TTL=60s（INTRADAY_TTL）。
    """
    return f"market:limit-up:intra:{trade_date.isoformat()}"


def _now_sh() -> datetime:
    """当前上海时区时间。模块级函数让测试可 monkeypatch（钉死时钟）。"""
    from zoneinfo import ZoneInfo  # noqa: PLC0415

    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _empty(as_of: date | None, reason: str) -> dict[str, Any]:
    return {
        "as_of": as_of,
        "as_of_prev": None,
        "as_of_quality": "partial",
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
    cache: CacheClient | None,
    as_of: date | None = None,
    lookback: int = calc.LOOKBACK_TRADE_DAYS,
    *,
    mode: SnapshotMode = "close",
) -> dict[str, Any]:
    """一次查询 → 一份快照 → 3 个端点共享，保证口径同源。

    Parameters
    ----------
    cache
        Redis 客户端；None 时跳过所有缓存读写（close 路径行为不变）。
    as_of
        显式查询日（ISO date）；None 时 close 路径走 `resolve_latest_complete_day`，
        intraday 路径固定为"今天"。
    lookback
        连板窗口交易日数（close 路径用）。intraday 路径忽略。
    mode
        `close`（默认）= 本地口径；`intraday` = 盘中口径（仅今日）。
        intraday + 历史日期 → ValueError（端点转 400，盘中无历史意义）。
    """
    if mode == "intraday":
        if as_of is not None and as_of != _today_sh():
            raise ValueError(
                f"mode=intraday does not accept historical as_of={as_of}; "
                "intraday is today-only by design"
            )
        return await _get_intraday_snapshot(cache)
    # 原 close 路径——保持字节级一致（不引入 mode/as_of_label 等副作用）。
    async with async_session_factory() as db:
        quality: market_day_service.MarketQuality = "partial"
        if as_of is not None:
            target: date | None = as_of
        else:
            # 无显式日期 → 走完整性判据，脏的最新日不得把情绪面打成空快照。
            md = await market_day_service.resolve_latest_complete_day(db, cache=cache)
            target = md.day if md is not None else None
            if md is not None:
                quality = md.quality
        if target is None:
            return _empty(None, "no_quotes")
        key = f"market:limit-up:snapshot:{target.isoformat()}:{lookback}"
        if cache is not None:
            cached: dict[str, Any] | None = await cache.get(key)
            if cached:
                # 缓存体只由 (target, lookback) 决定，as_of_quality 是**逐请求**标签：
                # 同一 target 既可来自显式 ?date=（partial），也可来自判据（complete/fallback）。
                # 若原样返回，先写缓存的那个请求会把标签固化到 TTL 结束（显式日期写下
                # partial 会污染默认请求，反之默认请求的 complete 会让 ?date= 谎报完整度）。
                # 故命中路径一律用**本次请求**已知的 quality 覆盖标签，两条分支互不串味。
                return {**cached, "as_of_quality": quality}
        market_days = await limit_up_repo.list_recent_trade_dates(
            db, target, window_trade_days(lookback)
        )
        if len(market_days) < 2:
            return _empty(target, "insufficient_trade_days")
        breadth = await limit_up_repo.fetch_day_breadth(db, target)
        limits_present = await limit_up_repo.has_price_limits(db, target)
        snap = _empty(target, None)  # type: ignore[arg-type]
        snap["as_of_quality"] = quality
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
        item["seal_time"] = None  # 本地路径不具备（Web 增强在 Task 6 注入）
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
    if snap["degraded_reason"] is None:
        snap = await enrich_from_web(snap, target.strftime("%Y%m%d"))
    if cache is not None and snap["degraded_reason"] is None:
        await cache.set(key, snap, ttl=SNAPSHOT_TTL)
    return snap


# ----- 盘中分支（Task 11）--------------------------------------------------------


async def _get_intraday_snapshot(cache: CacheClient | None) -> dict[str, Any]:
    """盘中分支：东财涨停池 → 与 close 路径同构的 snapshot。

    不变量（Task 11 brief）：

    - **不调 `persist_snapshot`**——盘中数据**不写** `market_sentiment_daily`。
      兜底分支（fetch_intraday_pool 抛错）才允许走 close 路径落库。
    - **历史日期不接**——`mode=intraday` + 显式 `as_of` 已在 `get_snapshot` 入口
      抛 ValueError（端点转 400）。这里 `_today_sh()` 即"今天"。
    - **缓存独立**——`market:limit-up:intra:{trade_date}` 60s，与 close 路径
      `market:limit-up:snapshot:{date}:{lookback}` 不串味。
    - **回落 close**——东财失败 → 走 close 路径，as_of_label 替换为回落标注，
      `as_of_quality` 保持 close 路径的口径（不强行改成 "partial"）。
    """
    # 导入放在函数内避免 import cycle（intraday_sentiment_service 间接 import
    # 链可能拖出 limit_up_service）；同时让 monkeypatch.fetch_intraday_pool 生效。
    from app.services import intraday_sentiment_service  # noqa: PLC0415

    today = _today_sh()
    intra_key = _intraday_cache_key(today)

    # 1) 缓存命中：直接返回盘中快照，**不**调 fetch_intraday_pool、**不**调
    #    persist_snapshot。
    if cache is not None:
        cached: dict[str, Any] | None = await cache.get(intra_key)
        if cached is not None:
            return cached

    # 2) 抓东财池 → build_intraday_snapshot；失败则回落 close 路径。
    captured_at = _now_sh()
    try:
        pool_rows = await intraday_sentiment_service.fetch_intraday_pool(today.strftime("%Y%m%d"))
    except Exception:  # noqa: BLE001 —— IO 失败可观测：回落 close，as_of_label 标不可用
        logger.warning(
            "intraday: eastmoney pool fetch failed; falling back to close path",
            exc_info=True,
        )
        # 回落 close 路径——**走标准 close 路径**；as_of_label 后置覆盖。
        # 落库也走：brief 要求 fallback 调用 persist_snapshot（intraday 自身**不**
        # 落库，但 fallback 走 close 路径就按 close 口径处理——当天的 close 数据
        # 提前被用户拉到，可顺便写盘）。
        snap = await get_snapshot(cache, as_of=today, mode="close")
        snap["as_of_label"] = "盘中不可用，已回落收盘"
        # close 路径的 as_of_prev/yesterday/echelons 是收盘口径，保留。
        # 落库：复用 close 路径的 persist_snapshot——它本身又会调 get_snapshot，
        # 拿到同样的 snap，跳过自身已跳过的所有降级判据，最终走 upsert_sentiment_daily。
        try:
            await persist_snapshot(db=None, cache=cache, as_of=today)
        except Exception:  # noqa: BLE001 —— 落库失败也不应挡住回落响应
            logger.warning(
                "intraday fallback: persist_snapshot failed; serving close snap only",
                exc_info=True,
            )
        return snap

    # 3) 正常盘中路径——build_intraday_snapshot 是纯函数，无 IO、无落库。
    snap = intraday_sentiment_service.build_intraday_snapshot(
        pool_rows, as_of=today, captured_at=captured_at
    )

    # 4) 写缓存（60s）。降级快照（degraded_reason 非 None）也缓存——前端应在徽标
    #    渲染"无涨停"并避免 30s 雪崩重试；TTL 短到失效可接受。
    if cache is not None:
        await cache.set(intra_key, snap, ttl=INTRADAY_TTL)
    return snap


async def _fetch_web_pool(trade_date: str) -> list[dict[str, Any]]:
    from app.core.providers.eastmoney_client import get_eastmoney_client  # noqa: PLC0415

    return await get_eastmoney_client().fetch_limit_up_pool(trade_date)


def apply_web_enrichment(
    snapshot: dict[str, Any], web_rows: list[dict[str, Any]]
) -> dict[str, Any]:
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


def sector_payload(snap: dict[str, Any], sw_l1: str | None = None) -> dict[str, Any]:
    """申万 L3 最高板投影；`sw_l1` 过滤为客户端维度，缓存不受其影响。

    intraday 路径：sectors.items 内 `l1_code` 全 None，`sw_l1` 过滤会
    命中空集——预期行为（前端 intraday 切到板块视图时显示"盘中无 SW 映射"）。
    """
    items = snap["sectors"]["items"]
    if sw_l1:
        items = [i for i in items if i["l1_code"] == sw_l1]
    return {
        "as_of": snap["as_of"],
        "as_of_quality": snap.get("as_of_quality", "partial"),
        "source": snap["source"],
        "degraded_reason": snap["degraded_reason"],
        "unclassified_count": snap["sectors"]["unclassified_count"],
        "items": items,
        # 收盘路径不设、默认 None（api 端点构造时不传），intraday 路径 build 已带
        "as_of_label": snap.get("as_of_label"),
    }


def yesterday_payload(snap: dict[str, Any]) -> dict[str, Any]:
    """昨日涨停今日表现投影。

    intraday 路径：`snap["yesterday"]` 是 None（盘中无"昨日→今日"语义），
    走空 kpis/items；端点响应仍是合法 `YesterdayLimitUpOut`。
    """
    yest = snap.get("yesterday") or {"kpis": {}, "items": []}
    return {
        "as_of": snap["as_of"],
        "as_of_prev": snap["as_of_prev"],
        "as_of_quality": snap.get("as_of_quality", "partial"),
        "source": snap["source"],
        "degraded_reason": snap["degraded_reason"],
        "kpis": yest.get("kpis", {}),
        "items": yest.get("items", []),
        "as_of_label": snap.get("as_of_label"),
    }


async def _invalidate_calendar_cache() -> int:
    """失效情绪周期读缓存（Redis 不可用时 delete_pattern 内部静默返回 0）。

    calendar 缓存"非空即缓存"会把旧结果集固化（TTL 900s 内补数也不可见），
    实测 9/17 补完两日数据后日历端点仍只返回 1 点——失效必须与落库同点发生。
    """
    from app.core.redis import CacheClient, get_redis_pool  # noqa: PLC0415

    cache = CacheClient(await get_redis_pool())
    return await cache.delete_pattern(_CALENDAR_CACHE_PATTERN)


async def persist_snapshot(
    db: AsyncSession | None, cache: CacheClient | None, as_of: date | None = None
) -> dict[str, Any]:
    """盘后把当日情绪聚合写入派生表（幂等，可重复跑）。

    is_partial / 限价缺失 / 空候选 / 无行情一律不写：时序图里塞进一个假低谷比缺一天更糟；
    `no_limit_up_rows`（快照 kpis 为空）漏在名单外会直接 `k["zt_count"]` KeyError。
    """
    snap = await get_snapshot(cache, as_of)
    # 部分行情日优先按 partial_day 跳过：get_snapshot 在「候选窗口为空」时先报
    # no_limit_up_rows，但部分 ingest 才是更本质的跳过原因（数据没拉全，而非真的没涨停）。
    if snap.get("is_partial"):
        return {"status": "skipped", "reason": "partial_day"}
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
        "trade_date": trade_date,
        "zt_count": k["zt_count"],
        "dt_count": k["dt_count"],
        "zb_count": k["zb_count"],
        "broken_rate": k["broken_rate"],
        "yzt_avg_pct": k["yzt_avg_pct"],
        "promo_1to2": k["promo_1to2"],
        "promo_1to2_n": k["promo_1to2_n"],
        "promo_2to3": k["promo_2to3"],
        "promo_2to3_n": k["promo_2to3_n"],
        "max_streak": k["max_streak"],
        "max_streak_symbol": leaders[0]["symbol"] if leaders else None,
        "source": snap["source"],
    }
    if db is None:
        async with async_session_factory() as session:
            await limit_up_repo.upsert_sentiment_daily(session, row)
            await session.commit()
    else:
        await limit_up_repo.upsert_sentiment_daily(db, row)
    await _invalidate_calendar_cache()
    return {"status": "ok", "trade_date": trade_date, "zt_count": k["zt_count"]}


async def get_calendar(cache: CacheClient | None, days: int = 30) -> list[dict[str, Any]]:
    """情绪周期时序（来自 `market_sentiment_daily` 派生缓存，升序）。

    缓存 key 含 `days`（改变结果集的维度）；空结果不缓存，避免把"还没落库"固化成空图。
    """
    key = f"market:limit-up:calendar:{days}"
    if cache is not None:
        cached: list[dict[str, Any]] | None = await cache.get(key)
        if cached:
            return cached
    async with async_session_factory() as db:
        rows = await limit_up_repo.list_sentiment_calendar(db, days)
    if cache is not None and rows:
        await cache.set(key, rows, ttl=CALENDAR_TTL)
    return rows
