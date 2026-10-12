"""S1.4 · 申万行业三级下钻（SCD2 分类归属 + 7 项行情字段聚合）。

三条读路:
    ``get_level_items``  —— L1 列表 / 某 L1 的 L2 列表 / 某 L2 的 L3 列表（各带 7 项聚合）
    ``get_level_metrics`` —— 单个节点的 7 项聚合
    ``get_members``      —— 末级成分股逐只 7 项字段

**SCD2 归属是唯一真相**：所有成分解析都走 ``sw_industry_member_periods``
（``effective_from <= T`` 且（``effective_to IS NULL`` 或 ``T < effective_to``）），
**不再**用 ``sw_industry_members`` 的当前态快照做历史还原。

**成员取自节点"自身层级"，上卷只是兜底**（Fix round 1 · 评审 C1）：
原先的实现把成员 CTE 硬钉在 ``c3.level = 3``，即"L1/L2 成分 = 其下 L3 的并集"。
**这个等价关系实测为假** —— 31 个一级行业里 **24 个**的上游直取数大于其下 L3 并集
（合计 5211 vs 4950；农林牧渔 104 vs 80、钢铁 43 vs 28、饲料 18 vs 11），
因为上游存在**直接挂在 L1/L2 而未落在任何 L3 的成员**。
故本模块：

1. 先查节点**自身层级**的成员（``exact``）；
2. 该层级一条都没有时，才退回"其下 L3 的并集"（``rollup``）；
3. 两种来源都在响应里用 ``member_source`` 标注，``rollup`` 时额外给
   ``member_source_note``，**不声称两者等价**。

**时间轴有两条，必须分开外露**（``scd2_as_of`` vs ``as_of``）：

* ``scd2_as_of`` = 最近一次**完整成功** ingest 的日期（``fully_ingested`` 且 ``status='ok'``）
  = **分类归属可还原能力的上界**。``--limit`` 冒烟或有失败的运行**不抬高**它。
  查 ``T > scd2_as_of`` 时归属退化为"截至 ``scd2_as_of`` 的成分"。
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
    Scd2CoverageOut,
)

logger = logging.getLogger(__name__)

# 与 market_service 同口径的按日缓存 TTL（当前读路未挂缓存，留作后续评估；见报告）
_CACHE_TTL = 300

# 上游 daily_quotes.amount（千元）→ 元
_AMOUNT_TO_YUAN = 1000.0

MEMBER_SOURCE_LEVEL = "level"
MEMBER_SOURCE_ROLLUP = "rollup"

SCD2_NO_DATA_NOTE = (
    "SCD2 分类归属尚无数据：sw_industry_member_periods 为空。"
    "请先运行 backend/scripts/ingest_sw_member_periods.py。"
    "本响应中的成分一律为空——这不代表当日无成分。"
)
SCD2_STALE_NOTE = "分类归属口径日（最近一次完整 ingest）早于行情口径日，成分可能已变更。"
ROLLUP_NOTE = (
    "本节点自身层级在 sw_industry_member_periods 中没有记录，成分退回"
    "「其下 L3 的并集」估算；该并集**不等于**上游该指数的成分（实测 24/31 个一级行业偏少），"
    "成交额/换手率等聚合同样偏少。请补跑 ingest（默认已灌全部三级）。"
)

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

# 按 **节点自身层级** 取成员（`:scope_code` 是节点自己的行业代码，任意层级）。
_EXACT_MEMBERS_SQL = """
SELECT m.symbol,
       m.stock_code,
       m.stock_name,
       m.industry_code AS node_code,
       m.effective_from,
       m.effective_to
FROM sw_industry_member_periods m
WHERE m.industry_code = :scope_code
  AND m.effective_from <= :asof
  AND (m.effective_to IS NULL OR :asof < m.effective_to)
"""

# 兜底：其下 L3 的并集（只在该节点自身层级无记录时使用）。
# `distinct`：同一 symbol 可能在其下多个 L3 各有一段区间（上游允许），不去重会重复计数。
_ROLLUP_MEMBERS_SQL = """
SELECT DISTINCT ON (m.symbol)
       m.symbol,
       m.stock_code,
       m.stock_name,
       m.industry_code AS node_code,
       m.effective_from,
       m.effective_to
FROM sw_industry_member_periods m
JOIN sw_industry_classes c3
  ON c3.industry_code = m.industry_code AND c3.level = 3
JOIN sw_industry_classes c2
  ON c2.industry_code = c3.parent_code AND c2.level = 2
JOIN sw_industry_classes c1
  ON c1.industry_code = c2.parent_code AND c1.level = 1
WHERE (:l1 = '' OR c1.industry_code = :l1)
  AND (:l2 = '' OR c2.industry_code = :l2)
  AND (:l3 = '' OR m.industry_code = :l3)
  AND m.effective_from <= :asof
  AND (m.effective_to IS NULL OR :asof < m.effective_to)
ORDER BY m.symbol, m.effective_from
"""

_MEMBER_JOIN = """
FROM members m
LEFT JOIN stocks st ON st.symbol = m.symbol
LEFT JOIN daily_quotes q ON q.stock_id = st.id AND q.trade_date = :trade_date
LEFT JOIN daily_basic_indicators b ON b.stock_id = st.id AND b.trade_date = :trade_date
"""


def _members_cte(source: str) -> str:
    body = _EXACT_MEMBERS_SQL if source == MEMBER_SOURCE_LEVEL else _ROLLUP_MEMBERS_SQL
    return f"WITH members AS ({body})"


# 行业层聚合：7 项字段 + 各自非空覆盖数。
# `sum(...) FILTER (WHERE x IS NOT NULL)` = 只对有值的股票求和/加权，缺失不拉低也不填 0。
# K 线用 `percentile_cont(0.5)`（中位数）——不做价格指数，理由见 schemas/sw_drilldown.py。
_LEVEL_AGG_BODY = """
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
       -- 成交额：源是 TuShare 原生千元 → ×1000 输出**元**
       sum(q.amount * :amount_to_yuan) FILTER (WHERE q.amount IS NOT NULL) AS sum_amount_all,
       count(q.amount) AS amount_covered
"""


def _agg_sql(source: str) -> str:
    return f"{_members_cte(source)}\n{_LEVEL_AGG_BODY}\n{_MEMBER_JOIN}"


def _members_sql(source: str) -> str:
    return f"""
{_members_cte(source)}
SELECT m.symbol,
       m.stock_code,
       COALESCE(m.stock_name, st.name) AS name,
       m.node_code,
       m.effective_from,
       m.effective_to,
       q.open, q.high, q.low, q.close, q.pre_close, q.pct_chg,
       q.volume,
       q.amount * :amount_to_yuan AS amount,
       b.turnover_rate, b.volume_ratio
{_MEMBER_JOIN}
ORDER BY m.symbol
"""


_EXACT_EXISTS_SQL = text("""
SELECT EXISTS (
    SELECT 1 FROM sw_industry_member_periods m
    WHERE m.industry_code = :scope_code
) AS has_rows
""")

_CHILDREN_SQL = text("""
SELECT industry_code, industry_name, level, parent_code
FROM sw_industry_classes
WHERE parent_code IS NOT DISTINCT FROM :parent
ORDER BY industry_code
""")

_CLASS_NODE_SQL = text("""
SELECT industry_code, industry_name, level, parent_code
FROM sw_industry_classes
WHERE industry_code = :code AND level = :level
""")


def _member_params(
    *,
    asof: date,
    trade_date: date | None,
    scope_code: str = "",
    l1: str = "",
    l2: str = "",
    l3: str = "",
) -> dict[str, Any]:
    return {
        "asof": asof,
        "trade_date": trade_date,
        "scope_code": scope_code,
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
    row: dict[str, Any],
    *,
    code: str,
    name: str,
    level: int,
    parent_code: str | None,
    member_source: str = MEMBER_SOURCE_LEVEL,
) -> MarketIndustryQuoteOut:
    """聚合行 + 节点身份 → ``MarketIndustryQuoteOut``（纯函数）。"""
    return MarketIndustryQuoteOut(
        code=code,
        name=name,
        level=level,
        parent_code=parent_code,
        member_source=cast(Any, member_source),
        member_source_note=ROLLUP_NOTE if member_source == MEMBER_SOURCE_ROLLUP else None,
        member_count=_i(row.get("member_count")),
        quoted_count=_i(row.get("quoted_count")),
        up_count=_i(row.get("up_count")),
        down_count=_i(row.get("down_count")),
        kline_median=KlineMedianOut(
            open=_f(row.get("median_open")),
            high=_f(row.get("median_high")),
            low=_f(row.get("median_low")),
            close=_f(row.get("median_close")),
            quoted_count=_i(row.get("quoted_count")),
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


async def _scd2_state(db: AsyncSession) -> tuple[date | None, dict[str, Any] | None]:
    from app.services.sw_member_scd2_service import (  # noqa: PLC0415
        get_last_run,
        get_member_as_of,
    )

    return await get_member_as_of(db), await get_last_run(db)


async def _coverage_out(db: AsyncSession) -> Scd2CoverageOut:
    """最近一次运行的覆盖摘要（Fix round 1 · 评审 I5：把下界信号外露到读面）。"""
    _as_of_value, last = await _scd2_state(db)
    if last is None:
        return Scd2CoverageOut()
    return Scd2CoverageOut(
        last_run_as_of=cast(date | None, last["as_of"]),
        last_run_status=cast(str, last["status"]),
        fully_ingested=bool(last["fully_ingested"]),
        index_codes=int(last["index_codes"]),
        rows_inserted=int(last["rows_inserted"]),
        rows_updated=int(last["rows_updated"]),
        zero_row_indices=int(last["zero_row_indices"]),
        detail=cast(str | None, last["detail"]),
    )


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


async def _has_exact_level(db: AsyncSession, scope_code: str) -> bool:
    """该节点自身层级是否有任何 SCD2 记录（有则用自身层级，无则上卷兜底）。"""
    return bool((await db.execute(_EXACT_EXISTS_SQL, {"scope_code": scope_code})).scalar())


async def _member_source_for(db: AsyncSession, scope_code: str) -> str:
    if await _has_exact_level(db, scope_code):
        return MEMBER_SOURCE_LEVEL
    return MEMBER_SOURCE_ROLLUP


async def _agg(db: AsyncSession, params: dict[str, Any], source: str) -> dict[str, Any]:
    row = (await db.execute(text(_agg_sql(source)), params)).mappings().one()
    return dict(row)


async def _children(db: AsyncSession, parent_code: str | None) -> list[dict[str, Any]]:
    rows = (await db.execute(_CHILDREN_SQL, {"parent": parent_code})).mappings().all()
    return [dict(r) for r in rows]


async def _class_node(db: AsyncSession, code: str, *, level: int) -> dict[str, Any] | None:
    result = await db.execute(_CLASS_NODE_SQL, {"code": code, "level": level})
    row = result.mappings().one_or_none()
    return None if row is None else dict(row)


_ANCESTORS_SQL = text("""
WITH RECURSIVE up AS (
    SELECT industry_code, parent_code, level FROM sw_industry_classes WHERE industry_code = :code
  UNION ALL
    SELECT c.industry_code, c.parent_code, c.level
    FROM sw_industry_classes c JOIN up ON c.industry_code = up.parent_code
)
SELECT level, industry_code FROM up WHERE level IN (1, 2)
""")


async def _ancestor_scope(db: AsyncSession, code: str) -> tuple[str, str]:
    """节点的 (L1 代码, L2 代码)——上卷兜底查询要用它限定子树。

    为什么需要：``_ROLLUP_MEMBERS_SQL`` 的 ``:l1/:l2/:l3`` 是**子树**过滤器，
    只有把节点自己的祖先链填进去，"其下 L3 的并集"才真的是**这个节点**的并集
    （不填 = 全市场并集，实测会算出 4951 只）。
    """
    rows = (await db.execute(_ANCESTORS_SQL, {"code": code})).all()
    by_level = {int(r[0]): str(r[1]) for r in rows}
    return by_level.get(1, ""), by_level.get(2, "")


async def _node(
    db: AsyncSession,
    *,
    code: str,
    name: str,
    level: int,
    parent_code: str | None,
    asof: date,
    trade_date: date | None,
    source: str | None = None,
) -> MarketIndustryLevelItemOut:
    """一个行业节点：**自身层级**的 7 项聚合（缺失时才上卷）+ 成分家数。"""
    src = source or await _member_source_for(db, code)
    l1, l2 = await _ancestor_scope(db, code)
    row = await _agg(
        db,
        _member_params(
            asof=asof,
            trade_date=trade_date,
            scope_code=code,
            l1=l1,
            l2=l2,
            l3=code if level == 3 else "",
        ),
        src,
    )
    metrics = assemble_quote(
        row, code=code, name=name, level=level, parent_code=parent_code, member_source=src
    )
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
    scd2_as_of, _last = await _scd2_state(db)
    coverage = await _coverage_out(db)
    available = scd2_as_of is not None
    note = scd2_note(available=available, scd2_as_of=scd2_as_of, as_of=trade_date)

    if not level1_code:
        out_level, parent = 1, None
        items = [
            await _node(
                db,
                code=node["industry_code"],
                name=node["industry_name"],
                level=1,
                parent_code=None,
                asof=asof,
                trade_date=trade_date,
            )
            for node in await _children(db, None)
        ]
    elif not level2_code:
        out_level = 2
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
            )
            for node in await _children(db, level1_code)
        ]
    else:
        out_level = 3
        l1 = await _class_node(db, level1_code, level=1)
        l2 = await _class_node(db, level2_code, level=2)
        _require_chain(l1, l2, None, level1_code, level2_code, "")
        assert l2 is not None  # _require_chain 已保证
        parent = await _node(
            db,
            code=l2["industry_code"],
            name=l2["industry_name"],
            level=2,
            parent_code=level1_code,
            asof=asof,
            trade_date=trade_date,
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
            )
            for node in await _children(db, level2_code)
        ]

    return MarketIndustryLevelOut(
        as_of=trade_date,
        as_of_quality=cast(Any, quality),
        as_of_reason=reason,
        scd2_as_of=scd2_as_of,
        scd2_available=available,
        scd2_note=note,
        scd2_coverage=coverage,
        level=out_level,
        parent=parent,
        items=items,
    )


def _require_chain(
    l1: dict[str, Any] | None,
    l2: dict[str, Any] | None,
    l3: dict[str, Any] | None,
    level1_code: str,
    level2_code: str,
    level3_code: str,
) -> None:
    """层级归属校验（Fix round 1 · 评审 Minor）。

    原先只校验"代码存在于该层"：传一组**不构成父子关系**的 (l1, l2) 会命中一个
    0 成员的节点、静默返回空成分，而不是 404。这里逐级核对 ``parent_code``。
    """
    if l1 is None:
        raise LookupError(f"SW level1 {level1_code} not found")
    if level2_code:
        if l2 is None:
            raise LookupError(f"SW level2 {level1_code}/{level2_code} not found")
        if l2["parent_code"] != l1["industry_code"]:
            raise LookupError(
                f"SW level2 {level2_code} 不属于 {level1_code}"
                f"（其 parent_code={l2['parent_code']}）"
            )
    if level3_code:
        if l3 is None:
            raise LookupError(f"SW level3 {level1_code}/{level2_code}/{level3_code} not found")
        if l3["parent_code"] != (l2 or {}).get("industry_code"):
            raise LookupError(
                f"SW level3 {level3_code} 不属于 {level2_code}"
                f"（其 parent_code={l3['parent_code']}）"
            )


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
    l1 = await _class_node(db, level1_code, level=1)
    l2 = await _class_node(db, level2_code, level=2) if level2_code else None
    l3 = await _class_node(db, level3_code, level=3) if level3_code else None
    _require_chain(l1, l2, l3, level1_code, level2_code, level3_code)
    node = l3 if level3_code else (l2 if level2_code else l1)
    assert node is not None  # _require_chain 已保证
    src = await _member_source_for(db, code)
    # 上卷兜底必须带**本节点的子树范围**（Fix round 1 re-review · NEW-B）：
    # `_ROLLUP_MEMBERS_SQL` 的 `:l1/:l2/:l3` 是子树过滤器，不填 = 全市场并集
    # （自身层级无行的节点，如 110900 农业综合Ⅱ，曾在 /metrics 返回 ~4,951 只）。
    # 与 list 路径 `_node` 的口径一致：经 `_ancestor_scope` 加范围，L3 再显式钉住自己。
    l1_scope, l2_scope = await _ancestor_scope(db, code)
    row = await _agg(
        db,
        _member_params(
            asof=asof,
            trade_date=trade_date,
            scope_code=code,
            l1=l1_scope,
            l2=l2_scope,
            l3=code if node_level == 3 else "",
        ),
        src,
    )
    return assemble_quote(
        row,
        code=node["industry_code"],
        name=node["industry_name"],
        level=node_level,
        parent_code=node["parent_code"],
        member_source=src,
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
    asof = _scd2_query_day(as_of, trade_date)
    scd2_as_of, _last = await _scd2_state(db)
    coverage = await _coverage_out(db)

    l1 = await _class_node(db, level1_code, level=1)
    l2 = await _class_node(db, level2_code, level=2)
    l3 = await _class_node(db, level3_code, level=3)
    _require_chain(l1, l2, l3, level1_code, level2_code, level3_code)
    assert l1 is not None and l2 is not None and l3 is not None

    src = await _member_source_for(db, level3_code)
    params = _member_params(
        asof=asof,
        trade_date=trade_date,
        scope_code=level3_code,
        l1=level1_code,
        l2=level2_code,
        l3=level3_code,
    )
    rows = (await db.execute(text(_members_sql(src)), params)).mappings().all()
    aggregate = await _agg(db, params, src)
    metrics = assemble_quote(
        aggregate,
        code=l3["industry_code"],
        name=l3["industry_name"],
        level=3,
        parent_code=level2_code,
        member_source=src,
    )
    # chain 的每一级都带该级的真实成分家数（Fix round 1 · 评审 Minor：原先是 0 占位，
    # 而本响应契约明写"缺失一律 None、不得用 0 顶替"——0 会让前端误显示"0 只成分"）
    chain: list[MarketIndustryLevelItemOut] = []
    for node, lvl in ((l1, 1), (l2, 2), (l3, 3)):
        if lvl == 3:
            chain.append(
                MarketIndustryLevelItemOut(
                    code=l3["industry_code"],
                    name=l3["industry_name"],
                    level=3,
                    stock_count=metrics.member_count,
                    metrics=metrics,
                )
            )
            continue
        chain.append(
            await _node(
                db,
                code=node["industry_code"],
                name=node["industry_name"],
                level=lvl,
                parent_code=node["parent_code"],
                asof=asof,
                trade_date=trade_date,
            )
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
        scd2_coverage=coverage,
        chain=chain,
        metrics=metrics,
        items=[assemble_member(dict(r)) for r in rows],
    )
