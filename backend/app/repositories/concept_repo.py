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

from sqlalchemy import Boolean, Integer, String, bindparam, delete, func, select, text, update
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
# 可选板码过滤子句：`aggregate_boards`（全库）与 `aggregate_boards_for_codes`（按码收窄）
# 共用**同一份**聚合 SQL，唯一差异就是这个 AND。绝不另写第二条聚合 SQL——两份 SQL 会各自漂移，
# 且与 `avg_pct DESC NULLS LAST` 的分页顺序契约脱钩。
#
# 形态说明（取代 `(:board_codes IS NULL OR b.board_code = ANY(:board_codes))` 的等价写法）：
# `= ANY(...)` 是 Postgres 专有语法，会让 `tests/test_concept_agg_sql.py`（SQLite 内存库、
# 默认门禁）无法直接跑生产 SQL；`:param IS NULL` 配上 expanding 绑定还会在非空列表时被展开成
# `(?, ?) IS NULL`（SQLite 报 "row value misused"）。故用 `:all_boards OR board_code IN :…`
# ——SQLAlchemy expanding 绑定在 Postgres 与 SQLite 上都把列表展开成逐项占位符，语义完全等价。
_AGG_BOARD_FILTER = "AND (:all_boards OR b.board_code IN :board_codes)"

_AGG_SQL = f"""
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
  {_AGG_BOARD_FILTER}
GROUP BY b.board_code, b.board_name
ORDER BY avg_pct DESC NULLS LAST, b.board_code ASC
LIMIT :limit OFFSET :offset
"""

# 生产语句：expanding 绑定必须钉在 `bindparams` 上（`text()` 默认把列表当单个标量）。
# `type_=String` 不可省：无类型时 SQLAlchemy 把空列表渲染成 `CAST(NULL AS INTEGER)`，
# Postgres 会因 `varchar = integer` 直接报 no operator。
_AGG_STMT = text(_AGG_SQL).bindparams(
    bindparam("board_codes", expanding=True, type_=String),
    bindparam("all_boards", type_=Boolean),
)


def aggregate_params(
    as_of: date, limit: int, offset: int, board_codes: list[str] | None
) -> dict[str, Any]:
    """`_AGG_STMT` 的绑定参数：`board_codes=None` → 不过滤（全库）。

    暴露给 `tests/test_concept_agg_sql.py` 复用，避免测试自造一份会漂移的绑定约定。
    `all_boards` 与列表分开传：空列表 ≠ 不过滤（空列表必须继续匹配 0 行）。
    """
    return {
        "as_of": as_of,
        "limit": limit,
        "offset": offset,
        "board_codes": list(board_codes) if board_codes is not None else [],
        "all_boards": board_codes is None,
    }


def build_aggregate_sql_for_explain() -> str:
    """给计划守卫测试用的同源 SQL（避免测试里复制一份会漂移的 SQL 文本）。

    计划守卫只绑 `:as_of/:limit/:offset`（见 `test_concept_repo`），故这里去掉可选板码过滤
    子句——**不是第二份聚合 SQL**，仍是从 `_AGG_SQL` 同一常量里摘出来的文本。`aggregate_boards`
    传 `all_boards=True` 时 Postgres 的自定义计划同样会把这个恒真 OR 简化掉，计划形状一致。
    """
    return _AGG_SQL.replace(f"\n  {_AGG_BOARD_FILTER}\n", "\n")


def _agg_row_dict(r: Any) -> dict[str, Any]:
    """`_AGG_SQL` 一行的投影（两条调用路径共用，防止字段名/类型在两边漂移）。"""
    return {
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


async def aggregate_boards(
    db: AsyncSession, as_of: date, limit: int, offset: int
) -> list[dict[str, Any]]:
    """所有启用板块的当日聚合（`avg_pct DESC NULLS LAST, board_code ASC`）。

    **不接 `sort`**：资金流（东财快照）不在这条 SQL 的数据源里，按资金流排序只能由 service
    在 Python 侧 join 后做；本函数只保证均价口径下的确定性顺序。
    """
    params = aggregate_params(as_of, limit, offset, None)
    rows = (await db.execute(_AGG_STMT, params)).mappings().all()
    return [_agg_row_dict(r) for r in rows]


async def aggregate_boards_for_codes(
    db: AsyncSession, as_of: date, board_codes: list[str]
) -> list[dict[str, Any]]:
    """**指定板块码**的当日聚合（同一份 `_AGG_SQL`，只是多了板码过滤）。

    `by-symbol` 必须走这条路径：先全库聚合再在 Python 里按码过滤会让一个 symbol 的板块在
    `is_active=false`、活跃板块数 > `limit`、或 NULL `avg_pct` 被 `NULLS LAST` 截断时**静默消失**
    ——这是"查询参数没有进 SQL"的典型症状。板码过滤进 SQL 后，返回集合只由库内数据决定。

    `is_active` 过滤**保留**（与 `aggregate_boards` 一致）：本轮东财列表已下架的板块不给可导航
    链接，而不是让前端点进一个死板。空 `board_codes` → 空列表（空列表 ≠ 不过滤，不发 SQL）。
    """
    if not board_codes:
        return []
    params = aggregate_params(as_of, len(board_codes), 0, board_codes)
    rows = (await db.execute(_AGG_STMT, params)).mappings().all()
    return [_agg_row_dict(r) for r in rows]


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
#
# 哨兵限价行不参与 never_broken（2026-09-18 数据核对发现的**口径 bug**）：新股上市后的前 5
# 个交易日（科创板/创业板/注册制主板）TuShare `stk_limit` 返回"无涨跌幅限制"哨兵，本库实测
# 取值 99999.99 / 99999.999 / 999999.999；该行 close 永远 < 哨兵 → is_lu 恒 false，于是
# "上市以来每一行都涨停"**结构性不可达**。2026-09-18 实测：BK0501 的 162 只成分
# （stock_price_limits 当时覆盖 245 个交易日）里 159 只有可判行，0 只 unbroken，且这 159 只
# 失败**只**因为首日哨兵行（920298 腾信精密：2026-09-16 上市日 up_limit=99999.99 → is_lu
# false；09-17 +8.24% 未涨停；09-18 close == up_limit → is_lu true，涨停判定本身是对的）。
#
# 判据必须是**相对**比较 `l.up_limit < q.close * 10`，绝不能用固定阈值（如 `< 1000`）：
# 真实日涨停幅 ≤ 前收 ×1.31（主板 10% / 创业板·科创板 20% / 上市后 5 日的 30%），故真实限价
# 至多 ≈ 现价 ×1.31，而哨兵恒 ≥ 10× 现价（99999.99 vs 现价 ~64），两者相差一个数量级以上，
# `close × 10` 是天然分界。固定阈值会误伤高价股的真实限价：实测 `688808 联讯仪器`（BK0501
# 成分）**102 行限价全部 > 1000**（前 5 个交易日是哨兵 99999.999；首个真实限价 2026-05-06
# = 1240.00，真实限价区间 1221.60–3240.00，其中 2026-08-04 限价 1948.80 == close 涨停），
# 被 `< 1000` 整体误判为哨兵 → 零个可判行 → never_broken 从 `False`（已开板）错退化为
# `None`（不可判），**丢信号**（同类：贵州茅台 ~1393、中际旭创 ~1075）。
# 故只有 `has_real_limit`（up_limit 非空且 < close × 10）的行才进 bool_and；`listed_trade_days`
# 仍数**全部**行（口径 = 有行情的交易日数，含哨兵日）。
#
# 两条不可混淆的 NULL 语义：
#   1. 缺限价行（LEFT JOIN 未命中 → up_limit IS NULL）仍经 bool_or(limits_missing) 毒化为
#      NULL；新过滤器只排除哨兵行，绝不把"缺限价"偷换成"忽略该行"（缺失 ≠ 0）。
#   2. 一只票**没有任何**真实限价行（如 688837 上市前 5 个交易日全是哨兵）→
#      count(*) FILTER (WHERE has_real_limit) = 0 → NULL（不可判）。绝不能因"零个可判行都
#      涨停"而真值化为 true，更不能写成 false（false 会把"还没开始可判"误报成"已开板"）。
_HISTORY_SQL = """
WITH h AS (
    SELECT cm.stock_id, cm.symbol, q.trade_date, q.open,
           (l.up_limit IS NOT NULL AND q.close >= l.up_limit - 0.005) AS is_lu,
           (l.up_limit IS NULL) AS limits_missing,
           (l.up_limit IS NOT NULL AND l.up_limit < q.close * 10) AS has_real_limit
    FROM concept_members cm
    JOIN daily_quotes q ON q.stock_id = cm.stock_id
    LEFT JOIN stock_price_limits l ON l.stock_id = q.stock_id AND l.trade_date = q.trade_date
    WHERE cm.board_code = :board_code AND cm.stock_id IS NOT NULL
)
SELECT symbol, stock_id, count(*) AS listed_trade_days,
       CASE
           WHEN bool_or(limits_missing) THEN NULL
           WHEN count(*) FILTER (WHERE has_real_limit) = 0 THEN NULL
           ELSE bool_and(is_lu) FILTER (WHERE has_real_limit)
       END AS never_broken,
       (array_agg(open ORDER BY trade_date))[1] AS first_open
FROM h GROUP BY symbol, stock_id
"""


async def member_history_stats(db: AsyncSession, board_code: str) -> dict[str, dict[str, Any]]:
    """某板块每只成分的上市以来统计：`listed_trade_days` / `never_broken` / `first_open`。

    `never_broken` 为 `None` 表示不可判（UI 渲染 `--`）：任一行缺限价，或**没有任何**真实限价行
    （全是哨兵，见 `_HISTORY_SQL` 注释）。`False` 只表示"确有非涨停的真实限价行"（已开板），
    两者严格区分。
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


# T10 KPI 的涨跌幅口径（plans 全局约束 + §2.3）：必须取 `daily_quotes.pct_chg` —— TuShare
# 原生的当日涨跌幅（已含除权口径）。绝不用 enriched 行的 `change_percent`：它是"最新两行
# close 相除"的推导值，既不带 `trade_date = :as_of` 过滤（as_of 停牌的票会被拿陈旧行情分档），
# 也会在除权日与官方 pct_chg 分叉——两套"涨跌幅"会让 KPI 家数与东财对不上。
# 单日 + stock_id 集合，走 daily_quotes 的 (stock_id, trade_date) 唯一索引。
_DAILY_PCT_CHG_SQL = (
    "SELECT stock_id, pct_chg FROM daily_quotes "
    "WHERE trade_date = :as_of AND stock_id IN :stock_ids"
)
# expanding 绑定必须钉在 `bindparams`（与 `_AGG_STMT` 同款约定）：裸 `text()` 会把列表当单个
# 标量。`type_` 不可省：无类型时 SQLAlchemy 把空列表渲染成 `CAST(NULL AS INTEGER)`。
_DAILY_PCT_CHG_STMT = text(_DAILY_PCT_CHG_SQL).bindparams(
    bindparam("stock_ids", expanding=True, type_=Integer)
)


async def daily_pct_chg_by_stock_ids(
    db: AsyncSession, as_of: date, stock_ids: list[int]
) -> dict[int, float | None]:
    """`{stock_id: as_of 当日 pct_chg}`；**键存在**才代表该票有 as_of 行情行（值可为 `None`）。

    调用方按 `.get(stock_id)` 取值：不在表里 ⇒ 当日停牌/无行情 ⇒ `pct_chg=None`（缺失 ≠ 0，
    不能被算成"平盘"）。空输入不发 SQL（空列表的 expanding 绑定无意义）。
    """
    if not stock_ids:
        return {}
    rows = (
        (
            await db.execute(
                _DAILY_PCT_CHG_STMT, {"as_of": as_of, "stock_ids": [int(s) for s in stock_ids]}
            )
        )
        .mappings()
        .all()
    )
    return {
        int(r["stock_id"]): float(r["pct_chg"]) if r["pct_chg"] is not None else None for r in rows
    }


# ── 单板读路径的小查询（T8 详情 / T9 by-symbol 专用，口径见 plans §2.2/§2.3） ────────────
_BOARD_MEMBERSHIP_DATE_SQL = (
    "SELECT max(last_seen_on) FROM concept_members WHERE board_code = :board_code"
)
_BOARD_ROW_SQL = "SELECT board_code, board_name FROM concept_boards WHERE board_code = :board_code"
_SYMBOL_BOARD_CODES_SQL = (
    "SELECT board_code FROM concept_members WHERE symbol = :symbol ORDER BY board_code"
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
    return {"board_code": str(row["board_code"]), "board_name": str(row["board_name"])}


async def list_symbol_board_codes(db: AsyncSession, symbol: str) -> list[str]:
    """个股所属全部板块码（`by-symbol` 的反向查，走 `concept_members(symbol)` 索引）。

    按 `board_code` 升序：调用方还要按涨跌幅重排，但缺行情（`pct_change=null`）的并列必须靠
    板块码兜底，否则同一请求两次的顺序会随计划漂移。
    """
    rows = (await db.execute(text(_SYMBOL_BOARD_CODES_SQL), {"symbol": symbol})).scalars().all()
    return [str(code) for code in rows]
