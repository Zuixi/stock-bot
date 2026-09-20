"""概念板块成分采集：东财全量板块 → 逐板成分 → 当前态 + 差分变更。

口径见 plans/2026-09-18-concept-boards-and-new-stocks.md §4；三条不可退让的约束：

1. **完整性门禁先于写入**：单板先剔脏行（缺 symbol/name）再用**清洗后**的行数与
   `board["member_total"]` 对账；`len(valid) < member_total` 或出现任何脏行时判该板 partial，
   **不调 `upsert_members`** —— 差分只看"本轮 seen 集合 vs 库内当前态"，少一页/少一行会被解读成
   "这些成分退出了板块"，从而删成员 + 写入幻影 remove 变更行（append-only，无法回滚）。T3 的空
   `seen` 守卫只在整板 0 行时兜底，抓 61/63 或 62 valid + 1 脏行这种"半截/缩水集合"它看不出来。
   `member_total is None` 无法判完整性：仍采集（客户端空页护栏已跑过），但计入 `partial_boards`
   让"无法对账"可见而非静默。
2. **单板失败隔离**：任一客户端/仓库异常只计入 `failed_boards` 并跳过该板，绝不删除既有成分、
   绝不写变更行。板块列表本身抓取失败则向上抛（T5 的调度任务负责记录）；列表为空同样不会造成
   "全部下架"——T3 的 `deactivate_missing_boards` 对空 set 是文档化 no-op（见其 docstring），
   但空列表意味着整轮静默落空，故单独打 WARNING 让它可见。列表**非空但短于库内启用板块数**
   （分页被服务端截断）时同样跳过 `deactivate_missing_boards`：短列表不是"本轮在册全集"，
   照常停用会把未出现在列表里的板块整批下架（静默、无回滚），故与列表失败同一条失败隔离
   原则（I1）。
3. **一次映射、不做交易所推断**：全 run 只用一条 `symbol_to_stock_ids` 建 `symbol → stocks.id`
   映射（`stocks` 名录会滞后），未命中的成员 `stock_id` 留 NULL 并计入 `unresolved`
   （见 docs/references/best-practices.md「源表被别的管道当映射表」条）。
4. **本 run 行不带 `stock_id`**：`concept_repo.upsert_members` 内部自行 `symbol_to_stock_ids`
   再解析一次（T3 权威），故服务层预解析并塞回行内是死代码，已删除；这里保留的这一次映射
   只服务 `unresolved` 指标。重复的点查走 `idx_stocks_symbol`，5 分钟节流下可接受
   （T3 变更，不在 T4 范围内）。

本模块的 repo 函数一律以模块属性调用（`concept_repo.<fn>`），T4 测试据此 monkeypatch。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from app.repositories import concept_repo, market_data_repo
from app.services import limit_up_service, market_day_service, market_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.redis import CacheClient

logger = logging.getLogger(__name__)

_SH = ZoneInfo("Asia/Shanghai")


def _today_sh() -> date:
    """与全系统一致的上海日期（容器 TZ 未设，直接 fromtimestamp 会落 UTC 语义）。"""
    return datetime.now(_SH).date()


def _get_eastmoney() -> Any:
    from app.core.providers.eastmoney_client import get_eastmoney_client  # noqa: PLC0415

    return get_eastmoney_client()


def _valid_members(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """成分行归一为 `{symbol, stock_name}`；缺 symbol/name 的脏行丢弃。

    非空列会让整板 INSERT 失败，故脏行一律不进 `upsert_members`。

    丢弃的行不进 `unresolved`：那是"名录滞后"的度量，不是"源数据脏"的度量。它们单独计入
    返回值的 `dropped_members`，并且**整板**会被判 partial 跳过（见 `ingest_concept_members`）。
    """
    return [
        {"symbol": str(m["symbol"]), "stock_name": str(m["name"])}
        for m in raw
        if m.get("symbol") and m.get("name")
    ]


async def ingest_concept_members(db: AsyncSession) -> dict[str, int]:
    """东财概念板块名录 + 全量成分 → `concept_boards` / `concept_members` / 变更表（不 commit）。

    返回 `{boards, members_upserted, added, removed, failed_boards, partial_boards, unresolved,
    dropped_members}`：`members_upserted = added + updated`（实际写入的行数，删除不算）；
    `unresolved` 只统计**本轮真正入库的板块**的成员，partial/failed 板未写入故不计入，避免门禁
    拦截把指标虚高；`dropped_members` 是本轮源数据里缺 symbol/name 的脏行数（见 `_valid_members`）。
    调用方（T5 的 job/手动入口）负责 `db.commit()`。
    """
    em = _get_eastmoney()
    today = _today_sh()

    # 列表抓不到 → 直接抛：空列表必须绝不被当成"全部下架"。
    boards = await em.fetch_concept_boards()
    if not boards:
        # 东财可以 rc=0 + 空 data：这不是失败，但等于整轮空转，必须让它可见。
        logger.warning(
            "CONCEPT_INGEST board list empty; nothing ingested "
            "(deactivate_missing_boards is a no-op on empty set)"
        )
    # 停用判据的基线：本轮开始前库内启用板块数。必须在 upsert_boards 之前读——upsert 会把
    # 本轮新板置为 is_active=true，之后再读会把"新板"算进基线，掩盖截断。
    active_boards = await concept_repo.count_active_boards(db)
    await concept_repo.upsert_boards(db, boards, today)

    failed_boards = 0
    partial_boards = 0
    dropped_members = 0
    pending: list[tuple[str, list[dict[str, Any]]]] = []
    for board in boards:
        code = str(board["board_code"])
        try:
            members = await em.fetch_concept_members(code)
        except Exception:
            failed_boards += 1
            logger.warning("concept board %s members fetch failed, skipped", code, exc_info=True)
            continue
        # 脏行先剔除再对账：缺 symbol/name 的行无法 INSERT，且会让 `seen` 少一个仍在该板的成员
        # → 差分把它写成"退出板块"（幻影 remove）。故见脏行即整板 partial 跳过，用清洗后的
        # 行数对账（62 valid + 1 脏 = 63 total 这种"刚好凑数"必须判 partial）。
        valid = _valid_members(members)
        dropped = len(members) - len(valid)
        if dropped:
            dropped_members += dropped
            partial_boards += 1
            logger.warning(
                "concept board %s has %d dirty member row(s) (fetched=%d valid=%d), skipped",
                code,
                dropped,
                len(members),
                len(valid),
            )
            continue
        total = board.get("member_total")
        if total is None:
            # 无法对账：仍入库（客户端空页护栏已跑过），但让"无 total"可见。
            partial_boards += 1
            logger.warning(
                "concept board %s member_total unknown, ingested without completeness check "
                "(fetched=%d)",
                code,
                len(valid),
            )
        elif len(valid) < total:
            # 半截页绝不能进差分：会删成员 + 写幻影 remove。
            partial_boards += 1
            logger.warning(
                "concept board %s incomplete: fetched=%d < member_total=%d, skipped",
                code,
                len(valid),
                total,
            )
            continue
        pending.append((code, valid))

    # 全 run 一次映射（不做交易所推断；stocks 名录滞后由 unresolved 暴露）。
    # `unresolved` 按**行**计（同一未收录 symbol 出现在 N 个板块算 N 次），与
    # `select count(*) from concept_members where stock_id is null` 逐一对账。
    # 不把结果塞回行内：`upsert_members` 会自行再解析（T3 权威），塞了也是死字段。
    symbols = [row["symbol"] for _, rows in pending for row in rows]
    stock_ids = await concept_repo.symbol_to_stock_ids(db, symbols)
    unresolved = sum(1 for symbol in symbols if symbol not in stock_ids)

    added = updated = removed = 0
    for code, rows in pending:
        try:
            board_added, board_updated, board_removed = await concept_repo.upsert_members(
                db, code, rows, today
            )
        except Exception:
            failed_boards += 1
            logger.warning("concept board %s members write failed, skipped", code, exc_info=True)
            continue
        added += board_added
        updated += board_updated
        removed += board_removed

    # 列表短于库内启用板块数 = 抓到的是被截断的"半截全集"（客户端已在 `_paged_clist` 打
    # WARNING）。此时照常 `deactivate_missing_boards` 会把没出现在列表里的活跃板块整批停用，
    # 而停用板会从 `aggregate_boards` / 详情 / by-symbol 全部消失且无回滚手段 —— 跳过停用，
    # 用 WARNING 说明原因（I1：接口应构造安全，而不是把责任推给调用方记得加守卫）。
    if len(boards) < active_boards:
        logger.warning(
            "CONCEPT_INGEST board list shorter than active boards (fetched=%d < active=%d); "
            "skipping deactivate_missing_boards to avoid mass deactivation",
            len(boards),
            active_boards,
        )
    else:
        await concept_repo.deactivate_missing_boards(db, {str(b["board_code"]) for b in boards})

    members_upserted = added + updated
    result = {
        "boards": len(boards),
        "members_upserted": members_upserted,
        "added": added,
        "removed": removed,
        "failed_boards": failed_boards,
        "partial_boards": partial_boards,
        "unresolved": unresolved,
        "dropped_members": dropped_members,
    }
    log_line = (
        "CONCEPT_INGEST boards=%d members=%d added=%d removed=%d failed=%d partial=%d "
        "unresolved=%d dropped=%d"
    )
    logger.info(
        log_line,
        result["boards"],
        members_upserted,
        added,
        removed,
        failed_boards,
        partial_boards,
        unresolved,
        dropped_members,
    )
    if failed_boards or partial_boards or unresolved:
        # "把 skipped 拆出来并升 warning"：只在有退化时再打一条，指向同一组数字。
        logger.warning(
            log_line,
            result["boards"],
            members_upserted,
            added,
            removed,
            failed_boards,
            partial_boards,
            unresolved,
            dropped_members,
        )
    return result


# ── 读路径：GET /api/v1/concepts（T6） ────────────────────────────────────────
# 两源分离（plans §2.2）：涨跌/家数全部本地聚合（`price_source="local_agg"`），资金流来自
# 东财快照（有匹配行才 `flow_source="em_clist"`）。**绝不做 SQL join**：快照一天只覆盖
# 当日主力流入 Top-100 的震荡集，join 要么按 board_code 过滤丢行、要么一对多放大行数；
# Python 侧按 `board_code` 取交集才是正确语义，缺行保持 None（= 缺失，不是 0）。
CONCEPT_LIST_CACHE_KEY = "market:concept:list:{sort}:{limit}:{offset}"
CONCEPT_LIST_TTL = 300
CONCEPT_FLOW_LIMIT = 100  # 与 /market/sector-moneyflow 端点上限（le=100）一致


def _degraded_reason(as_of: date | None, membership_as_of: date | None) -> str | None:
    """§2.3 降级词表：无行情 → `no_quotes`；无成分 → `no_members`；有数据必须是 None。"""
    if as_of is None:
        return "no_quotes"
    if membership_as_of is None:
        return "no_members"
    return None


async def _latest_complete_quote_day(
    db: AsyncSession, cache: CacheClient | None
) -> date | None:
    """概念端点信封的 `as_of`：与 `limit_up_service.get_snapshot` **同一个**最新日判据。

    裸 `max(daily_quotes.trade_date)`（`limit_up_repo.latest_quote_date`）会把脏/半截的
    最新日 D 顶成 `as_of`，而详情里的梯队来自 `get_snapshot`——后者走
    `market_day_service.resolve_latest_complete_day`，脏 D 会被跳过回落到 D-1。结果是同一个
    信封里聚合按 D、梯队按 D-1，还报 `as_of=D` 且无 `degraded_reason`。这里统一成快照的判据
    （模块属性访问，测试可 monkeypatch）；无行情时 `resolve_latest_complete_day` 返回 `None`，
    端点照旧降级 `no_quotes`。
    """
    md = await market_day_service.resolve_latest_complete_day(db, cache=cache)
    return md.day if md is not None else None


def _inflow_sort_key(item: dict[str, Any]) -> tuple[bool, float, str]:
    """`main_net_inflow DESC NULLS LAST, board_code ASC`——缺资金流的排最后，同值按码升序。

    Python 侧排序必须自带 `board_code` tiebreak：`sorted` 稳定但输入顺序来自 avg_pct 分页，
    两个同流入板块的先后会随均价名次漂移，前端翻页就会重复/漏项。
    """
    inflow = item["main_net_inflow"]
    return (inflow is None, -(inflow or 0.0), item["board_code"])


async def _enrich_board_rows(db: AsyncSession, as_of: date, items: list[dict[str, Any]]) -> bool:
    """就地补东财资金流快照 + 领涨股到 `aggregate_boards` 的行上，返回 `bool(flow_map)`。

    T6 列表与 T8 详情共用本函数：字段映射只此一处，两个端点不可能各自漂移。**绝不做 SQL
    join**（plans §2.2）：快照一天只覆盖当日主力流入 Top-100 的震荡集，join 要么过滤丢行、
    要么按 board_code 一对多放大；Python 侧取交集、缺行保持 `None` 才正确。

    `items` 为空时也要查快照：`flow_source` 是**数据集级**声明，offset 越界空页必须与首页
    给出同一个值。领涨股查询对空 `items` 是文档化 no-op（`board_leaders` 自带空输入护栏）。
    """
    flow_map = {
        snap.board_code: snap
        for snap in await market_data_repo.list_sector_moneyflow(
            db, as_of, "concept", CONCEPT_FLOW_LIMIT
        )
    }
    leaders = await concept_repo.board_leaders(db, as_of, [i["board_code"] for i in items])
    for item in items:
        snap = flow_map.get(item["board_code"])
        item.update(
            {
                "main_net_inflow": snap.main_net_inflow if snap else None,
                "main_net_ratio": snap.main_net_ratio if snap else None,
                "lead_stock_name": snap.lead_stock_name if snap else None,
                "lead_stock_code": snap.lead_stock_code if snap else None,
                "lead_stock_pct": snap.lead_stock_pct if snap else None,
                "leaders": leaders.get(item["board_code"], []),
            }
        )
    return bool(flow_map)


async def list_boards(
    db: AsyncSession,
    cache: CacheClient | None,
    sort: str = "pct",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """概念板块列表：`aggregate_boards`（本地聚合） + 东财资金流内存 join + Redis 300s。

    `sort="pct"` 保留 repo 的 `avg_pct DESC NULLS LAST, board_code ASC`；`sort="inflow"` 只
    对**当前分页**重排（repo 没有资金流列，全库按流入排序需要另一条 SQL——本期不做，见 plans
    §2.3）：分页外的板块不参与流入排序，UI 需知这一点。

    缓存**只在 items 非空时写**（"数据不完整时宁可不缓存"）：整页空/降级的响应若被缓存，
    上游数据补齐后还要再等一个 TTL 才可见。

    `as_of` 来自 `_latest_complete_quote_day`（与 `get_snapshot` 同判据）：脏的最新日回落到
    完整日，绝不让"聚合按 D、快照按 D-1"的两套最新日同时出现在一个信封里。
    """
    key = CONCEPT_LIST_CACHE_KEY.format(sort=sort, limit=limit, offset=offset)
    if cache is not None:
        cached: dict[str, Any] | None = await cache.get(key)
        if cached:
            return cached

    as_of = await _latest_complete_quote_day(db, cache)
    membership_as_of = await concept_repo.latest_membership_date(db)
    total = await concept_repo.count_active_boards(db)
    degraded_reason = _degraded_reason(as_of, membership_as_of)

    items: list[dict[str, Any]] = []
    flow_source: str | None = None
    if degraded_reason is None:
        assert as_of is not None  # `_degraded_reason` 已保证；mypy 需要这个收窄
        items = await concept_repo.aggregate_boards(db, as_of, limit, offset)
        # 快照源声明是**数据集级**（与 as_of / total 同一个信封），不是"本页恰好命中几行"：
        # 逐页统计会让同一 as_of 的首页返回 "em_clist"、尾页（甚至 offset 越界空页）返回 null。
        flow_source = "em_clist" if await _enrich_board_rows(db, as_of, items) else None
        if sort == "inflow":
            items.sort(key=_inflow_sort_key)

    body: dict[str, Any] = {
        "as_of": as_of.isoformat() if as_of is not None else None,
        "membership_as_of": membership_as_of.isoformat() if membership_as_of is not None else None,
        "price_source": "local_agg",
        "flow_source": flow_source,
        "total": total,
        "degraded_reason": degraded_reason,
        "items": items,
    }
    if cache is not None and items:
        await cache.set(key, body, ttl=CONCEPT_LIST_TTL)
    return body


# ── 读路径：GET /concepts/{board_code} + /stocks + /by-symbol（T8/T9，plans §2.2） ──────
# 全库聚合一次的上限：dev 库 504 板，1k 留 2x 头寸。详情（已知单板码）用 `aggregate_boards`
# 全库取一份再按码挑行；`by-symbol` 必须走 `aggregate_boards_for_codes`（板码过滤进 SQL）——
# 反向查在 Python 侧从"全库 top-N"里挑会让一个 symbol 的板块在停用/被窗口截断/NULLS LAST 边界
# 处静默消失。两条路径共用 `_AGG_SQL` 同一份聚合（只有可选板码过滤子句不同），绝不另写
# "单板聚合 SQL"。排序仍由 `avg_pct DESC NULLS LAST, board_code ASC` 保证。
CONCEPT_AGG_LIMIT = 1000
CONCEPT_BY_SYMBOL_CACHE_KEY = "market:concept:by-symbol:{symbol}"
CONCEPT_BY_SYMBOL_TTL = 300


def _stock_streak(stock: dict[str, Any]) -> int | None:
    """梯队票的连板数：正式快照的 `streak`（`get_snapshot` 已把 `streak_upto` 规范成它）。

    **缺失返回 `None`，绝不回退成 0**：0 会被读成"未涨停"，而 `None` 是"不可判"（缺失 ≠ 0）。
    原先的 `streak_upto` 回退是生产死分支（`get_snapshot` 的 echelons 一律带 `streak`），
    只会让纯函数测试喂进生产永远看不到的形状，已删（fix round 1 / M2）。
    """
    value = stock.get("streak")
    return int(value) if value is not None else None


def _filter_echelons(
    echelons: list[dict[str, Any]], member_symbols: set[str]
) -> list[dict[str, Any]]:
    """把快照梯队按板内成分过滤（**原样透传**，绝不重算 streak/amount/封板字段）。

    空档整档丢弃（前端 `<LimitUpLadder>` 不接受空 stocks）；有成员的档保持原顺序与形状，
    这样前端可以把它直接交给梯队卡渲染。
    """
    filtered: list[dict[str, Any]] = []
    for echelon in echelons:
        stocks = [s for s in echelon.get("stocks", []) if str(s.get("symbol")) in member_symbols]
        if stocks:
            filtered.append({**echelon, "stocks": stocks})
    return filtered


def _ladder_kpis(echelons: list[dict[str, Any]]) -> dict[str, Any]:
    """板内连板 KPI（**纯函数**）：`zt_count` / `max_streak` / 龙头。

    龙头 = 梯队顺序里**第一个最高板**的行，也就是前端梯队卡的第一行：快照的档位顺序是
    `streak DESC`、档内是 `amount DESC, symbol ASC`（`calc.ladder`），卡片渲染的就是这个顺序。
    "KPI 龙头 == 卡片首行"因此永不漂移，也不需要二次排序。

    **不重排**：快照 echelons 的 stock 字典里没有 `amount`（`limit_up_service` 的 ladder 投影
    只带 `streak/days_span/boards_in_window/missing_days/seal_*`），任何 `(-streak, -amount,
    symbol)` 形式的排序 key 在生产中等价于按 symbol 升序，会选出与卡片首行不同的票（fix
    round 1 / I1）。`max_streak` 只在有 `streak` 的行上取最大；全缺失时保持 0（= 不可判）。
    """
    rows = [s for echelon in echelons for s in echelon.get("stocks", [])]
    if not rows:
        return {"zt_count": 0, "max_streak": 0, "leader_symbol": None, "leader_name": None}
    leader = rows[0]
    max_streak = 0
    for stock in rows:
        streak = _stock_streak(stock)
        if streak is not None and streak > max_streak:
            max_streak = streak
            leader = stock
    return {
        "zt_count": len(rows),
        "max_streak": max_streak,
        "leader_symbol": str(leader.get("symbol")),
        "leader_name": leader.get("name"),
    }


async def get_board_detail(
    db: AsyncSession, board_code: str, cache: CacheClient | None
) -> dict[str, Any] | None:
    """板块详情（§2.2）：单板 `BoardItem` + 板内梯队 + KPI；未知板块码 → `None`（端点 404）。

    板内梯队**同源于** `/market/limit-up` 的快照（`limit_up_service.get_snapshot`，模块属性
    调用以便测试 monkeypatch），只按成分过滤，不重算 streak——板内口径与全市场梯队必须永远一致。

    `as_of` 与下面 `get_snapshot` 用同一个 `_latest_complete_quote_day` 判据（脏的最新日不得让
    板聚合与板内梯队落在两个不同的日子）。`membership_as_of` 用**该板**的 `max(last_seen_on)`，
    不是 T6 列表的全表值。`degraded_reason` 原样透传快照自己的词（`no_quotes` /
    `price_limits_missing` / `partial_day` / `insufficient_trade_days`），不自立门户；例外有两个：
    板内无成分行时 `no_members` 优先，以及 `no_limit_up_rows` **不**透传（市场级"今天没有涨停"
    是合法空态，KPI 诚实报 0）。
    """
    board_meta = await concept_repo.find_board(db, board_code)
    if board_meta is None:
        return None
    members = await concept_repo.list_member_symbols(db, board_code)
    member_symbols = {symbol for symbol, _ in members}
    membership_as_of = await concept_repo.board_membership_date(db, board_code)
    as_of = await _latest_complete_quote_day(db, cache)

    row: dict[str, Any] | None = None
    if as_of is not None and members:
        agg = await concept_repo.aggregate_boards(db, as_of, CONCEPT_AGG_LIMIT, 0)
        row = next((r for r in agg if r["board_code"] == board_code), None)
    if row is None:
        # 无行情 / 停用板 / 聚合未覆盖：价格列留空，计数仍从成分行现算，保持 BoardItem 形状。
        # 未解析数这里用当前 stocks 名录反解（symbol_to_stock_ids）而非 `stock_id IS NULL`：
        # 只在聚合不可用时兜底，正常路径（有行情 + 活跃板）走 aggregate_boards 的精确计数。
        resolved = await concept_repo.symbol_to_stock_ids(db, sorted(member_symbols))
        row = {
            "board_code": board_code,
            "board_name": board_meta["board_name"],
            "member_count": len(members),
            "unresolved_count": len(members) - len(resolved),
            "priced_count": 0,
            "up_count": 0,
            "flat_count": 0,
            "down_count": 0,
            "avg_pct": None,
        }
    if as_of is not None:
        await _enrich_board_rows(db, as_of, [row])

    snap = await limit_up_service.get_snapshot(cache)
    echelons = _filter_echelons(snap.get("echelons") or [], member_symbols)
    kpis = _ladder_kpis(echelons)
    # 降级词表**照抄快照**（`no_quotes` / `price_limits_missing` / `partial_day` /
    # `insufficient_trade_days`）：本端点不重导口径，否则快照说"当前没有行情"，详情页却报
    # "限价缺失"（把两种完全不同的退化来源混成一种）。`no_limit_up_rows` 是唯一例外——市场级
    # "今天没有涨停"是合法的空态，KPI 诚实地报 0，不是概念数据的退化（见 schema 字段注释）。
    # 板内无成分行优先置 `no_members`：那是本端点自己的一级空态。
    if not members:
        degraded_reason: str | None = "no_members"
    else:
        snapshot_reason = snap.get("degraded_reason")
        degraded_reason = (
            str(snapshot_reason)
            if snapshot_reason and snapshot_reason != "no_limit_up_rows"
            else None
        )

    unresolved_count = int(row["unresolved_count"])
    return {
        "as_of": as_of.isoformat() if as_of is not None else None,
        "membership_as_of": (
            membership_as_of.isoformat() if membership_as_of is not None else None
        ),
        "source": "em_clist",
        "degraded_reason": degraded_reason,
        "board": row,
        "kpis": kpis,
        "echelons": echelons,
        "unresolved_count": unresolved_count,
        "stock_count": int(row["member_count"]) - unresolved_count,
    }


def _stock_change_sort_key(row: dict[str, Any]) -> tuple[bool, float, str]:
    """`change_percent DESC NULLS LAST, symbol ASC`——缺行情的成分排最后，同值按码升序。"""
    change = row.get("change_percent")
    return (change is None, -(float(change) if change is not None else 0.0), str(row["symbol"]))


async def get_board_stocks(db: AsyncSession, board_code: str) -> list[dict[str, Any]]:
    """板块成分股，**纯数组**（无 envelope）：前端复用 `mapBackendStockEnriched` 零改映射。

    形状 = `StockEnrichedOut`（`market_service.get_stocks_enriched_by_symbols` 的产物）；
    排序在 Python 侧做——成分最多几千只，不值得再写一条 SQL。未知板块码返回 `[]`（端点也是）。
    """
    members = await concept_repo.list_member_symbols(db, board_code)
    rows = await market_service.get_stocks_enriched_by_symbols(db, [s for s, _ in members])
    items = [row.model_dump() for row in rows]
    items.sort(key=_stock_change_sort_key)
    return items


async def get_concepts_by_symbol(
    db: AsyncSession, symbol: str, cache: CacheClient | None
) -> dict[str, Any]:
    """个股所属概念（触点 C）：items 为 `{board_code, board_name, pct_change}` 列表。

    未知 symbol → `items: []`（前端据此整块不渲染），**不是 404**。板块涨跌幅来自
    `aggregate_boards_for_codes`（与 `aggregate_boards` 共用同一份聚合 SQL，只多一个板码过滤），
    不另写第二条 SQL。Redis 300s：该查询在每次个股详情页都会触发，且空结果不缓存
    （"数据不完整时宁可不缓存"，与 T6 列表同一条原则）。
    """
    key = CONCEPT_BY_SYMBOL_CACHE_KEY.format(symbol=symbol)
    if cache is not None:
        cached: dict[str, Any] | None = await cache.get(key)
        if cached:
            return cached

    as_of = await _latest_complete_quote_day(db, cache)
    membership_as_of = await concept_repo.latest_membership_date(db)
    items: list[dict[str, Any]] = []
    codes = await concept_repo.list_symbol_board_codes(db, symbol)
    if codes and as_of is not None:
        # 板码过滤进 SQL（`aggregate_boards_for_codes`）：全库聚合再 Python 过滤会让该 symbol
        # 的板块在停用/被窗口截断/NULLS LAST 边界处静默消失（fix round 1 / I3）。
        rows = await concept_repo.aggregate_boards_for_codes(db, as_of, codes)
        items = [
            {
                "board_code": r["board_code"],
                "board_name": r["board_name"],
                "pct_change": r["avg_pct"],
            }
            for r in rows
        ]
        items.sort(
            key=lambda i: (
                i["pct_change"] is None,
                -(float(i["pct_change"]) if i["pct_change"] is not None else 0.0),
                i["board_code"],
            )
        )

    body: dict[str, Any] = {
        "as_of": as_of.isoformat() if as_of is not None else None,
        "membership_as_of": (
            membership_as_of.isoformat() if membership_as_of is not None else None
        ),
        "items": items,
    }
    if cache is not None and items:
        await cache.set(key, body, ttl=CONCEPT_BY_SYMBOL_TTL)
    return body
