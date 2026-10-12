"""spec §S1.2 数据契约（真 PG，目标库 = **`stock_bot_test`**）。

**为什么不照抄 `test_intraday_snapshot_table.py`**：那个文件把 dev 库 `stock_bot`
硬编码在 ``:58-59``（Ruling 17），在共享环境下必然失败（dev 库停在其他分支的
revision）。本文件从 ``TEST_DATABASE_URL`` 取 URL、默认落到 ``stock_bot_test``
（端口 5433），并在库不可达时 skip 而不是红。

覆盖：
1. 迁移**只加 2 列**且两列都可空（``information_schema`` 断言 is_nullable=YES）
2. ``downgrade -1`` 再 ``upgrade head`` 干净往返（列消失 → 列回来）
3. PIT 落库往返：写入带 ``published_at`` 的行 → 读回 → 值一致
4. 重跑同一 ingest → 新增行数 0（既有 6 列 unique 仍生效）
5. upsert 覆盖写会把新 PIT/口径值写上（不是只在首插生效）
6. 通用读面从行上取四徽章；series 只取 registry 源优先级最高的一个源
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from app.services import industry_metric_service as service

REVISION = "f1a2b3c4d5e6"
PREV_REVISION = "a9b3c7d1e5f2"

# 目标库 = stock_bot_test（Ruling 17）；与 brief 的运行环境变量一致
_DEFAULT_ASYNC = "postgresql+asyncpg://stock_user:stock_pass@localhost:5433/stock_bot_test"
ASYNC_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or _DEFAULT_ASYNC
SYNC_URL = ASYNC_URL.replace("+asyncpg", "")

ALEMBIC_BIN = str(Path(__file__).resolve().parents[1] / ".venv" / "bin" / "alembic")
BACKEND_DIR = str(Path(__file__).resolve().parents[1])


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
    not _PG_OK,
    reason=f"PostgreSQL unavailable at {SYNC_URL} (set TEST_DATABASE_URL)",
)


def _alembic(*args: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = ASYNC_URL
    env["TUSHARE_TOKEN"] = ""
    result = subprocess.run(
        [ALEMBIC_BIN, *args],
        capture_output=True,
        text=True,
        cwd=BACKEND_DIR,
        env=env,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"alembic {' '.join(args)} failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


async def _column_state() -> dict[str, str]:
    """{列名: is_nullable}——只关心本次新增的两列。"""
    conn = await asyncpg.connect(SYNC_URL)
    try:
        rows = await conn.fetch(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name = $1 AND column_name = ANY($2::text[])",
            "industry_metrics",
            ["published_at", "caliber_version"],
        )
        return {r["column_name"]: r["is_nullable"] for r in rows}
    finally:
        await conn.close()


# ── 1) 迁移只加 2 列 + 都可空 ──────────────────────────────────────────


@requires_pg
def test_migration_adds_two_nullable_columns() -> None:
    """两列存在且 `is_nullable = YES`（不许 NOT NULL）。"""
    _alembic("upgrade", "head")
    state = asyncio.run(_column_state())
    assert state == {"published_at": "YES", "caliber_version": "YES"}


def test_migration_file_has_no_create_table() -> None:
    """硬性约束 1：本迁移只加列，`grep -n create_table` 必须无命中。"""
    path = (
        Path(BACKEND_DIR)
        / "app"
        / "migrations"
        / "versions"
        / f"{REVISION}_add_metric_pit_and_caliber.py"
    )
    text = path.read_text()
    assert "create_table" not in text
    assert f'down_revision: str | None = "{PREV_REVISION}"' in text
    assert text.count("op.add_column") == 2
    assert text.count("op.drop_column") == 2


# ── 2) downgrade → upgrade 干净往返 ────────────────────────────────────


@requires_pg
def test_migration_up_down_up_round_trip() -> None:
    """down 掉两列 → up 回来。失败也把库恢复到 head（不把库留在中间版本）。"""
    _alembic("upgrade", "head")
    assert set(asyncio.run(_column_state())) == {"published_at", "caliber_version"}
    try:
        _alembic("downgrade", PREV_REVISION)
        assert asyncio.run(_column_state()) == {}
        _alembic("upgrade", "head")
        assert set(asyncio.run(_column_state())) == {"published_at", "caliber_version"}
    finally:
        _alembic("upgrade", "head")


# ── 3~6) 读写路径（真库，走生产 repo/service） ─────────────────────────


async def _with_session(fn: Any) -> Any:
    """开一个真 async session 跑 fn（每次独立 engine，测试完 dispose）。"""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    eng = create_async_engine(ASYNC_URL)
    try:
        factory = sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)
        async with factory() as db:
            return await fn(db)
    finally:
        await eng.dispose()


async def _cleanup(metric_key: str) -> None:
    conn = await asyncpg.connect(SYNC_URL)
    try:
        await conn.execute(
            "DELETE FROM industry_metrics WHERE metric_key = $1 AND industry_key = $2",
            metric_key,
            "pig",
        )
    finally:
        await conn.close()


@requires_pg
def test_pit_round_trip_and_write_path() -> None:
    """写入带 `published_at` + `caliber_version` 的行 → 读回 → 值一致。

    用生产 `batch_upsert_metrics`（人工/CSV 通道 = 公告类数据入口）写，用生产
    `get_latest_metrics_across_scopes` 读——两端都是要被复用的真实路径。
    探针 scope 用 'pig' 但 metric_key 未登记 → 由 registry 拒绝，故这里改用
    registry 中真实登记的 `industry_cost_avg`（monthly，人工源）。
    """
    metric_key = "industry_cost_avg"
    published = datetime(2026, 10, 8, 16, 30, tzinfo=UTC)
    period = date(2026, 8, 31)

    async def _scenario(db: Any) -> tuple[Any, int]:
        await _cleanup(metric_key)
        res = await service.batch_upsert_metrics(
            db,
            "pig",
            [
                {
                    "metric_key": metric_key,
                    "period": period,
                    "value": 13.55,
                    "source": "manual",
                    "published_at": published,
                    "caliber_version": "M1-2026",
                }
            ],
        )
        await db.commit()
        latest = await service.get_latest_metrics_across_scopes(
            db, scope_keys=["pig"], metric_keys=[metric_key]
        )
        return latest[0], res["upserted"]

    try:
        out, upserted = asyncio.run(_with_session(_scenario))
    finally:
        asyncio.run(_cleanup(metric_key))

    assert upserted >= 1
    assert out.value == pytest.approx(13.55)
    assert out.published_at is not None
    assert out.published_at.astimezone(UTC) == published, "published_at 落库往返必须一致"
    assert out.as_of == date(2026, 10, 8)  # PIT 徽章 = 实际可得时点，不是 period
    assert out.caliber_version == "M1-2026"


@requires_pg
def test_ingest_rerun_is_idempotent_and_overwrites_pit() -> None:
    """重跑同一 ingest → 新增行数 0（6 列 unique 仍生效）；PIT 值被覆盖写。"""
    metric_key = "industry_cost_avg"
    period = date(2026, 8, 31)
    first = datetime(2026, 10, 8, 16, 30, tzinfo=UTC)
    second = datetime(2026, 10, 9, 9, 0, tzinfo=UTC)

    async def _scenario(db: Any) -> tuple[int, datetime | None]:
        await _cleanup(metric_key)
        item = {
            "metric_key": metric_key,
            "period": period,
            "value": 13.55,
            "source": "manual",
            "published_at": first,
            "caliber_version": "M1-2026",
        }
        await service.batch_upsert_metrics(db, "pig", [item])
        await db.commit()
        # 同一 (scope, stock_id, metric_key, source, freq, period) 再写一次：不新增行，只覆盖
        await service.batch_upsert_metrics(db, "pig", [{**item, "published_at": second}])
        await db.commit()
        conn = await asyncpg.connect(SYNC_URL)
        try:
            n = await conn.fetchval(
                "SELECT count(*) FROM industry_metrics WHERE industry_key = $1 AND metric_key = $2",
                "pig",
                metric_key,
            )
            ts = await conn.fetchval(
                "SELECT published_at FROM industry_metrics "
                "WHERE industry_key = $1 AND metric_key = $2",
                "pig",
                metric_key,
            )
        finally:
            await conn.close()
        return int(n), ts

    try:
        n, ts = asyncio.run(_with_session(_scenario))
    finally:
        asyncio.run(_cleanup(metric_key))

    assert n == 1, f"重跑同一 ingest 新增了行（unique 失效）: {n}"
    assert ts is not None and ts.astimezone(UTC) == second, "覆盖写没把新 PIT 值写上"


@requires_pg
def test_series_takes_highest_priority_source_and_carries_pit() -> None:
    """series 只取 registry 源优先级最高的一个源，且点带 PIT 徽章。

    同一 period 故意放 manual + mock 两行（mock 不在人工导入白名单里，故直插），
    序列必须只输出 manual —— 否则图会把两套口径连成一条假趋势。
    """
    metric_key = "pork_wholesale"  # sources = ["manual", "mock"]
    periods = [date(2026, 7, 31), date(2026, 8, 31)]

    async def _scenario() -> Any:
        conn = await asyncpg.connect(SYNC_URL)
        try:
            await conn.execute(
                "DELETE FROM industry_metrics WHERE industry_key = 'pig' AND metric_key = $1",
                metric_key,
            )
            for p, v, src in [
                (periods[0], 19.5, "manual"),
                (periods[0], 19.9, "mock"),
                (periods[1], 20.1, "manual"),
            ]:
                await conn.execute(
                    "INSERT INTO industry_metrics "
                    "(industry_key, stock_id, metric_key, source, source_tier, freq, period, "
                    " value, unit, published_at, caliber_version) "
                    "VALUES ('pig', 0, $1, $2, 'official', 'daily', $3, $4, '元/kg', NULL, NULL)",
                    metric_key,
                    src,
                    p,
                    v,
                )
        finally:
            await conn.close()

        async def _read(db: Any) -> Any:
            return await service.get_series(db, "pig", metric_key, "1y")

        return await _with_session(_read)

    try:
        out = asyncio.run(_scenario())
    finally:
        asyncio.run(_cleanup(metric_key))

    assert out.source == "manual", "未按 registry 优先级排除低优先源（mock 混入）"
    assert [p.value for p in out.points] == pytest.approx([19.5, 20.1])
    assert all(p.as_of == p.period for p in out.points)  # published_at NULL → 回退 period
    assert all(p.published_at is None for p in out.points), "回退值不得写进 PIT 字段"
    assert out.range == "1y" and out.limit == 366
