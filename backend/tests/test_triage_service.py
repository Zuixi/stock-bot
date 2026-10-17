"""组合层与装载层：单股合并 / severity 排序 / ≤8 折叠 / quiet_count / 固定查询数.

两类用例分开写：
- **纯函数**（``compose``）：不碰库，锁 spec §S8.4 的合并/排序/折叠语义；
- **真 PG**（``load_context`` / ``get_triage`` / ``user_symbols``）：锁"查询数不随自选数
  增长"这条反 N+1 门禁，以及"缺数据进 ``unavailable`` 而不是静默无事件"。

真 PG 用例一律在**回滚**的事务里跑（Ruling 17）：跑完库与跑前一致；库不可达时 skip 而非红。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date, datetime, timedelta
from typing import Any

import asyncpg
import pytest
from sqlalchemy import event, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.market_data import Announcement, DragonTigerEntry, StockPriceLimit
from app.models.quote import DailyQuote
from app.models.stock import Stock
from app.models.sw_industry import SwIndustryClass, SwIndustryMember
from app.models.watchlist import UserWatchlist, UserWatchlistItem
from app.services import triage_detectors as td
from app.services import triage_service as svc
from app.services.market_day_service import MarketDay

_DEFAULT_ASYNC = "postgresql+asyncpg://stock_user:stock_pass@localhost:5433/stock_bot_test"
ASYNC_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or _DEFAULT_ASYNC
SYNC_URL = ASYNC_URL.replace("+asyncpg", "")

#: 合成数据的"当日"：刻意取库里不存在的日期，避免与真实行情/申万成分互相污染
AS_OF = date(2026, 10, 16)
_NOW = datetime(2026, 10, 16, 18, 0)
_SYMBOLS = [f"T9{i:04d}" for i in range(1, 22)]  # T90001..T90021（T90021 故意无行情）
_QUOTE_DAYS = 70


def _pg_reachable() -> bool:
    async def _go() -> bool:
        try:
            conn = await asyncpg.connect(SYNC_URL)
        except Exception:
            return False
        await conn.close()
        return True

    return asyncio.run(_go())


_PG_OK = _pg_reachable()

requires_pg = pytest.mark.skipif(
    not _PG_OK, reason=f"PostgreSQL unavailable at {SYNC_URL} (set TEST_DATABASE_URL)"
)


# ---------------------------------------------------------------------------
# 纯函数：compose（spec §S8.4）
# ---------------------------------------------------------------------------


def _item(
    symbol: str,
    severity: str,
    detector: str = "drop_sharp",
    *,
    facts: tuple[td.Fact, ...] = (),
    day_pct: float | None = None,
) -> td.TriageItem:
    return td.TriageItem(
        symbol=symbol,
        name=symbol,
        detector=detector,
        severity=severity,
        headline=f"{detector} 命中",
        facts=facts,
        as_of=AS_OF,
        source="daily_quotes",
        verify="kline",
        day_pct=day_pct,
    )


def test_compose_merges_same_symbol_and_keeps_highest_severity() -> None:
    items = [
        _item("600000", "attention", "volume_spike"),
        _item("600000", "risk"),
        _item("000001", "event"),
    ]
    merged, folded = svc.compose(items)
    assert folded == 0
    assert [item.symbol for item in merged] == ["600000", "000001"]
    assert merged[0].severity == "risk"
    assert "另有 1 项" in merged[0].headline


def test_compose_merges_facts_of_all_hits() -> None:
    """合并会隐藏细节 → facts 必须合并（spec §S8.4 的"facts 合并 + 复核入口保留"）。"""
    items = [
        _item("600000", "risk", "drop_sharp", facts=(td.Fact("当日涨跌", -8.0, "%"),)),
        _item("600000", "attention", "volume_spike", facts=(td.Fact("量比(20日)", 3.0, "倍"),)),
        _item("600000", "event", "dragon_tiger", facts=(td.Fact("上榜次数", "1", "次"),)),
    ]
    merged, _ = svc.compose(items)
    assert len(merged) == 1
    # facts 顺序 = 主条目（最高 severity）在前，其余按 severity 次序并入
    labels = [fact.label for fact in merged[0].facts]
    assert labels == ["当日涨跌", "上榜次数", "量比(20日)"]
    assert "另有 2 项" in merged[0].headline


def test_compose_sorts_by_severity_then_abs_day_pct() -> None:
    items = [
        _item("a", "background"),
        _item("b", "risk", day_pct=-1.0),
        _item("c", "event"),
        _item("d", "risk", day_pct=-9.0),
    ]
    merged, _ = svc.compose(items)
    # 同级按 |当日涨跌幅| 降序：d(-9%) 在 b(-1%) 之前
    assert [item.symbol for item in merged] == ["d", "b", "c", "a"]
    assert [item.severity for item in merged] == ["risk", "risk", "event", "background"]


def test_compose_folds_beyond_limit() -> None:
    items = [_item(f"{i:06d}", "attention") for i in range(12)]
    merged, folded = svc.compose(items, limit=8)
    assert len(merged) == 8
    assert folded == 4


def test_compose_keeps_everything_when_under_limit() -> None:
    items = [_item("600000", "risk", day_pct=-8.0)]
    merged, folded = svc.compose(items)
    assert merged == items
    assert folded == 0


def test_compose_with_zero_limit_folds_everything() -> None:
    items = [_item("600000", "risk"), _item("000001", "event")]
    merged, folded = svc.compose(items, limit=0)
    assert merged == []
    assert folded == 2


def test_load_context_without_symbols_makes_no_query() -> None:
    """空自选 → 空 context 且**一条查询都不发**（否则每次空页都白打库）。"""

    async def _go() -> td.TriageContext:
        return await svc.load_context(None, [], AS_OF)  # type: ignore[arg-type]

    ctx = asyncio.run(_go())
    assert ctx.symbols_order == []
    assert ctx.quotes == {}


def test_user_symbols_filters_by_watchlist_id() -> None:
    """指定 ``watchlist_id`` 时只看那一张表；别人的表 id → 空（不得越权）。"""
    user_a = uuid.uuid4()
    foreign_id = uuid.uuid4()

    async def _go() -> Any:
        engine = create_async_engine(ASYNC_URL)
        try:
            async with engine.connect() as conn:
                trans = await conn.begin()
                session = AsyncSession(bind=conn)
                try:
                    wl_a = UserWatchlist(
                        id=uuid.uuid4(), user_id=user_a, name="默认自选", is_default=True
                    )
                    wl_b = UserWatchlist(
                        id=uuid.uuid4(), user_id=user_a, name="第二张", is_default=False
                    )
                    session.add_all([wl_a, wl_b])
                    await session.flush()
                    session.add_all(
                        [
                            UserWatchlistItem(
                                id=uuid.uuid4(),
                                watchlist_id=wl_a.id,
                                symbol="600000",
                                sort_order=0,
                            ),
                            UserWatchlistItem(
                                id=uuid.uuid4(),
                                watchlist_id=wl_b.id,
                                symbol="000001",
                                sort_order=0,
                            ),
                        ]
                    )
                    await session.flush()
                    own = await svc.user_symbols(session, user_a, watchlist_id=wl_b.id)
                    foreign = await svc.user_symbols(session, user_a, watchlist_id=foreign_id)
                    return own, foreign
                finally:
                    await session.close()
                    await trans.rollback()
        finally:
            await engine.dispose()

    own, foreign = asyncio.run(_go())
    assert own == ["000001"]
    assert foreign == []


# ---------------------------------------------------------------------------
# 无可用交易日：不静默报"都平静"，而是全部探测器进 unavailable
# ---------------------------------------------------------------------------


def test_get_triage_without_any_trading_day_marks_all_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _no_day(_db: Any) -> None:
        return None

    monkeypatch.setattr(svc, "resolve_latest_complete_day", _no_day)
    result = asyncio.run(svc.get_triage(None, ["600000"]))  # type: ignore[arg-type]
    assert result.items == []
    assert result.quiet_count == 0, "没有数据就不得宣称'今天都平静'"
    # 第四态：有自选但没有完整交易日 → no_data（不是 no_watchlist，也不是 ok）
    assert result.state == "no_data"
    assert result.unavailable_keys == sorted(spec.key for spec in td.TRIAGE_DETECTORS)
    # 上屏字段是**人话**：一个机器串都不许有（含 "_" 即漏了内部 key）
    assert result.unavailable == [spec.label for spec in td.TRIAGE_DETECTORS], (
        "unavailable 必须是 registry 的 label（registry 顺序）"
    )
    assert not any("_" in label for label in result.unavailable)


def test_get_triage_without_watchlist_is_no_watchlist_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一只自选都没有 → ``no_watchlist``，且不报"8 项检查缺数据"（没有东西可查）。

    这条防的是前端把"有自选但今天没数据"渲染成"还没有自选股"：两种空态必须
    在**契约层**就能区分，不能靠 ``items=[] & quiet_count=0`` 猜。
    """

    async def _boom(_db: Any) -> None:
        raise AssertionError("没有自选不得去解析交易日（白跑查询）")

    monkeypatch.setattr(svc, "resolve_latest_complete_day", _boom)
    result = asyncio.run(svc.get_triage(None, []))  # type: ignore[arg-type]
    assert result.state == "no_watchlist"
    assert result.items == []
    assert result.quiet_count == 0
    assert result.unavailable == [], "无自选时没有'缺数据'可言，别拿 8 条 label 吓用户"
    assert result.unavailable_keys == []


# ---------------------------------------------------------------------------
# 真 PG：装载层
# ---------------------------------------------------------------------------


async def _with_rollback(coro_factory: Any) -> Any:
    """在一个**回滚**的事务里跑（测试不向库里留任何行）。"""
    engine = create_async_engine(ASYNC_URL)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            session = AsyncSession(bind=conn)
            try:
                return await coro_factory(session, conn)
            finally:
                await session.close()
                await trans.rollback()
    finally:
        await engine.dispose()


async def _seed(session: AsyncSession) -> None:
    """合成底座：21 只股票 / 70 天行情 / 涨停价 / 公告 / 龙虎榜 / 申万两级成分。"""
    await session.execute(
        insert(Stock),
        [
            {
                "exchange": "Shanghai_Stocks",
                "symbol": symbol,
                "name": f"测试{symbol}",
                "category": "A股",
                "asof": _NOW,
            }
            for symbol in _SYMBOLS
        ],
    )
    stock_rows = (
        await session.execute(select(Stock.id, Stock.symbol).where(Stock.symbol.in_(_SYMBOLS)))
    ).all()
    id_of = {str(symbol): int(stock_id) for stock_id, symbol in stock_rows}

    # 甲板块的成分股当日 -8%（命中 drop_sharp / break_ma20 / new_high_low）；
    # 乙板块的 T90020 当日 0%（给"分位"提供分母，自身不命中任何探测器）；
    # T90021 刻意不给行情（"未被判定"≠"今天没事"，不得计入 quiet_count）。
    quote_rows: list[dict[str, Any]] = []
    for symbol in _SYMBOLS[:20]:
        for offset in range(_QUOTE_DAYS, 0, -1):
            day = AS_OF - timedelta(days=offset - 1)
            last = day == AS_OF
            drop = last and symbol != "T90020"
            quote_rows.append(
                {
                    "stock_id": id_of[symbol],
                    "trade_date": day,
                    "close": 9.2 if drop else 10.0,
                    "pre_close": 10.0,
                    "high": 10.2 if last else 10.1,
                    "low": 9.1 if drop else 9.9,
                    "open": 10.0,
                    "amount": 1000.0,
                }
            )
    await session.execute(insert(DailyQuote), quote_rows)

    await session.execute(
        insert(StockPriceLimit),
        [
            {
                "trade_date": AS_OF,
                "stock_id": id_of["T90001"],
                "ts_code": "T90001.SH",
                "pre_close": 10.0,
                "up_limit": 11.0,
                "down_limit": 9.0,
            }
        ],
    )
    await session.execute(
        insert(Announcement),
        [
            {
                "announcement_id": "TRIAGE-TEST-1",
                "sec_code": "T90001",
                "sec_name": "测试T90001",
                "title": "关于股东权益变动的提示性公告",
                "announce_time": datetime(2026, 10, 15, 9, 30),
                "category": "equity_change",
            }
        ],
    )
    await session.execute(
        insert(DragonTigerEntry),
        [
            {
                "trade_date": AS_OF,
                "ts_code": "T90001.SH",
                "name": "测试T90001",
                "net_amount": 1.2e7,
                "reason": "日涨幅偏离值达 7%",
            }
        ],
    )
    await session.execute(
        insert(SwIndustryClass),
        [
            {"industry_code": "T91001", "level": 1, "industry_name": "测试一级甲"},
            {
                "industry_code": "T92001",
                "level": 2,
                "industry_name": "测试二级甲",
                "parent_code": "T91001",
            },
            {
                "industry_code": "T93001",
                "level": 3,
                "industry_name": "测试三级甲",
                "parent_code": "T92001",
            },
            {"industry_code": "T91002", "level": 1, "industry_name": "测试一级乙"},
            {
                "industry_code": "T92002",
                "level": 2,
                "industry_name": "测试二级乙",
                "parent_code": "T91002",
            },
            {
                "industry_code": "T93002",
                "level": 3,
                "industry_name": "测试三级乙",
                "parent_code": "T92002",
            },
        ],
    )
    await session.execute(
        insert(SwIndustryMember),
        [
            {
                "industry_code": "T93001",
                "stock_code": f"{symbol}.SH",
                "symbol": symbol,
                "stock_name": f"测试{symbol}",
            }
            for symbol in _SYMBOLS[:19]
        ]
        + [
            {
                "industry_code": "T93002",
                "stock_code": "T90020.SH",
                "symbol": "T90020",
                "stock_name": "测试T90020",
            }
        ],
    )


@requires_pg
def test_load_context_query_count_is_constant() -> None:
    """**反 N+1 门禁**：查询数 ≤6 且不随自选数增长（1 / 5 / 20 只都一样）。"""

    async def _go(session: AsyncSession, conn: Any) -> tuple[int, int, int]:
        await _seed(session)
        counts: list[int] = []
        for n in (1, 5, 20):
            calls = 0

            def _count(*_args: Any, **_kwargs: Any) -> None:
                nonlocal calls
                calls += 1

            event.listen(conn.sync_engine, "before_cursor_execute", _count)
            try:
                ctx = await svc.load_context(session, _SYMBOLS[:n], AS_OF)
            finally:
                event.remove(conn.sync_engine, "before_cursor_execute", _count)
            assert ctx.symbols_order == _SYMBOLS[:n]
            counts.append(calls)
        return counts[0], counts[1], counts[2]

    one, five, twenty = asyncio.run(_with_rollback(_go))
    assert twenty <= 6, f"一次请求的查询数必须 ≤6，实际 {twenty}"
    assert one == five == twenty, "查询数不得随自选数增长（N+1 回归）"


@requires_pg
def test_load_context_loads_every_face() -> None:
    """六个数据面都要真的装进 context（否则探测器静默不出、假事实当"今天没事"）。"""

    async def _go(session: AsyncSession, _conn: Any) -> td.TriageContext:
        await _seed(session)
        return await svc.load_context(session, _SYMBOLS[:2], AS_OF)

    ctx = asyncio.run(_with_rollback(_go))
    assert ctx.as_of == AS_OF
    assert ctx.names["T90001"] == "测试T90001"
    # 行情窗口按 WINDOW_DAYS 截断（合成 70 天 → 只留最近 WINDOW_DAYS 行）
    assert len(ctx.quotes["T90001"]) == svc.WINDOW_DAYS
    assert ctx.quotes["T90001"][-1].trade_date == AS_OF
    assert ctx.quotes["T90001"][-1].close == pytest.approx(9.2)
    # 涨停价 + 当日高低
    assert ctx.limits["T90001"].up_limit == pytest.approx(11.0)
    assert ctx.limits["T90001"].high == pytest.approx(10.2)
    # 公告（equity_change 类目）
    assert ctx.announcements["T90001"][0].category == "equity_change"
    # 龙虎榜（ts_code 前缀匹配回 symbol）
    assert ctx.dragon_tiger["T90001"][0].net_amount == pytest.approx(1.2e7)
    # 板块：甲板块 -8%，乙板块 +0.5% → 甲的分位是 1.0（变动最大）
    assert ctx.sector_of["T90001"] == "T91001"
    assert ctx.sector_name["T91001"] == "测试一级甲"
    assert ctx.sector_pct["T91001"] == pytest.approx(-8.0, abs=0.5)
    assert ctx.sector_rank["T91001"] == pytest.approx(1.0)
    assert "sector_move" not in ctx.unavailable


@requires_pg
def test_load_context_marks_unavailable_when_symbols_unknown() -> None:
    """自选代码在 stocks 里一个都查不到 → 全部探测器进 unavailable（不静默当作无事件）。"""

    async def _go(session: AsyncSession, _conn: Any) -> td.TriageContext:
        return await svc.load_context(session, ["ZZ0001"], AS_OF)

    ctx = asyncio.run(_with_rollback(_go))
    assert ctx.names == {}
    assert ctx.unavailable == {spec.key for spec in td.TRIAGE_DETECTORS}


@requires_pg
def test_get_triage_composes_merges_and_folds() -> None:
    """端到端：19 只各命中 4 项 → 每只合并 1 条、展示 8 条、折叠 11 条。"""

    async def _go(session: AsyncSession, _conn: Any) -> svc.TriageResult:
        await _seed(session)
        return await svc.get_triage(session, _SYMBOLS[:21], limit=svc.LIMIT)

    async def _patched_day(_db: Any) -> MarketDay:
        return MarketDay(
            day=AS_OF,
            quality="complete",
            reason=None,
            rows=5485,
            universe=5513,
            pct_chg_ratio=1.0,
            limits_present=True,
        )

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(svc, "resolve_latest_complete_day", _patched_day)
        result = asyncio.run(_with_rollback(_go))
    assert result.as_of == AS_OF
    assert len(result.items) == svc.LIMIT
    assert result.folded == 19 - svc.LIMIT, "19 只命中、展示 8 条 → 折叠 11 条（不丢数据）"
    # 只有"有行情且无命中"的 T90020 才算平静；T90021 没有行情 = 未被判定，不得冒充平静
    assert result.quiet_count == 1
    primary = result.items[0]
    assert primary.symbol == "T90001"
    # T90001 命中 6 项（跌破 20 日线 / 跌得急 / 新低 / 板块 / 公告 / 龙虎榜）→ 合并 1 条
    assert primary.detector == "break_ma20"
    assert "另有 5 项" in primary.headline
    assert result.unavailable == [], "合成数据齐备时不得有缺数据探测器"


@requires_pg
def test_load_context_marks_sector_unavailable_without_industry_data() -> None:
    """没有行情/没有申万归属 → ``sector_move`` 进 unavailable（不静默当"板块没动"）。

    T90021 只有 stocks 行、没有行情：龙虎榜走"无交易日"兜底分支，板块面无映射。
    """

    async def _go(session: AsyncSession, _conn: Any) -> td.TriageContext:
        await _seed(session)
        return await svc.load_context(session, ["T90021"], AS_OF)

    ctx = asyncio.run(_with_rollback(_go))
    assert ctx.quotes == {}
    assert ctx.sector_of == {}
    assert "sector_move" in ctx.unavailable
    assert ctx.unavailable < {spec.key for spec in td.TRIAGE_DETECTORS}, "只该标板块一项"


@requires_pg
def test_user_symbols_is_user_scoped_and_sorted() -> None:
    """自选代码按 ``sort_order`` 返回，且严格限定当前用户（跨用户串号 = 数据泄露）。"""

    user_a, user_b = uuid.uuid4(), uuid.uuid4()

    async def _go(session: AsyncSession, _conn: Any) -> list[str]:
        wl_a = UserWatchlist(id=uuid.uuid4(), user_id=user_a, name="默认自选", is_default=True)
        wl_b = UserWatchlist(id=uuid.uuid4(), user_id=user_b, name="默认自选", is_default=True)
        session.add_all([wl_a, wl_b])
        await session.flush()
        session.add_all(
            [
                UserWatchlistItem(
                    id=uuid.uuid4(), watchlist_id=wl_a.id, symbol="600000", sort_order=2
                ),
                UserWatchlistItem(
                    id=uuid.uuid4(), watchlist_id=wl_a.id, symbol="000001", sort_order=0
                ),
                UserWatchlistItem(
                    id=uuid.uuid4(), watchlist_id=wl_a.id, symbol="600519", sort_order=1
                ),
                UserWatchlistItem(
                    id=uuid.uuid4(), watchlist_id=wl_b.id, symbol="000002", sort_order=0
                ),
            ]
        )
        await session.flush()
        return await svc.user_symbols(session, str(user_a))

    symbols = asyncio.run(_with_rollback(_go))
    assert symbols == ["000001", "600519", "600000"]
    assert "000002" not in symbols, "不得串到别人的自选"


@requires_pg
def test_user_symbols_returns_empty_without_watchlist() -> None:
    """没有自选表 → ``[]``（**不**在 GET 里创建默认自选：读面零写）。"""

    async def _go(session: AsyncSession, _conn: Any) -> list[str]:
        return await svc.user_symbols(session, str(uuid.uuid4()))

    assert asyncio.run(_with_rollback(_go)) == []
