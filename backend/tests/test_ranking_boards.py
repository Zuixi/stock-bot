"""S1.6 A股 7 榜：榜型抽象 / 口径三件事 / 缓存 / **PIT 正确性**（真 PG）。

分两层：

* **纯形状层**（无库）：榜型注册表、SQL 判别式、口径必须非空、缓存键与降级路径。
* **真库层**（``requires_pg``）：用 ``_with_rollback`` 在**一个事务里**种合成数据
  然后 ROLLBACK——库内容跑前跑后逐字节一致（不 DELETE、不 DROP、不影响别人预置的数据）。
  合成数据专门造出 **PIT 陷阱**：某股在口径交易日**尚未公告**某期财报，
  该期利润远大于榜上第一名。若实现取"库里最新一期"，它会排到第一——本文件的
  用例就是守这一条（**前视 = 历史榜单被未来信息污染**）。

口径与阈值的权威定义在 ``app/services/ranking_board_service.py`` 的模块 docstring
与 ``docs/decisions/0011-point-in-time-data-contract.md``。
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.repositories import financial_repo
from app.schemas.ranking_board import RankingBoardResponseOut, RankingCaliberOut
from app.services import market_day_service
from app.services import ranking_board_service as rbs

# ---------------------------------------------------------------------------
# 真库连接（与 brief 的运行环境一致；默认落到 stock_bot_test:5433）
# ---------------------------------------------------------------------------

_DEFAULT_ASYNC = "postgresql+asyncpg://stock_user:stock_pass@localhost:5433/stock_bot_test"
ASYNC_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or _DEFAULT_ASYNC


def _pg_reachable() -> bool:
    async def _go() -> bool:
        try:
            conn = await asyncpg.connect(ASYNC_URL.replace("+asyncpg", ""))
        except Exception:
            return False
        await conn.close()
        return True

    return asyncio.run(_go())


requires_pg = pytest.mark.skipif(
    not _pg_reachable(), reason=f"PostgreSQL unavailable at {ASYNC_URL} (set TEST_DATABASE_URL)"
)

#: 合成数据统一前缀，避免与库内既有行撞（symbol 是 unique 的一部分）。
PREFIX = "T10B"

#: 口径交易日（周一）。合成行情**只到 2026-12-31**，故 ``as_of`` 是真库里的最新日。
AS_OF = date(2027, 1, 4)

#: 参考日 = 口径日往前第 20 个交易日（含 2027-01-01 这一天，因为生成器按"工作日"而非
#: 真实交易日历走）。见 tests 里的 ``_trading_days``。
REF_DAY = date(2026, 12, 7)


def _trading_days(as_of: date, n: int) -> list[date]:
    """``as_of`` 之前最近 n 个**工作日**（与测试内生成的行情序列同源）。"""
    out: list[date] = []
    day = as_of - timedelta(days=1)
    while len(out) < n:
        if day.isoweekday() <= 5:
            out.append(day)
        day -= timedelta(days=1)
    return out


TRADING_DAYS = _trading_days(AS_OF, 20)
assert TRADING_DAYS[-1] == REF_DAY, (TRADING_DAYS[-1], REF_DAY)


async def _with_rollback(fn: Any) -> Any:
    """在一个事务里跑 fn，结束一律 ROLLBACK（库内容与跑前一致）。"""
    eng = create_async_engine(ASYNC_URL)
    try:
        async with eng.connect() as conn:
            trans = await conn.begin()
            session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint")
            try:
                return await fn(session)
            finally:
                await session.close()
                await trans.rollback()
    finally:
        await eng.dispose()


async def _seed_stock(
    db: AsyncSession, symbol: str, name: str, list_date: date, *, category: str = "沪深A股"
) -> int:
    row = (
        await db.execute(
            text(
                """
                INSERT INTO stocks (exchange, symbol, name, category, list_date, asof)
                VALUES ('Shanghai_Stocks', :symbol, :name, :category, :list_date, :asof)
                RETURNING id
                """
            ),
            {
                "symbol": symbol,
                "name": name,
                "category": category,
                "list_date": list_date,
                "asof": datetime.now(UTC),
            },
        )
    ).scalar_one()
    await db.flush()
    return int(row)


async def _seed_quote_series(
    db: AsyncSession,
    stock_id: int,
    closes: dict[date, float],
    *,
    adj: dict[date, float] | None = None,
) -> None:
    adj = adj or {}
    for day, close in closes.items():
        await db.execute(
            text(
                """
                INSERT INTO daily_quotes (stock_id, trade_date, close, adj_factor, source)
                VALUES (:sid, :day, :close, :adj, 'test')
                """
            ),
            {"sid": stock_id, "day": day, "close": close, "adj": adj.get(day, 1.0)},
        )
    await db.flush()


async def _seed_basic(
    db: AsyncSession,
    stock_id: int,
    *,
    total_mv: float | None = None,
    dv_ratio: float | None = None,
    close: float | None = None,
) -> None:
    await db.execute(
        text(
            """
            INSERT INTO daily_basic_indicators
                (stock_id, trade_date, close, total_mv, dv_ratio, source)
            VALUES (:sid, :day, :close, :mv, :dv, 'test')
            """
        ),
        {"sid": stock_id, "day": AS_OF, "close": close, "mv": total_mv, "dv": dv_ratio},
    )
    await db.flush()


async def _seed_financials(
    db: AsyncSession,
    stock_id: int,
    *,
    end_date: date,
    report_type: str,
    ann_date: date | None,
    f_ann_date: date | None,
    n_income_attr_p: float | None,
    roe: float | None,
) -> None:
    """走生产 repo 写入（与 ingest 同一路径，避免测试自造一份落库形状）。"""
    version = await financial_repo.upsert_report_version(
        db,
        stock_id=stock_id,
        ts_code=f"{PREFIX}.SH",
        end_date=end_date,
        report_type=report_type,
        ann_date=ann_date,
        f_ann_date=f_ann_date,
        comp_type="1",
        source="test",
        source_record_key=f"{stock_id}:{end_date.isoformat()}",
        update_flag=None,
        currency="CNY",
        quality_status="reported",
        as_of=datetime.now(UTC),
        raw_record_id=None,
    )
    await financial_repo.upsert_income_facts(
        db, version.id, {"n_income": n_income_attr_p, "n_income_attr_p": n_income_attr_p}
    )
    if roe is not None:
        await financial_repo.upsert_metric(
            db,
            stock_id=stock_id,
            report_version_id=version.id,
            metric_key="roe",
            value=roe,
            unit="%",
            period_type="report",
            calc_method="reported_by_provider",
            source="test",
            quality_status="reported",
            as_of=datetime.now(UTC),
        )


def _pin_day(monkeypatch: pytest.MonkeyPatch, day: date) -> None:
    """把口径日解析器固定到合成数据的日期（口径日解析本身由 market_day_service 的用例守）。"""

    async def _resolve(_db: Any, *, cache: Any = None) -> market_day_service.MarketDay:
        return market_day_service.MarketDay(day, "complete", None, 999, 999, 1.0, True)

    monkeypatch.setattr(market_day_service, "resolve_latest_complete_day", _resolve, raising=True)
    monkeypatch.setattr(
        rbs.market_day_service, "resolve_latest_complete_day", _resolve, raising=True
    )


def _run(coro_fn: Any, monkeypatch: pytest.MonkeyPatch, day: date) -> Any:
    _pin_day(monkeypatch, day)
    return asyncio.run(_with_rollback(coro_fn))


# ===========================================================================
# 1) 纯形状层：榜型抽象 / 口径 / SQL 判别式 / 缓存
# ===========================================================================


def test_seven_boards_registered_and_ordered() -> None:
    assert rbs.BOARD_ORDER == (
        "market_cap",
        "dividend_yield",
        "monthly_gain",
        "monthly_loss",
        "half_year_profit",
        "annual_profit",
        "roe",
    )
    assert set(rbs.BOARDS) == set(rbs.BOARD_ORDER)
    labels = [rbs.BOARDS[k].label for k in rbs.BOARD_ORDER]
    assert labels == ["市值", "股息率", "股价月度涨幅", "股价月度跌幅", "半年利润", "年利润", "ROE"]


def test_existing_five_single_day_boards_untouched() -> None:
    """Ruling 30：既有 5 个单日榜不得被本任务改动/替换。"""
    from app.services.market_service import _QUOTE_RANK_SQL, _RANKING_ORDER

    assert set(_RANKING_ORDER) == {"gainers", "losers", "amount", "volume", "turnover_rate"}
    assert _RANKING_ORDER["gainers"] == ("pct_chg", "DESC")
    assert _RANKING_ORDER["losers"] == ("pct_chg", "ASC")
    assert set(_QUOTE_RANK_SQL) == {"gainers", "losers", "amount", "volume"}
    # 7 榜与 5 榜的 key 空间不相交（不是"往 _RANKING_ORDER 里塞 key"）
    assert not (set(rbs.BOARDS) & set(_RANKING_ORDER))


def test_board_keys_are_url_safe_ascii() -> None:
    """key 要进 URL 路径段（``/market/rankings/boards/:key``）。"""
    for key in rbs.BOARD_ORDER:
        assert key.isascii() and key.replace("_", "").isalnum()


def test_unknown_board_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unknown ranking board"):
        rbs.get_board("gainers")  # 既有 5 榜的 key 不是 7 榜的 key


def test_each_board_declares_source_and_columns() -> None:
    for key in rbs.BOARD_ORDER:
        spec = rbs.BOARDS[key]
        assert spec.metric_unit, key
        assert spec.sort_direction in {"ASC", "DESC"}
        if spec.source_kind == "daily_basic":
            assert spec.basic_column in {"total_mv", "dv_ratio"}
        elif spec.source_kind == "financial_statement":
            assert spec.financial_column in {"n_income_attr_p", "roe"}
            assert spec.financial_metric_key == "roe"
        else:
            assert key in {"monthly_gain", "monthly_loss"}


def test_financial_column_source_registry_is_explicit() -> None:
    """实测落库位置：利润在 income_statement_facts，ROE 在 financial_metrics（长表）。"""
    assert rbs.FINANCIAL_COLUMN_SOURCE == {
        "n_income_attr_p": "income_statement_facts",
        "roe": "financial_metrics",
    }
    # 长表没有 roe 列，必须取 value；宽表取同名列
    assert "im.value" in str(rbs.FINANCIAL_SQL["roe"])
    assert "im.n_income_attr_p" in str(rbs.FINANCIAL_SQL["n_income_attr_p"])


def test_momentum_boards_are_window_aggregations_not_single_day_pct_chg() -> None:
    """现有 gainers/losers 是单日 ``pct_chg`` 排序，**不能**当"月度"用。"""
    for key in ("monthly_gain", "monthly_loss"):
        sql = str(rbs.MOMENTUM_SQL[key])  # type: ignore[index]
        assert "ref_day" in sql and "make_interval" in sql
        assert "adj_factor" in sql, "复权必须进窗口收益（否则除权日会造出假涨跌）"
        assert "ORDER BY pct_chg" not in sql and "pct_chg DESC" not in sql


def test_financial_sql_is_point_in_time_bounded() -> None:
    """PIT 的核心：可见性上界是「公告日 < 口径交易日」，且带来源判别式。"""
    for column in ("n_income_attr_p", "roe"):
        sql = str(rbs.FINANCIAL_SQL[column])
        assert "coalesce(v.f_ann_date, v.ann_date)" in sql
        assert "coalesce(v.f_ann_date, v.ann_date) <= CAST(:as_of AS date)" in sql
        assert "ann_date_source" in sql
        # 不许出现"取库里最新一期"的实现：比较 max(end_date) 之类
        assert "max(v.end_date)" not in sql
        assert "max(end_date)" not in sql
        # 分红/合并报表口径：只认合并报表
        assert "v.comp_type = '1'" in sql


def test_financial_sqls_share_one_pit_cte_source() -> None:
    """分母与排序必须同源：否则"为什么不足 100 条"会说错。"""
    for column in ("n_income_attr_p", "roe"):
        top = str(rbs.FINANCIAL_SQL[column])
        stats = str(rbs.FINANCIAL_STATS_SQL[column])
        assert rbs._FINANCIAL_PIT_CTE.strip() in top
        assert rbs._FINANCIAL_PIT_CTE.strip() in stats


def test_caliber_always_fills_three_items() -> None:
    """口径三件事（交易日 / 财务期 / 样本范围）逐榜非空——空串会被 UI 渲染成「没写口径」。"""
    for key in rbs.BOARD_ORDER:
        spec = rbs.BOARDS[key]
        caliber = rbs._caliber(spec, AS_OF, [("2026-06-30", "中报", 3)])
        assert caliber.trade_date == AS_OF
        assert caliber.financial_period.strip()
        assert caliber.sample_scope.strip()
        if spec.source_kind == "financial_statement":
            assert "coalesce(f_ann_date, ann_date)" in caliber.financial_period
            assert "2026-06-30" in caliber.financial_period, "实际取到的报告期必须可见"
        elif spec.source_kind == "daily_basic":
            # 估值类榜不消费财务数据 → 显式写「不适用」，不留空
            assert caliber.financial_period == rbs._NO_FINANCIAL_CALIBER
        else:
            # 窗口类榜不消费财务数据，但**要**写清自己用的窗口口径
            assert "coalesce(adj_factor, 1)" in caliber.financial_period
            assert str(rbs.MOMENTUM_WINDOW_TRADING_DAYS) in caliber.financial_period


def test_momentum_caliber_states_the_actual_window_numbers() -> None:
    spec = rbs.BOARDS["monthly_gain"]
    caliber = rbs._caliber(spec, AS_OF, [])
    for fragment in (
        str(rbs.MOMENTUM_WINDOW_TRADING_DAYS),
        str(rbs.MOMENTUM_LOOKBACK_CALENDAR_DAYS),
        "adj_factor",
    ):
        assert fragment in caliber.financial_period
    # 涨/跌两个榜的口径文案必须不同（一个讲涨幅、一个讲跌幅），否则就是把口径说反
    loss = rbs._caliber(rbs.BOARDS["monthly_loss"], AS_OF, [])
    assert loss.financial_period != caliber.financial_period


def test_notes_explain_fewer_than_limit_rows() -> None:
    spec = rbs.BOARDS["dividend_yield"]
    notes = rbs._notes(
        spec,
        returned_rows=7,
        limit=100,
        eligible_rows=7,
        truncated_ties=False,
        all_have_financials=None,
    )
    assert any("不足 100 条" in n and "7" in n for n in notes)
    # 足 100 条时不得出现"不足"噪音
    assert not [
        n
        for n in rbs._notes(
            spec,
            returned_rows=100,
            limit=100,
            eligible_rows=5559,
            truncated_ties=False,
            all_have_financials=None,
        )
        if "不足" in n
    ]


def test_notes_flag_truncated_ties() -> None:
    spec = rbs.BOARDS["market_cap"]
    notes = rbs._notes(
        spec,
        returned_rows=100,
        limit=100,
        eligible_rows=5559,
        truncated_ties=True,
        all_have_financials=None,
    )
    assert any("并列" in n for n in notes)


def test_notes_explain_no_announced_period_with_real_denominator() -> None:
    spec = rbs.BOARDS["half_year_profit"]
    notes = rbs._notes(
        spec,
        returned_rows=0,
        limit=100,
        eligible_rows=0,
        truncated_ties=False,
        all_have_financials=1234,
    )
    joined = "".join(notes)
    assert "1234" in joined and "NULL" in joined, "必须说清是没有已公告期、还是该指标无值"


class _RecordingCache:
    def __init__(self) -> None:
        self.store: dict[str, object] = {}
        self.set_calls: list[tuple[str, object, int | None]] = []

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def set(self, key: str, value: object, ttl: int | None = None) -> None:
        self.store[key] = value
        self.set_calls.append((key, value, ttl))


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _Rows:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def one(self) -> dict[str, Any]:
        return self._rows[0]


class _FakeDb:
    """按调用顺序回放行；同时记录 SQL 文本供断言。

    ``_scalar_count`` 走 ``.one()``，所以每个批次的最后要给一个 ``stock_count`` 行；
    没登记的批次回一个空列表（``.all()`` 路径用）。
    """

    def __init__(self, *batches: list[dict[str, Any]]) -> None:
        self._batches = list(batches)
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def execute(self, stmt: object, params: dict[str, Any] | None = None) -> _Rows:
        self.calls.append((str(stmt), params))
        sql = str(stmt)
        if "stock_count" in sql:  # 分母聚合（BASIC_COUNT_SQL / MOMENTUM_COUNT_SQL）
            return _Rows([{"stock_count": 5559}])
        return _Rows(self._batches.pop(0) if self._batches else [])


def _basic_row(symbol: str, name: str, *, mv: float | None = None) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": name,
        "exchange": "Shanghai_Stocks",
        "close": 10.0,
        "metric": mv,
        "total_mv": mv,
        "dv_ratio": None,
        "list_date": date(2015, 1, 1),
    }


@pytest.mark.asyncio
async def test_get_ranking_board_cache_hit_skips_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    _pin_day_async(monkeypatch)
    cache = _RecordingCache()
    payload = RankingBoardResponseOut(
        as_of=AS_OF,
        is_latest_trading_day=True,
        key="market_cap",
        label="市值",
        category="valuation",
        source="daily_basic",
        sort_direction="DESC",
        metric_unit="万元",
        limit=100,
        returned_rows=0,
        eligible_rows=0,
        caliber=RankingCaliberOut(trade_date=AS_OF, financial_period="x", sample_scope="y"),
        items=[],
    ).model_dump(mode="json")
    cache.store[f"market:ranking-board:market_cap:100:{AS_OF.isoformat()}"] = payload
    db = _FakeDb()

    out = await rbs.get_ranking_board(db, cache, "market_cap", 100)  # type: ignore[arg-type]

    assert out.as_of == AS_OF and out.key == "market_cap"
    assert db.calls == [], "缓存命中不得再跑 SQL"


@pytest.mark.asyncio
async def test_get_ranking_board_does_not_reuse_another_days_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """键里必须带 resolved 口径日；上一日的键命中不了。"""
    _pin_day_async(monkeypatch)
    cache = _RecordingCache()
    cache.store["market:ranking-board:market_cap:100:2026-12-31"] = {"stale": True}
    db = _FakeDb([_basic_row("600000", "浦发银行", mv=1e6)], [])

    out = await rbs.get_ranking_board(db, cache, "market_cap", 100)  # type: ignore[arg-type]

    assert out.as_of == AS_OF
    assert cache.set_calls[0][0] == f"market:ranking-board:market_cap:100:{AS_OF.isoformat()}"
    assert cache.set_calls[0][1]["as_of"] == AS_OF.isoformat()  # type: ignore[index]


@pytest.mark.asyncio
async def test_get_ranking_board_legacy_payload_without_quality_reads_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_day_async(monkeypatch)
    cache = _RecordingCache()
    legacy = RankingBoardResponseOut(
        as_of=AS_OF,
        is_latest_trading_day=True,
        key="market_cap",
        label="市值",
        category="valuation",
        source="daily_basic",
        sort_direction="DESC",
        metric_unit="万元",
        limit=100,
        returned_rows=0,
        eligible_rows=0,
        caliber=RankingCaliberOut(trade_date=AS_OF, financial_period="x", sample_scope="y"),
        items=[],
    ).model_dump(mode="json")
    del legacy["as_of_quality"]
    cache.store[f"market:ranking-board:market_cap:100:{AS_OF.isoformat()}"] = legacy

    out = await rbs.get_ranking_board(_FakeDb(), cache, "market_cap", 100)  # type: ignore[arg-type]

    assert out.as_of_quality == "partial", "缺 as_of_quality 的旧负载不得谎报 complete"


@pytest.mark.asyncio
async def test_get_ranking_board_degrades_on_empty_db(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _empty(_db: Any, *, cache: Any = None) -> None:
        return None

    monkeypatch.setattr(market_day_service, "resolve_latest_complete_day", _empty)
    monkeypatch.setattr(rbs.market_day_service, "resolve_latest_complete_day", _empty)
    monkeypatch.setattr(rbs, "_today_sh", lambda: date(2026, 9, 19))  # 周六
    cache = _RecordingCache()

    out = await rbs.get_ranking_board(_FakeDb(), cache, "market_cap", 100)  # type: ignore[arg-type]

    assert out.items == [] and out.as_of == date(2026, 9, 18)
    assert out.as_of_quality == "partial"
    assert out.notes and "不足 100 条" in out.notes[0]
    assert cache.set_calls == [], "空库降级不得写缓存（首次 ingest 后必须立刻恢复）"


@pytest.mark.asyncio
async def test_get_ranking_board_rejects_unknown_key() -> None:
    with pytest.raises(ValueError):
        await rbs.get_ranking_board(_FakeDb(), None, "gainers", 100)  # type: ignore[arg-type]


def _pin_day_async(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _resolve(_db: Any, *, cache: Any = None) -> market_day_service.MarketDay:
        return market_day_service.MarketDay(AS_OF, "complete", None, 999, 999, 1.0, True)

    monkeypatch.setattr(market_day_service, "resolve_latest_complete_day", _resolve)
    monkeypatch.setattr(rbs.market_day_service, "resolve_latest_complete_day", _resolve)


# ===========================================================================
# 2) 真库层：PIT / 窗口聚合 / 分母 / 并列
# ===========================================================================


@requires_pg
def test_pit_financial_board_never_ranks_future_periods(monkeypatch: pytest.MonkeyPatch) -> None:
    """**本任务最关键的一条**：口径日尚未公告的那一期，利润再高也不许进榜。

    合成数据（口径日 2027-01-04）：

    * ``T10B001 n_income_attr_p = 9,000,000,000``，``end_date = 2026-12-31``（FY），
      ``f_ann_date = 2027-02-15`` → **尚未公告**。若实现取"库里最新一期"，它必然第一。
    * ``T10B002 n_income_attr_p =   90,000,000``，``end_date = 2026-06-30``，
      ``ann_date = 2026-08-20``（只有 ann_date，走回退分支）→ 应第一。
    * ``T10B003 n_income_attr_p =   30,000,000``，``end_date = 2026-03-31``，
      ``f_ann_date = 2026-04-25`` → 应第二（逐行报告期不同，是 PIT 的正常结果）。
    * ``T10B004`` 有已公告版本但 ``n_income_attr_p = NULL`` → 不进榜（NULL 不是 0）。
    """

    async def _scenario(db: AsyncSession) -> RankingBoardResponseOut:
        id1 = await _seed_stock(db, f"{PREFIX}001", "未来期未公告", date(2015, 1, 1))
        id2 = await _seed_stock(db, f"{PREFIX}002", "中报已公告", date(2015, 1, 1))
        id3 = await _seed_stock(db, f"{PREFIX}003", "一季报已公告", date(2015, 1, 1))
        id4 = await _seed_stock(db, f"{PREFIX}004", "利润为空值", date(2015, 1, 1))
        # 未来期（口径日之后才公告）—— 利润最高
        await _seed_financials(
            db,
            id1,
            end_date=date(2026, 12, 31),
            report_type="FY",
            ann_date=date(2027, 2, 10),
            f_ann_date=date(2027, 2, 15),
            n_income_attr_p=9_000_000_000.0,
            roe=99.0,
        )
        # 已公告中报，ann_date 有值 / f_ann_date 为 NULL → 走回退分支
        await _seed_financials(
            db,
            id2,
            end_date=date(2026, 6, 30),
            report_type="H1",
            ann_date=date(2026, 8, 20),
            f_ann_date=None,
            n_income_attr_p=90_000_000.0,
            roe=12.5,
        )
        await _seed_financials(
            db,
            id3,
            end_date=date(2026, 3, 31),
            report_type="Q1",
            ann_date=date(2026, 4, 20),
            f_ann_date=date(2026, 4, 25),
            n_income_attr_p=30_000_000.0,
            roe=4.0,
        )
        await _seed_financials(
            db,
            id4,
            end_date=date(2026, 6, 30),
            report_type="H1",
            ann_date=date(2026, 8, 20),
            f_ann_date=None,
            n_income_attr_p=None,
            roe=None,
        )
        return await rbs.get_ranking_board(db, None, "half_year_profit", 100)

    out = _run(_scenario, monkeypatch, AS_OF)

    symbols = [i.symbol for i in out.items]
    assert f"{PREFIX}001" not in symbols, (
        "前视！口径日尚未公告的 2026-12-31 年报（利润 90 亿）被排进了 2027-01-04 的榜"
    )
    assert symbols == [f"{PREFIX}002", f"{PREFIX}003"], symbols
    assert out.items[0].n_income_attr_p == pytest.approx(90_000_000.0)
    # 逐行给出实际用的报告期（不同股票可以不同期——这正是 PIT 的样子）
    assert out.items[0].financial_report is not None
    assert out.items[0].financial_report.end_date == date(2026, 6, 30)
    assert out.items[0].financial_report.report_type == "H1"
    assert out.items[0].financial_report.ann_date == date(2026, 8, 20)
    assert out.items[0].financial_report.ann_date_source == "ann_date", "回退必须带判别式"
    assert out.items[1].financial_report is not None
    assert out.items[1].financial_report.end_date == date(2026, 3, 31)
    assert out.items[1].financial_report.ann_date_source == "f_ann_date"
    # 分母 = 该榜指标可取值的标的数（id1 未被公告、id4 值为 NULL → 都不在）
    assert out.eligible_rows == 2
    # 不足 100 条必须说明原因
    notes = "".join(out.notes)
    assert "不足 100 条" in notes, notes
    # 该交易日"有已公告期"的标的数：本用例种下 3 只（id1 的期在口径日之后才公告，
    # 故它不算"已公告"）。共享库里可能有别的行，故只断言 >= 3。
    announced = int(re.search(r"已公告财报的标的有 (\d+) 只", notes).group(1))  # type: ignore[union-attr]
    assert announced >= 3, notes
    # 口径里出现"实际取到的报告期分布"
    assert "2026-06-30" in out.caliber.financial_period
    assert "2026-03-31" in out.caliber.financial_period


@requires_pg
def test_pit_takes_latest_period_not_the_highest(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一只股票按「已公告最新期」取值，不是「数值最高的那一期」。

    某股 2026-03-31 一季报利润 50 亿（已公告），2026-06-30 中报利润 10 亿（已公告）。
    榜上必须用它**最新一期（中报 10 亿）**；若实现按"利润最高"取，会拿一季报的 50 亿
    ——那等于把已被更新的旧数当现状。
    """

    async def _scenario(db: AsyncSession) -> RankingBoardResponseOut:
        sid = await _seed_stock(db, f"{PREFIX}010", "多期已公告", date(2015, 1, 1))
        await _seed_financials(
            db,
            sid,
            end_date=date(2026, 3, 31),
            report_type="Q1",
            ann_date=date(2026, 4, 25),
            f_ann_date=None,
            n_income_attr_p=5_000_000_000.0,
            roe=30.0,
        )
        await _seed_financials(
            db,
            sid,
            end_date=date(2026, 6, 30),
            report_type="H1",
            ann_date=date(2026, 8, 25),
            f_ann_date=None,
            n_income_attr_p=1_000_000_000.0,
            roe=6.0,
        )
        return await rbs.get_ranking_board(db, None, "half_year_profit", 100)

    out = _run(_scenario, monkeypatch, AS_OF)

    assert [i.symbol for i in out.items] == [f"{PREFIX}010"]
    assert out.items[0].n_income_attr_p == pytest.approx(1_000_000_000.0)
    assert out.items[0].financial_report is not None
    assert out.items[0].financial_report.end_date == date(2026, 6, 30)


@requires_pg
def test_pit_boundary_announced_exactly_on_trade_date_is_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """边界语义：``ann_date == 口径日`` 视为**当日可得**（``<=``）。这条要被机械守住。"""

    async def _scenario(db: AsyncSession) -> RankingBoardResponseOut:
        sid = await _seed_stock(db, f"{PREFIX}012", "当日公告", date(2015, 1, 1))
        await _seed_financials(
            db,
            sid,
            end_date=date(2026, 9, 30),
            report_type="Q3",
            ann_date=AS_OF,
            f_ann_date=None,
            n_income_attr_p=7_000_000.0,
            roe=3.0,
        )
        return await rbs.get_ranking_board(db, None, "half_year_profit", 100)

    out = _run(_scenario, monkeypatch, AS_OF)
    assert [i.symbol for i in out.items] == [f"{PREFIX}012"]


@requires_pg
def test_financial_board_ranks_by_roe_from_financial_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ROE 落在 ``financial_metrics``（长表 value），PIT 判据同样生效。"""

    async def _scenario(db: AsyncSession) -> RankingBoardResponseOut:
        id_future = await _seed_stock(db, f"{PREFIX}020", "未来期高 ROE", date(2015, 1, 1))
        id_now = await _seed_stock(db, f"{PREFIX}021", "已公告中 ROE", date(2015, 1, 1))
        await _seed_financials(
            db,
            id_future,
            end_date=date(2026, 12, 31),
            report_type="FY",
            ann_date=None,
            f_ann_date=date(2027, 3, 1),
            n_income_attr_p=1.0,
            roe=88.0,
        )
        await _seed_financials(
            db,
            id_now,
            end_date=date(2026, 3, 31),
            report_type="Q1",
            ann_date=None,
            f_ann_date=date(2026, 4, 28),
            n_income_attr_p=1.0,
            roe=21.5,
        )
        return await rbs.get_ranking_board(db, None, "roe", 100)

    out = _run(_scenario, monkeypatch, AS_OF)
    assert [i.symbol for i in out.items] == [f"{PREFIX}021"], "未来期的 88% ROE 不许进榜"
    assert out.items[0].roe == pytest.approx(21.5)
    assert out.eligible_rows == 1


@requires_pg
def test_momentum_boards_use_20_trading_day_adjusted_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """月度涨/跌 = 20 交易日窗口的**复权**收益；窗口不足的整只剔除。

    合成数据（口径日 2027-01-04，参考日 2026-12-07）：

    * ``T10B031`` 参考日 100 → 口径日 150：+50% → 涨幅榜第一
    * ``T10B032`` 参考日 100 → 口径日 50：−50% → 跌幅榜第一
    * ``T10B033`` 参考日 100 → 口径日 100，但口径日发生 2:1 拆分
      （``adj_factor`` 2.0 vs 1.0，未复权价 50）→ 复权后 0%；
      **若漏了复权因子，它会被算成 −50%** 并与 T10B032 并列。
    * ``T10B034`` 窗口内行情缺失（2026-11-01 ~ 2026-12-20 无行）→ 整只剔除。
    """

    async def _scenario(db: AsyncSession) -> tuple[Any, Any]:
        close_days = [*TRADING_DAYS, AS_OF]
        assert len(close_days) == 21

        up = await _seed_stock(db, f"{PREFIX}031", "窗口上涨", date(2015, 1, 1))
        down = await _seed_stock(db, f"{PREFIX}032", "窗口下跌", date(2015, 1, 1))
        split = await _seed_stock(db, f"{PREFIX}033", "窗口内拆分", date(2015, 1, 1))
        hole = await _seed_stock(db, f"{PREFIX}034", "窗口缺数据", date(2015, 1, 1))

        await _seed_quote_series(db, up, {**{d: 100.0 for d in TRADING_DAYS}, AS_OF: 150.0})
        await _seed_quote_series(db, down, {**{d: 100.0 for d in TRADING_DAYS}, AS_OF: 50.0})
        await _seed_quote_series(
            db,
            split,
            {**{d: 100.0 for d in TRADING_DAYS}, AS_OF: 50.0},
            adj={AS_OF: 2.0},  # 拆分：价减半、因子加倍 → 复权收益 0
        )
        # 缺数据：只种不含参考日的那一段
        missing_days = [d for d in close_days if d < date(2026, 11, 1) or d > date(2026, 12, 20)]
        await _seed_quote_series(db, hole, {d: 100.0 for d in missing_days})

        return (
            await rbs.get_ranking_board(db, None, "monthly_gain", 100),
            await rbs.get_ranking_board(db, None, "monthly_loss", 100),
        )

    gain, loss = _run(_scenario, monkeypatch, AS_OF)

    assert gain.items[0].symbol == f"{PREFIX}031"
    assert gain.items[0].monthly_pct_chg == pytest.approx(50.0)
    # 拆分股复权收益 0%，不许被当成 −50%
    split_item = next(i for i in gain.items if i.symbol == f"{PREFIX}033")
    assert split_item.monthly_pct_chg == pytest.approx(0.0), "漏复权因子会把它算成 −50%"
    assert f"{PREFIX}034" not in [i.symbol for i in gain.items], "窗口不足必须整只剔除"

    assert loss.items[0].symbol == f"{PREFIX}032"
    assert loss.items[0].monthly_pct_chg == pytest.approx(-50.0)
    # 跌幅榜是"收益最小优先"，涨 50% 的那只仍在榜上（只是排在最后）——不是被剔除
    assert [i.symbol for i in loss.items] == [f"{PREFIX}032", f"{PREFIX}033", f"{PREFIX}031"]
    # 分母 = 两个交易日都有正复权收盘的标的数 = 3（第 4 只窗口不足）
    assert gain.eligible_rows == 3
    assert loss.eligible_rows == 3
    # 口径必须写出窗口长度与实际参考口径
    assert f"{rbs.MOMENTUM_WINDOW_TRADING_DAYS}" in gain.caliber.financial_period


@requires_pg
def test_dividend_board_excludes_null_and_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """0 不是「股息率极低」，是「该指标无值」→ 不进榜；分母与排序同源。"""

    async def _scenario(db: AsyncSession) -> RankingBoardResponseOut:
        good = await _seed_stock(db, f"{PREFIX}041", "有股息", date(2015, 1, 1))
        zero = await _seed_stock(db, f"{PREFIX}042", "零股息", date(2015, 1, 1))
        null = await _seed_stock(db, f"{PREFIX}043", "空股息", date(2015, 1, 1))
        await _seed_basic(db, good, dv_ratio=6.5, total_mv=1000.0)
        await _seed_basic(db, zero, dv_ratio=0.0, total_mv=2000.0)
        await _seed_basic(db, null, dv_ratio=None, total_mv=3000.0)
        return await rbs.get_ranking_board(db, None, "dividend_yield", 100)

    out = _run(_scenario, monkeypatch, AS_OF)
    assert [i.symbol for i in out.items] == [f"{PREFIX}041"]
    assert out.eligible_rows == 1
    assert any("0" in n and "不进榜" in n for n in out.notes)


@requires_pg
def test_market_cap_board_denominator_is_whole_market_not_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分母是**全市场可排标的数**，不是取回行数（否则「为什么不足 100 条」必错）。"""

    async def _scenario(db: AsyncSession) -> tuple[RankingBoardResponseOut, int]:
        ids = []
        for index in range(5):
            sid = await _seed_stock(db, f"{PREFIX}05{index}", f"市值{index}", date(2015, 1, 1))
            await _seed_basic(db, sid, total_mv=float(index + 1) * 1000.0)
            ids.append(sid)
        out = await rbs.get_ranking_board(db, None, "market_cap", 2)
        return out, len(ids)

    out, seeded = _run(_scenario, monkeypatch, AS_OF)
    assert out.limit == 2 and out.returned_rows == 2
    assert out.eligible_rows >= seeded, (
        f"分母 {out.eligible_rows} 小于本次种下的 {seeded} 只 —— 说明分母退化成了取回行数"
    )
    assert out.items[0].total_mv is not None


@requires_pg
def test_st_and_new_stock_are_flagged_not_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    """样本口径 = 含 ST、含次新股；两者**逐行标记**，不静默剔除。"""

    async def _scenario(db: AsyncSession) -> RankingBoardResponseOut:
        st = await _seed_stock(db, f"{PREFIX}051", "*ST测试", date(2015, 1, 1))
        fresh = await _seed_stock(db, f"{PREFIX}052", "次新测试", AS_OF - timedelta(days=30))
        await _seed_basic(db, st, total_mv=9.0e9)
        await _seed_basic(db, fresh, total_mv=8.0e9)
        return await rbs.get_ranking_board(db, None, "market_cap", 100)

    out = _run(_scenario, monkeypatch, AS_OF)
    by_symbol = {i.symbol: i for i in out.items}
    assert by_symbol[f"{PREFIX}051"].is_st is True
    assert by_symbol[f"{PREFIX}051"].is_new_stock is False
    assert by_symbol[f"{PREFIX}052"].is_new_stock is True
    assert by_symbol[f"{PREFIX}052"].is_st is False
    assert "含 ST" in out.caliber.sample_scope
    assert "含次新股" in out.caliber.sample_scope


@requires_pg
def test_truncated_ties_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """第 limit+1 名并列时必须标注榜尾是按 stock_id 切的。"""

    async def _scenario(db: AsyncSession) -> RankingBoardResponseOut:
        for index in range(3):
            sid = await _seed_stock(db, f"{PREFIX}06{index}", f"并列{index}", date(2015, 1, 1))
            await _seed_basic(db, sid, total_mv=1234.0)  # 三者完全同值
        return await rbs.get_ranking_board(db, None, "market_cap", 2)

    out = _run(_scenario, monkeypatch, AS_OF)
    assert out.returned_rows == 2
    assert out.truncated_ties is True
    assert any("并列" in n for n in out.notes)
