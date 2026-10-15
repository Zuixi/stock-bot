"""北向口径的**真 PG** 用例（只用 ``TEST_DATABASE_URL``；不联网、不写死库名）。

纯函数不变量在 ``tests/test_market_data_northbound_caliber.py``；本文件锁"只有真库才能证"的三段：

**A. 库层口径闸门**（迁移 `b6a1f0c2d7e4` 的产物）
1. ``northbound_daily.amount_kind`` 存在且 **NOT NULL**：漏标口径的行**插不进去**。
2. ``ck_northbound_amount_kind`` 只允许 ``net_buy`` / ``turnover``：**第三类在库层就不存在**。

**B. 写路径**（``market_data_repo.upsert_northbound``）
3. 幂等 upsert 带上口径；冲突时 **``amount`` 与 ``amount_kind`` 一起刷**——
   只刷数值不刷口径会给"成交额被当成净买入"留回程。

**C. 读路径**
4. ``list_northbound`` 把**行级口径**原样带出：断点前的净买入历史仍在，但**标着断点前口径**，
   不会被静默混进成交额序列。

**不污染库**：所有写入都包在 ``rollback`` 的事务里（Ruling 17 的做法）。
**测试隔离**：库地址**只**取 ``TEST_DATABASE_URL``，**不写死任何库名**；未设置时整文件 skip。
"""

from __future__ import annotations

import asyncio
import os
from datetime import date
from typing import Any

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.repositories import market_data_repo
from app.services.northbound_caliber import NET_BUY, TURNOVER

#: **只**认 `TEST_DATABASE_URL`。没有就 skip —— **绝不**回落到写死的 dev 库名。
ASYNC_URL = os.environ.get("TEST_DATABASE_URL", "")
SYNC_URL = ASYNC_URL.replace("+asyncpg", "")


def _pg_reachable() -> bool:
    if not ASYNC_URL:
        return False

    async def _go() -> bool:
        try:
            conn = await asyncpg.connect(SYNC_URL)
        except Exception:  # noqa: BLE001 — 连不上就 skip，不猜库
            return False
        await conn.close()
        return True

    return asyncio.run(_go())


requires_pg = pytest.mark.skipif(
    not _pg_reachable(), reason="需要 TEST_DATABASE_URL 指向可达的 PostgreSQL（本文件不写死库名）"
)


async def _with_rollback(coro_factory: Any) -> Any:
    """在一个**回滚**的事务里跑：测试不向库里留任何行。"""
    engine = create_async_engine(ASYNC_URL)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            session = AsyncSession(bind=conn)
            try:
                result = await coro_factory(session)
                await session.flush()
                return result
            finally:
                await session.close()
                await trans.rollback()
    finally:
        await engine.dispose()


#: 合成日：断点前 / 断点后 各一日（远离已上线数据，且都在 `list_northbound(1000)` 窗口内）。
_PRE_DAY = date(2024, 8, 16)
_POST_DAY = date(2026, 9, 7)


# ---------------------------------------------------------------------------
# A. 库层口径闸门
# ---------------------------------------------------------------------------


@requires_pg
async def test_amount_kind_column_is_not_null_and_check_constrained() -> None:
    async def _go(session: AsyncSession) -> dict[str, Any]:
        nullable = await session.scalar(
            text(
                "select is_nullable from information_schema.columns "
                "where table_name = 'northbound_daily' and column_name = 'amount_kind'"
            )
        )
        constraint = await session.scalar(
            text(
                "select pg_get_constraintdef(oid) from pg_constraint "
                "where conname = 'ck_northbound_amount_kind'"
            )
        )
        assert (
            await session.scalar(
                text(
                    "select count(*) from information_schema.columns "
                    "where table_name = 'northbound_daily' and column_name = 'net_amount'"
                )
            )
            == 0
        ), "旧列名 net_amount（净流入口径的字段名）必须已改掉"
        return {"nullable": nullable, "constraint": constraint}

    info = await _with_rollback(_go)

    assert info["nullable"] == "NO", "口径列必须 NOT NULL：漏标口径的行不得入库"
    assert info["constraint"] is not None, "库层缺少 amount_kind 的 CHECK 约束"
    assert "'net_buy'" in info["constraint"] and "'turnover'" in info["constraint"]


@requires_pg
async def test_rows_without_or_with_unknown_caliber_are_rejected() -> None:
    async def _go(session: AsyncSession) -> list[str]:
        failures: list[str] = []

        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(
                    text(
                        "insert into northbound_daily (trade_date, amount, source) "
                        "values (:d, :a, 'test')"
                    ),
                    {"d": _POST_DAY, "a": 273259.26},
                )
        failures.append("missing_caliber")

        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(
                    text(
                        "insert into northbound_daily (trade_date, amount, amount_kind, source) "
                        "values (:d, :a, 'net_inflow', 'test')"
                    ),
                    {"d": _POST_DAY, "a": 273259.26},
                )
        failures.append("unknown_caliber")

        return failures

    assert await _with_rollback(_go) == ["missing_caliber", "unknown_caliber"]


# ---------------------------------------------------------------------------
# B. 写路径（幂等 + 数值与口径同步）
# ---------------------------------------------------------------------------

_SELECT_ONE = text("select amount, amount_kind from northbound_daily where trade_date = :d")


@requires_pg
async def test_upsert_northbound_refreshes_caliber_together_with_the_value() -> None:
    """冲突时 ``amount`` 与 ``amount_kind`` **一起**刷：错标的口径必须能被写路径掰回来。

    反过来（只刷数值）就留下"库里是成交额、口径列写着净买入"的行——正是要修的错标。
    """

    async def _go(session: AsyncSession) -> None:
        rows = [
            {"trade_date": _PRE_DAY, "amount": -3209.23, "amount_kind": NET_BUY},
            # 先故意标错：CHECK 只限取值域，不判"日期↔口径"一致（那是写路径的职责）
            {"trade_date": _POST_DAY, "amount": 273259.26, "amount_kind": NET_BUY},
        ]
        assert await market_data_repo.upsert_northbound(session, rows) == 2
        assert tuple((await session.execute(_SELECT_ONE, {"d": _POST_DAY})).one()) == (
            273259.26,
            NET_BUY,
        )

        # 正确的 upsert 把口径与数值一起刷回来
        assert (
            await market_data_repo.upsert_northbound(
                session,
                [{"trade_date": _POST_DAY, "amount": 300000.0, "amount_kind": TURNOVER}],
            )
            == 1
        )
        assert tuple((await session.execute(_SELECT_ONE, {"d": _POST_DAY})).one()) == (
            300000.0,
            TURNOVER,
        )

        # 幂等：重跑同一批不新增行，断点前的净买入历史仍在且口径不变
        assert (
            await market_data_repo.upsert_northbound(
                session,
                [
                    {"trade_date": _PRE_DAY, "amount": -3209.23, "amount_kind": NET_BUY},
                    {"trade_date": _POST_DAY, "amount": 300000.0, "amount_kind": TURNOVER},
                ],
            )
            == 2
        )
        assert tuple((await session.execute(_SELECT_ONE, {"d": _PRE_DAY})).one()) == (
            -3209.23,
            NET_BUY,
        )
        total = await session.scalar(
            text("select count(*) from northbound_daily where trade_date in (:a, :b)"),
            {"a": _PRE_DAY, "b": _POST_DAY},
        )
        assert total == 2, "幂等：重跑不新增行"

    await _with_rollback(_go)


# ---------------------------------------------------------------------------
# C. 读路径：行级口径原样带出（断点前的净买入历史仍可读，但标着口径）
# ---------------------------------------------------------------------------


@requires_pg
async def test_list_northbound_surfaces_per_row_caliber() -> None:
    async def _go(session: AsyncSession) -> list[tuple[date, str]]:
        await market_data_repo.upsert_northbound(
            session,
            [
                {"trade_date": _PRE_DAY, "amount": -3209.23, "amount_kind": NET_BUY},
                {"trade_date": _POST_DAY, "amount": 273259.26, "amount_kind": TURNOVER},
            ],
        )
        rows = await market_data_repo.list_northbound(session, 1000)
        return [(r.trade_date, r.amount_kind) for r in rows]

    kinds = await _with_rollback(_go)
    assert (_PRE_DAY, NET_BUY) in kinds, "断点前的净买入历史必须仍在，且标着净买入口径"
    assert (_POST_DAY, TURNOVER) in kinds
    assert all(kind in (NET_BUY, TURNOVER) for _, kind in kinds)
