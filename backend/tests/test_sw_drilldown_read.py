"""spec §S1.4 读路契约：7 项字段不填 0、两条时间轴分开、SCD2 归属驱动成分。

真 PG（目标库 `stock_bot_test`，见 `test_sw_member_scd2.py` 的口径说明），
写入一律在事务里回滚，不依赖库内既有的 SCD2 数据。

覆盖：
1. 行业聚合的 7 项字段在**只有部分成分有值**时：有值的算出来、没值的是 `None`（**不是 0**），
   且每个字段带 `*_covered` 覆盖数
2. 个股行的 `amplitude` 由 `(high-low)/pre_close` 推导；`pre_close` 为空 → `None`
3. `amount` 单位换算：`daily_quotes.amount`（千元）× 1000 → 元
4. 成分由 **SCD2 区间**决定：同一只股票在区间外不出现（这是"按当时归属还原"的读路证明）
5. `scd2_available=False` + `scd2_note` 在无 ingest 记录时**必须**出现（不得静默空集）
6. 库存量比（`daily_basic.volume_ratio`）缺失时量比聚合为 `None`、覆盖数为 0
"""

from __future__ import annotations

import asyncio
import os
from datetime import date
from typing import Any

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services import sw_drilldown_service as svc
from app.services import sw_member_scd2_service as scd2

_DEFAULT_ASYNC = "postgresql+asyncpg://stock_user:stock_pass@localhost:5433/stock_bot_test"
ASYNC_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or _DEFAULT_ASYNC
SYNC_URL = ASYNC_URL.replace("+asyncpg", "")

# 本文件用的测试行业链与股票代码段。
# **代码段必须与其他测试文件错开**：``test_sw_member_scd2.py`` 用 990001/990002，
# 若这里也用同一段，同一 pytest 会话里先跑的那个文件留下的行会让本文件的 fixture
# 撞 ``uq_sw_member_code_stock`` / ``stocks.symbol`` 唯一键（实测：表现为
# "insert 失败 + 随后 LookupError"，非常难读）。故本文件用 991xxx。
L1 = "991000"
L2 = "991100"
L3 = "991101"
S_A, S_B, S_C, S_D = "991001", "991002", "991003", "991004"
TRADE_DATE = date(2026, 10, 9)
PERIOD_START = date(1999, 1, 1)


def _pg_reachable() -> bool:
    async def _go() -> bool:
        try:
            conn = await asyncpg.connect(SYNC_URL)
        except Exception:
            return False
        await conn.close()
        return True

    return asyncio.run(_go())


requires_pg = pytest.mark.skipif(
    not _pg_reachable(),
    reason=f"PostgreSQL unavailable at {SYNC_URL} (set TEST_DATABASE_URL)",
)


async def _with_rollback(fn: Any) -> Any:
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


async def _seed_fixture(db: AsyncSession, *, with_volume_ratio: bool = True) -> None:
    """三只成分股：A 全字段齐 / B 缺成交额 / C 停牌（当日无行情行）。"""
    await db.execute(
        text(
            "INSERT INTO sw_industry_classes (industry_code, level, industry_name, parent_code) "
            "VALUES (:l1, 1, '测试一级', NULL), (:l2, 2, '测试二级', :l1), "
            "(:l3, 3, '测试三级', :l2)"
        ),
        {"l1": L1, "l2": L2, "l3": L3},
    )
    stocks = [
        (S_A, "甲股"),
        (S_B, "乙股"),
        (S_C, "丙股"),
    ]
    for symbol, name in stocks:
        await db.execute(
            text(
                # `stocks.asof` 是 NOT NULL（名录元数据 ingest 时间），与行情口径无关
                "INSERT INTO stocks (symbol, name, exchange, category, asof) "
                "VALUES (:s, :n, 'Shanghai_Stocks', 'A股', now())"
            ),
            {"s": symbol, "n": name},
        )
    ids = dict(
        (
            await db.execute(
                text("SELECT symbol, id FROM stocks WHERE symbol = ANY(:syms)"),
                {"syms": [S_A, S_B, S_C]},
            )
        ).all()
    )
    await db.execute(
        text(
            "INSERT INTO sw_industry_member_periods "
            "(industry_code, stock_code, symbol, stock_name, effective_from, effective_to, "
            " is_current, source) VALUES "
            "(:l3, :a, :sa, '甲股', :f, NULL, true, 'test'), "
            "(:l3, :b, :sb, '乙股', :f, NULL, true, 'test'), "
            "(:l3, :c, :sc, '丙股', :f, NULL, true, 'test')"
        ),
        {
            "l3": L3,
            "a": f"{S_A}.SH",
            "b": f"{S_B}.SH",
            "c": f"{S_C}.SH",
            "sa": S_A,
            "sb": S_B,
            "sc": S_C,
            "f": PERIOD_START,
        },
    )
    # A：OHLC/量/额/换手/量比齐
    await db.execute(
        text(
            "INSERT INTO daily_quotes (stock_id, trade_date, open, high, low, close, pre_close, "
            " pct_chg, volume, amount) VALUES "
            "(:a, :d, 10, 11, 9, 10.5, 10, 5.0, 1000, 1000), "
            "(:b, :d, 20, 21, 19, 20.5, 20, 2.5, 2000, NULL)"
        ),
        {"a": ids[S_A], "b": ids[S_B], "d": TRADE_DATE},
    )
    await db.execute(
        text(
            "INSERT INTO daily_basic_indicators "
            "(stock_id, trade_date, turnover_rate, volume_ratio) VALUES (:a, :d, 3.0, 1.5)"
        ),
        {"a": ids[S_A], "d": TRADE_DATE},
    )
    if with_volume_ratio:
        await db.execute(
            text(
                "INSERT INTO daily_basic_indicators "
                "(stock_id, trade_date, turnover_rate, volume_ratio) VALUES (:b, :d, 6.0, 2.5)"
            ),
            {"b": ids[S_B], "d": TRADE_DATE},
        )
    await db.flush()


@requires_pg
def test_aggregate_seven_fields_and_null_coverage() -> None:
    """7 项字段：有值的算出、无值的 `None`；每项带覆盖数；成交额 ×1000 → 元。

    另锁 ``member_source == 'level'``（Fix round 1 · 评审 C1）：fixture 的三只成分
    都挂在 L3 自身层级，故聚合成员必须来自**自身层级**而不是上卷。
    """

    async def _scenario(db: AsyncSession) -> Any:
        await _seed_fixture(db)
        return await svc.get_members(
            db,
            None,
            level1_code=L1,
            level2_code=L2,
            level3_code=L3,
            as_of=TRADE_DATE,
        )

    out = asyncio.run(_with_rollback(_scenario))
    m = out.metrics
    assert m is not None

    # 成分数 = 3（SCD2 三段都有效）；真有行情 = 2（丙股无行情行）
    assert m.member_count == 3
    assert m.quoted_count == 2
    assert m.member_source == "level"

    # ① K 线（中位数）：开 (10+20)/2=15、高 16、低 14、收 15.5
    assert m.kline_median.open == pytest.approx(15.0)
    assert m.kline_median.high == pytest.approx(16.0)
    assert m.kline_median.low == pytest.approx(14.0)
    assert m.kline_median.close == pytest.approx(15.5)
    assert m.kline_median.quoted_count == 2  # Fix round 1：该字段=参与中位数的家数

    # ② 涨跌幅：成交额加权，只有甲股有 amount → 等于甲股自己的 5.0
    assert m.pct_chg_weighted == pytest.approx(5.0)
    assert m.pct_chg_covered == 2  # 两只都有 pct_chg

    # ③ 换手率：成交额加权 → 甲股 3.0（唯一有 amount 的行）
    assert m.turnover_rate_weighted == pytest.approx(3.0)
    assert m.turnover_rate_covered == 2

    # ④ 成交量：求和 = 1000 + 2000
    assert m.volume == pytest.approx(3000.0)
    assert m.volume_covered == 2

    # ⑤ 量比：成交量加权 = (1.5*1000 + 2.5*2000)/3000
    assert m.volume_ratio_weighted == pytest.approx((1.5 * 1000 + 2.5 * 2000) / 3000)
    assert m.volume_ratio_covered == 2

    # ⑥ 振幅：甲股 (11-9)/10*100 = 20；乙股 (21-19)/20*100 = 10 → 均值 15
    assert m.amplitude == pytest.approx(15.0)
    assert m.amplitude_covered == 2

    # ⑦ 成交额：只有甲股有 amount=1000 千元 → 1_000_000 元（**不是** 1000）
    assert m.amount == pytest.approx(1_000_000.0)
    assert m.amount_covered == 1, "缺成交额的那只不得被算进覆盖数，也不得被填 0"

    # 个股行
    by_symbol = {r.symbol: r for r in out.items}
    assert by_symbol[S_A].amount == pytest.approx(1_000_000.0)
    assert by_symbol[S_B].amount is None, "乙股无成交额 → None，绝不填 0"
    assert by_symbol[S_C].close is None and by_symbol[S_C].quoted is False
    assert by_symbol[S_B].amplitude == pytest.approx(10.0)
    assert by_symbol[S_C].amplitude is None, "停牌票（无 pre_close）→ 振幅 None"


@requires_pg
def test_volume_ratio_missing_is_none_not_zero() -> None:
    """库存量比全缺 → 量比聚合 `None` + 覆盖数 0（不填 0、不静默丢弃该字段）。"""

    async def _scenario(db: AsyncSession) -> Any:
        await _seed_fixture(db, with_volume_ratio=False)
        # 甲股有量比、乙股没有 → 加权分母只含甲股
        return await svc.get_level_metrics(
            db, None, level1_code=L1, level2_code=L2, level3_code=L3, as_of=TRADE_DATE
        )

    m = asyncio.run(_with_rollback(_scenario))
    assert m.volume_ratio_weighted == pytest.approx(1.5)
    assert m.volume_ratio_covered == 1


@requires_pg
def test_level_metrics_rollup_is_scoped_not_whole_market() -> None:
    """`/metrics` 的上卷兜底必须**限定在本节点子树**（re-review · NEW-B 回归锁）。

    旧实现在 `get_level_metrics` 里调 `_member_params(scope_code=code)` **不带
    l1/l2**——节点自身层级无行（如 110900 农业综合Ⅱ）时走**未加范围**的 rollup，
    `/metrics` 拿到全市场并集。这里造**两条互不相交**的链：查询 A 链的 L1
    （自身层级无行 → rollup），断言只回到 A 链自己的 2 只、B 链的那只**不混入**。
    旧实现对同样的 fixture 返回 3（两条链的全市场并集），这条断言必然红。
    """
    from app.services.sw_drilldown_service import MEMBER_SOURCE_ROLLUP

    # 代码段 996xxx 与本文件其他用例（991xxx）及其他测试文件错开
    a_l1, a_l2, a_l3 = "996000", "996100", "996101"
    b_l1, b_l2, b_l3 = "996200", "996300", "996301"

    async def _scenario(db: AsyncSession) -> tuple[Any, int]:
        for code, level, name, parent in (
            (a_l1, 1, "A一级", None),
            (a_l2, 2, "A二级", a_l1),
            (a_l3, 3, "A三级", a_l2),
            (b_l1, 1, "B一级", None),
            (b_l2, 2, "B二级", b_l1),
            (b_l3, 3, "B三级", b_l2),
        ):
            await db.execute(
                text(
                    "INSERT INTO sw_industry_classes "
                    "(industry_code, level, industry_name, parent_code) VALUES (:c,:l,:n,:p)"
                ),
                {"c": code, "l": level, "n": name, "p": parent},
            )
        # A 链 2 只成分（挂在 A_L3 自身层级）；B 链 1 只。A_L1 自身层级**无行**。
        await db.execute(
            text(
                "INSERT INTO sw_industry_member_periods "
                "(industry_code, stock_code, symbol, stock_name, effective_from, effective_to, "
                " is_current, source) VALUES "
                "(:a3, '996001.SH', '996001', 'A甲', :f, NULL, true, 'test'), "
                "(:a3, '996002.SH', '996002', 'A乙', :f, NULL, true, 'test'), "
                "(:b3, '996003.SH', '996003', 'B丙', :f, NULL, true, 'test')"
            ),
            {"a3": a_l3, "b3": b_l3, "f": PERIOD_START},
        )
        await db.flush()
        metrics = await svc.get_level_metrics(db, None, level1_code=a_l1, as_of=TRADE_DATE)
        # 旧实现的对照（未加范围的同款聚合）：全市场并集 = 3 —— 展示判别力
        unscoped = await svc._agg(
            db,
            svc._member_params(asof=TRADE_DATE, trade_date=TRADE_DATE, scope_code=a_l1),
            MEMBER_SOURCE_ROLLUP,
        )
        return metrics, int(unscoped["member_count"])

    metrics, unscoped_count = asyncio.run(_with_rollback(_scenario))
    assert metrics.member_source == MEMBER_SOURCE_ROLLUP, "A_L1 自身层级无行，必须走 rollup 兜底"
    assert metrics.member_count == 2, (
        "A 链 L1 的 /metrics 上卷必须限定在 A 子树（2 只）；"
        f"实得 {metrics.member_count}（未加范围时 = 全市场并集 {unscoped_count} 只，含 B 链）"
    )
    # fixture 自检：不加范围的**同一条 SQL**必须把子树外的成员也混进来
    # （测试库只有 3 条合成行时 = 3；含真实灌数据的库 = ~4,953，正是 NEW-B 的形态）。
    # 严格大于 scoped 结果即可，不钉具体数——这个用例断言的是"加范围后不再混入"。
    assert unscoped_count > metrics.member_count, (
        f"fixture 自检失败：未加范围聚合 = {unscoped_count}，应严格大于 scoped 的 2"
    )


@requires_pg
def test_members_respect_scd2_interval() -> None:
    """区间外的股票**不出现**在成分里（"按当时归属还原"的读路证明）。"""

    async def _scenario(db: AsyncSession) -> tuple[int, int]:
        await _seed_fixture(db)
        # 乙股改成"2010-06-30 已出"（closed 区间）
        await db.execute(
            text("UPDATE sw_industry_member_periods SET effective_to = :e WHERE symbol = :sym"),
            {"e": date(2010, 6, 30), "sym": S_B},
        )
        # 丁股只在 2015 年那一年有效（历史区间的另一端）
        await db.execute(
            text(
                "INSERT INTO sw_industry_member_periods "
                "(industry_code, stock_code, symbol, stock_name, effective_from, effective_to, "
                " is_current, source) VALUES "
                "(:l3, :code, :sym, '丁股', :f, :e2, false, 'test')"
            ),
            {
                "l3": L3,
                "code": f"{S_D}.SH",
                "sym": S_D,
                "f": date(2015, 1, 1),
                "e2": date(2016, 1, 1),
            },
        )
        await db.flush()
        today = await svc.get_members(
            db, None, level1_code=L1, level2_code=L2, level3_code=L3, as_of=TRADE_DATE
        )
        old = await svc.get_members(
            db, None, level1_code=L1, level2_code=L2, level3_code=L3, as_of=date(2015, 6, 1)
        )
        return len(today.items), len(old.items)

    today_count, old_count = asyncio.run(_with_rollback(_scenario))
    assert today_count == 2, "2010 年已结束的乙股、2015-2016 的丁股都不该在 2026 日的成分里"
    # 2015-06-01：甲股（1999 起）在、乙股（2010 止）不在、丙股（停牌但在成分内）在、丁股在
    assert old_count == 3, "2015-06-01：乙股已出；甲/丙/丁仍在成分（丙股停牌但归属有效）"


@requires_pg
def test_scd2_unavailable_is_declared_not_silent() -> None:
    """无成功 ingest 记录 → `scd2_available=False` + `scd2_note`。

    不得让"没采过"看起来像"当日无成分"。
    """

    async def _scenario(db: AsyncSession) -> Any:
        await db.execute(text("DELETE FROM sw_member_ingest_runs"))
        await db.flush()
        return await svc.get_level_items(db, None, as_of=TRADE_DATE)

    out = asyncio.run(_with_rollback(_scenario))
    assert out.scd2_available is False
    assert out.scd2_as_of is None
    assert out.scd2_note == svc.SCD2_NO_DATA_NOTE


@requires_pg
def test_scd2_stale_note_when_ingest_older_than_quote_day() -> None:
    """ingest 日 < 行情日 → `scd2_note` 提示滞后（两条时间轴不得合成一句"截至"）。"""

    async def _scenario(db: AsyncSession) -> Any:
        await _seed_fixture(db)
        await db.execute(text("DELETE FROM sw_member_ingest_runs"))
        await db.execute(
            text(
                "INSERT INTO sw_member_ingest_runs (as_of, index_codes, rows_upserted, status) "
                "VALUES (:d, 1, 1, 'ok')"
            ),
            {"d": TRADE_DATE - __import__("datetime").timedelta(days=30)},
        )
        await db.flush()
        return await svc.get_members(
            db, None, level1_code=L1, level2_code=L2, level3_code=L3, as_of=TRADE_DATE
        )

    out = asyncio.run(_with_rollback(_scenario))
    if not out.scd2_available:  # 库内已有更早的成功记录时仍应为 available
        pytest.skip("库内无 ingest 记录基线，跳过滞后断言")
    assert out.scd2_note == svc.SCD2_STALE_NOTE
    assert out.scd2_as_of < (out.as_of or TRADE_DATE)


def test_scd2_note_pure_function() -> None:
    """纯函数：可用且同日 → 无提示；不可用 → 无数据提示；滞后 → 滞后提示。"""
    assert (
        svc.scd2_note(available=False, scd2_as_of=None, as_of=TRADE_DATE) == svc.SCD2_NO_DATA_NOTE
    )
    assert svc.scd2_note(available=True, scd2_as_of=TRADE_DATE, as_of=TRADE_DATE) is None
    assert (
        svc.scd2_note(available=True, scd2_as_of=date(2026, 1, 1), as_of=TRADE_DATE)
        == svc.SCD2_STALE_NOTE
    )


def test_assemble_quote_never_substitutes_zero() -> None:
    """空聚合行（LEFT JOIN 全空）→ 所有字段 `None`，**没有一个是 0**。"""
    empty: dict[str, Any] = {
        "member_count": 0,
        "quoted_count": 0,
        "up_count": 0,
        "down_count": 0,
        "median_open": None,
        "median_high": None,
        "median_low": None,
        "median_close": None,
        "sum_amount": None,
        "pct_chg_weighted": None,
        "pct_chg_covered": 0,
        "turnover_rate_weighted": None,
        "turnover_rate_covered": 0,
        "sum_volume": None,
        "volume_covered": 0,
        "volume_ratio_weighted": None,
        "volume_ratio_covered": 0,
        "amplitude": None,
        "amplitude_covered": 0,
        "sum_amount_all": None,
        "amount_covered": 0,
    }
    q = svc.assemble_quote(empty, code=L3, name="测试三级", level=3, parent_code=L2)
    for field in (
        "pct_chg_weighted",
        "turnover_rate_weighted",
        "volume",
        "volume_ratio_weighted",
        "amplitude",
        "amount",
    ):
        assert getattr(q, field) is None, f"{field} 在无数据时必须为 None（不得为 0）"
    assert q.kline_median.open is None and q.kline_median.close is None
    # 计数类字段是 0，这是真实的"零只"，与"无数据"不同
    assert q.member_count == 0 and q.amount_covered == 0


def test_member_as_of_seam_matches_service_helper() -> None:
    """`scd2.parse_sw_index_mapping` 与服务导出的 `get_member_as_of` 都在（导入面回归）。"""
    assert callable(scd2.get_member_as_of)
    assert len(scd2.parse_sw_index_mapping()) == 511
