"""Market endpoints for dashboard data."""

import re
from datetime import date, timedelta
from typing import Literal, cast

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status

from app.api.deps import CacheDep, DbDep, require_permissions
from app.core.exceptions import not_found_response
from app.schemas.heatmap import ConceptBoardRef, HeatmapTreemapOut
from app.schemas.market import BoardStockOut, HotBoardsOut, MarketListOut
from app.schemas.panorama import MarketPanoramaOut
from app.schemas.quote import IndexDailyOut, IndexKlineResponse
from app.schemas.ranking import RankingResponseOut, RankingType
from app.schemas.ranking_board import RankingBoardResponseOut
from app.schemas.sse_index import (
    BackfillRequest,
    BackfillResponse,
    SseIntradayPoint,
    SseIntradayResponse,
    SseSnapshotOut,
)
from app.schemas.stock import StockEnrichedOut, StockOut
from app.schemas.sw_drilldown import (
    MarketIndustryLevelOut,
    MarketIndustryMembersOut,
    MarketIndustryQuoteOut,
)
from app.schemas.sw_performance import SwPerformanceResponseOut
from app.services import (
    heatmap_service,
    market_panorama_service,
    market_service,
    ranking_board_service,
    stock_tag_service,
    sw_drilldown_service,
)

router = APIRouter()


@router.get("/indices", response_model=list[dict])
async def list_market_indices(cache: CacheDep) -> list[dict]:
    return await market_service.list_market_indices(cache=cache)


@router.get("/panorama", response_model=MarketPanoramaOut)
async def get_market_panorama(
    cache: CacheDep,
    days: int = Query(default=60, ge=5, le=250, description="展示交易日数（读日表，不现算）"),
) -> MarketPanoramaOut:
    """A股全景：广度 + 结构 + 资金（读 `market_breadth_daily` / `market_industry_daily`）。

    一次请求 4 组范围查询（无 N+1），Redis 120s；响应自带 `as_of/expected_as_of/stale`
    与 `flow_available_from`，缺失不补 0。
    """
    payload = await market_panorama_service.get_panorama(cache, days)
    return MarketPanoramaOut.model_validate(payload)


@router.get("/distribution", response_model=MarketListOut)
async def get_distribution(cache: CacheDep) -> MarketListOut:
    return MarketListOut.model_validate(await market_service.get_distribution(cache=cache))


@router.get("/rankings", response_model=RankingResponseOut)
async def get_rankings(
    cache: CacheDep,
    db: DbDep,
    type: RankingType = Query(default="gainers"),
    limit: int = Query(default=20, ge=1, le=100),
) -> RankingResponseOut:
    """公开榜单。口径 T+1，见响应 as_of。"""
    return await market_service.get_rankings(db, cache, type, limit)


# ---------------------------------------------------------------------------
# S1.6 A股 7 榜（公开免登录）
#
# 为什么是**新端点**而不是给 /rankings 加 type：那 5 个榜是 daily_quotes 单列排序
# （_RANKING_ORDER 的形状装不下财务来源 + 窗口聚合），7 榜另有来源表、PIT 报告期与
# 样本口径，形状不同则端点分开——既有 5 个榜的入参、响应与缓存键一个字节都没动
# （Ruling 30）。
#
# 路径段用 `boards/` 而非平铺 `/rankings/{key}`：`/rankings` 是既有端点，平铺会把
# 新 key 与既有 `type` 的查询参数语义挤在同一个 URL 空间里；显式一段前缀让两者
# 各自可演进而互不干扰。
# ---------------------------------------------------------------------------


@router.get("/rankings/boards/{key}", response_model=RankingBoardResponseOut)
async def get_ranking_board(
    key: str,
    cache: CacheDep,
    db: DbDep,
    limit: int = Query(default=100, ge=1, le=100),
) -> RankingBoardResponseOut:
    """A股 7 榜之一（市值 / 股息率 / 月度涨幅 / 月度跌幅 / 半年利润 / 年利润 / ROE）。

    每榜默认 100 强；响应携带口径三件事（交易日 / 财务期 / 样本范围）与
    「不足 100 条」的结构化说明，前端不得自行编造口径。
    """
    try:
        return await ranking_board_service.get_ranking_board(db, cache, key, limit)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown ranking board key: {key}",
        ) from exc


@router.get("/sectors", response_model=MarketListOut)
async def get_sectors(
    cache: CacheDep,
    day: date | None = Query(
        default=None,
        alias="date",
        description="回看交易日（缺省=最近完整日；非交易日/未来日 404）",
    ),
) -> MarketListOut:
    payload = await market_service.get_sectors(cache=cache, day=day)
    if payload is None:
        raise HTTPException(status_code=404, detail="该日没有完整行情")
    return MarketListOut.model_validate(payload)


@router.get("/capital-flow", response_model=MarketListOut)
async def get_capital_flow(cache: CacheDep) -> MarketListOut:
    return MarketListOut.model_validate(await market_service.get_capital_flow(cache=cache))


@router.get("/hot-boards", response_model=HotBoardsOut)
async def get_hot_boards(
    cache: CacheDep,
    category: Literal["industry", "concept", "region"] = Query(default="industry"),
) -> HotBoardsOut:
    """热门板块：东财板块体系（真实 BK code）；东财不可用时回落本地分组并标注。"""
    return HotBoardsOut.model_validate(await market_service.get_hot_boards(category, cache=cache))


#: 东财板块码：BK + 数字（如 BK1518）。其它一律 400——不把畸形码透给上游。
_BOARD_CODE_PATTERN = re.compile(r"^BK\d+$")


@router.get("/boards/{board_code}/stocks", response_model=list[BoardStockOut])
async def get_board_stocks(
    board_code: str,
    cache: CacheDep,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[BoardStockOut]:
    """东财板块成分股（主力净流入降序）；上游不可用 → 502（不返回假空列表）。"""
    if _BOARD_CODE_PATTERN.fullmatch(board_code) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="board_code must look like an East Money board code, e.g. BK1518",
        )
    try:
        rows = await market_service.get_board_stocks(board_code, limit=limit, cache=cache)
    except Exception as exc:
        # 无本地替代源：空列表会被前端当"该板块没有成分股"，比 502 更坏。
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="eastmoney board constituents unavailable",
        ) from exc
    return [BoardStockOut.model_validate(row) for row in rows]


@router.get("/indices/{ts_code}/kline", response_model=IndexKlineResponse)
async def get_index_kline(
    ts_code: str,
    cache: CacheDep,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
) -> IndexKlineResponse:
    """Return index daily K-line data for charting."""
    if start is None:
        start = date.today() - timedelta(days=365)
    data = await market_service.get_index_kline(
        ts_code,
        start_date=start,
        end_date=end,
        cache=cache,
    )
    name = market_service.INDEX_NAME_MAP.get(ts_code, ts_code)
    return IndexKlineResponse(
        ts_code=ts_code,
        name=name,
        data=[IndexDailyOut.model_validate(d) for d in data],
    )


@router.get("/sw-industry/tree", response_model=list[dict])
async def get_sw_industry_tree(cache: CacheDep) -> list[dict]:
    return await market_service.get_sw_industry_tree(cache=cache)


@router.get("/sw-industry/options", response_model=list[dict])
async def get_sw_industry_options(
    db: DbDep,
    level: int = Query(ge=2, le=3, description="SW industry level (2 or 3)"),
) -> list[dict]:
    """Return SW industry nodes at the given level for dropdown selection."""
    return await stock_tag_service.list_sw_options(db, level)


@router.get("/sw-industry/performance", response_model=SwPerformanceResponseOut)
async def get_sw_industry_performance(
    cache: CacheDep,
    db: DbDep,
    limit: int = Query(default=31, ge=1, le=31),
) -> SwPerformanceResponseOut:
    """公开申万一级行业行情聚合。口径 T+1，见响应 as_of。"""
    return await market_service.get_sw_industry_performance(db, cache, limit)


# ---------------------------------------------------------------------------
# S1.5 市场/个股热力图（批量读路）
#
# 为什么是**新端点**而不是提高某个既有端点的 limit：现有行情读路全部是小上限
# 分页（rankings ≤100 / board stocks ≤200 / sw-performance ≤31 / market_data ≤100），
# 各自的 SQL 与缓存形状都为「Top-N」服务；热力图要的是「一天 × 全市场」的一次性
# 快照（≈5500 行 × 5 字段），复用任何一个都要么被 limit 卡死、要么把聚合 SQL
# 改成全量行——后者会动到既有端点的缓存契约（Ruling 30 的回归面）。
# 独立读路 = 独立缓存键（`market:heatmap:treemap:{day}`，TTL 300s），互不牵连。
#
# 鉴权：`/market/**` 公开免登录（ADR 0008），**不挂 RequireAuth / require_permissions**。
# ---------------------------------------------------------------------------


@router.get("/heatmap/treemap", response_model=HeatmapTreemapOut)
async def get_heatmap_treemap(
    cache: CacheDep,
    db: DbDep,
    day: date | None = Query(
        default=None,
        alias="date",
        description="回看交易日（缺省=最近完整日；非交易日/未来日 404）",
    ),
) -> HeatmapTreemapOut:
    """全市场热力图批量读路：判据日一行一票，≈5500 行 × 5 字段 + 2 个分组码。

    四个分组维度（全市场 / 申万一级 / 概念板块 / 自选）共用这一批 items，
    分组在前端完成——分组形态随用户切换而变，数据不该重新传输。
    单位见响应 `amount_unit`（千元）/ `mv_unit`（万元），前端标注直接消费。
    """
    out = await heatmap_service.get_treemap(db, cache, day)
    if out is None:
        raise HTTPException(status_code=404, detail="该日没有完整行情")
    return out


@router.get("/heatmap/concept-boards", response_model=list[ConceptBoardRef])
async def get_heatmap_concept_boards(cache: CacheDep, db: DbDep) -> list[ConceptBoardRef]:
    """概念板块下拉名录（活跃板块，码升序）：给热力图「概念板块」维度做码→名映射。"""
    boards = await heatmap_service.list_concept_boards(db, cache)
    return [ConceptBoardRef.model_validate(b) for b in boards]


@router.get("/heatmap/sw-l1", response_model=list[dict])
async def get_heatmap_sw_l1(cache: CacheDep, db: DbDep) -> list[dict]:
    """申万一级行业 ``{code, name}``：给热力图「申万一级」维度做码→名映射。

    与 `/sw-industry/options?level=1` 的差别：那条走 `stock_tag_service`（个股打标
    场景），本条是热力图组头的薄映射（同一张权威表 `sw_industry_classes`，不另立口径）。
    """
    return await heatmap_service.list_sw_l1(db, cache)


# ---------------------------------------------------------------------------
# S1.4 申万三级下钻（SCD2 分类归属 + 7 项行情字段）
#
# 路径前缀 `/sw-industry/drill/` 而不是复用一个 `/{code}` 段：本文件里既有
# `/sw-industry/{level1_code}/stocks` 这类"单段即 level1"的路由，若把
# `/sw-industry/levels/{level}` 放在它们前面，FastAPI 先注册先匹配不会出问题，
# 但交换声明顺序就会把 `levels` 当成 level1_code（实测易错）。显式 `drill` 段
# 把新读路与既有读路的命名空间彻底分开，顺序无关。
#
# 鉴权：`/market/**` 公开免登录（ADR 0008），**不挂 RequireAuth / require_permissions**。
# ---------------------------------------------------------------------------


@router.get("/sw-industry/drill/levels/{level}", response_model=MarketIndustryLevelOut)
async def get_sw_drill_levels(
    level: int,
    db: DbDep,
    cache: CacheDep,
    level1_code: str = Query(default="", description="按 L1 过滤（level>=2 时必填）"),
    level2_code: str = Query(default="", description="按 L2 过滤（level=3 时给）"),
    as_of: date | None = Query(default=None, description="行情口径日；缺省 = 最新完整交易日"),
) -> MarketIndustryLevelOut:
    """申万行业某一层的下钻列表（逐子行业带 7 项行情聚合）。

    * ``level=1`` → 31 个一级行业；
    * ``level=2&level1_code=`` → 该 L1 下的二级行业（``parent`` 带 L1 自身聚合）；
    * ``level=3&level1_code=&level2_code=`` → 该 L2 下的三级行业。

    ``as_of`` 可传历史交易日：成分归属按 **SCD2** 还原（``effective_from``/``effective_to``）。
    """
    if level not in (1, 2, 3):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="level 只能取 1/2/3"
        )
    if level >= 2 and not level1_code:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="level>=2 需要 level1_code"
        )
    if level == 3 and not level2_code:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="level=3 需要 level2_code"
        )
    try:
        return await sw_drilldown_service.get_level_items(
            db, cache, level1_code=level1_code, level2_code=level2_code, as_of=as_of
        )
    except LookupError as exc:
        raise not_found_response("SW industry", str(exc)) from exc


@router.get("/sw-industry/drill/metrics", response_model=MarketIndustryQuoteOut)
async def get_sw_drill_metrics(
    db: DbDep,
    cache: CacheDep,
    level1_code: str = Query(..., description="L1 行业代码"),
    level2_code: str = Query(default=""),
    level3_code: str = Query(default=""),
    as_of: date | None = Query(default=None),
) -> MarketIndustryQuoteOut:
    """单个申万行业节点的 7 项行情聚合。

    7 项 = K 线（成分股 OHLC 中位数）· 涨跌幅 · 换手率 · 成交量 · 量比 · 振幅 · 成交额。
    """
    try:
        return await sw_drilldown_service.get_level_metrics(
            db,
            cache,
            level1_code=level1_code,
            level2_code=level2_code,
            level3_code=level3_code,
            as_of=as_of,
        )
    except LookupError as exc:
        raise not_found_response("SW industry", str(exc)) from exc


@router.get("/sw-industry/drill/members", response_model=MarketIndustryMembersOut)
async def get_sw_drill_members(
    db: DbDep,
    cache: CacheDep,
    level1_code: str = Query(...),
    level2_code: str = Query(...),
    level3_code: str = Query(...),
    as_of: date | None = Query(default=None),
) -> MarketIndustryMembersOut:
    """末级（L3）成分股列表：逐只 7 项字段 + 该节点聚合 + L1→L2→L3 链。

    成分按 **SCD2 区间**还原（``as_of`` 当日有效的那一段），响应里的
    ``scd2_as_of`` 是"分类归属口径日"（最近一次成功 ingest），不得省略。
    """
    try:
        return await sw_drilldown_service.get_members(
            db,
            cache,
            level1_code=level1_code,
            level2_code=level2_code,
            level3_code=level3_code,
            as_of=as_of,
        )
    except LookupError as exc:
        raise not_found_response("SW industry", str(exc)) from exc


@router.get("/sw-industry/{level1_code}/stocks", response_model=list[StockOut])
async def get_sw_level1_stocks(level1_code: str, db: DbDep) -> list[StockOut]:
    if await market_service.get_sw_level1(level1_code) is None:
        raise not_found_response("SW level1", level1_code)
    symbols = await market_service.list_symbols_by_level1(level1_code)
    return await market_service.list_stocks_by_symbols(db, symbols)


@router.get("/sw-industry/{level1_code}/{level2_code}/stocks", response_model=list[StockOut])
async def get_sw_level2_stocks(level1_code: str, level2_code: str, db: DbDep) -> list[StockOut]:
    if level1_code == "OTHER" and level2_code.startswith("OTHER_"):
        industry_name = level2_code[len("OTHER_") :]
        info = await market_service.get_sw_other_level2(industry_name)
        if info is None:
            raise not_found_response("SW OTHER level2", level2_code)
        symbols = await market_service.list_symbols_by_other_level2(industry_name)
        return await market_service.list_stocks_by_symbols(db, symbols)

    if await market_service.get_sw_level2(level1_code, level2_code) is None:
        raise not_found_response("SW level2", f"{level1_code}/{level2_code}")
    symbols = await market_service.list_symbols_by_level2(level1_code, level2_code)
    return await market_service.list_stocks_by_symbols(db, symbols)


@router.get(
    "/sw-industry/{level1_code}/{level2_code}/{level3_code}/stocks",
    response_model=list[StockOut],
)
async def get_sw_level3_stocks(
    level1_code: str, level2_code: str, level3_code: str, db: DbDep
) -> list[StockOut]:
    if await market_service.get_sw_level3(level1_code, level2_code, level3_code) is None:
        raise not_found_response("SW level3", f"{level1_code}/{level2_code}/{level3_code}")
    symbols = await market_service.list_symbols_by_level3(level1_code, level2_code, level3_code)
    return await market_service.list_stocks_by_symbols(db, symbols)


# ---------------------------------------------------------------------------
# Enriched SW industry stock endpoints — include latest quote + daily_basic
# ---------------------------------------------------------------------------


@router.get(
    "/sw-industry/{level1_code}/stocks/enriched",
    response_model=list[StockEnrichedOut],
)
async def get_sw_level1_stocks_enriched(
    level1_code: str,
    db: DbDep,
) -> list[StockEnrichedOut]:
    if await market_service.get_sw_level1(level1_code) is None:
        raise not_found_response("SW level1", level1_code)
    symbols = await market_service.list_symbols_by_level1(level1_code)
    return await market_service.get_stocks_enriched_by_symbols(db, symbols)


@router.get(
    "/sw-industry/{level1_code}/{level2_code}/stocks/enriched",
    response_model=list[StockEnrichedOut],
)
async def get_sw_level2_stocks_enriched(
    level1_code: str,
    level2_code: str,
    db: DbDep,
) -> list[StockEnrichedOut]:
    if level1_code == "OTHER" and level2_code.startswith("OTHER_"):
        industry_name = level2_code[len("OTHER_") :]
        info = await market_service.get_sw_other_level2(industry_name)
        if info is None:
            raise not_found_response("SW OTHER level2", level2_code)
        symbols = await market_service.list_symbols_by_other_level2(industry_name)
        return await market_service.get_stocks_enriched_by_symbols(db, symbols)

    if await market_service.get_sw_level2(level1_code, level2_code) is None:
        raise not_found_response("SW level2", f"{level1_code}/{level2_code}")
    symbols = await market_service.list_symbols_by_level2(level1_code, level2_code)
    return await market_service.get_stocks_enriched_by_symbols(db, symbols)


@router.get(
    "/sw-industry/{level1_code}/{level2_code}/{level3_code}/stocks/enriched",
    response_model=list[StockEnrichedOut],
)
async def get_sw_level3_stocks_enriched(
    level1_code: str,
    level2_code: str,
    level3_code: str,
    db: DbDep,
) -> list[StockEnrichedOut]:
    if await market_service.get_sw_level3(level1_code, level2_code, level3_code) is None:
        raise not_found_response("SW level3", f"{level1_code}/{level2_code}/{level3_code}")
    symbols = await market_service.list_symbols_by_level3(level1_code, level2_code, level3_code)
    return await market_service.get_stocks_enriched_by_symbols(db, symbols)


# ---------------------------------------------------------------------------
# SSE index snapshots
# ---------------------------------------------------------------------------

_SSE_CACHE_TTL = 60  # 1 minute — snapshots update every 10 min


@router.get("/sse-snapshots/latest", response_model=list[SseSnapshotOut])
async def get_sse_latest_snapshots(db: DbDep, cache: CacheDep) -> list[SseSnapshotOut]:
    """Return the most recent snapshot for every tracked SSE index."""
    cache_key = "market:sse-snapshots:latest"
    cached = await cache.get(cache_key)
    if cached is not None:
        return cast(list[SseSnapshotOut], cached)

    from app.repositories import sse_index_repo  # noqa: PLC0415

    rows = await sse_index_repo.get_latest_snapshots(db)
    result = [SseSnapshotOut.model_validate(r) for r in rows]
    if result:
        await cache.set(cache_key, [r.model_dump(mode="json") for r in result], _SSE_CACHE_TTL)
    return result


@router.get("/sse-snapshots/{code}/intraday", response_model=SseIntradayResponse)
async def get_sse_intraday(
    code: str,
    db: DbDep,
    cache: CacheDep,
    trade_date: date | None = Query(default=None, alias="date"),
) -> SseIntradayResponse:
    """Return intraday snapshots for a single index on the given date."""
    if trade_date is None:
        trade_date = date.today()

    cache_key = f"market:sse-intraday:{code}:{trade_date}"
    cached = await cache.get(cache_key)
    if cached is not None:
        return SseIntradayResponse(**cached)

    from app.repositories import sse_index_repo  # noqa: PLC0415

    rows = await sse_index_repo.get_intraday_by_date(db, code, trade_date)
    if not rows:
        return SseIntradayResponse(code=code, name=code, trade_date=trade_date, data=[])

    name = rows[0].name
    data = [
        SseIntradayPoint(
            time=r.collect_time.strftime("%H:%M"),
            last=float(r.last),
            chg_rate=float(r.chg_rate) if r.chg_rate is not None else None,
        )
        for r in rows
    ]
    resp = SseIntradayResponse(code=code, name=name, trade_date=trade_date, data=data)
    await cache.set(cache_key, resp.model_dump(mode="json"), _SSE_CACHE_TTL)
    return resp


@router.post(
    "/sse-snapshots/backfill",
    response_model=BackfillResponse,
    status_code=202,
    dependencies=[require_permissions("tasks:trigger")],
)
async def trigger_sse_backfill(
    req: BackfillRequest,
    background_tasks: BackgroundTasks,
) -> BackfillResponse:
    """Trigger a background backfill of historical SSE snapshots."""
    from app.services import sse_scraper_service  # noqa: PLC0415

    background_tasks.add_task(
        sse_scraper_service.batch_backfill,
        req.start_date,
        req.end_date,
    )
    return BackfillResponse(
        message="Backfill task started",
        start_date=req.start_date,
        end_date=req.end_date,
    )
