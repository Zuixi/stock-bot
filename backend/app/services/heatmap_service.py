"""市场/个股热力图（S1.5）读面：单日全市场一行一票的**批量**响应。

与既有小上限端点（rankings ≤100 / board stocks ≤200 / concepts 分页）的关系：
本模块是**唯一**回全市场（≈5500 行）的读路。payload 是热力图页一次性数据
（面积 = 市值或成交额、颜色 = 涨跌幅、四个分组维度共用同一批行），字段刻意
只留 5 个标量 + 2 个分组码——多一列都是千行级传输。

口径（ADR 0004 / `market_day_service`）：行情取 `resolve_latest_complete_day`
判据日 + `as_of_quality`；市值当日优先、缺日回看 ``< :day`` 最近一条（防读到
未来），两天都缺回 `null`（**不回填**），前端如实画「无面积」而不是用 0 冒充。

分组归属（sw_l1 / concept）与行情行在**同一条 SQL** 里取（各多一个 join，
不是二次往返）；watchlist 由前端用既有 watchlist 名单过滤（自选是
登录态数据，不进这条公开免登录读路——ADR 0008）。纯函数（行 → schema、
分组树构建）与 SQL 分离，单测不经 DB 直接锁口径。
"""

from __future__ import annotations

from datetime import date
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import CacheClient
from app.schemas.heatmap import HeatmapGroup, HeatmapTreemapOut
from app.services import market_day_service

_CACHE_TTL = 300  # 与 market_service._MARKET_CACHE_TTL 对齐：日级快照不追盘中
_CACHE_KEY = "market:heatmap:treemap:{day}"
_CONCEPT_BOARDS_CACHE_KEY = "market:heatmap:concept-boards"

#: 标的级响应行键（键序即 SQL 选择顺序；前端消费这些键，见 frontend shared/api/heatmap.ts）
ROW_KEYS: tuple[str, ...] = (
    "symbol",
    "name",
    "pct_chg",
    "amount",
    "total_mv",
    "sw_l1_code",
    "concept_code",
)

# 单条 SQL 回齐「行情 + 市值 + 两个分组码」（dev 库 5559 行实测 ~100ms；全
# LATERAL 形态 ~135ms——分组列的 35ms 不值，测量记录见 task-9 报告）。写法对齐
# market_service._SW_PERF_SQL（text() + 具名参数）：
# - total_mv：**当日优先** LEFT JOIN daily_basic（``idx_daily_basic_stock_date``
#   等值查找，实测同日全量 ~2ms），缺行再 LATERAL 回看 ``< :day`` 最近一条
#   （走同索引逐股查找）。``< :day`` / ``= :day`` 合起来与 snapshot loader 的
#   ``<= :day`` 同义：杜绝把未来某天的市值提前用在这一天；两天都没有 →
#   ``NULL``，消费方按「无面积」处理，**绝不**回填成 0。
#   为什么不只用 LATERAL：plan 实测该 LATERAL 是查询大头（5559 次 backward
#   index scan，buffers ~152k）；当日行命中率 ≈100%（dev 库 5559/5559），
#   等值 join 便宜一个量级。
# - sw_l1：当前态 sw_industry_members 经 level3→level2→level1 two-hop 上卷
#   （与 _SW_PERF_SQL 同链；一只股票可属多个 L1 → LATERAL 取 industry_code
#   最小的一个，分组树里每股只落一组、不重复计数）。
# - concept：``min(board_code) GROUP BY symbol``（每股一组，与 LATERAL 语义
#   相同但一次聚合扫描搞定全部查找——实测比逐股 LATERAL 快 ~30ms）。
# - ``amount`` 是 TuShare 原生**千元**、``total_mv`` 是**万元**：单位原样返回，
#   换算在展示层做（面积只比较相对大小；见 schemas/heatmap.py 的 *_unit 字段）。
_TREEMAP_SQL = text(
    """
    WITH day_stocks AS (
        SELECT q.stock_id, s.symbol, s.name, q.pct_chg, q.amount
        FROM daily_quotes q
        JOIN stocks s ON s.id = q.stock_id
        WHERE q.trade_date = :day
          AND q.pct_chg IS NOT NULL
    )
    SELECT d.stock_id,
           d.symbol,
           d.name,
           d.pct_chg,
           d.amount,
           COALESCE(b.total_mv, lb.total_mv) AS total_mv,
           l1.l1_code                        AS sw_l1_code,
           cb.board_code                     AS concept_code
    FROM day_stocks d
    LEFT JOIN daily_basic_indicators b
      ON b.stock_id = d.stock_id AND b.trade_date = :day
    LEFT JOIN LATERAL (
        SELECT bb.total_mv
        FROM daily_basic_indicators bb
        WHERE bb.stock_id = d.stock_id AND bb.trade_date < :day
        ORDER BY bb.trade_date DESC
        LIMIT 1
    ) lb ON true
    LEFT JOIN LATERAL (
        SELECT c1.industry_code AS l1_code
        FROM sw_industry_members m
        JOIN sw_industry_classes c3 ON c3.industry_code = m.industry_code AND c3.level = 3
        JOIN sw_industry_classes c2 ON c2.industry_code = c3.parent_code AND c2.level = 2
        JOIN sw_industry_classes c1 ON c1.industry_code = c2.parent_code AND c1.level = 1
        WHERE m.symbol = d.symbol
        ORDER BY c1.industry_code
        LIMIT 1
    ) l1 ON true
    LEFT JOIN (
        SELECT symbol, min(board_code) AS board_code
        FROM concept_members
        GROUP BY symbol
    ) cb ON cb.symbol = d.symbol
    ORDER BY d.stock_id
    """
)

_CONCEPT_BOARDS_SQL = text(
    """
    SELECT board_code, board_name
    FROM concept_boards
    WHERE is_active
    ORDER BY board_code
    """
)


async def _fetch_rows(db: AsyncSession, day: date) -> list[dict[str, Any]]:
    """取行 SQL seam（单测 monkeypatch 本函数即不碰库）。返回原始 DB 映射。"""
    rows = (await db.execute(_TREEMAP_SQL, {"day": day})).mappings().all()
    return [dict(r) for r in rows]


def _f(value: Any) -> float | None:
    """Decimal/int → float；None 原样（数值列缺就是不缺，不造 0）。"""
    return None if value is None else float(value)


def row_to_item(row: dict[str, Any]) -> dict[str, Any]:
    """DB 行 → 响应项（纯函数；缓存/直读两路共用，同型输出）。"""
    return {
        "symbol": str(row["symbol"]),
        "name": row.get("name"),
        "pct_chg": _f(row.get("pct_chg")),
        "amount": _f(row.get("amount")),
        "total_mv": _f(row.get("total_mv")),
        "sw_l1_code": row.get("sw_l1_code"),
        "concept_code": row.get("concept_code"),
    }


def rows_to_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """逐行规范化（纯函数）。"""
    return [row_to_item(r) for r in rows]


async def get_treemap(
    db: AsyncSession, cache: CacheClient | None, day: date | None = None
) -> HeatmapTreemapOut | None:
    """热力图批量读路：判据日全市场行（含两个分组码）一次回齐。

    ``day`` 缺省 = 最近完整日；显式传入可回看历史（只允许 <= 最近完整日且有行情的
    交易日，否则返回 ``None``，由 API 转 404）。历史日的市值仍按当日优先/回看，
    行业归属仍是**当前**申万快照（C 类，响应不承诺当时成分）。


    四个分组维度共用这一批行：
    - ``all``：平铺 items 本身；
    - ``sw_l1`` / ``concept``：前端按行上的 ``sw_l1_code`` / ``concept_code`` 组树
      （组名从本模块 ``list_concept_boards`` / 申万类目表取，或由前端回落码显示）；
    - ``watchlist``：前端用既有 watchlist 名单对 items 过滤（登录态数据不出后端）。

    降级与既有日级端点一致：空库回空信封（不 500、不缓存）。
    """
    md = await market_day_service.resolve_latest_complete_day(db, cache=cache)
    if md is None:
        if day is None:
            return HeatmapTreemapOut(
                as_of=None,
                as_of_quality="partial",
                count=0,
                amount_unit="千元",
                mv_unit="万元",
                items=[],
            )
        return None

    if day is not None and day > md.day:
        return None
    target = day if day is not None else md.day
    quality = md.quality if target == md.day else "complete"
    cache_key = _CACHE_KEY.format(day=target.isoformat())
    if cache:
        cached = await cache.get(cache_key)
        if isinstance(cached, dict) and isinstance(cached.get("items"), list):
            # 部署前的旧 payload 少 quality 字段时不谎报 complete
            cached.setdefault("as_of_quality", "partial")
            return HeatmapTreemapOut.model_validate(cached)

    rows = await _fetch_rows(db, target)
    if day is not None and not rows:
        return None  # 非交易日/无行情：不要返回"空热力图"冒充那一天
    items = rows_to_items(rows)
    out = HeatmapTreemapOut(
        as_of=target,
        as_of_quality=quality,
        count=len(items),
        amount_unit="千元",
        mv_unit="万元",
        items=items,
    )
    if cache and items:
        await cache.set(cache_key, out.model_dump(mode="json"), _CACHE_TTL)
    return out


async def _fetch_concept_boards(db: AsyncSession) -> list[dict[str, Any]]:
    """概念板块名录 SQL seam（单测 monkeypatch 即不碰库）。"""
    rows = (await db.execute(_CONCEPT_BOARDS_SQL)).mappings().all()
    return [dict(r) for r in rows]


async def list_concept_boards(db: AsyncSession, cache: CacheClient | None) -> list[dict[str, str]]:
    """活跃概念板块名录（``boardCode`` → ``name``，code 升序）。

    与成分表解耦：成分归属已随批量端点回齐（``concept_code``），本列表只给
    UI 做板块下拉与码→名映射。形状守卫：缓存回读非预期形状一律按 miss。
    """
    if cache:
        cached = await cache.get(_CONCEPT_BOARDS_CACHE_KEY)
        if isinstance(cached, list):
            return [
                {"boardCode": str(v["boardCode"]), "name": str(v["name"])}
                for v in cached
                if isinstance(v, dict) and "boardCode" in v and "name" in v
            ]
    rows = await _fetch_concept_boards(db)
    out = [
        {"boardCode": str(r["board_code"]), "name": str(r["board_name"])}
        for r in rows
        if r.get("board_code") is not None
    ]
    if cache and out:
        await cache.set(_CONCEPT_BOARDS_CACHE_KEY, out, _CACHE_TTL)
    return cast(list[dict[str, str]], out)


# ---------------------------------------------------------------------------
# 申万 L1 组名（码 → 名称）：从 `sw_industry_classes` 取（**权威源是表**）。
# ---------------------------------------------------------------------------

_SW_L1_NAMES_SQL = text(
    """
    SELECT industry_code, industry_name
    FROM sw_industry_classes
    WHERE level = 1
    ORDER BY industry_code
    """
)


async def list_sw_l1(db: AsyncSession, cache: CacheClient | None) -> list[dict[str, str]]:
    """申万一级行业 ``{code, name}``（码升序；当前态类目表）。

    与热力图行上的 ``sw_l1_code`` 配对做组头名称；表里没有的码（理论上不会）
    前端回落显示码本身。分类树权威在 ``sw_industry_classes``（S1.4 同源），
    这里只是读面的薄封装。
    """
    cache_key = "market:heatmap:sw-l1"
    if cache:
        cached = await cache.get(cache_key)
        if isinstance(cached, list):
            return [
                {"code": str(v["code"]), "name": str(v["name"])}
                for v in cached
                if isinstance(v, dict) and "code" in v and "name" in v
            ]
    rows = (await db.execute(_SW_L1_NAMES_SQL)).mappings().all()
    out = [{"code": str(r["industry_code"]), "name": str(r["industry_name"])} for r in rows]
    if cache and out:
        await cache.set(cache_key, out, _CACHE_TTL)
    return out


def build_heatmap_groups(
    items: list[dict[str, Any]],
    *,
    group: str,
    code_to_name: dict[str, str] | None = None,
) -> list[HeatmapGroup]:
    """把平铺 items 组成分组树（纯函数，前端/后端同口径；前端镜像在
    frontend/src/features/market/heatmapTree.ts —— 两边只锁「键与排序」）。

    * ``group="all"`` → 单一「全市场」组（前端可直接平铺，组结构仍回以保证
      树形协议一致）；
    * ``group="sw_l1"`` → 按 ``sw_l1_code`` 分组，组名走 ``code_to_name``
      （来自 :func:`list_sw_l1`），无码行落「未分类」；
    * ``group="concept"`` → 按 ``concept_code`` 分组，组名走 ``code_to_name``
      （来自 :func:`list_concept_boards`），无码行落「未分类」；
    * ``group="watchlist"`` → 调用方先自行过滤 items，再以「自选」单组进来。

    组序 = 组内首个成员的行序（行序 = ``stock_id`` 升序，稳定可复现）；
    组内排序 = 面积字段在前端做（本函数不排序数值，只定组结构）。
    """
    if group == "sw_l1":
        key_field = "sw_l1_code"
    elif group == "concept":
        key_field = "concept_code"
    else:  # all / watchlist：单组
        key_field = None

    if key_field is None:
        if not items:
            return []
        label = "自选" if group == "watchlist" else "全市场"
        return [HeatmapGroup(code="", name=label, count=len(items))]

    names = code_to_name or {}
    groups: dict[str, int] = {}
    order: list[str] = []
    for item in items:
        code = item.get(key_field)
        code = str(code) if code else ""
        if code not in groups:
            groups[code] = 0
            order.append(code)
        groups[code] += 1
    out: list[HeatmapGroup] = []
    for code in order:
        out.append(HeatmapGroup(code=code, name=names.get(code, ""), count=groups[code]))
    return out
