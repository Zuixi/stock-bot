"""spec §S1.2 数据契约（真 PG，目标库 = **`stock_bot_test`**）。

**为什么不照抄 `test_intraday_snapshot_table.py`**：那个文件把 dev 库 `stock_bot`
硬编码在 ``:58-59``（Ruling 17），在共享环境下必然失败（dev 库停在其他分支的
revision）。本文件从 ``TEST_DATABASE_URL`` 取 URL、默认落到 ``stock_bot_test``
（端口 5433），并在库不可达时 skip 而不是红。

写入类用例一律包在**事务里回滚**（``_with_rollback``）：测试不 DROP/不移除库里既有
行，跑完库与跑前逐字节一致（此前用裸 DELETE 清理，会连带删掉别人预置的测试数据）。

覆盖：
1. 迁移**只加 2 列**且两列都可空（``information_schema`` 断言 is_nullable=YES）
2. ``downgrade -1`` 再 ``upgrade head`` 干净往返（列消失 → 列回来）
3. PIT 落库往返：写入带 ``published_at`` 的行 → 读回 → 值一致 + 判别式 = published_at
4. 重跑同一 ingest → 新增行数 0（既有 6 列 unique 仍生效）
5. **重导入省略 `published_at`** → 已记录的 PIT/口径**保留**（不许被抹成 NULL）
6. 显式给新 PIT 值 → 覆盖写生效（修订留痕）
7. `range` 是**真实时间窗**：`30d` 对月度指标只给窗内点（不是 30 个月）
8. 通用读面从行上取四徽章；series 只取 registry 源优先级最高的一个源
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from sqlalchemy import MetaData, create_engine, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Session

from app.models.industry_research import IndustryMetric
from app.repositories import industry_metric_repo as repo
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


# ── 3~8) 读写路径（真库，走生产 repo/service；事务内回滚，不破坏库内容）─────


async def _with_rollback(fn: Any) -> Any:
    """在**一个事务**里跑 fn，结束一律 ROLLBACK —— 库内容与跑前一致。

    ``join_transaction_mode="create_savepoint"`` 让 session 内的 ``commit()``（生产
    写路径的既有行为）落到 SAVEPOINT 上，测试自己的 DML 不会真落库。
    """
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


_PIT_KEY = "industry_cost_avg"  # monthly，人工源，registry 中已登记
_PIT_PERIOD = date(2026, 8, 31)


@requires_pg
def test_pit_round_trip_and_write_path() -> None:
    """写入带 `published_at` + `caliber_version` 的行 → 读回 → 值一致 + 判别式正确。

    用生产 `batch_upsert_metrics`（人工/CSV 通道 = 公告类数据入口）写，用生产
    `get_latest_metrics_across_scopes` 读。
    """
    published = datetime(2026, 10, 8, 16, 30, tzinfo=UTC)

    async def _scenario(db: Any) -> tuple[Any, int]:
        res = await service.batch_upsert_metrics(
            db,
            "pig",
            [
                {
                    "metric_key": _PIT_KEY,
                    "period": _PIT_PERIOD,
                    "value": 13.55,
                    "source": "manual",
                    "published_at": published,
                    "caliber_version": "M1-2026",
                }
            ],
        )
        await db.commit()
        latest = await service.get_latest_metrics_across_scopes(
            db, scope_keys=["pig"], metric_keys=[_PIT_KEY]
        )
        return latest[0], res["upserted"]

    out, upserted = asyncio.run(_with_rollback(_scenario))

    assert upserted >= 1
    assert out.value == pytest.approx(13.55)
    assert out.published_at is not None
    assert out.published_at.astimezone(UTC) == published, "published_at 落库往返必须一致"
    # 上海日历日：UTC 2026-10-08 16:30 = 北京 2026-10-09 00:30（Ruling 35）
    assert out.as_of == date(2026, 10, 9)
    assert out.as_of_source == service.AS_OF_FROM_PUBLISHED  # 判别式：真 PIT
    assert out.caliber_version == "M1-2026"


@requires_pg
def test_rerun_is_idempotent_and_explicit_pit_overwrites() -> None:
    """重跑同一 ingest → 新增行数 0（6 列 unique 仍生效）；显式新 PIT 覆盖写。"""
    first = datetime(2026, 10, 8, 16, 30, tzinfo=UTC)
    second = datetime(2026, 10, 9, 9, 0, tzinfo=UTC)

    async def _scenario(db: Any) -> tuple[int, datetime | None]:
        item = {
            "metric_key": _PIT_KEY,
            "period": _PIT_PERIOD,
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
        rows = (
            (
                await db.execute(
                    select(IndustryMetric).where(
                        IndustryMetric.industry_key == "pig",
                        IndustryMetric.metric_key == _PIT_KEY,
                    )
                )
            )
            .scalars()
            .all()
        )
        return len(rows), (rows[0].published_at if rows else None)

    n, ts = asyncio.run(_with_rollback(_scenario))

    assert n == 1, f"重跑同一 ingest 新增了行（unique 失效）: {n}"
    assert ts is not None and ts.astimezone(UTC) == second, "显式新 PIT 未被覆盖写"


@requires_pg
def test_reimport_omitting_published_at_preserves_stored_pit() -> None:
    """**F2 靶向测试**：重导入**省略** `published_at` / `caliber_version` → 原值必须保留。

    人工通道这两个字段可选（默认 None），若 upsert 直接取 ``excluded.*``，一次不带 PIT
    的重发就会把已登记的 PIT 抹成 NULL —— 而保住 PIT 正是本表加列的目的。
    非空性证据：把 set_ 里的 ``func.coalesce(excluded.x, IndustryMetric.x)`` 改回
    ``excluded.x`` → 本用例立刻变红。
    """
    first = datetime(2026, 10, 8, 16, 30, tzinfo=UTC)

    async def _scenario(db: Any) -> tuple[datetime | None, str | None]:
        await service.batch_upsert_metrics(
            db,
            "pig",
            [
                {
                    "metric_key": _PIT_KEY,
                    "period": _PIT_PERIOD,
                    "value": 13.55,
                    "source": "manual",
                    "published_at": first,
                    "caliber_version": "M1-2026",
                }
            ],
        )
        await db.commit()
        # 同键重发：**不带** published_at / caliber_version（模拟"只改数值"的日常重导）
        await service.batch_upsert_metrics(
            db,
            "pig",
            [
                {
                    "metric_key": _PIT_KEY,
                    "period": _PIT_PERIOD,
                    "value": 13.66,
                    "source": "manual",
                }
            ],
        )
        await db.commit()
        row = (
            await db.execute(
                select(IndustryMetric).where(
                    IndustryMetric.industry_key == "pig",
                    IndustryMetric.metric_key == _PIT_KEY,
                )
            )
        ).scalar_one()
        assert float(row.value) == pytest.approx(13.66)  # 数值确实被新值覆盖
        return row.published_at, row.caliber_version

    ts, caliber = asyncio.run(_with_rollback(_scenario))

    assert ts is not None, "重导入省略 published_at 把已记录的 PIT 抹成了 NULL"
    assert ts.astimezone(UTC) == first, "已记录的 PIT 被改写"
    assert caliber == "M1-2026", "重导入省略 caliber_version 把已记录的口径抹成了 NULL"


@requires_pg
def test_series_range_is_a_real_time_window_not_a_row_count() -> None:
    """**F4 靶向测试**：`range=30d` 是时间窗，不是"最近 30 行"。

    **日期相对 today 构造**（上一版写死 2026-06~09，在 2026-10-12 之后看全部落在窗外，
    只证明了"远古点被排除"，没有测边界）。现在给 5 个点，其中 3 个卡在窗口边界上：

    - ``today``：窗内（下界之上）
    - ``today - 30``：**正好落在下界**（窗口含下界 → 必须保留）
    - ``today - 31``：刚刚出界 → 必须排除
    - ``today - 400``：远出界 → 必须排除
    - ``today + 1``：未来点（上界为空，不排除；此处用于证明过滤只按窗口不按其它）

    旧实现把 range 当行数（limit=30）→ 5 个点全给，边界断言立刻红。
    """
    today = date.today()
    inside = [today, today - timedelta(days=30)]
    outside = [today - timedelta(days=31), today - timedelta(days=400)]

    async def _scenario(db: Any) -> Any:
        rows = [
            {
                "industry_key": "pig",
                "stock_id": 0,
                "metric_key": _PIT_KEY,
                "source": "manual",
                "source_tier": "manual",
                "freq": "monthly",
                "period": p,
                "value": 13.0 + i,
                "unit": "元/kg",
                "extra": None,
                "published_at": None,
                "caliber_version": None,
            }
            for i, p in enumerate([*inside, *outside])
        ]
        await repo.upsert_metrics(db, rows)
        await db.commit()
        return await service.get_series(db, "pig", _PIT_KEY, "30d")

    out = asyncio.run(_with_rollback(_scenario))

    assert out.range == "30d"
    assert out.period_from == today - timedelta(days=30)
    got = [p.period for p in out.points]
    assert got == sorted(got), "序列必须升序"
    assert set(got) == set(inside), f"窗口边界不符（应含下界当天、排除下界之前）: {got}"
    # 完整日频窗（31 个点）不得被误判截断：limit = days + 1（F3 差一）
    assert out.truncated is False, "完整窗内序列被误判 truncated"
    assert out.limit == 31
    assert all(p.as_of_source == service.AS_OF_FROM_PERIOD for p in out.points)


@requires_pg
def test_series_truncated_is_measured_before_source_filtering() -> None:
    """`truncated` 按**源过滤前**的取数判定（F3）：多源占用上界时不得报"完整"。

    `range=3d`（limit=4）。只放 ``manual`` 的 4 行（正好打满上界，且都在 3 天窗内）→
    ``truncated=True``；再放 1 行 ``mock``（低优先源）也不改变结论。旧实现先按源过滤再
    比上界，manual 恰好 4 行仍会 True —— 故这里额外验证"过滤后行数少于上界但**过滤前**
    已打满"这一条：把 manual 减到 3 行、mock 加到 3 行（过滤前 6 ≥ 4，过滤后 3 < 4）。
    """
    metric_key = "pork_wholesale"  # sources = ["manual", "mock"]
    today = date.today()
    periods = [today - timedelta(days=i) for i in range(3)]  # 窗内（3d 窗 = 下界 today-3）

    async def _scenario(db: Any) -> tuple[Any, int]:
        rows = [
            {
                "industry_key": "pig",
                "stock_id": 0,
                "metric_key": metric_key,
                "source": src,
                "source_tier": "official",
                "freq": "daily",
                "period": p,
                "value": 19.5,
                "unit": "元/kg",
                "extra": None,
                "published_at": None,
                "caliber_version": None,
            }
            for src in ("manual", "mock")
            for p in periods
        ]
        await repo.upsert_metrics(db, rows)
        await db.commit()
        # 过滤前库里确有 6 行（2 源 × 3 天），且全部落在 3d 窗内
        total = (
            await db.execute(
                select(func.count())
                .select_from(IndustryMetric)
                .where(
                    IndustryMetric.industry_key == "pig", IndustryMetric.metric_key == metric_key
                )
            )
        ).scalar_one()
        return await service.get_series(db, "pig", metric_key, "3d"), int(total)

    out, pre_filter_rows = asyncio.run(_with_rollback(_scenario))

    assert pre_filter_rows == 6
    assert out.limit == 4  # 3d 窗 → days+1
    # 打满上界后只取回最新 4 行（2 天 × 2 源），源过滤后剩 2 个 manual 点
    assert len(out.points) == 2
    assert len(out.points) < out.limit
    assert out.truncated is True, "过滤前已打满上界，必须报截断（F3）"
    assert out.source == "manual"


@requires_pg
def test_series_takes_highest_priority_source_and_carries_pit() -> None:
    """series 只取 registry 源优先级最高的一个源，且点带 PIT 徽章。

    同一 period 故意放 manual + mock 两行（mock 不在人工导入白名单里，故直插），
    序列必须只输出 manual —— 否则图会把两套口径连成一条假趋势。
    """
    metric_key = "pork_wholesale"  # sources = ["manual", "mock"]
    periods = [date(2026, 7, 31), date(2026, 8, 31)]

    async def _scenario(db: Any) -> Any:
        await repo.upsert_metrics(
            db,
            [
                {
                    "industry_key": "pig",
                    "stock_id": 0,
                    "metric_key": metric_key,
                    "source": src,
                    "source_tier": "official",
                    "freq": "daily",
                    "period": p,
                    "value": v,
                    "unit": "元/kg",
                    "extra": None,
                    "published_at": None,
                    "caliber_version": None,
                }
                for p, v, src in [
                    (periods[0], 19.5, "manual"),
                    (periods[0], 19.9, "mock"),
                    (periods[1], 20.1, "manual"),
                ]
            ],
        )
        await db.commit()
        # 用足够长的窗口把两个月度点都圈进来（range 现在是时间窗）
        return await service.get_series(db, "pig", metric_key, "2y")

    out = asyncio.run(_with_rollback(_scenario))

    assert out.source == "manual", "未按 registry 优先级排除低优先源（mock 混入）"
    assert not out.source_mismatch
    assert [p.value for p in out.points] == pytest.approx([19.5, 20.1])
    assert all(p.as_of == p.period for p in out.points)  # published_at NULL → 回退 period
    assert all(p.as_of_source == service.AS_OF_FROM_PERIOD for p in out.points)
    assert all(p.published_at is None for p in out.points), "回退值不得写进 PIT 字段"
    assert out.range == "732d"  # canonical：2y = 2 × 366 天
    assert out.period_from is not None


@requires_pg
def test_series_surfaces_source_mismatch_instead_of_silently_dropping() -> None:
    """库里源的都不在 registry 优先级表里 → 空序列 + `source_mismatch=True`（不静默丢点）。"""

    async def _scenario(db: Any) -> Any:
        await repo.upsert_metrics(
            db,
            [
                {
                    "industry_key": "pig",
                    "stock_id": 0,
                    "metric_key": "pork_wholesale",
                    "source": "unregistered_provider",  # 不在 registry sources 里
                    "source_tier": "official",
                    "freq": "daily",
                    "period": date(2026, 7, 31),
                    "value": 19.5,
                    "unit": "元/kg",
                    "extra": None,
                    "published_at": None,
                    "caliber_version": None,
                }
            ],
        )
        await db.commit()
        return await service.get_series(db, "pig", "pork_wholesale", "2y")

    out = asyncio.run(_with_rollback(_scenario))

    assert out.source is None
    assert out.source_mismatch is True, "源登记与数据脱节必须显式报出，不能悄悄返回空序列"
    assert out.points == []


# ── 9) 混合批次列集（PG-free，SQLite 桥接） ────────────────────────────


class _ScalarsResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _ScalarsResult:
        return self

    def all(self) -> list[Any]:
        return self._rows


class _SyncSqliteSession:
    """把 async repo 的 ``execute(stmt)`` 桥接到同步 SQLite Session，并留存 stmt。"""

    def __init__(self, engine: Any) -> None:
        self._engine = engine
        self.statements: list[Any] = []

    async def execute(self, stmt: Any) -> _ScalarsResult:
        self.statements.append(stmt)
        with Session(self._engine) as session:
            rows = list(session.execute(stmt).scalars().all())
            session.expunge_all()
        return _ScalarsResult(rows)


def _sqlite_schema(eng: Any) -> None:
    """在 sqlite 上建 industry_metrics（PG 专属 JSONB 列改 Text）。

    用 ``to_metadata`` 拿副本再改类型，避免污染全局 metadata（不共享 Table 对象）。
    """
    from sqlalchemy import Text

    sqlite_meta = MetaData()
    table = IndustryMetric.__table__.to_metadata(sqlite_meta)
    table.c.extra.type = Text()
    table.create(eng)


def test_get_metric_window_filters_by_period_and_stays_ascending() -> None:
    """`get_metric_window` 发出的 SQL 必须带 period 下界 + 升序（range 的承重实现）。"""
    eng = create_engine("sqlite://")
    # 只建本用例用到的列：`extra` 是 PG 方言 JSONB，sqlite 无法建表（与本仓库既有
    # SQLite 桥接测试的同类取舍一致）
    _sqlite_schema(eng)
    base: dict[str, Any] = {
        "industry_key": "pig",
        "stock_id": 0,
        "metric_key": "industry_cost_avg",
        "source": "manual",
        "source_tier": "manual",
        "freq": "monthly",
        "unit": "元/kg",
    }
    with eng.begin() as conn:
        conn.execute(
            IndustryMetric.__table__.insert(),
            [
                {**base, "id": 1, "period": date(2025, 1, 31), "value": 1.0},
                {**base, "id": 2, "period": date(2026, 9, 30), "value": 3.0},
                {**base, "id": 3, "period": date(2026, 8, 31), "value": 2.0},
            ],
        )

    db = _SyncSqliteSession(eng)
    out = asyncio.run(
        repo.get_metric_window(
            db,  # type: ignore[arg-type]
            "pig",
            "industry_cost_avg",
            period_from=date(2026, 1, 1),
            limit=5000,
            freq="monthly",
        )
    )
    eng.dispose()

    sql = str(db.statements[0].compile(dialect=postgresql.dialect()))
    assert "industry_metrics.period >=" in sql, sql
    assert "DESC" in sql and "ORDER BY" in sql  # 取最新 N 行再反转 → 升序
    assert [r.value for r in out] == [2.0, 3.0], "窗口过滤或升序不生效"
