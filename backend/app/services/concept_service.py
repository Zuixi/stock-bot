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
   但空列表意味着整轮静默落空，故单独打 WARNING 让它可见。
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

from app.repositories import concept_repo, limit_up_repo, market_data_repo
from app.services import limit_up_service, market_service

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
HOT_BOARD_LIMIT = 10  # 与 market_service 行业分支的 LIMIT 10 对齐（卡片再按 |涨幅| 切 top6）


def _degraded_reason(as_of: date | None, membership_as_of: date | None) -> str | None:
    """§2.3 降级词表：无行情 → `no_quotes`；无成分 → `no_members`；有数据必须是 None。"""
    if as_of is None:
        return "no_quotes"
    if membership_as_of is None:
        return "no_members"
    return None


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
    """
    key = CONCEPT_LIST_CACHE_KEY.format(sort=sort, limit=limit, offset=offset)
    if cache is not None:
        cached: dict[str, Any] | None = await cache.get(key)
        if cached:
            return cached

    as_of = await limit_up_repo.latest_quote_date(db)
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


async def hot_board_rows(
    cache: CacheClient | None, limit: int = HOT_BOARD_LIMIT
) -> list[dict[str, Any]]:
    """T7 委托入口：概念分类的热门板块行，形状与 `market_service.get_hot_boards` 的行业分支
    **逐键一致**（`id/name/code/changePercent/upCount/flatCount/downCount/leaders`）。

    自开 `async_session_factory()` 会话：`get_hot_boards(category, cache)` 没有 `db` 参数
    （与 `market_data_service.get_sector_moneyflow` 同款）。`id` 用 `concept-{board_code}`
    前缀，避免与行业（`industry-{名}`）/地域（`region-{名}`）的 id 撞车。
    """
    from app.core.database import async_session_factory  # noqa: PLC0415

    async with async_session_factory() as db:
        body = await list_boards(db, cache, sort="pct", limit=limit, offset=0)

    rows: list[dict[str, Any]] = []
    for item in body["items"]:
        rows.append(
            {
                "id": f"concept-{item['board_code']}",
                "name": item["board_name"],
                "code": item["board_code"],
                # 行业分支同款 round(avg or 0, 2)：卡片契约是 number，缺均价渲染 0.00 而不是崩
                "changePercent": round(float(item["avg_pct"] or 0), 2),
                "upCount": int(item["up_count"]),
                "flatCount": int(item["flat_count"]),
                "downCount": int(item["down_count"]),
                # 前端 HotBoardLeader.changePercent 是 number 并直接 `.toFixed(2)`：
                # 无行情成分（change_percent=None）不得作为领涨股下发，否则卡片 TypeError。
                "leaders": [
                    {
                        "symbol": leader["symbol"],
                        "name": leader["name"],
                        "changePercent": leader["change_percent"],
                    }
                    for leader in item["leaders"]
                    if leader["change_percent"] is not None
                ],
            }
        )
    return rows


# ── 读路径：GET /concepts/{board_code} + /stocks + /by-symbol（T8/T9，plans §2.2） ──────
# 全库聚合一次的上限：dev 库 504 板，1k 留 2x 头寸。详情/反向查都在 Python 侧按 board_code
# 过滤这份结果，绝不另写一条"单板聚合 SQL"（两份 SQL 会各自漂移，且与 avg_pct 的 NULLS LAST
# 顺序契约脱钩）。排序仍由 `aggregate_boards` 的 `avg_pct DESC NULLS LAST, board_code ASC` 保证。
CONCEPT_AGG_LIMIT = 1000
CONCEPT_BY_SYMBOL_CACHE_KEY = "market:concept:by-symbol:{symbol}"
CONCEPT_BY_SYMBOL_TTL = 300


def _stock_streak(stock: dict[str, Any]) -> int:
    """梯队票的连板数：正式快照用 `streak`，原始 ladder 行用 `streak_upto`（两处都收）。

    服务层投影（`get_snapshot`）已把 `streak_upto` 规范成 `streak`；保留回退只是为了让
    纯函数测试可以直接喂 `calc.ladder` 的原始形状，而不是再造一套夹具。
    """
    value = stock.get("streak")
    if value is None:
        value = stock.get("streak_upto")
    return int(value or 0)


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
    """板内连板 KPI（**纯函数**）：`zt_count` / `max_streak` / 龙头 `(-streak, -amount, symbol)`。

    龙头裁决必须确定性（与 `calc.sector_ladder` 同一个 key）：`min` 在并列时靠 symbol 兜底，
    否则同一请求两次的"龙头"会跟着行序漂移。
    """
    rows = [s for echelon in echelons for s in echelon.get("stocks", [])]
    if not rows:
        return {"zt_count": 0, "max_streak": 0, "leader_symbol": None, "leader_name": None}
    leader = min(
        rows,
        key=lambda s: (
            -_stock_streak(s),
            -(float(s.get("amount") or 0.0)),
            str(s.get("symbol")),
        ),
    )
    return {
        "zt_count": len(rows),
        "max_streak": max(_stock_streak(s) for s in rows),
        "leader_symbol": str(leader.get("symbol")),
        "leader_name": leader.get("name"),
    }


async def get_board_detail(
    db: AsyncSession, board_code: str, cache: CacheClient | None
) -> dict[str, Any] | None:
    """板块详情（§2.2）：单板 `BoardItem` + 板内梯队 + KPI；未知板块码 → `None`（端点 404）。

    板内梯队**同源于** `/market/limit-up` 的快照（`limit_up_service.get_snapshot`，模块属性
    调用以便测试 monkeypatch），只按成分过滤，不重算 streak——板内口径与全市场梯队必须永远一致。

    `membership_as_of` 用**该板**的 `max(last_seen_on)`，不是 T6 列表的全表值。降级词表：
    无成分 → `no_members`；快照 `limits_present is False` → `price_limits_missing`（照抄情绪
    口径，不自立门户）；否则 `None`（有梯队但为空是"今日板内无涨停"，不是降级）。
    """
    board_meta = await concept_repo.find_board(db, board_code)
    if board_meta is None:
        return None
    members = await concept_repo.list_member_symbols(db, board_code)
    member_symbols = {symbol for symbol, _ in members}
    membership_as_of = await concept_repo.board_membership_date(db, board_code)
    as_of = await limit_up_repo.latest_quote_date(db)

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
    if not members:
        degraded_reason: str | None = "no_members"
    elif snap.get("limits_present") is False:
        degraded_reason = "price_limits_missing"
    else:
        degraded_reason = None

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

    未知 symbol → `items: []`（前端据此整块不渲染），**不是 404**。板块涨跌幅复用同一次
    `aggregate_boards` 全库聚合再按码过滤，不另写 SQL。Redis 300s：该查询在每次个股详情页
    都会触发，且空结果不缓存（"数据不完整时宁可不缓存"，与 T6 列表同一条原则）。
    """
    key = CONCEPT_BY_SYMBOL_CACHE_KEY.format(symbol=symbol)
    if cache is not None:
        cached: dict[str, Any] | None = await cache.get(key)
        if cached:
            return cached

    as_of = await limit_up_repo.latest_quote_date(db)
    membership_as_of = await concept_repo.latest_membership_date(db)
    items: list[dict[str, Any]] = []
    codes = await concept_repo.list_symbol_board_codes(db, symbol)
    if codes and as_of is not None:
        target = set(codes)
        rows = await concept_repo.aggregate_boards(db, as_of, CONCEPT_AGG_LIMIT, 0)
        items = [
            {
                "board_code": r["board_code"],
                "board_name": r["board_name"],
                "pct_change": r["avg_pct"],
            }
            for r in rows
            if r["board_code"] in target
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
