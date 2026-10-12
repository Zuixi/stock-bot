"""连板梯队与市场情绪：快照编排（唯一带 try/except 的层）。

source 由**数据可用性**决定，不由偏好决定：有完整限价 + 完整当日行情 → 本地自算（权威、
可回放）；否则返回空 payload + degraded_reason，**不猜**。读路径只读，绝不外呼写库。

`mode=intraday`（Task 11）：盘中口径（今日），源=东财涨停池，**不写** `market_sentiment_daily`。
`mode=close`（默认）：原有本地口径，行为与今天字节级一致。
"""

from __future__ import annotations

import logging
import math
import statistics
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Literal

from app.core.database import async_session_factory
from app.repositories import limit_up_repo
from app.schemas.limit_up import MIN_PERCENTILE_SAMPLES, PERCENTILE_FIELDS
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


async def window_limits_complete(
    db: AsyncSession, as_of: date, lookback: int = calc.LOOKBACK_TRADE_DAYS
) -> bool:
    """窗口完备性守卫（E2）：窗口内**每一天**都必须有限价，而不是只看 `as_of`。

    窗口取自 `daily_quotes`（库里自 2023-05 起），限价自 2025-09-16 起：`lookback + 6`
    个交易日窗口的**开头**若干天一旦落在限价上线前，`_WINDOW_SQL` 的
    `LEFT JOIN limits` 就整天落空 → `is_lu` 全 false → streak 被截平、晋级率
    (`promotion_rate` 读的正是 `as_of_prev` 的 streak) 系统性低估。

    这种日子**不是"没涨停"**，而是"算不出来"——整日跳过（`window_limits_incomplete`），
    绝不落一行假的低 streak。窗口凑不满（数据起点附近）或 `as_of` 自己不是窗口末日
    （无行情/非交易日）同样判不完备。
    """
    needed = window_trade_days(lookback)
    market_days = await limit_up_repo.list_recent_trade_dates(db, as_of, needed)
    if len(market_days) < needed or market_days[-1] != as_of:
        return False
    return not await limit_up_repo.missing_price_limit_dates(db, market_days)


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
    enrich: bool = True,
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
    enrich
        是否允许东财 Web 增强（默认 True，端点/实时路径行为不变）。历史回补传 False：
        封板明细是 A 类**当天不抓就永久丢**的数据，"回补 239 天"不该对历史日期外呼 239 次
        （既浪费外部配额，也让回补的耗时/成败绑在第三方可用性上）。
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
    if enrich and snap["degraded_reason"] is None:
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
    db: AsyncSession | None,
    cache: CacheClient | None,
    as_of: date | None = None,
    *,
    fact_source: str = "local_calc",
    enrich: bool = True,
) -> dict[str, Any]:
    """盘后把当日情绪聚合写入派生表（幂等，可重复跑）。

    is_partial / 限价缺失 / 空候选 / 无行情一律不写：时序图里塞进一个假低谷比缺一天更糟；
    `no_limit_up_rows`（快照 kpis 为空）漏在名单外会直接 `k["zt_count"]` KeyError。

    `fact_source` 写给 `source` 列（默认 `local_calc` = 实时/定时口径；回补脚本传
    `backfill`）。`fact_source=BACKFILL_SOURCE` 时 upsert 走 **Ruling 18 守卫**：只能修
    已标记 `source='backfill'` 的行，当天实时行字节不变（见 `upsert_sentiment_daily`）。
    `enrich=False` 只给历史回补用（理由见 `get_snapshot`），默认 True 保持端点行为不变。
    """
    snap = await get_snapshot(cache, as_of, enrich=enrich)
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
        "source": fact_source,
    }
    backfill_only = fact_source == limit_up_repo.BACKFILL_SOURCE
    if db is None:
        async with async_session_factory() as session:
            upserted = await limit_up_repo.upsert_sentiment_daily(
                session, row, backfill_only=backfill_only
            )
            await session.commit()
    else:
        upserted = await limit_up_repo.upsert_sentiment_daily(db, row, backfill_only=backfill_only)
    await _invalidate_calendar_cache()
    return {
        "status": "ok",
        "trade_date": trade_date,
        "zt_count": k["zt_count"],
        # **实际受影响行数**（不是"打算写几行"）：回补 upsert 被 Ruling 18 守卫拒绝时 PG 的
        # rowcount 就是 0。丢掉它会让调用方把"被守卫拒改的实时行"当成"成功回补"
        # （T4 评审 Important #1：21 个 local_calc 日被报成 ok 正是这个丢 rowcount 的后果）。
        "upserted": upserted,
        "kept_realtime": backfill_only and upserted == 0,
    }


# ----- 个股级涨停事实 + 东财 EOD 增强（Task 1）------------------------------------
#
# 两个关注点，互不越界：
# 1) **判定列**（is_lu/touched/is_zb/streak/N天M板/missing_days/prev_is_lu，B 类可回算）
#    恒来自交易所限价：候选窗口 `_WINDOW_SQL` + 当日炸板选行 `_DAY_BROKEN_SQL`。
# 2) **东财独有列**（seal_time/seal_fund/break_count，A 类不可回补）由 EOD 增强写入，
#    UPDATE-only，**永不**覆写判定列。

# 增强告警原因码（区分"空池"与"池里有票但当日事实行不存在"——后者才是数据丢了）
ENRICH_REASON_POOL_FAILED = "pool_fetch_failed"
ENRICH_REASON_EMPTY_POOL = "empty_pool"
ENRICH_REASON_NO_ROWS = "no_fact_rows"
ENRICH_REASON_WRITE_FAILED = "enrich_write_failed"


def build_stock_fact_rows(
    window_rows: list[dict[str, Any]],
    broken_rows: list[dict[str, Any]],
    *,
    as_of: date,
    as_of_prev: date,
    market_days: list[date],
    lookback: int = calc.LOOKBACK_TRADE_DAYS,
    fact_source: str = "local_calc",
) -> list[dict[str, Any]]:
    """当日个股事实行集 = 候选窗口 as_of 行 ∪ 当日炸板行（按 stock_id 去重）。

    - 窗口 as_of 行来自 `_WINDOW_SQL` 的 `cand`（含 `as_of_prev` 涨停的票）→ "昨日涨停
      今日未板"的断板票天然在内；`prev_is_lu` 取同一只票在窗口里的 `as_of_prev` 行，
      该行缺失（前一日停牌/无行情）→ `False`（"前一日是否涨停"在无行情日不成立）。
    - 炸板行来自 `_DAY_BROKEN_SQL`（`cand` 不含炸板票：实测 09-08 的 39 只炸板里只有
      12 只在候选窗口内）。
    - **窗口派生列只对候选窗口内的票可算**：炸板-only 的票（今日触板但昨日未涨停）在
      窗口里没有任何行 → `days_span`/`boards_in_window`/`missing_days` 落 `None`
      （缺失 ≠ 0；落 0 会被下游读成"无板/无停牌"这一事实断言）。
    - `streak` 恒为"截至 as_of 的连板数"：当日未涨停即确定归零（0 是事实，不是缺失），
      与 `limit_up_calculator.ladder` 的 `streak_upto` 同源。
    - 东财独有列恒 `None`（本地路径不具备），由 EOD 增强填。
    """
    today_by_id = {r["stock_id"]: r for r in window_rows if r["trade_date"] == as_of}
    prev_by_id = {r["stock_id"]: r for r in window_rows if r["trade_date"] == as_of_prev}
    broken_by_id = {r["stock_id"]: r for r in broken_rows}

    rows: list[dict[str, Any]] = []
    for stock_id in sorted(set(today_by_id) | set(broken_by_id)):
        today = today_by_id.get(stock_id)
        if today is not None:
            symbol = today["symbol"]
            is_lu = bool(today["is_lu"])
            touched = bool(today["touched"])
        else:
            # 炸板-only：`_DAY_BROKEN_SQL` 的谓词已保证 high ≥ 涨停价 且 close < 涨停价
            broken = broken_by_id[stock_id]
            symbol = broken["symbol"]
            is_lu = False
            touched = True
        prev = prev_by_id.get(stock_id)
        if today is not None:
            days_span, boards = calc.n_day_m_board(
                window_rows, stock_id, as_of, market_days, lookback
            )
            missing_days: int | None = calc.missing_days(window_rows, stock_id, market_days)
        else:
            days_span, boards, missing_days = None, None, None
        rows.append(
            {
                "trade_date": as_of,
                "stock_id": stock_id,
                "symbol": symbol,
                "is_lu": is_lu,
                "touched": touched,
                "is_zb": touched and not is_lu,
                "prev_is_lu": bool(prev["is_lu"]) if prev is not None else False,
                "streak": int(today["streak_upto"]) if (today is not None and is_lu) else 0,
                "days_span": days_span,
                "boards_in_window": boards,
                "missing_days": missing_days,
                "seal_time": None,
                "seal_fund": None,
                "break_count": None,
                "fact_source": fact_source,
                "enrich_source": None,
            }
        )
    return rows


async def persist_stock_facts(
    db: AsyncSession, as_of: date, *, fact_source: str = "local_calc"
) -> dict[str, int]:
    """把某交易日的个股涨停/炸板事实写入 `market_limit_up_stock_daily`（幂等）。

    窗口参数与 `get_snapshot` 完全同参（`window_trade_days(lookback)` 个交易日），
    否则 `streak`/N天M板/missing_days 会与梯队口径分叉。

    `as_of` 不在 `daily_quotes` 的交易日集合里（节假日/脏日期）→ 返回全 0 并告警：
    不写一行假事实、也不抛错打断调用方的盘后链路。

    `fact_source=BACKFILL_SOURCE`（历史回补）→ upsert 走 Ruling 18 守卫：只修
    `fact_source='backfill'` 的行，当天实时行（含东财增强列）字节不变。

    返回 `rows`（行集大小）与 `upserted`（**实际受影响行数**，即 PG rowcount 之和）：
    守卫拒绝的实时行不计入 `upserted`，故 `upserted < rows` 说明这一天有行被"当作实时行
    保留"（`kept_realtime`）——调用方不得把它当"成功回补"（T4 评审 Important #1）。
    `build_stock_fact_rows` 保证 `(trade_date, stock_id)` 唯一，故 `rows` 就是 dedup 后的行数。
    """
    market_days = await limit_up_repo.list_recent_trade_dates(
        db, as_of, window_trade_days(calc.LOOKBACK_TRADE_DAYS)
    )
    if len(market_days) < 2 or market_days[-1] != as_of:
        logger.warning(
            "persist_stock_facts skipped: as_of=%s not the latest quoted trade date (%s)",
            as_of,
            market_days[-3:],
        )
        return {
            "rows": 0,
            "is_lu": 0,
            "is_zb": 0,
            "prev_is_lu": 0,
            "upserted": 0,
            "kept_realtime": False,
        }
    window_rows = await limit_up_repo.fetch_limit_up_window(
        db, as_of=as_of, as_of_prev=market_days[-2], window_start=market_days[0]
    )
    broken_rows = await limit_up_repo.fetch_day_broken_board_stocks(db, as_of)
    rows = build_stock_fact_rows(
        window_rows,
        broken_rows,
        as_of=as_of,
        as_of_prev=market_days[-2],
        market_days=market_days,
        fact_source=fact_source,
    )
    upserted = await limit_up_repo.upsert_limit_up_stock_daily(
        db, rows, backfill_only=fact_source == limit_up_repo.BACKFILL_SOURCE
    )
    return {
        "rows": len(rows),
        "upserted": upserted,
        # 有行被守卫拒绝（已存在的实时行）即 True：整日被拒时 upserted == 0。
        "kept_realtime": bool(rows) and upserted < len(rows),
        "is_lu": sum(1 for r in rows if r["is_lu"]),
        "is_zb": sum(1 for r in rows if r["is_zb"]),
        "prev_is_lu": sum(1 for r in rows if r["prev_is_lu"]),
    }


async def enrich_stock_facts_from_pool(
    db: AsyncSession, as_of: date, pool_rows: list[dict[str, Any]]
) -> int:
    """东财涨停池 → 事实表的**增强**列（UPDATE-only，返回命中行数）。

    **只**写 `seal_time`/`seal_fund`/`break_count`/`enrich_source`：判定列恒由交易所限价
    决定，东财池永不覆写（口径冻结）。池里不存在的 symbol 不新建行 —— 行集由判定口径
    定义，不由装饰层定义。
    """
    from app.repositories import market_data_repo  # noqa: PLC0415
    from app.services import intraday_sentiment_service  # noqa: PLC0415

    enriched_rows = intraday_sentiment_service.build_enriched_rows(pool_rows)
    if not enriched_rows:
        return 0
    return await market_data_repo.enrich_limit_up_stock_daily_from_pool(db, as_of, enriched_rows)


async def persist_and_enrich_stock_facts(
    db: AsyncSession, as_of: date, *, fact_source: str = "local_calc"
) -> dict[str, Any]:
    """盘后主链（双轨共用）：落判定列 → **commit** → 东财 EOD 增强（best-effort）。

    顺序是硬约束：增强是 UPDATE-only，当日事实行必须先 commit 才对它可见。这也是
    "为什么增强不能单独挂在 15:05" 的原因 —— 15:05 时限价（16:50）与 facts（本 job）
    都还没跑，增强必然命中 0 行，每天白造一条告警。

    返回 `{"rows","is_lu","is_zb","prev_is_lu","enriched","enrich_reason"}`：
    `enrich_reason` 为 `None` 表示增强成功；否则是原因码（`pool_fetch_failed` /
    `empty_pool` / `no_fact_rows` / `enrich_write_failed`），由调用方升级成 `job:failures`
    告警。**增强失败绝不影响已提交的事实行，也绝不让 job 整体失败**（B 类事实优先于
    A 类装饰）—— 包括增强写库本身抛错（Task 8：best-effort 覆盖整段增强）。
    """
    result: dict[str, int] = await persist_stock_facts(db, as_of, fact_source=fact_source)
    await db.commit()
    enriched, reason = await _enrich_stock_facts_day(db, as_of)
    return {**result, "enriched": enriched, "enrich_reason": reason}


async def _enrich_stock_facts_day(db: AsyncSession, as_of: date) -> tuple[int, str | None]:
    """抓 `as_of` 的东财涨停池并增强当日事实行；任何失败都降级为原因码，不抛错。

    best-effort **覆盖整段增强**（Ruling 10 的"增强失败绝不影响已提交事实"）：外呼失败、
    空池、0 命中、以及**增强写库本身抛错**都只记原因码 + `logger.warning`，由调用方升级成
    `job:failures`。facts 已由调用方 commit，故这里失败不回滚、不上抛。
    """
    from app.services import intraday_sentiment_service  # noqa: PLC0415

    try:
        pool_rows = await intraday_sentiment_service.fetch_intraday_pool(as_of.strftime("%Y%m%d"))
    except Exception:  # noqa: BLE001 —— A 类装饰失败不得上升为 job 失败
        logger.warning(
            "stock facts EOD enrichment: eastmoney pool fetch failed (trade_date=%s)",
            as_of,
            exc_info=True,
        )
        return 0, ENRICH_REASON_POOL_FAILED
    if not pool_rows:
        # Ruling 10 要求空池**也告警**（与 0 命中并列）：空池同样是"A 类封板明细当天没拿到"
        logger.warning("stock facts EOD enrichment: eastmoney pool is empty (trade_date=%s)", as_of)
        return 0, ENRICH_REASON_EMPTY_POOL
    try:
        enriched = await enrich_stock_facts_from_pool(db, as_of, pool_rows)
    except Exception:  # noqa: BLE001 —— 写库失败同样是"装饰丢了"，不得让 job 整体失败
        logger.warning(
            "stock facts EOD enrichment: write failed (trade_date=%s, pool=%d)",
            as_of,
            len(pool_rows),
            exc_info=True,
        )
        return 0, ENRICH_REASON_WRITE_FAILED
    if not enriched:
        logger.warning(
            "stock facts EOD enrichment: pool=%d but 0 fact rows matched (trade_date=%s)",
            len(pool_rows),
            as_of,
        )
        return 0, ENRICH_REASON_NO_ROWS
    return enriched, None


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


# ----- Task 6a：情绪趋势 + 历史分位（读时计算，不物化）--------------------------------
#
# 三层分工（计划 Architecture §3）：
# - **repo**：只做窗口读（`list_sentiment_days` / `latest_sentiment_day`）；
# - **本模块的纯函数**：把行投影成序列、按**有效样本**算分位、组装 payload；
# - **服务函数**：编排 + Redis 缓存（键含 `days`，空结果不缓存）。
#
# 不物化：没有新表、没有新 Job、没有写路径。分位每次请求现算（窗口 ≤ 500 行，
# `statistics` 级开销可忽略；一旦需要 SQL 侧 `percentile_cont` 再谈，但那时必须
# 同步"NULL 被 `FILTER (WHERE col IS NOT NULL)` 排除"这条口径）。

TRENDS_TTL = 900
_TRENDS_CACHE_KEY = "market:limit-up:trends:{days}"

#: 分位口径（Ruling 4）：只 3 个指标（`PERCENTILE_FIELDS`，拍板 R6 —— 契约常量在
#: schema 里，服务层**不复制一份**，加了指标两处一起动）。`zt_count` 每日必有效；
#: 另两个在分母为 0 的日子是 NULL，必须**排除**而不是按 0 计。
#: `promo_1to2_n` 是第二重判据：分母为 0 时比值无意义（写入方目前恒写 NULL，防御未来改口径）。
_PERCENTILE_VALIDITY: dict[str, str] = {f: f for f in PERCENTILE_FIELDS}
_PERCENTILE_POINTS: tuple[int, ...] = (0, 10, 25, 50, 75, 90, 100)


def sentiment_series_row(row: Any) -> dict[str, Any]:
    """`market_sentiment_daily` 行 → 趋势序列行（**直读属性**：列改名即 AttributeError）。

    只投影 brief 6a 列出的 7 个字段 + `promo_1to2_n`（分位的有效样本判据，见
    `_PERCENTILE_VALIDITY`）。缺值恒 `None`（前端 `--`），**绝不** 0。
    """
    return {
        "trade_date": _as_date(row.trade_date),
        "zt_count": row.zt_count,
        "dt_count": row.dt_count,
        "zb_count": row.zb_count,
        "broken_rate": row.broken_rate,
        "promo_1to2": row.promo_1to2,
        "promo_1to2_n": row.promo_1to2_n,
        "max_streak": row.max_streak,
    }


def _as_date(value: Any) -> date:
    """日期归一：`date` 原样、ISO 字符串解析（缓存 JSON 回读会变成 str）。

    与 `persist_snapshot` 里 `date.fromisoformat(str(...))` 是同一坑的两处防御：
    Redis 回读把 `date` 变 `str`，不归一就会在 `toordinal`/比较上炸。
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def percentile_from_series(series: list[dict[str, Any]], field: str) -> dict[str, Any] | None:
    """单指标的历史分位（**nearest-rank**，只用有效样本）。

    - 定义：升序样本第 `ceil(pct/100 × n)` 个（`pct=0` → 第 1 个 = 最小值，
      `pct=100` → 第 n 个 = 最大值）→ **端点值 0/100 可达且正确**，全程无插值。
    - **有效样本**：该字段非 `None` 才计入（NULL 的日子既不进样本也不计 0）；
      `promo_1to2` 还要求 `promo_1to2_n > 0`（分母为 0 时比值无意义）。
    - 样本为 0 → 返回 `None`（**不是**一个全 0 的分位对象）。
    """
    values: list[float] = []
    stamps: list[date] = []
    flag_field = "promo_1to2_n" if field == "promo_1to2" else None
    for row in series:
        value = row.get(_PERCENTILE_VALIDITY[field])
        if value is None:
            continue
        if flag_field is not None and not row.get(flag_field):
            continue
        values.append(float(value))
        stamps.append(row["trade_date"])
    if not values:
        return None

    values.sort()
    n = len(values)

    def at(pct: int) -> float:
        rank = max(1, math.ceil(pct / 100 * n))
        return values[rank - 1]

    return {
        "field": field,
        "sample_count": n,
        "from": stamps[0],
        "to": stamps[-1],
        **{f"p{pct}": at(pct) for pct in _PERCENTILE_POINTS},
    }


def zt_above_median_days(series: list[dict[str, Any]]) -> int:
    """`zt_count` **严格大于**窗口中位数的天数（中位数为偶数样本的均值）。"""
    values = [float(r["zt_count"]) for r in series if r.get("zt_count") is not None]
    if not values:
        return 0
    median = statistics.median(values)
    return sum(1 for v in values if v > median)


def sentiment_trends_payload(
    rows: list[Any],
    *,
    as_of: date | None,
    as_of_quality: str,
    days: int,
) -> dict[str, Any]:
    """组装 6a 响应（**纯函数**，无 IO —— 缓存由 :func:`get_sentiment_trends` 负责）。

    **行序 = 交易日升序**（repo 保证，这里再排一次以便缓存 JSON 回读后仍然稳定）。
    `insufficient_history = samples < MIN_PERCENTILE_SAMPLES`（Ruling 4）→
    `percentile=None`（不是空 dict、更不是 0 分位）。`days` 原样带回请求值；
    `window.days` 是**请求**的交易日数，`window.samples` 才是实际纳入的行数。
    **不碰 Redis**：缓存（含"空结果不缓存"）全部由 :func:`get_sentiment_trends` 负责。
    """
    series = [sentiment_series_row(r) for r in rows]
    series.sort(key=lambda r: r["trade_date"])
    samples = len(series)
    insufficient = samples < MIN_PERCENTILE_SAMPLES

    return {
        "as_of": as_of,
        "as_of_quality": as_of_quality,
        "window": {
            "days": days,
            "from": series[0]["trade_date"] if series else None,
            "to": series[-1]["trade_date"] if series else None,
            "samples": samples,
        },
        "series": series,
        "percentile": (
            None
            if insufficient
            else {
                f: p
                for f in _PERCENTILE_VALIDITY
                if (p := percentile_from_series(series, f)) is not None
            }
        ),
        "streak": {"zt_above_median_days": zt_above_median_days(series)},
        "insufficient_history": insufficient,
    }


async def get_sentiment_trends(cache: CacheClient | None, days: int = 250) -> dict[str, Any]:
    """`GET /market/sentiment/trends` 的编排（读时计算 + Redis 缓存，**不物化**）。

    - `as_of` = `market_sentiment_daily` 的 `max(trade_date)`：这是**派生表的最近日**，
      不是"今天"（表滞后时如实陈旧，与 `/market/sector-moneyflow` 同款约定）；
    - `as_of_quality` 复用 `market_day_service.resolve_latest_complete_day` 的完整性判据
      （完整/回落/部分），避免这里发明第二套"这天数据够不够"；
    - 缓存键含 `days`（改变结果集的唯一维度）；**空结果不缓存**（空图不得被冻结）。
    """
    from app.services import market_day_service  # noqa: PLC0415

    key = _TRENDS_CACHE_KEY.format(days=days)
    if cache is not None:
        cached: dict[str, Any] | None = await cache.get(key)
        if cached:
            return cached

    async with async_session_factory() as db:
        as_of = await limit_up_repo.latest_sentiment_day(db)
        rows = await limit_up_repo.list_sentiment_days(db, days)
        market = await market_day_service.resolve_latest_complete_day(db)
    quality = market.quality if market is not None else "partial"

    payload = sentiment_trends_payload(rows, as_of=as_of, as_of_quality=quality, days=days)
    if cache is not None and payload["window"]["samples"]:
        await cache.set(key, payload, ttl=TRENDS_TTL)
    return payload
