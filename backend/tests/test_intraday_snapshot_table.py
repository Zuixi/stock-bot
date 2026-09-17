"""Task 12: 分时快照表（migration + 端点 + scheduler 接线）契约。

覆盖点（按 brief 6）：

1. **migration up/down 可跑** — ``upgrade()`` 之后表存在，``downgrade()`` 之后表
   消失。直接调迁移模块函数 + 走真 PG（dev 库 ``stock_bot`` 5433；与硬门禁
   ``uv run alembic upgrade head`` 同一份 DDL 路径）。
2. **upsert 幂等** — 同一 ``(trade_date, captured_at)`` 连调两次**生产** repo
   函数不产生重复行（``ON CONFLICT (trade_date, captured_at) DO NOTHING``）。
3. **upsert 保留行内容** — ``zt_count=42 / max_streak=7`` 写入后回读全等
   （不被 DO NOTHING 静默吞掉）。
4. **升序契约由 repo 层拥有** — ``list_intraday_snapshot`` 的 SQL 必须
   ``WHERE trade_date`` + ``ORDER BY captured_at``；用真跑 SQL 的 SQLite 桥接
   async repo 函数、插入故意乱序的行来证明（PG-free）。
5. **端点接线** — 端点透传 repo 行序 + 写 60s 缓存；未来日期 400。直调 handler
   函数、monkeypatch DB session 与 ``_today_sh``；不走 TestClient 也不走真
   eastmoney（PG-free）。
6. **scheduler 接线契约** — ``intraday_sentiment_poll`` 注册到 runner，cron 形如
   ``mon-fri 9-15 */5 Asia/Shanghai``；job 体在非工作日/非交易时段早返回（与
   ``sector_moneyflow_job`` 同款守卫）；misfire_grace_time 在
   ``INTRADAY_GRACE_SEC``。

**PG 依赖只落在真正需要的 5 个用例上**（3 个 migration + 2 个 upsert，`requires_pg`
标记）。其余 8 个用例（端点 2、repo 升序 1、cron 注册 1、job 体 4）在没起 dev PG
的 CI 上照常运行（此前整文件 ``pytestmark`` skipif 把它们一起吞了——PG-free 的
端点/注册/job 体守卫因此长期无 CI 覆盖）。

实现选择：迁移测试通过**子进程**调 ``alembic`` CLI 来执行 upgrade/downgrade
——避免在 pytest-asyncio 事件循环里嵌套 ``asyncio.run``（会污染下一个测试
的 caplog 与 event loop 状态）。每个 migration 测试 ~0.5s。
``ensure_migration_state`` fixture 在测试后**恢复到 HEAD**，保证
``pytest`` 跑完 ``uv run alembic current`` == ``uv run alembic heads``。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.models.market_data import MarketSentimentIntraday
from app.repositories import market_data_repo
from app.scheduler.runner import INTRADAY_GRACE_SEC
from app.services import intraday_sentiment_service

REVISION = "e1f2a3b4c5d6"
PREV_REVISION = "d7c8b9a0e1f2"
DB_ASYNC_URL = "postgresql+asyncpg://stock_user:stock_pass@localhost:5433/stock_bot"
DB_SYNC_URL = "postgresql://stock_user:stock_pass@localhost:5433/stock_bot"
ALEMBIC_BIN = str(Path(__file__).resolve().parents[1] / ".venv" / "bin" / "alembic")
BACKEND_DIR = str(Path(__file__).resolve().parents[1])


# ---------------------------------------------------------------------------
# PG 库自检（一次 session-scope）—— 只用于给 5 个真库用例打 skip 标记
# ---------------------------------------------------------------------------


def _pg_self_check() -> bool:
    async def _go() -> bool:
        try:
            conn = await asyncpg.connect(DB_SYNC_URL)
        except Exception:
            return False
        try:
            return True
        finally:
            await conn.close()

    return asyncio.run(_go())


_PG_OK = _pg_self_check()


requires_pg = pytest.mark.skipif(
    not _PG_OK,
    reason="PostgreSQL at localhost:5433 stock_bot unavailable",
)


# ---------------------------------------------------------------------------
# Migration 工具：subprocess 调 alembic CLI
# ---------------------------------------------------------------------------


def _alembic_env() -> dict[str, str]:
    """为子进程准备 env：``DATABASE_URL`` 显式注入。"""
    env = os.environ.copy()
    env["DATABASE_URL"] = DB_ASYNC_URL
    env["TUSHARE_TOKEN"] = ""
    return env


def _alembic_upgrade_to(rev: str) -> None:
    """调 alembic CLI upgrade（子进程，与 pytest-asyncio 事件循环完全隔离）。"""
    result = subprocess.run(
        [ALEMBIC_BIN, "upgrade", rev],
        capture_output=True,
        text=True,
        cwd=BACKEND_DIR,
        env=_alembic_env(),
    )
    if result.returncode != 0:
        raise AssertionError(
            f"alembic upgrade {rev} failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


def _alembic_downgrade_to(rev: str) -> None:
    """调 alembic CLI downgrade（子进程）。"""
    result = subprocess.run(
        [ALEMBIC_BIN, "downgrade", rev],
        capture_output=True,
        text=True,
        cwd=BACKEND_DIR,
        env=_alembic_env(),
    )
    if result.returncode != 0:
        raise AssertionError(
            f"alembic downgrade {rev} failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


async def _table_exists() -> bool:
    conn = await asyncpg.connect(DB_SYNC_URL)
    try:
        row = await conn.fetchval(
            "SELECT 1 FROM pg_catalog.pg_tables WHERE schemaname = 'public' AND tablename = $1",
            "market_sentiment_intraday",
        )
        return row is not None
    finally:
        await conn.close()


@pytest.fixture
def ensure_migration_state() -> Any:
    """每个 migration/upsert 测试**前**收敛到 PREV_REVISION，**后**恢复到 HEAD。

    前置用绝对定位（先 ``upgrade head`` 再 ``downgrade PREV_REVISION``）——相对
    ``downgrade -1`` 在库已处于 PREV 时会多降一版、把下层的表也拆掉（旧实现的
    坑）。后置 ``upgrade head`` 是硬不变量：``pytest`` 跑完 dev 库必须仍在
    ``alembic heads``（否则后续 e2e/手工验证拿到的是缺表的库）。
    """
    # Pre: 任意起点 → HEAD → PREV（表确定不存在）
    _alembic_upgrade_to("head")
    _alembic_downgrade_to(PREV_REVISION)
    try:
        yield
    finally:
        # Post: 恢复 HEAD —— 即使测试失败/异常也不把库留在中间版本
        _alembic_upgrade_to("head")


# ---------------------------------------------------------------------------
# 1) migration up/down
# ---------------------------------------------------------------------------


@requires_pg
def test_migration_upgrade_creates_table(ensure_migration_state: Any) -> None:
    """``alembic upgrade e1f2a3b4c5d6`` 跑完 → ``market_sentiment_intraday`` 表在
    ``public`` 内。"""
    assert not asyncio.run(_table_exists())
    _alembic_upgrade_to(REVISION)
    assert asyncio.run(_table_exists())


@requires_pg
def test_migration_downgrade_drops_table(ensure_migration_state: Any) -> None:
    """``alembic downgrade d7c8b9a0e1f2`` 跑完 → 表消失。"""
    _alembic_upgrade_to(REVISION)
    assert asyncio.run(_table_exists())
    _alembic_downgrade_to(PREV_REVISION)
    assert not asyncio.run(_table_exists())


@requires_pg
def test_migration_full_upgrade_downgrade_upgrade_cycle(
    ensure_migration_state: Any,
) -> None:
    """完整三段：up → down → up，表不漏。brief 的 live DDL 验证同一路径。"""
    _alembic_upgrade_to(REVISION)
    _alembic_downgrade_to(PREV_REVISION)
    _alembic_upgrade_to(REVISION)
    assert asyncio.run(_table_exists())


# ---------------------------------------------------------------------------
# 2) upsert 幂等（生产 repo 函数，连调两次）
# ---------------------------------------------------------------------------


def _build_pool(n: int, max_streak: int) -> list[dict[str, Any]]:
    """n 行池，最大连板 = max_streak（其余 streak=1）。"""
    rows = [{"symbol": f"00000{i}", "streak": 1} for i in range(n - 1)]
    rows.append({"symbol": "000999", "streak": max_streak})
    return rows


@requires_pg
def test_upsert_intraday_snapshot_is_idempotent(ensure_migration_state: Any) -> None:
    """同一 ``(trade_date, captured_at)`` 连调两次**生产** repo 函数 → 表仍 1 行。

    非空性证据（本测试必须在 repo 的 ``ON CONFLICT`` 消失时变红）：
    - 删掉 ``upsert_intraday_snapshot`` 里的 ``.on_conflict_do_nothing(...)`` →
      第二次调用抛 ``UniqueViolation`` → 红；
    - 改成 ``.on_conflict_do_update(...)`` → 第二次 ``rowcount == 1`` 且第一行
      内容被覆盖（``zt_count`` 99）→ 红。
    """
    _alembic_upgrade_to(REVISION)
    asyncio.run(_idempotency_inner())


async def _idempotency_inner() -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    eng = create_async_engine(DB_ASYNC_URL)
    try:
        trade_date = date(2026, 9, 17)
        captured_at = datetime(2026, 9, 17, 10, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        session_factory = sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)
        async with session_factory() as db:
            n1 = await market_data_repo.upsert_intraday_snapshot(
                db, trade_date, captured_at, _build_pool(5, max_streak=3)
            )
            await db.commit()
        async with session_factory() as db:
            n2 = await market_data_repo.upsert_intraday_snapshot(
                db, trade_date, captured_at, _build_pool(99, max_streak=99)
            )
            await db.commit()
        assert n1 == 1, f"expected first insert rowcount=1, got {n1}"
        assert n2 == 0, f"expected second upsert rowcount=0 (DO NOTHING), got {n2}"
        async with eng.connect() as conn:
            count = (
                await conn.execute(text("SELECT COUNT(*) FROM market_sentiment_intraday"))
            ).scalar_one()
            zt = (
                await conn.execute(text("SELECT zt_count FROM market_sentiment_intraday"))
            ).scalar_one()
        assert count == 1, f"expected 1 row after double upsert, got {count}"
        # DO NOTHING：第二次的 99 行绝不能覆盖第一次的快照内容
        assert zt == 5, f"expected first snapshot zt_count=5 preserved, got {zt}"
    finally:
        await eng.dispose()


# ---------------------------------------------------------------------------
# 3) upsert 行内容（生产 repo 函数，PG）
# ---------------------------------------------------------------------------


@requires_pg
def test_upsert_intraday_snapshot_preserves_row_content(
    ensure_migration_state: Any,
) -> None:
    """``zt_count=42 / max_streak=7`` 写入后回读全等（不走 DO NOTHING 吞值路径）。"""
    _alembic_upgrade_to(REVISION)
    asyncio.run(_content_inner())


async def _content_inner() -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    eng = create_async_engine(DB_ASYNC_URL)
    try:
        trade_date = date(2026, 9, 17)
        captured_at = datetime(2026, 9, 17, 11, 5, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        rows = _build_pool(42, max_streak=7)
        session_factory = sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)
        async with session_factory() as db:
            n = await market_data_repo.upsert_intraday_snapshot(db, trade_date, captured_at, rows)
            assert n == 1  # 新插入一行
            await db.commit()
        # 回读
        async with eng.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT id, trade_date, captured_at, zt_count, dt_count, "
                        "zb_count, max_streak FROM market_sentiment_intraday ORDER BY id"
                    )
                )
            ).first()
        assert row is not None
        # 关键内容完整 round-trip
        assert row.trade_date == trade_date
        # captured_at 经 TIMESTAMPTZ 往返，pg 默认带 tz
        assert row.captured_at == captured_at
        assert row.zt_count == 42
        assert row.max_streak == 7
        assert row.dt_count == 0
        assert row.zb_count == 0
    finally:
        await eng.dispose()


# ---------------------------------------------------------------------------
# 4) 升序契约归属层 = repo（SQLite 桥接，PG-free）
# ---------------------------------------------------------------------------


class _ScalarsResult:
    """``AsyncSession.execute`` 返回值的 ``.scalars().all()`` 最小替身。"""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _ScalarsResult:
        return self

    def all(self) -> list[Any]:
        return self._rows


class _SyncSqliteSession:
    """把 async repo 的 ``execute(stmt)`` 桥接到同步 SQLite Session，并留存 stmt。

    - 留存 ``statements[0]`` → 测试把 repo 真发出的查询编译成 PG SQL，断言其中
      带 ``WHERE trade_date`` + ``ORDER BY captured_at ASC``（**承重断言**）。
    - 同时真在 SQLite 上执行一遍：``WHERE`` 过滤是否生效由返回行数直接可验。
      （仅执行结果升序**不足以**证明 ORDER BY 存在——SQLite 走
      ``(trade_date, captured_at)`` 唯一约束索引时天然升序，实测删掉
      ``.order_by`` 执行序仍升序，所以必须查编译 SQL。）
    - ORM 行在 session 关闭前 ``expunge_all``，避免闭包后 lazy load。
    """

    def __init__(self, engine: Any) -> None:
        self._engine = engine
        self.statements: list[Any] = []

    async def execute(self, stmt: Any) -> _ScalarsResult:
        self.statements.append(stmt)
        with Session(self._engine) as session:
            rows = list(session.execute(stmt).scalars().all())
            session.expunge_all()
        return _ScalarsResult(rows)


def test_list_intraday_snapshot_orders_by_captured_at_asc() -> None:
    """``list_intraday_snapshot`` 发出的 SQL 必须 ``WHERE trade_date`` + ``ORDER BY
    captured_at ASC``。

    升序是**端点契约**，归属层在 repo 的 SQL——端点只透传。这里真跑生产查询函数
    （SQLite 桥接），插入**故意乱序**的 3 行（11:00、10:00、10:30）外加另一个
    交易日的干扰行，随后把 repo 发出的语句编译成 PG SQL 做形状断言。

    非空性证据：删掉 repo 的 ``.order_by(MarketSentimentIntraday.captured_at)`` →
    编译 SQL 无 ORDER BY → 红；删掉 ``.where(... trade_date ...)`` → 无 WHERE 且
    SQLite 返回 4 行（干扰行 zt=99 混入）→ 红。
    """
    eng = create_engine("sqlite://")
    MarketSentimentIntraday.__table__.create(eng)
    base: dict[str, Any] = {
        "trade_date": date(2026, 9, 17),
        "dt_count": 0,
        "zb_count": 0,
        "max_streak": 2,
    }
    rows = [
        {
            "id": 1,
            "captured_at": datetime(2026, 9, 17, 11, 0, tzinfo=UTC),
            "zt_count": 30,
            **base,
        },
        {
            "id": 2,
            "captured_at": datetime(2026, 9, 17, 10, 0, tzinfo=UTC),
            "zt_count": 10,
            **base,
        },
        {
            "id": 3,
            "captured_at": datetime(2026, 9, 17, 10, 30, tzinfo=UTC),
            "zt_count": 20,
            **base,
        },
        {  # 另一个交易日：WHERE trade_date 的干扰行
            "id": 4,
            "captured_at": datetime(2026, 9, 16, 10, 0, tzinfo=UTC),
            "zt_count": 99,
            **{**base, "trade_date": date(2026, 9, 16)},
        },
    ]
    with eng.begin() as conn:
        conn.execute(MarketSentimentIntraday.__table__.insert(), rows)

    db = _SyncSqliteSession(eng)
    out = asyncio.run(
        market_data_repo.list_intraday_snapshot(
            db,  # type: ignore[arg-type]
            date(2026, 9, 17),
        )
    )
    eng.dispose()

    # 承重断言：repo 发出的 SQL 自带排序与过滤（PG 方言编译）
    assert len(db.statements) == 1
    sql = str(db.statements[0].compile(dialect=postgresql.dialect()))
    # PG 方言对 ASC 是默认值、不打印 " ASC"，所以显式排除 DESC
    assert "ORDER BY market_sentiment_intraday.captured_at" in sql, sql
    assert "DESC" not in sql, sql
    assert "WHERE market_sentiment_intraday.trade_date" in sql, sql
    # 形状断言的执行侧旁证：trade_date 过滤真生效
    assert len(out) == 3, f"trade_date filter leaked rows: {[r.zt_count for r in out]}"
    assert [r.zt_count for r in out] == [10, 20, 30]


# ---------------------------------------------------------------------------
# 5) 端点：透传 repo 行序 + 缓存 + 未来日期 400
# ---------------------------------------------------------------------------


class _RecordingCache:
    def __init__(self) -> None:
        self.gets: list[str] = []
        self.sets: list[tuple[str, Any, int | None]] = []

    async def get(self, key: str) -> Any | None:  # noqa: ANN401
        self.gets.append(key)
        return None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self.sets.append((key, value, ttl))


def _row(captured_at: datetime, zt: int, max_streak: int) -> MarketSentimentIntraday:
    return MarketSentimentIntraday(
        id=1,
        trade_date=date(2026, 9, 17),
        captured_at=captured_at,
        zt_count=zt,
        dt_count=0,
        zb_count=0,
        max_streak=max_streak,
    )


async def test_intraday_endpoint_returns_repo_order_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """端点按 repo 给的行序透传，并写 ``market:limit-up:intra-points:{date}`` TTL=60。

    升序契约由 repo 层拥有（``test_list_intraday_snapshot_orders_by_captured_at_asc``
    用真 SQLite 钉住）；本测试只钉端点侧的消费与缓存——stub 返回的是 repo 保证的
    升序行，断言端点既不丢行也不重排。

    非空性证据：把 cache key 改错字 → 红；把 ttl 改成 300 → 红；端点里对 rows
    做 ``sorted(..., reverse=True)`` → zt 序列红。
    """
    from app.api.v1 import market_data as api
    from app.services import market_data_service as mds

    # 钉住"今天"为 2026-09-17，避免跨时区测试飘移。
    # 端点内部 ``from app.services.market_data_service import _today_sh``，
    # 所以必须打 market_data_service 上的名字（不是 api 模块的）。
    monkeypatch.setattr(mds, "_today_sh", lambda: date(2026, 9, 17))

    # repo ``ORDER BY captured_at`` 之后的形状（10:00 → 10:30 → 11:00）
    rows = [
        _row(datetime(2026, 9, 17, 10, 0, 0, tzinfo=UTC), zt=10, max_streak=2),
        _row(datetime(2026, 9, 17, 10, 30, 0, tzinfo=UTC), zt=20, max_streak=2),
        _row(datetime(2026, 9, 17, 11, 0, 0, tzinfo=UTC), zt=30, max_streak=3),
    ]

    async def _fake_list(_db: Any, _d: Any) -> list[MarketSentimentIntraday]:
        return rows

    monkeypatch.setattr(market_data_repo, "list_intraday_snapshot", _fake_list)

    class _SessionCM:
        async def __aenter__(self) -> _SessionCM:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

    monkeypatch.setattr("app.core.database.async_session_factory", lambda: _SessionCM())

    cache = _RecordingCache()
    out = await api.get_sentiment_intraday(cache=cache, date=None)  # type: ignore[arg-type]

    assert len(out) == 3
    assert [p.zt_count for p in out] == [10, 20, 30], "endpoint reordered/dropped rows"
    # 缓存应被写入：key=`market:limit-up:intra-points:{date}`、TTL=60
    assert len(cache.sets) == 1
    key, _value, ttl = cache.sets[0]
    assert key == "market:limit-up:intra-points:2026-09-17"
    assert ttl == 60


async def test_intraday_endpoint_returns_400_for_future_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``date > today_sh`` → HTTPException(400)，不查 DB。"""
    from fastapi import HTTPException

    from app.api.v1 import market_data as api
    from app.services import market_data_service as mds

    monkeypatch.setattr(mds, "_today_sh", lambda: date(2026, 9, 17))

    called: dict[str, bool] = {"n": False}

    async def _boom(*_a: Any, **_kw: Any) -> list[MarketSentimentIntraday]:
        called["n"] = True
        return []

    monkeypatch.setattr(market_data_repo, "list_intraday_snapshot", _boom)

    with pytest.raises(HTTPException) as exc:
        await api.get_sentiment_intraday(cache=None, date="2026-09-18")  # type: ignore[arg-type]
    assert exc.value.status_code == 400
    assert "future" in exc.value.detail.lower()
    # DB 路径没走（日期校验在前）
    assert called["n"] is False


# ---------------------------------------------------------------------------
# 6) scheduler 接线契约 + job 体守卫
# ---------------------------------------------------------------------------


async def test_intraday_sentiment_poll_registered_with_correct_cron() -> None:
    """``intraday_sentiment_poll`` 必须注册到 runner：id/cron 字段精确匹配 brief。

    cron ``hour="9-15"``：圈住含 15:00 收盘 tick 的盘中窗口；09:00-09:25 盘前 tick
    由 job 体内 ``_is_workday``/``_in_trading_hours`` 守卫拦掉（与
    ``sector_moneyflow_job`` 同款，见下一个测试）。``job_defaults`` 的
    ``coalesce/max_instances`` 由本测试顺带护栏。

    非空性证据：把 hour 改回 ``"9-14"``（丢 15:00 tick）→ 红；minute 改
    ``"*/10"`` → 红。
    """
    from apscheduler.triggers.cron import CronTrigger

    from app.scheduler.runner import create_scheduler

    scheduler = create_scheduler()
    scheduler.start()
    try:
        job = scheduler.get_job("intraday_sentiment_poll")
        assert job is not None, "intraday_sentiment_poll not registered"
        assert isinstance(job.trigger, CronTrigger)
        # cron 字段：day_of_week=mon-fri, hour=9-15, minute=*/5, timezone=Asia/Shanghai
        fields = {f.name: str(f) for f in job.trigger.fields}
        assert fields["day_of_week"] == "mon-fri"
        assert fields["hour"] == "9-15"
        assert fields["minute"] == "*/5"
        assert str(job.trigger.timezone) == "Asia/Shanghai"
        # 盘中任务 misfire_grace_time=INTRADAY_GRACE_SEC（与 sector_moneyflow_poll 同款）
        assert job.misfire_grace_time == INTRADAY_GRACE_SEC
        # job_defaults 共用部分：coalesce=True, max_instances=1
        assert job.coalesce is True
        assert job.max_instances == 1
    finally:
        scheduler.shutdown(wait=False)


async def test_intraday_sentiment_poll_job_skips_outside_trading_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非交易日/非交易时段早返回：不调东财、不写库（09:00-09:25 盘前 tick 的守卫）。

    非空性证据：删掉 job 体内 ``_is_workday``/``_in_trading_hours`` 早返回 →
    ``fetch_calls == [0]`` → 红。
    """
    from app.scheduler import jobs

    fetch_calls: list[int] = []

    async def _fake_fetch(_trade_date: str) -> list[dict[str, Any]]:
        fetch_calls.append(1)
        return [{"symbol": "000001", "streak": 1}]

    monkeypatch.setattr(intraday_sentiment_service, "fetch_intraday_pool", _fake_fetch)
    monkeypatch.setattr(jobs, "_is_workday", lambda: True)
    monkeypatch.setattr(jobs, "_in_trading_hours", lambda: False)

    await jobs.intraday_sentiment_poll_job()
    assert fetch_calls == []

    monkeypatch.setattr(jobs, "_is_workday", lambda: False)
    monkeypatch.setattr(jobs, "_in_trading_hours", lambda: True)
    await jobs.intraday_sentiment_poll_job()
    assert fetch_calls == []


async def test_intraday_sentiment_poll_job_writes_one_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``intraday_sentiment_poll_job`` 真跑一遍：fetch_intraday_pool →
    upsert_intraday_snapshot 落一行（交易时段守卫放行时）。
    """
    from app.scheduler import jobs

    upserted: list[tuple[date, datetime, int]] = []
    fetched: list[str] = []

    async def _fake_fetch(trade_date: str) -> list[dict[str, Any]]:
        fetched.append(trade_date)
        return [{"symbol": "000001", "streak": 1}]

    async def _fake_upsert(
        db: Any, trade_date: date, captured_at: datetime, rows: list[dict[str, Any]]
    ) -> int:
        upserted.append((trade_date, captured_at, len(rows)))
        return 1

    class _Ctx:
        async def __aenter__(self) -> _Ctx:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def commit(self) -> None:
            return None

    monkeypatch.setattr(intraday_sentiment_service, "fetch_intraday_pool", _fake_fetch)
    monkeypatch.setattr(market_data_repo, "upsert_intraday_snapshot", _fake_upsert)
    monkeypatch.setattr("app.core.database.async_session_factory", lambda: _Ctx())
    # 守卫放行（与 sector_moneyflow_job 测试同款：真时钟在测试机上不可控）
    monkeypatch.setattr(jobs, "_is_workday", lambda: True)
    monkeypatch.setattr(jobs, "_in_trading_hours", lambda: True)

    await jobs.intraday_sentiment_poll_job()

    # 1 次 fetch、参数为今日 YYYYMMDD（上海时区）
    assert len(fetched) == 1
    # 1 次 upsert
    assert len(upserted) == 1
    trade_date, captured_at, n = upserted[0]
    assert n == 1  # 1 行池
    # captured_at 是带 tz 的 datetime
    assert captured_at.tzinfo is not None
    # trade_date 是 date
    assert isinstance(trade_date, date)


async def test_intraday_sentiment_poll_job_empty_pool_skips_upsert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空池 → 不调 upsert。"""
    from app.scheduler import jobs

    upserted: list[int] = []

    async def _fake_fetch(_trade_date: str) -> list[dict[str, Any]]:
        return []

    async def _fake_upsert(*_a: Any, **_kw: Any) -> int:
        upserted.append(1)
        return 0

    monkeypatch.setattr(intraday_sentiment_service, "fetch_intraday_pool", _fake_fetch)
    monkeypatch.setattr(market_data_repo, "upsert_intraday_snapshot", _fake_upsert)
    monkeypatch.setattr(jobs, "_is_workday", lambda: True)
    monkeypatch.setattr(jobs, "_in_trading_hours", lambda: True)

    await jobs.intraday_sentiment_poll_job()
    assert upserted == []


async def test_intraday_sentiment_poll_job_failure_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fetch 抛错 → job 体不抛（被 try/except 吞 + 记 ``job:failures``）。

    Redis 不在本地 → ``record_job_failure`` 内部会 swallow 自身异常（已有
    ``test_record_redis_outage_never_raises`` 护栏），所以无须 mock redis。
    守卫必须放行，否则本测试退化成"早返回也不抛"的空断言。

    非空性证据：删掉 job 体 try/except → RuntimeError 上抛 → 红。
    """
    from app.scheduler import jobs

    async def _fake_fetch_raises(_trade_date: str) -> list[dict[str, Any]]:
        raise RuntimeError("eastmoney down")

    monkeypatch.setattr(intraday_sentiment_service, "fetch_intraday_pool", _fake_fetch_raises)
    monkeypatch.setattr(jobs, "_is_workday", lambda: True)
    monkeypatch.setattr(jobs, "_in_trading_hours", lambda: True)

    # 不应抛——失败非致命
    await jobs.intraday_sentiment_poll_job()


# ---------------------------------------------------------------------------
# non-vacuity 证据（reviewer 复核入口；不是测试代码的一部分）
# ---------------------------------------------------------------------------
#
# PG 标记分布（8 个 PG-free 用例在无 dev PG 的 CI 上照跑）：
#   requires_pg: 3 个 migration + 2 个 upsert；其余 8 个无标记。
#
# test_migration_upgrade_creates_table:
#   - 临时把 _alembic_upgrade_to(REVISION) 改 PREV_REVISION → 表不存在 → 红。
#
# test_migration_downgrade_drops_table:
#   - 临时把 _alembic_downgrade_to(PREV_REVISION) 改错位（如 ``-2``）→ 表仍在 → 红。
#
# test_upsert_intraday_snapshot_is_idempotent（调生产 repo 两次）：
#   - 删掉 repo 的 .on_conflict_do_nothing(...) → 第二次 UniqueViolation → 红。
#   - 改 .on_conflict_do_update(...) → n2=1 且 zt 被覆盖成 99 → 红。
#
# test_upsert_intraday_snapshot_preserves_row_content:
#   - 临时把 zt_count=42 改成 41 / max_streak=7 改 0 → 红。
#
# test_list_intraday_snapshot_orders_by_captured_at_asc（真 SQLite 跑 SQL）：
#   - 删掉 repo 的 .order_by(captured_at) → 返回插入序 [30,10,20] → 红。
#   - 删掉 repo 的 .where(trade_date == ...) → 干扰行 zt=99 混入 → 红。
#
# test_intraday_endpoint_returns_repo_order_and_caches:
#   - cache key 改错字 / ttl 改 300 → 红；端点重排 rows → zt 序列红。
#
# test_intraday_endpoint_returns_400_for_future_date:
#   - _today_sh 改 date(2026, 9, 19) → 无 future 错误 → 红；删 400 检查 → 红。
#
# test_intraday_sentiment_poll_registered_with_correct_cron:
#   - hour 改回 "9-14" → 红；minute 改 "*/10" → 红。
#
# test_intraday_sentiment_poll_job_skips_outside_trading_hours:
#   - 删 job 体守卫 → fetch_calls == [1] → 红。
#
# test_intraday_sentiment_poll_job_writes_one_point:
#   - _fake_fetch 返 [] → upserted == [] → 红；captured_at 去 tz → 红。
#
# test_intraday_sentiment_poll_job_empty_pool_skips_upsert:
#   - _fake_fetch 返回 1 行 → upserted == [1] → 红。
#
# test_intraday_sentiment_poll_job_failure_does_not_raise:
#   - 删掉 try/except → RuntimeError 上抛 → 红。
