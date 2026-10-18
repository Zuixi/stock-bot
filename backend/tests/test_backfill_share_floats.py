"""S11 解禁回填脚本（`scripts/backfill_share_floats.py`）：分窗 / 分页 / 拆窗 / 幂等。

**不网页**：脚本的 ``fetch`` 是注入参数，用例喂固定 DataFrame，一次真实 TuShare 调用都不发。
需要真库的用例在**回滚事务**里跑（测试库 ``share_floats`` 跑前跑后都是空的），库不可达时 skip。

钉住的三件事（对应 spec §S11.1/§S11.4）：

1. **分批 ≤90 天**且区间无缝无重叠（``chunk_windows``）；
2. **幂等**：同一批数据重跑，第二次 ``rows_inserted == 0``，库里行数不变；
3. **取不完不许静默丢行**：分页到 TuShare 的 ``offset`` 上限还取不完 → 拆窗重取；
   单日仍取不完 → 该窗口进 ``failures``（调用方据此非零退出）。
"""

from __future__ import annotations

import asyncio
import os
from datetime import date
from typing import Any

import asyncpg
import pandas as pd
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.market_data import ShareFloat
from scripts import backfill_share_floats as bf

_DEFAULT_ASYNC = "postgresql+asyncpg://stock_user:stock_pass@localhost:5433/stock_bot_test"
ASYNC_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or _DEFAULT_ASYNC
SYNC_URL = ASYNC_URL.replace("+asyncpg", "")

START = date(2026, 3, 2)
END = date(2026, 3, 4)


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
    not _pg_reachable(), reason=f"PostgreSQL unavailable at {SYNC_URL} (set TEST_DATABASE_URL)"
)


async def _with_rollback(factory: Any) -> Any:
    """回滚事务；脚本内部会 ``commit``，Session 默认以 SAVEPOINT 加入外部事务，回滚仍然有效。"""
    engine = create_async_engine(ASYNC_URL)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            session = AsyncSession(bind=conn)
            try:
                return await factory(session)
            finally:
                await session.close()
                await trans.rollback()
    finally:
        await engine.dispose()


def _frame(rows: list[tuple[str, str, str, float, float, str]]) -> pd.DataFrame:
    """TuShare 形状的 DataFrame（日期是 ``YYYYMMDD`` 字符串，数值列 float）。"""
    return pd.DataFrame(
        rows,
        columns=["ann_date", "float_date", "ts_code", "float_share", "float_ratio", "holder_name"],
    ).assign(share_type="首发原始股")


def _fixed_fetch(frame: pd.DataFrame, *, single_day_frame: pd.DataFrame | None = None) -> Any:
    """第一页返回 ``frame``，之后为空（= 一个窗口就这么多行）。"""

    async def fetch(start: date, end: date, offset: int) -> pd.DataFrame:
        if offset:
            return frame.iloc[0:0]
        if single_day_frame is not None and start == end:
            return single_day_frame
        return frame

    return fetch


# ---------------------------------------------------------------------------
# 纯函数：分批 / 月份平移 / 去重
# ---------------------------------------------------------------------------


def test_chunk_windows_is_contiguous_and_never_exceeds_ninety_days() -> None:
    windows = bf.chunk_windows(date(2026, 1, 1), date(2026, 4, 15), 30)
    assert all((end - start).days + 1 <= 90 for start, end in windows)
    assert all((end - start).days + 1 <= 30 for start, end in windows), "默认批 ≤30 天"
    assert windows[0][0] == date(2026, 1, 1)
    assert windows[-1][1] == date(2026, 4, 15)
    for (_, prev_end), (next_start, _) in zip(windows, windows[1:]):
        assert next_start == prev_end.fromordinal(prev_end.toordinal() + 1), (
            "批之间必须无缝且不重叠"
        )


def test_chunk_windows_rejects_windows_over_the_spec_cap() -> None:
    """spec §S11.1：每批 ≤90 天。超过就不是"分批"，直接拒绝（不静默截断）。"""
    with pytest.raises(ValueError, match="1..90"):
        bf.chunk_windows(date(2026, 1, 1), date(2026, 3, 1), 91)
    with pytest.raises(ValueError, match="1..90"):
        bf.chunk_windows(date(2026, 1, 1), date(2026, 3, 1), 0)


def test_default_window_is_24_months_back_and_6_forward(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bf, "_today_sh", lambda: date(2026, 10, 19))
    assert bf.default_window() == (date(2024, 10, 19), date(2027, 4, 19))


def test_add_months_lands_on_the_last_valid_day() -> None:
    assert bf._add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert bf._add_months(date(2024, 2, 29), -12) == date(2023, 2, 28)
    assert bf._add_months(date(2026, 10, 19), -24) == date(2024, 10, 19)


def test_dedupe_uses_the_database_unique_key() -> None:
    rows = [
        {
            "ann_date": START,
            "ts_code": "600519.SH",
            "holder_name": "甲",
            "share_type": "首发原始股",
        },
        {
            "ann_date": START,
            "ts_code": "600519.SH",
            "holder_name": "甲",
            "share_type": "首发原始股",
        },
        {
            "ann_date": START,
            "ts_code": "600519.SH",
            "holder_name": "乙",
            "share_type": "首发原始股",
        },
    ]
    assert len(bf._dedupe(rows)) == 2


# ---------------------------------------------------------------------------
# 真库：幂等 / 拆窗 / 响亮失败 / 覆盖率
# ---------------------------------------------------------------------------


@requires_pg
def test_backfill_is_idempotent_on_rerun() -> None:
    """重跑同一窗口：第二次新增 0 行，库里行数不变（spec §S11.4 的硬判据）。"""

    frame = _frame(
        [
            ("20260301", "20260302", "600519.SH", 1000.0, 4.2, "甲"),
            ("20260301", "20260302", "600519.SH", 500.0, 1.1, "乙"),
            ("20260301", "20260303", "600036.SH", 800.0, 2.0, "丙"),
        ]
    )

    async def _go(session: AsyncSession) -> tuple[Any, Any, int]:
        first = await bf.backfill_range(_fixed_fetch(frame), session, START, END, chunk_days=7)
        second = await bf.backfill_range(_fixed_fetch(frame), session, START, END, chunk_days=7)
        count = int((await session.execute(select(func.count(ShareFloat.id)))).scalar_one())
        return first, second, count

    first, second, count = asyncio.run(_with_rollback(_go))
    assert first.failures == [] and second.failures == []
    assert first.rows_inserted == 3
    assert second.rows_inserted == 0, "重跑必须新增 0 行（唯一键 + ON CONFLICT DO NOTHING）"
    assert second.rows_existing == 3, "第二次的 3 行全部命中已有行"
    assert count == 3, "库内行数不因重跑变化"


@requires_pg
def test_backfill_splits_a_window_that_hits_the_offset_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分页到 ``offset`` 上限还取不完 → 对半拆窗重取（不静默丢行）。

    把上限压到 2 行来模拟"一个月 >10 万行"的真实情形（真库 2026-10 单日就 1.8 万行）。
    """
    monkeypatch.setattr(bf, "PAGE_SIZE", 2)
    monkeypatch.setattr(bf, "MAX_OFFSET", 2)
    busy = _frame(
        [
            ("20260301", "20260302", "600519.SH", 1000.0, 4.2, "甲"),
            ("20260301", "20260302", "600519.SH", 500.0, 1.1, "乙"),
        ]
    )
    quiet = _frame([("20260301", "20260303", "600036.SH", 800.0, 2.0, "丙")])

    async def fetch(start: date, end: date, offset: int) -> pd.DataFrame:
        return quiet if start == end else busy

    async def _go(session: AsyncSession) -> bf.BackfillStats:
        return await bf.backfill_range(fetch, session, START, END, chunk_days=7)

    stats = asyncio.run(_with_rollback(_go))
    assert stats.failures == [], "拆窗后应当取全（不是失败）"
    assert stats.splits >= 1, "触到 offset 上限必须拆窗，不能静默截断"
    assert stats.rows_inserted >= 3


@requires_pg
def test_single_day_over_the_page_cap_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """单日仍取不完 → 该窗口进 ``failures``（调用方据此非零退出），不假装成功。"""
    monkeypatch.setattr(bf, "PAGE_SIZE", 1)
    monkeypatch.setattr(bf, "MAX_OFFSET", 1)
    frame = _frame([("20260301", "20260302", "600519.SH", 1000.0, 4.2, "甲")])

    async def fetch(start: date, end: date, offset: int) -> pd.DataFrame:
        return frame

    async def _go(session: AsyncSession) -> bf.BackfillStats:
        return await bf.backfill_range(fetch, session, START, START, chunk_days=1)

    stats = asyncio.run(_with_rollback(_go))
    assert len(stats.failures) == 1
    assert "2026-03-02..2026-03-02" in stats.failures[0]
    assert "_WindowTooLarge" in stats.failures[0]


@requires_pg
def test_coverage_counts_stocks_rows_and_upcoming_window() -> None:
    """覆盖率 = 有解禁记录的股票数 / 全市场股票数；未来窗口单独数（探测器有效覆盖）。"""

    async def _go(session: AsyncSession) -> bf.Coverage:
        await session.execute(
            ShareFloat.__table__.insert(),
            [
                {
                    "ann_date": START,
                    "float_date": date(2099, 1, 5),
                    "ts_code": "600519.SH",
                    "float_share": 1000.0,
                    "float_ratio": 4.2,
                    "holder_name": "甲",
                    "share_type": "首发原始股",
                },
                {
                    "ann_date": START,
                    "float_date": date(2099, 2, 5),
                    "ts_code": "600519.SH",
                    "float_share": 500.0,
                    "float_ratio": 1.1,
                    "holder_name": "乙",
                    "share_type": "首发原始股",
                },
                {
                    "ann_date": START,
                    "float_date": date(2099, 3, 5),
                    "ts_code": "600036.SH",
                    "float_share": 800.0,
                    "float_ratio": 2.0,
                    "holder_name": "丙",
                    "share_type": "首发原始股",
                },
            ],
        )
        return await bf.coverage(session)

    cov = asyncio.run(_with_rollback(_go))
    assert cov.stocks_with_unlock == 2
    assert cov.rows == 3
    assert cov.universe > 0, "全市场股票数必须来自 stocks 表（不是硬编码）"
    assert 0.0 < cov.share_pct <= 100.0
    assert cov.float_date_to == date(2099, 3, 5)
