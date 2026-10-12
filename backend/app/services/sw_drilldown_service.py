"""S1.4 · 申万行业三级下钻（SCD2 分类归属 + 7 项行情字段聚合）。

三条读路:
    ``get_level_items``  —— L1 列表 / 某 L1 的 L2 列表 / 某 L2 的 L3 列表（各带 7 项聚合）
    ``get_level_metrics`` —— 单个节点的 7 项聚合
    ``get_members``      —— 末级成分股逐只 7 项字段

**SCD2 归属是唯一真相**：所有成分解析都走 ``sw_industry_member_periods``
（``effective_from <= T`` 且（``effective_to IS NULL`` 或 ``T < effective_to``）），
**不再**用 ``sw_industry_members`` 的当前态快照做历史还原。

**时间轴有两条，必须分开外露**（``scd2_as_of`` vs ``as_of``）：

* ``scd2_as_of`` = 最近一次成功 ingest 的日期 = **分类归属可还原能力的上界**。
  查 ``T > scd2_as_of`` 时归属退化为"截至 ``scd2_as_of`` 的成分"，
  故读面把 ``scd2_as_of`` 与 ``scd2_available`` 一并返回，前端必须显示。
* ``as_of`` = 行情口径日（``market_day_service`` 判据），T+1。

**单位**：``daily_quotes.amount`` 是 TuShare 原生**千元**，本模块统一 ×1000 → **元**。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import CacheClient
from app.schemas.sw_drilldown import (
    KlineMedianOut,
    MarketIndustryLevelItemOut,
    MarketIndustryLevelOut,
    MarketIndustryMemberOut,
    MarketIndustryMembersOut,
    MarketIndustryQuoteOut,
)

logger = logging.getLogger(__name__)

# 与 market_service 同口径的按日缓存 TTL
_CACHE_TTL = 300

# 上游 daily_quotes.amount（千元）→ 元
_AMOUNT_TO_YUAN = 1000.0

SCD2_NO_DATA_NOTE = (
    "SCD2 分类归属尚无数据：sw_industry_member_periods 为空。"
    "请先运行 backend/scripts/ingest_sw_member_periods.py。"
    "本响应中的成分一律为空——这不代表当日无成分。"
)
SCD2_STALE_NOTE = "分类归属口径日（最近一次成功 ingest）早于行情口径日，成分可能已变更。"

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

# 成分解析：申万三级链 + SCD2 区间。
# `:l1` 必填（L1 行业代码）；`:l2` / `:l3` 为空串时该条件恒真（纯 SQL，不做字符串拼接）。
_MEMBER_SQL = """
WITH scoped AS (
    SELECT m.symbol,
           m.stock_code,
           m.stock_name,
           m.industry_code AS l3_code,
           c3.industry_name AS l3_name,
           m.effective_from,
           m.effective_to
    FROM sw_industry_member_periods m
    JOIN sw_industry_classes c3
      ON c3.industry_code = m.industry_code AND c3.level = 3
    JOIN sw_industry_classes c2
      ON c2.industry_code = c3.parent_code AND c2.level = 2
    JOIN sw_industry_classes c1
      ON c1.industry_code = c2.parent_code AND c1.level = 1
    WHERE c1.parent_code IS NULL
      AND (:l1 = '' OR c1.industry_code = :l1)
      AND (:l2 = '' OR c2.industry_code = :l2)
      AND (:l3 = '' OR m.industry_code = :l3)
      AND m.effective_from <= :asof
      AND (m.effective_to IS NULL OR :asof < m.effective_to)
)
SELECT s.symbol,
       s.stock_code,
       COALESCE(s.stock_name, st.name) AS name,
       s.l3_code,
       s.l3_name,
       s.effective_from,
       s.effective_to,
       q.open, q.high, q.low, q.close, q.pre_close, q.pct_chg,
       q.volume,
       q.amount * :amount_to_yuan AS amount,
       b.turnover_rate, b.volume_ratio
FROM scoped s
LEFT JOIN stocks st ON st.symbol = s.symbol
LEFT JOIN daily_quotes q ON q.stock_id = st.id AND q.trade_date = :trade_date
LEFT JOIN daily_basic_indicators b ON b.stock_id = st.id AND b.trade_date = :trade_date
ORDER BY s.symbol
"""

# 行业层聚合：7 项字段 + 各自非空覆盖数。
# `sum(...) FILTER (WHERE x IS NOT NULL)` = 只对有值的股票求和/加权，缺失不拉低也不填 0。
# K 线用 `percentile_cont(0.5)`（中位数）——不做价格指数，理由见 schemas/sw_drilldown.py。
_LEVEL_AGG_SQL = """
WITH members AS (
    {member_sql}
)
SELECT count(*) AS member_count,
       count(q.close) AS quoted_count,
       count(*) FILTER (WHERE q.pct_chg > 0) AS up_count,
       count(*) FILTER (WHERE q.pct_chg < 0) AS down_count,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY q.open) AS median_open,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY q.high) AS median_high,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY q.low) AS median_low,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY q.close) AS median_close,
       sum(q.pct_chg * q.amount) FILTER (WHERE q.pct_chg IS NOT NULL AND q.amount IS NOT NULL)
           / NULLIF(sum(q.amount) FILTER (WHERE q.pct_chg IS NOT NULL AND q.amount IS NOT NULL), 0)
           AS pct_chg_weighted,
       count(q.pct_chg) AS pct_chg_covered,
       sum(b.turnover_rate * q.amount)
           FILTER (WHERE b.turnover_rate IS NOT NULL AND q.amount IS NOT NULL)
           / NULLIF(
               sum(q.amount)
               FILTER (WHERE b.turnover_rate IS NOT NULL AND q.amount IS NOT NULL),
               0
           )
           AS turnover_rate_weighted,
       count(b.turnover_rate) AS turnover_rate_covered,
       sum(q.volume) FILTER (WHERE q.volume IS NOT NULL) AS sum_volume,
       count(q.volume) AS volume_covered,
       sum(b.volume_ratio * q.volume)
           FILTER (WHERE b.volume_ratio IS NOT NULL AND q.volume IS NOT NULL AND q.volume > 0)
           / NULLIF(
               sum(q.volume)
               FILTER (WHERE b.volume_ratio IS NOT NULL AND q.volume IS NOT NULL AND q.volume > 0),
               0
           )
           AS volume_ratio_weighted,
       count(b.volume_ratio) AS volume_ratio_covered,
       avg((q.high - q.low) / NULLIF(q.pre_close, 0) * 100)
           FILTER (WHERE q.high IS NOT NULL AND q.low IS NOT NULL AND q.pre_close > 0)
           AS amplitude,
       count(*) FILTER (WHERE q.high IS NOT NULL AND q.low IS NOT NULL AND q.pre_close > 0)
           AS amplitude_covered,
       -- 成交额：源是 TuShare 原生千元 → ×1000 输出**元**（与 _MEMBER_SQL 同一换算）
       sum(q.amount * :amount_to_yuan) FILTER (WHERE q.amount IS NOT NULL) AS sum_amount_all,
       count(q.amount) AS amount_covered
FROM members m
LEFT JOIN stocks st ON st.symbol = m.symbol
LEFT JOIN daily_quotes q ON q.stock_id = st.id AND q.trade_date = :trade_date
LEFT JOIN daily_basic_indicators b ON b.stock_id = st.id AND b.trade_date = :trade_date
"""

_CHILDREN_SQL = text("""
SELECT industry_code, industry_name, level, parent_code
FROM sw_industry_classes
WHERE parent_code IS NOT DISTINCT FROM :parent
ORDER BY industry_code
""")


def _agg_sql() -> str:
    return _LEVEL_AGG_SQL.format(member_sql=_MEMBER_SQL)


def _member_params(
    *, asof: date, trade_date: date | None, l1: str = "", l2: str = "", l3: str = ""
) -> dict[str, Any]:
    return {
        "asof": asof,
        "trade_date": trade_date,
        "l1": l1,
        "l2": l2,
        "l3": l3,
        "amount_to_yuan": _AMOUNT_TO_YUAN,
    }


# ---------------------------------------------------------------------------
# 纯函数：SQL 行 → schema（单测不经 DB 直接锁口径）
# ---------------------------------------------------------------------------


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def _i(value: Any) -> int:
    return 0 if value is None else int(value)


def assemble_quote(
    row: dict[str, Any], *, code: str, name: str, level: int, parent_code: str | None
) -> MarketIndustryQuoteOut:
    """聚合行 + 节点身份 → ``MarketIndustryQuoteOut``（纯函数）。"""
    return MarketIndustryQuoteOut(
        code=code,
        name=name,
        level=level,
        parent_code=parent_code,
        member_count=_i(row.get("member_count")),
        quoted_count=_i(row.get("quoted_count")),
        up_count=_i(row.get("up_count")),
        down_count=_i(row.get("down_count")),
        kline_median=KlineMedianOut(
            open=_f(row.get("median_open")),
            high=_f(row.get("median_high")),
            low=_f(row.get("median_low")),
            close=_f(row.get("median_close")),
            member_count=_i(row.get("quoted_count")),
        ),
        pct_chg_weighted=_f(row.get("pct_chg_weighted")),
        pct_chg_covered=_i(row.get("pct_chg_covered")),
        turnover_rate_weighted=_f(row.get("turnover_rate_weighted")),
        turnover_rate_covered=_i(row.get("turnover_rate_covered")),
        volume=_f(row.get("sum_volume")),
        volume_covered=_i(row.get("volume_covered")),
        volume_ratio_weighted=_f(row.get("volume_ratio_weighted")),
        volume_ratio_covered=_i(row.get("volume_ratio_covered")),
        amplitude=_f(row.get("amplitude")),
        amplitude_covered=_i(row.get("amplitude_covered")),
        # 成交额：只对真有 amount 的行求和（缺失不填 0）；覆盖数点名缺了多少家
        amount=_f(row.get("sum_amount_all")),
        amount_covered=_i(row.get("amount_covered")),
    )


def assemble_member(row: dict[str, Any]) -> MarketIndustryMemberOut:
    """成分股行 → ``MarketIndustryMemberOut``（纯函数；7 项字段 + SCD2 区间）。"""
    high, low, pre_close = _f(row.get("high")), _f(row.get("low")), _f(row.get("pre_close"))
    amplitude: float | None = None
    if high is not None and low is not None and pre_close:
        amplitude = round((high - low) / pre_close * 100, 4)
    return MarketIndustryMemberOut(
        symbol=str(row["symbol"]),
        stock_code=str(row["stock_code"]),
        name=row.get("name"),
        effective_from=row["effective_from"],
        effective_to=row.get("effective_to"),
        quoted=row.get("close") is not None,
        open=_f(row.get("open")),
        high=high,
        low=low,
        close=_f(row.get("close")),
        pct_chg=_f(row.get("pct_chg")),
        turnover_rate=_f(row.get("turnover_rate")),
        volume=_f(row.get("volume")),
        volume_ratio=_f(row.get("volume_ratio")),
        amplitude=amplitude,
        amount=_f(row.get("amount")),
    )


def scd2_note(*, available: bool, scd2_as_of: date | None, as_of: date | None) -> str | None:
    """读面必须外露的"归属口径"说明（缺失/滞后各一句，否则 None）。"""
    if not available:
        return SCD2_NO_DATA_NOTE
    if scd2_as_of is not None and as_of is not None and scd2_as_of < as_of:
        return SCD2_STALE_NOTE
    return None


# ---------------------------------------------------------------------------
# 读服务
# ---------------------------------------------------------------------------


async def _scd2_as_of(db: AsyncSession) -> date | None:
    from app.services.sw_member_scd2_service import get_member_as_of  # noqa: PLC0415

    return await get_member_as_of(db)


async def _resolve_day(
    db: AsyncSession, cache: CacheClient | None
) -> tuple[date | None, str, str | None]:
    from app.services import market_day_service  # noqa: PLC0415

    md = await market_day_service.resolve_latest_complete_day(db, cache=cache)
    if md is None:
        return None, "partial", "daily_quotes 为空（尚无行情入库）"
    return md.day, md.quality, md.reason


def _scd2_query_day(as_of: date | None, trade_date: date | None) -> date:
    """SCD2 归属查询口径日（**必须**是具体日期，不能是 NULL）。

    优先级：显式 ``as_of`` → 行情口径日 → 上海当日。最后那条兜底只在"空库且未显式
    传 as_of"时命中：此时行情一无所知，但分类归属仍应按"今天"还原——返回空成分
    比拿 NULL 去比 ``effective_from``（恒 false，静默空集）更可解释。
    """
    if as_of is not None:
        return as_of
    if trade_date is not None:
        return trade_date
    from app.services.market_data_service import _today_sh  # noqa: PLC0415

    return _today_sh()


async def _agg(db: AsyncSession, params: dict[str, Any]) -> dict[str, Any]:
    row = (await db.execute(text(_agg_sql()), params)).mappings().one()
    return dict(row)


async def _children(db: AsyncSession, parent_code: str | None) -> list[dict[str, Any]]:
    rows = (await db.execute(_CHILDREN_SQL, {"parent": parent_code})).mappings().all()
    return [dict(r) for r in rows]


async def _node(
    db: AsyncSession,
    *,
    code: str,
    name: str,
    level: int,
    parent_code: str | None,
    asof: date,
    trade_date: date | None,
    scope_l1: str,
    scope_l2: str,
    scope_l3: str,
) -> MarketIndustryLevelItemOut:
    """一个子行业节点：自身 7 项聚合 + 成分家数。"""
    row = await _agg(
        db, _member_params(asof=asof, trade_date=trade_date, l1=scope_l1, l2=scope_l2, l3=scope_l3)
    )
    metrics = assemble_quote(row, code=code, name=name, level=level, parent_code=parent_code)
    return MarketIndustryLevelItemOut(
        code=code,
        name=name,
        level=level,
        stock_count=metrics.member_count,
        metrics=metrics,
    )


async def get_level_items(
    db: AsyncSession,
    cache: CacheClient | None,
    *,
    level1_code: str = "",
    level2_code: str = "",
    as_of: date | None = None,
) -> MarketIndustryLevelOut:
    """某一层的下钻列表。

    * 只给 ``level1_code`` 为空 → 31 个 L1 行业（每个带 7 项聚合）；
    * 给 ``level1_code`` → 该 L1 下的 L2 列表（响应 ``parent`` 带 L1 自身聚合）；
    * 给 ``level1_code`` + ``level2_code`` → 该 L2 下的 L3 列表。
    """
    day, quality, reason = await _resolve_day(db, cache)
    trade_date = as_of or day
    asof = _scd2_query_day(as_of, trade_date)  # SCD2 归属口径日必须具体
    scd2_as_of = await _scd2_as_of(db)

    if not level1_code:
        # L1 列表：逐个 L1 行业聚合（31 次轻查询；无 N+1 到股票级）
        out_level, parent = 1, None
        nodes = await _children(db, None)
        items = []
        for node in nodes:
            items.append(
                await _node(
                    db,
                    code=node["industry_code"],
                    name=node["industry_name"],
                    level=1,
                    parent_code=None,
                    asof=asof,
                    trade_date=trade_date,
                    scope_l1=node["industry_code"],
                    scope_l2="",
                    scope_l3="",
                )
            )
    elif not level2_code:
        out_level, parent = 2, None
        l1 = await _class_node(db, level1_code, level=1)
        if l1 is None:
            raise LookupError(f"SW level1 {level1_code} not found")
        parent = await _node(
            db,
            code=l1["industry_code"],
            name=l1["industry_name"],
            level=1,
            parent_code=None,
            asof=asof,
            trade_date=trade_date,
            scope_l1=l1["industry_code"],
            scope_l2="",
            scope_l3="",
        )
        items = [
            await _node(
                db,
                code=node["industry_code"],
                name=node["industry_name"],
                level=2,
                parent_code=level1_code,
                asof=asof,
                trade_date=trade_date,
                scope_l1=level1_code,
                scope_l2=node["industry_code"],
                scope_l3="",
            )
            for node in await _children(db, level1_code)
        ]
    else:
        out_level, parent = 3, None
        l1 = await _class_node(db, level1_code, level=1)
        l2 = await _class_node(db, level2_code, level=2)
        if l1 is None or l2 is None:
            raise LookupError(f"SW level2 {level1_code}/{level2_code} not found")
        parent = await _node(
            db,
            code=l2["industry_code"],
            name=l2["industry_name"],
            level=2,
            parent_code=level1_code,
            asof=asof,
            trade_date=trade_date,
            scope_l1=level1_code,
            scope_l2=level2_code,
            scope_l3="",
        )
        items = [
            await _node(
                db,
                code=node["industry_code"],
                name=node["industry_name"],
                level=3,
                parent_code=level2_code,
                asof=asof,
                trade_date=trade_date,
                scope_l1=level1_code,
                scope_l2=level2_code,
                scope_l3=node["industry_code"],
            )
            for node in await _children(db, level2_code)
        ]

    return MarketIndustryLevelOut(
        as_of=trade_date,
        as_of_quality=cast(Any, quality),
        as_of_reason=reason,
        scd2_as_of=scd2_as_of,
        scd2_available=scd2_as_of is not None,
        scd2_note=scd2_note(
            available=scd2_as_of is not None, scd2_as_of=scd2_as_of, as_of=trade_date
        ),
        level=out_level,
        parent=parent,
        items=items,
    )


async def _class_node(db: AsyncSession, code: str, *, level: int) -> dict[str, Any] | None:
    row = (
        (
            await db.execute(
                text(
                    "SELECT industry_code, industry_name, level, parent_code "
                    "FROM sw_industry_classes WHERE industry_code = :code AND level = :level"
                ),
                {"code": code, "level": level},
            )
        )
        .mappings()
        .one_or_none()
    )
    return None if row is None else dict(row)


async def get_level_metrics(
    db: AsyncSession,
    cache: CacheClient | None,
    *,
    level1_code: str,
    level2_code: str = "",
    level3_code: str = "",
    as_of: date | None = None,
) -> MarketIndustryQuoteOut:
    """单个节点的 7 项聚合（L1 / L2 / L3 三选一，按给到的层级）。"""
    day, _quality, _reason = await _resolve_day(db, cache)
    trade_date = as_of or day
    asof = _scd2_query_day(as_of, trade_date)
    if level3_code:
        node_level, code = 3, level3_code
    elif level2_code:
        node_level, code = 2, level2_code
    else:
        node_level, code = 1, level1_code
    node = await _class_node(db, code, level=node_level)
    if node is None:
        raise LookupError(f"SW level{node_level} {code} not found")
    row = await _agg(
        db,
        _member_params(
            asof=asof,
            trade_date=trade_date,
            l1=level1_code,
            l2=level2_code,
            l3=level3_code,
        ),
    )
    return assemble_quote(
        row,
        code=node["industry_code"],
        name=node["industry_name"],
        level=node_level,
        parent_code=node["parent_code"],
    )


async def get_members(
    db: AsyncSession,
    cache: CacheClient | None,
    *,
    level1_code: str,
    level2_code: str,
    level3_code: str,
    as_of: date | None = None,
) -> MarketIndustryMembersOut:
    """末级（L3）成分股列表（逐只 7 项字段）+ 该节点聚合 + L1→L2→L3 链。"""
    day, quality, reason = await _resolve_day(db, cache)
    trade_date = as_of or day
    scd2_as_of = await _scd2_as_of(db)

    l1 = await _class_node(db, level1_code, level=1)
    l2 = await _class_node(db, level2_code, level=2)
    l3 = await _class_node(db, level3_code, level=3)
    if l1 is None or l2 is None or l3 is None:
        raise LookupError(f"SW level3 {level1_code}/{level2_code}/{level3_code} not found")

    asof = _scd2_query_day(as_of, trade_date)
    params = _member_params(
        asof=asof,
        trade_date=trade_date,
        l1=level1_code,
        l2=level2_code,
        l3=level3_code,
    )
    rows = (await db.execute(text(_MEMBER_SQL), params)).mappings().all()
    aggregate = await _agg(db, params)
    metrics = assemble_quote(
        aggregate,
        code=l3["industry_code"],
        name=l3["industry_name"],
        level=3,
        parent_code=level2_code,
    )
    return MarketIndustryMembersOut(
        as_of=trade_date,
        as_of_quality=cast(Any, quality),
        as_of_reason=reason,
        scd2_as_of=scd2_as_of,
        scd2_available=scd2_as_of is not None,
        scd2_note=scd2_note(
            available=scd2_as_of is not None, scd2_as_of=scd2_as_of, as_of=trade_date
        ),
        chain=[
            MarketIndustryLevelItemOut(
                code=l1["industry_code"], name=l1["industry_name"], level=1, stock_count=0
            ),
            MarketIndustryLevelItemOut(
                code=l2["industry_code"], name=l2["industry_name"], level=2, stock_count=0
            ),
            MarketIndustryLevelItemOut(
                code=l3["industry_code"],
                name=l3["industry_name"],
                level=3,
                stock_count=metrics.member_count,
            ),
        ],
        metrics=metrics,
        items=[assemble_member(dict(r)) for r in rows],
    )
