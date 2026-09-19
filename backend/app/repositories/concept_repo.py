"""概念板块与成分股数据访问：成分差分语义 + 本地聚合 + 次新股统计。

口径权威见 plans/2026-09-18-concept-boards-and-new-stocks.md §2.1/§2.3。三条设计约束：

1. **失败隔离优先于紧凑**：成分差分把"本轮抓到的 symbol 集合"与"库内当前态"对比，
   `seen` 为空一律判 `degraded`（调用方跳过整板），任何抓取失败都不会被写成"全成分剔除"。
2. **业务键是 `symbol`**：`concept_members.stock_id` 只是可空解析列（`stocks` 名录会滞后），
   差分、聚合计数都不得以 `stock_id` 是否为键，`unresolved_count` 就是它 NULL 的暴露面。
3. **读路径本地聚合**：板块涨跌/家数从 `concept_members × daily_quotes` 现算（消除东财
   Top100 偏样本），资金流来自既有东财快照，两源在 service 层分字段返回、禁止混算。

本模块函数都在 T4 测试里被 monkeypatch（`concept_repo.<fn>`），故不做 `from … import fn`
重绑定，保持模块属性可达。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, cast

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.concept import ConceptBoard, ConceptMember, ConceptMemberChange


@dataclass(frozen=True)
class MemberDiff:
    """一次成分差分的结果：新增行 / 消失 symbol / 留存 symbol / 是否退化。"""

    added: list[dict[str, str]]
    removed: list[str]
    kept: list[str]
    degraded: bool = False


def diff_members(existing: dict[str, str], seen: dict[str, str], today: date) -> MemberDiff:
    """对比库内当前态与本轮抓取结果，产出落库计划（**纯函数**，无 DB/IO）。

    - `added`：`seen - existing`，形如 `{"symbol", "stock_name"}`（名字取本轮抓到的值）。
    - `removed`：`existing - seen`。
    - `kept`：交集，调用方只刷新 `last_seen_on`/`stock_name`/`stock_id`，**不写 change**。
    - `degraded`：`seen` 为空 → 真。空页/抓取失败绝不能表现为"成分清空"，调用方据此跳过整板。

    三个列表都按 symbol 升序，保证落库与测试可复现。`today` 由调用方持有（差分本身不用日期，
    保留参数是为了与 `upsert_members` 同一调用口径，避免两处各算一份"今天"）。
    """
    added = [
        {"symbol": symbol, "stock_name": name}
        for symbol, name in sorted(seen.items())
        if symbol not in existing
    ]
    removed = sorted(symbol for symbol in existing if symbol not in seen)
    kept = sorted(symbol for symbol in seen if symbol in existing)
    return MemberDiff(added=added, removed=removed, kept=kept, degraded=not seen)


async def upsert_boards(db: AsyncSession, rows: list[dict[str, Any]], today: date) -> int:
    """概念板块名录幂等 upsert（冲突刷 name/member_count/last_seen_at，复活 is_active）。

    `member_count` 是采集时刻与东财 total 的**对账值**，不是 API 口径（API 的 member_count
    一律取本地 `concept_members` 行数）。`today` 保留给调用方对齐日期口径；本表用
    `now()` 记 `last_seen_at`（timestamptz，date 会丢时刻）。
    """
    if not rows:
        return 0
    deduped = list({r["board_code"]: r for r in rows}.values())
    values = [
        {
            "board_code": r["board_code"],
            "board_name": r["board_name"],
            "source": r.get("source") or "em_clist",
            "member_count": r.get("member_total"),
        }
        for r in deduped
    ]
    stmt = (
        pg_insert(ConceptBoard)
        .values(values)
        .on_conflict_do_update(
            constraint="uq_concept_boards_code",
            set_={
                "board_name": pg_insert(ConceptBoard).excluded.board_name,
                "member_count": pg_insert(ConceptBoard).excluded.member_count,
                "last_seen_at": func.now(),
                "is_active": True,
            },
        )
    )
    result = cast("CursorResult[Any]", await db.execute(stmt))
    await db.flush()
    return int(result.rowcount)


async def deactivate_missing_boards(db: AsyncSession, codes: set[str]) -> int:
    """本轮列表未出现的板块置 `is_active=false`（**保留成分**，不参与后续 diff）。

    **不变量：`codes` 为空一律 no-op（返回 0，一行不改）** —— 空集合只可能来自"板块列表
    抓取失败/空页"，绝不能解读成"全部下架"：停用板会从 `aggregate_boards` 的
    `WHERE b.is_active` 消失，这条路径没有任何回滚手段（旧代码会把线上全部板块停用）。
    调用方若确实要停用全部，必须显式传在册 code 全集。与 `diff_members` 对空 `seen` 判
    `degraded` 是同一条失败隔离原则。
    """
    if not codes:
        # 空集合 = 板块列表抓取失败，绝不能当作"全部下架"
        return 0
    result = cast(
        "CursorResult[Any]",
        await db.execute(
            update(ConceptBoard)
            .where(ConceptBoard.is_active.is_(True), ConceptBoard.board_code.not_in(codes))
            .values(is_active=False)
        ),
    )
    await db.flush()
    return int(result.rowcount)


async def list_member_symbols(db: AsyncSession, board_code: str) -> list[tuple[str, str]]:
    """板块当前成分的 (symbol, stock_name)，按 symbol 升序（走唯一键前缀 board_code）。"""
    rows = (
        await db.execute(
            select(ConceptMember.symbol, ConceptMember.stock_name)
            .where(ConceptMember.board_code == board_code)
            .order_by(ConceptMember.symbol)
        )
    ).all()
    return [(symbol, name) for symbol, name in rows]


async def symbol_to_stock_ids(db: AsyncSession, symbols: list[str]) -> dict[str, int]:
    """symbol → stocks.id 一条查询解析（**不做交易所推断**：stocks 名录里没有就是没收录）。

    同名多行（理论上不该有，`uq_stocks_exchange_symbol` 只保证 exchange+symbol 唯一）取最小
    id，**不抛错**——采集路径上宁可返回一个确定性结果，也不要因为脏数据整板失败。
    """
    if not symbols:
        return {}
    rows = (
        await db.execute(
            text("SELECT symbol, id FROM stocks WHERE symbol = ANY(:symbols) ORDER BY id"),
            {"symbols": sorted(set(symbols))},
        )
    ).all()
    ids: dict[str, int] = {}
    for symbol, stock_id in rows:  # 已按 id 升序 → setdefault 天然保留最小 id
        ids.setdefault(symbol, int(stock_id))
    return ids


async def record_changes(db: AsyncSession, observed_on: date, changes: list[dict[str, Any]]) -> int:
    """追加成分变更行（永不更新）；唯一键 (observed_on, board_code, symbol, change_type)。

    冲突 DO NOTHING：同日重跑（补跑、单板重试）不得因为"当天已记过"而整批失败。
    """
    if not changes:
        return 0
    deduped = list({(c["board_code"], c["symbol"], c["change_type"]): c for c in changes}.values())
    values = [
        {
            "observed_on": observed_on,
            "board_code": c["board_code"],
            "symbol": c["symbol"],
            "stock_name": c.get("stock_name"),
            "change_type": c["change_type"],
        }
        for c in deduped
    ]
    stmt = (
        pg_insert(ConceptMemberChange)
        .values(values)
        .on_conflict_do_nothing(constraint="uq_concept_change_row")
    )
    result = cast("CursorResult[Any]", await db.execute(stmt))
    await db.flush()
    return int(result.rowcount)


# 留存行必须逐行刷新 stock_name/stock_id——单条 UPDATE 无法为多行设不同的值，
# 故用 `FROM (VALUES …)` 一次刷完一块板（逐 symbol 发 UPDATE 会让全量采集变成 5 万条语句）。
_KEPT_UPDATE_TMPL = """
UPDATE concept_members cm
SET last_seen_on = :today,
    stock_name = v.stock_name,
    stock_id = v.stock_id
FROM (VALUES {rows}) AS v(symbol, stock_name, stock_id)
WHERE cm.board_code = :board_code AND cm.symbol = v.symbol
"""


def _kept_update_sql(count: int) -> str:
    """VALUES 里显式 CAST：text() 绑定没有列类型信息，asyncpg 无法为 NULL 推断 int。"""
    rows = ", ".join(
        f"(CAST(:s{i} AS varchar), CAST(:n{i} AS varchar), CAST(:d{i} AS integer))"
        for i in range(count)
    )
    return _KEPT_UPDATE_TMPL.format(rows=rows)


async def upsert_members(
    db: AsyncSession,
    board_code: str,
    rows: list[dict[str, Any]],
    today: date,
) -> tuple[int, int, int]:
    """整块板的成分应用（读当前态 → 差分 → 增/删/刷 → 记 change），返回 (added, updated, removed)。

    T4 对每个**抓取成功**的板块恰调一次；单板失败由调用方 try/except 跳过，不进来。
    `rows` 取客户端形状 `{symbol, name}` 或已归一化的 `{symbol, stock_name}`——两种都收，
    免得采集层做一次无意义的字典改写。

    `seen` 为空 → `degraded`，**不写任何行**并返回 `(0, 0, 0)`（失败 ≠ 成分清空）。
    计数来自各自语句的 `rowcount`：`updated` 是实际被刷新的留存行数。
    """
    seen: dict[str, str] = {}
    for row in rows:
        name = row.get("stock_name") or row.get("name")
        if not row.get("symbol") or not name:
            continue  # 缺 symbol/名字的脏行跳过：非空列会让整板 INSERT 失败
        seen[str(row["symbol"])] = str(name)

    existing = dict(await list_member_symbols(db, board_code))
    plan = diff_members(existing, seen, today)
    if plan.degraded:
        return (0, 0, 0)

    ids = await symbol_to_stock_ids(db, list(seen))

    added = 0
    if plan.added:
        stmt = (
            pg_insert(ConceptMember)
            .values(
                [
                    {
                        "board_code": board_code,
                        "symbol": item["symbol"],
                        "stock_name": item["stock_name"],
                        "stock_id": ids.get(item["symbol"]),
                        "first_seen_on": today,
                        "last_seen_on": today,
                    }
                    for item in plan.added
                ]
            )
            .on_conflict_do_nothing(constraint="uq_concept_member_board_symbol")
        )
        added = int(cast("CursorResult[Any]", await db.execute(stmt)).rowcount)

    removed = 0
    if plan.removed:
        result = await db.execute(
            delete(ConceptMember).where(
                ConceptMember.board_code == board_code,
                ConceptMember.symbol.in_(plan.removed),
            )
        )
        removed = int(cast("CursorResult[Any]", result).rowcount)

    updated = 0
    if plan.kept:
        params: dict[str, Any] = {"today": today, "board_code": board_code}
        for i, symbol in enumerate(plan.kept):
            params[f"s{i}"] = symbol
            params[f"n{i}"] = seen[symbol]
            params[f"d{i}"] = ids.get(symbol)
        result = await db.execute(text(_kept_update_sql(len(plan.kept))), params)
        updated = int(cast("CursorResult[Any]", result).rowcount)

    await record_changes(
        db,
        today,
        [
            {
                "board_code": board_code,
                "symbol": item["symbol"],
                "stock_name": item["stock_name"],
                "change_type": "add",
            }
            for item in plan.added
        ]
        + [
            {
                "board_code": board_code,
                "symbol": symbol,
                "stock_name": existing[symbol],
                "change_type": "remove",
            }
            for symbol in plan.removed
        ],
    )
    await db.flush()
    return (added, updated, removed)


# 聚合形状即性能契约（口径见 plans §2.3）。4.37M 行的 daily_quotes 必须按 :as_of 单日收敛
# （任何访问都要带 trade_date 条件），成分行数与版块 code 是确定性的 tiebreak 兜底：
# 没有 board_code ASC 时同一 avg_pct 的板块顺序随计划变化，前端分页会看到重复/漏项。
# 计数口径：member_count 全部成分行；unresolved 只数 stock_id IS NULL；priced 只数非空
# pct_chg（行存在但 pct_chg 为空不算"有行情"）；均值分母只有非空 pct_chg（n≠member_count）。
_AGG_SQL = """
WITH px AS (
    SELECT stock_id, pct_chg FROM daily_quotes WHERE trade_date = :as_of
)
SELECT b.board_code, b.board_name,
       count(*)                                    AS member_count,
       count(*) FILTER (WHERE cm.stock_id IS NULL) AS unresolved_count,
       count(px.pct_chg)                           AS priced_count,
       count(*) FILTER (WHERE px.pct_chg > 0)      AS up_count,
       count(*) FILTER (WHERE px.pct_chg = 0)      AS flat_count,
       count(*) FILTER (WHERE px.pct_chg < 0)      AS down_count,
       avg(px.pct_chg)                             AS avg_pct
FROM concept_boards b
JOIN concept_members cm ON cm.board_code = b.board_code
LEFT JOIN px ON px.stock_id = cm.stock_id
WHERE b.is_active
GROUP BY b.board_code, b.board_name
ORDER BY avg_pct DESC NULLS LAST, b.board_code ASC
LIMIT :limit OFFSET :offset
"""


def build_aggregate_sql_for_explain() -> str:
    """给计划守卫测试用的同源 SQL（避免测试里复制一份会漂移的 SQL 文本）。"""
    return _AGG_SQL


async def aggregate_boards(
    db: AsyncSession, as_of: date, limit: int, offset: int
) -> list[dict[str, Any]]:
    """所有启用板块的当日聚合（`avg_pct DESC NULLS LAST, board_code ASC`）。

    **不接 `sort`**：资金流（东财快照）不在这条 SQL 的数据源里，按资金流排序只能由 service
    在 Python 侧 join 后做；本函数只保证均价口径下的确定性顺序。
    """
    rows = (
        (await db.execute(text(_AGG_SQL), {"as_of": as_of, "limit": limit, "offset": offset}))
        .mappings()
        .all()
    )
    return [
        {
            "board_code": r["board_code"],
            "board_name": r["board_name"],
            "member_count": int(r["member_count"]),
            "unresolved_count": int(r["unresolved_count"]),
            "priced_count": int(r["priced_count"]),
            "up_count": int(r["up_count"]),
            "flat_count": int(r["flat_count"]),
            "down_count": int(r["down_count"]),
            "avg_pct": float(r["avg_pct"]) if r["avg_pct"] is not None else None,
        }
        for r in rows
    ]


# 读路径的三个小查询（T6 list 端点专用；口径见 plans §2.2/§2.3）。
# `latest_membership_date` 必须是**全表** max，而不是活跃板的 max：离线累计的成员行本身就是
# "成分快照日"的证据，跟板块是否仍 active 无关。
_MEMBERSHIP_DATE_SQL = "SELECT max(last_seen_on) FROM concept_members"
_ACTIVE_BOARD_COUNT_SQL = "SELECT count(*) FROM concept_boards WHERE is_active"


async def latest_membership_date(db: AsyncSession) -> date | None:
    """成分快照日 = `max(last_seen_on)`（无成员 → None，service 据此降级 `no_members`）。"""
    return (await db.execute(text(_MEMBERSHIP_DATE_SQL))).scalar_one_or_none()


async def count_active_boards(db: AsyncSession) -> int:
    """启用板块总数（= list 响应的 `total`，与分页无关、不受 items 过滤影响）。"""
    return int((await db.execute(text(_ACTIVE_BOARD_COUNT_SQL))).scalar_one())


# 领涨股必须一次查完整个分页：逐板查会是 N 条 SQL。`row_number()` 而不是 LATERAL
# LIMIT 2 —— 同一 pct_chg 的并列必须由 `symbol ASC` 决定（UI 卡片会在请求间闪名）。
# LEFT JOIN：无行情成分也占位（pct_chg IS NULL，排序 NULLS LAST），与 §2.3 的
# "缺失 ≠ 0" 一致；前端卡片侧另有 null 过滤（见 concept_service.hot_board_rows）。
_LEADERS_SQL = """
WITH ranked AS (
    SELECT cm.board_code, cm.symbol, cm.stock_name, q.pct_chg,
           row_number() OVER (
               PARTITION BY cm.board_code
               ORDER BY q.pct_chg DESC NULLS LAST, cm.symbol ASC
           ) AS rn
    FROM concept_members cm
    LEFT JOIN daily_quotes q ON q.stock_id = cm.stock_id AND q.trade_date = :as_of
    WHERE cm.board_code = ANY(:codes)
)
SELECT board_code, symbol, stock_name, pct_chg
FROM ranked WHERE rn <= 2
ORDER BY board_code, rn
"""


async def board_leaders(
    db: AsyncSession, as_of: date, board_codes: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """分页内每板点击涨幅前 2 成分（`pct_chg DESC NULLS LAST, symbol ASC` 的确定性取前二）。

    返回 `{board_code: [{"symbol","name","change_percent"}, ...]}`；空输入不发 SQL。
    """
    if not board_codes:
        return {}
    rows = (
        (await db.execute(text(_LEADERS_SQL), {"as_of": as_of, "codes": list(board_codes)}))
        .mappings()
        .all()
    )
    leaders: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        leaders.setdefault(str(r["board_code"]), []).append(
            {
                "symbol": str(r["symbol"]),
                "name": str(r["stock_name"]),
                "change_percent": float(r["pct_chg"]) if r["pct_chg"] is not None else None,
            }
        )
    return leaders


# T10 次新股口径（plans §2.3）：listed_trade_days 只数"有行情"的交易日（停牌不计）；
# never_broken 是"上市以来每一行都涨停"，任一行缺限价（limits_missing）则不可判 → NULL，
# **绝不能写成 false**（false 会成为"已开板"的假信号）；first_open 取首日 open。
_HISTORY_SQL = """
WITH h AS (
    SELECT cm.stock_id, cm.symbol, q.trade_date, q.open,
           (l.up_limit IS NOT NULL AND q.close >= l.up_limit - 0.005) AS is_lu,
           (l.up_limit IS NULL) AS limits_missing
    FROM concept_members cm
    JOIN daily_quotes q ON q.stock_id = cm.stock_id
    LEFT JOIN stock_price_limits l ON l.stock_id = q.stock_id AND l.trade_date = q.trade_date
    WHERE cm.board_code = :board_code AND cm.stock_id IS NOT NULL
)
SELECT symbol, stock_id, count(*) AS listed_trade_days,
       CASE WHEN bool_or(limits_missing) THEN NULL ELSE bool_and(is_lu) END AS never_broken,
       (array_agg(open ORDER BY trade_date))[1] AS first_open
FROM h GROUP BY symbol, stock_id
"""


async def member_history_stats(db: AsyncSession, board_code: str) -> dict[str, dict[str, Any]]:
    """某板块每只成分的上市以来统计：`listed_trade_days` / `never_broken` / `first_open`。

    `never_broken` 为 `None` 表示限价缺失、不可判（UI 渲染 `--`），与 `False`（已开板）严格区分。
    """
    rows = (await db.execute(text(_HISTORY_SQL), {"board_code": board_code})).mappings().all()
    return {
        str(r["symbol"]): {
            "listed_trade_days": int(r["listed_trade_days"]),
            "never_broken": None if r["never_broken"] is None else bool(r["never_broken"]),
            "first_open": float(r["first_open"]) if r["first_open"] is not None else None,
        }
        for r in rows
    }


# ── 单板读路径的小查询（T8 详情 / T9 by-symbol 专用，口径见 plans §2.2/§2.3） ────────────
_BOARD_MEMBERSHIP_DATE_SQL = (
    "SELECT max(last_seen_on) FROM concept_members WHERE board_code = :board_code"
)
_BOARD_ROW_SQL = (
    "SELECT board_code, board_name, is_active FROM concept_boards WHERE board_code = :board_code"
)


async def board_membership_date(db: AsyncSession, board_code: str) -> date | None:
    """**单板**成分快照日 = 该板 `max(last_seen_on)`（无成分行 → None）。

    与 `latest_membership_date`（全表 max）严格区分：详情页头部展示的是"这一板的成分截至"，
    用全表值会让一个当天没刷到的板块显示别的板块的日期（口径漂移最隐蔽的一种）。
    """
    return (
        await db.execute(text(_BOARD_MEMBERSHIP_DATE_SQL), {"board_code": board_code})
    ).scalar_one_or_none()


async def find_board(db: AsyncSession, board_code: str) -> dict[str, Any] | None:
    """单板名录行（含停用板）→ 区分 **404（码不存在）** 与 `no_members`（板在、无成分）。

    必须直接查 `concept_boards`：`aggregate_boards` 是 `JOIN concept_members`，空成分板和未知
    板块码在它眼里完全一样，而两者响应必须不同（一个空态、一个 404）。
    """
    row = (await db.execute(text(_BOARD_ROW_SQL), {"board_code": board_code})).mappings().first()
    if row is None:
        return None
    return {
        "board_code": str(row["board_code"]),
        "board_name": str(row["board_name"]),
        "is_active": bool(row["is_active"]),
    }
