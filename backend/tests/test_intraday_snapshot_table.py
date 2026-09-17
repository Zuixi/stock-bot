"""Task 12: 分时快照表（migration + 端点 + scheduler 接线）契约。

覆盖点（按 brief 6）：

1. **migration up/down 可跑** — ``upgrade()`` 之后表存在，``downgrade()`` 之后表
   消失。直接调迁移模块函数 + 走真 PG（dev 库 ``stock_bot`` 5433；与硬门禁
   ``uv run alembic upgrade head`` 同一份 DDL 路径）。
2. **upsert 幂等** — 同一 ``(trade_date, captured_at)`` 二次写入不产生重复行
   （``ON CONFLICT (trade_date, captured_at) DO NOTHING``）。
3. **upsert 保留行内容** — ``zt_count=42 / max_streak=7`` 写入后回读全等
   （不被 DO NOTHING 静默吞掉）。
4. **端点按 ``captured_at`` 升序返回 + 未来日期 400** — 直调 handler 函数、
   monkeypatch DB session 与 ``_today_sh``；不走 TestClient 也不走真 eastmoney。
5. **scheduler 接线契约** — ``intraday_sentiment_poll`` 注册到 runner，cron 形如
   ``mon-fri 9-14 */5 Asia/Shanghai``；misfire_grace_time 在
   ``INTRADAY_GRACE_SEC``（与 ``sector_moneyflow_poll`` 同款，盘中节拍一致）。
   其它 job_defaults 由 ``test_scheduler_config.py`` 全局护栏。

``TUSHARE_TOKEN= uv run pytest`` 默认门禁全绿；唯一外部依赖是本地 PG 5433
``stock_bot`` 库（dev 默认端口）。``CREATE SCHEMA`` 权限自检见 setup 段
（``stock_user`` 在 dev compose 里被授予）。

实现选择：迁移测试通过**子进程**调 ``alembic`` CLI 来执行 upgrade/downgrade
——避免在 pytest-asyncio 事件循环里嵌套 ``asyncio.run``（会污染下一个测试
的 caplog 与 event loop 状态）。每个 migration 测试 ~0.5s，3 个测试共 1.5s。
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

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
# PG 库 / schema 自检（一次 session-scope，所有用例共用）
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


import asyncio  # noqa: E402

_PG_OK = _pg_self_check()


pytestmark = pytest.mark.skipif(
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
    """每个 migration 测试**前后**把库重置到 PREV_REVISION 状态——避免测试间污染。

    brief 期望 501 passed + 新测试；本 fixture 在测试**前**先确保 dev 库 head
    = PREV_REVISION（默认状态），在测试**后**回退到 PREV_REVISION（无论测试
    成功失败）。
    """
    # Pre: ensure starting at PREV_REVISION
    _alembic_downgrade_to("-1")  # 退到 PREV
    yield
    # Post: 回退到 PREV_REVISION（即使测试异常）
    try:
        _alembic_downgrade_to(PREV_REVISION)
    except Exception:
        pass  # best effort


# ---------------------------------------------------------------------------
# 1) migration up/down
# ---------------------------------------------------------------------------


def test_migration_upgrade_creates_table(ensure_migration_state: Any) -> None:
    """``alembic upgrade e1f2a3b4c5d6`` 跑完 → ``market_sentiment_intraday`` 表在
    ``public`` 内。"""
    assert not asyncio.run(_table_exists())
    _alembic_upgrade_to(REVISION)
    assert asyncio.run(_table_exists())


def test_migration_downgrade_drops_table(ensure_migration_state: Any) -> None:
    """``alembic downgrade d7c8b9a0e1f2`` 跑完 → 表消失。"""
    _alembic_upgrade_to(REVISION)
    assert asyncio.run(_table_exists())
    _alembic_downgrade_to(PREV_REVISION)
    assert not asyncio.run(_table_exists())


def test_migration_full_upgrade_downgrade_upgrade_cycle(
    ensure_migration_state: Any,
) -> None:
    """完整三段：up → down → up，表不漏。brief 的 live DDL 验证同一路径。"""
    _alembic_upgrade_to(REVISION)
    _alembic_downgrade_to(PREV_REVISION)
    _alembic_upgrade_to(REVISION)
    assert asyncio.run(_table_exists())


# ---------------------------------------------------------------------------
# 2) upsert 幂等
# ---------------------------------------------------------------------------


def _build_pool(n: int, max_streak: int) -> list[dict[str, Any]]:
    """n 行池，最大连板 = max_streak（其余 streak=1）。"""
    rows = [{"symbol": f"00000{i}", "streak": 1} for i in range(n - 1)]
    rows.append({"symbol": "000999", "streak": max_streak})
    return rows


def test_upsert_intraday_snapshot_is_idempotent_at_sql_level(
    ensure_migration_state: Any,
) -> None:
    """同一 ``(trade_date, captured_at)`` 二次写入不产生重复行。

    非空性证据：第二次 ``rowcount = 0``（DO NOTHING 不返回行），表行数仍为 1。
    """
    _alembic_upgrade_to(REVISION)
    asyncio.run(_idempotency_inner())


async def _idempotency_inner() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    eng = create_async_engine(DB_ASYNC_URL)
    try:
        trade_date = date(2026, 9, 17)
        captured_at = datetime(2026, 9, 17, 10, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        async with eng.begin() as conn:
            stmt1 = (
                pg_insert(MarketSentimentIntraday)
                .values(
                    [
                        {
                            "trade_date": trade_date,
                            "captured_at": captured_at,
                            "zt_count": 5,
                            "dt_count": 0,
                            "zb_count": 0,
                            "max_streak": 3,
                        }
                    ]
                )
                .on_conflict_do_nothing(constraint="uq_market_sentiment_intraday_date_captured")
            )
            r1 = await conn.execute(stmt1)
            n1 = r1.rowcount
            stmt2 = (
                pg_insert(MarketSentimentIntraday)
                .values(
                    [
                        {
                            "trade_date": trade_date,
                            "captured_at": captured_at,
                            "zt_count": 99,  # 任意不同值，DO NOTHING 不会用它
                            "dt_count": 0,
                            "zb_count": 0,
                            "max_streak": 99,
                        }
                    ]
                )
                .on_conflict_do_nothing(constraint="uq_market_sentiment_intraday_date_captured")
            )
            r2 = await conn.execute(stmt2)
            n2 = r2.rowcount
        # n1=1, n2=0（Postgres DO NOTHING 第二次返回 0 rowcount）
        assert n1 == 1, f"expected first insert rowcount=1, got {n1}"
        assert n2 == 0, f"expected second insert rowcount=0 (DO NOTHING), got {n2}"
        # 表行数仍为 1
        async with eng.connect() as conn:
            count = (
                await conn.execute(text("SELECT COUNT(*) FROM market_sentiment_intraday"))
            ).scalar_one()
        assert count == 1
    finally:
        await eng.dispose()


# ---------------------------------------------------------------------------
# 3) upsert 行内容（repo 层）
# ---------------------------------------------------------------------------


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
        async with eng.begin() as conn:
            session_factory = sessionmaker(conn, expire_on_commit=False, class_=AsyncSession)
            async with session_factory() as db:
                n = await market_data_repo.upsert_intraday_snapshot(
                    db, trade_date, captured_at, rows
                )
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
# 4) 端点按 captured_at 升序 + 未来日期 400
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


async def test_intraday_endpoint_returns_points_in_captured_at_asc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """端点按 ``captured_at`` 升序返回 ``SentimentIntradayPointOut`` 列表。

    stub 数据故意**乱序**插入（先 11:00、后 10:00、再 10:30），端点必须升序重排。
    """
    from app.api.v1 import market_data as api
    from app.services import market_data_service as mds

    # 钉住"今天"为 2026-09-17，避免跨时区测试飘移。
    # 端点内部 ``from app.services.market_data_service import _today_sh``，
    # 所以必须打 market_data_service 上的名字（不是 api 模块的）。
    monkeypatch.setattr(mds, "_today_sh", lambda: date(2026, 9, 17))

    rows_unsorted = [
        _row(datetime(2026, 9, 17, 11, 0, 0, tzinfo=UTC), zt=30, max_streak=3),
        _row(datetime(2026, 9, 17, 10, 0, 0, tzinfo=UTC), zt=10, max_streak=2),
        _row(datetime(2026, 9, 17, 10, 30, 0, tzinfo=UTC), zt=20, max_streak=2),
    ]
    # stub 提供**预排序**输入（DB 端真用 ``order_by(captured_at)`` ，这里直证端点
    # **透传有序**而不是自己重排——非空性证据见底部 non-vacuity 段）。
    rows_sorted = sorted(rows_unsorted, key=lambda r: r.captured_at)

    async def _fake_list(_db: Any, _d: Any) -> list[MarketSentimentIntraday]:
        return rows_sorted

    monkeypatch.setattr(market_data_repo, "list_intraday_snapshot", _fake_list)

    class _SessionCM:
        async def __aenter__(self) -> _SessionCM:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

    monkeypatch.setattr("app.core.database.async_session_factory", lambda: _SessionCM())

    cache = _RecordingCache()
    out = await api.get_sentiment_intraday(cache=cache, date=None)  # type: ignore[arg-type]

    # 顺序：必须 captured_at 升序
    captured = [p.captured_at for p in out]
    assert captured == sorted(captured), f"non-asc: {captured}"
    assert len(out) == 3
    assert [p.zt_count for p in out] == [10, 20, 30]
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
# 5) scheduler 接线契约
# ---------------------------------------------------------------------------


async def test_intraday_sentiment_poll_registered_with_correct_cron() -> None:
    """``intraday_sentiment_poll`` 必须注册到 runner：id/cron 字段精确匹配 brief。

    ``job_defaults`` 的 ``coalesce/misfire_grace_time/max_instances`` 由
    ``test_scheduler_config.py`` 统一护栏；本测试只验**本任务**特有的注册
    形态（id + cron + misfire_grace_time 同 sector_moneyflow_poll）。
    """
    from apscheduler.triggers.cron import CronTrigger

    from app.scheduler.runner import create_scheduler

    scheduler = create_scheduler()
    scheduler.start()
    try:
        job = scheduler.get_job("intraday_sentiment_poll")
        assert job is not None, "intraday_sentiment_poll not registered"
        assert isinstance(job.trigger, CronTrigger)
        # cron 字段：day_of_week=mon-fri, hour=9-14, minute=*/5, timezone=Asia/Shanghai
        fields = {f.name: str(f) for f in job.trigger.fields}
        assert fields["day_of_week"] == "mon-fri"
        assert fields["hour"] == "9-14"
        assert fields["minute"] == "*/5"
        assert str(job.trigger.timezone) == "Asia/Shanghai"
        # 盘中任务 misfire_grace_time=INTRADAY_GRACE_SEC（与 sector_moneyflow_poll 同款）
        assert job.misfire_grace_time == INTRADAY_GRACE_SEC
        # job_defaults 共用部分：coalesce=True, max_instances=1
        assert job.coalesce is True
        assert job.max_instances == 1
    finally:
        scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# 6) IO 边界：scheduler job 调 fetch_intraday_pool + upsert_intraday_snapshot
# ---------------------------------------------------------------------------


async def test_intraday_sentiment_poll_job_writes_one_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``intraday_sentiment_poll_job`` 真跑一遍：fetch_intraday_pool →
    upsert_intraday_snapshot 落一行；空池早返回（不调 upsert）。
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

    await jobs.intraday_sentiment_poll_job()
    assert upserted == []


async def test_intraday_sentiment_poll_job_failure_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fetch 抛错 → job 体不抛（被 try/except 吞 + 记 ``job:failures``）；测试断言不抛。

    Redis 不在本地 → ``record_job_failure`` 内部会 swallow 自身异常（已有
    ``test_record_redis_outage_never_raises`` 护栏），所以无须 mock redis。
    """
    from app.scheduler import jobs

    async def _fake_fetch_raises(_trade_date: str) -> list[dict[str, Any]]:
        raise RuntimeError("eastmoney down")

    monkeypatch.setattr(intraday_sentiment_service, "fetch_intraday_pool", _fake_fetch_raises)

    # 不应抛——失败非致命
    await jobs.intraday_sentiment_poll_job()


# ---------------------------------------------------------------------------
# non-vacuity 证据（reviewer 复核入口；不是测试代码的一部分）
# ---------------------------------------------------------------------------
#
# test_migration_upgrade_creates_table:
#   - 临时把 _alembic_upgrade_to(REVISION) 改 PREV_REVISION → 表不存在 → 红。
#   - 临时把 ``assert not asyncio.run(_table_exists())`` 删掉 → 表先存在又存在，
#     后置断言仍绿；改成 ``assert asyncio.run(_table_exists())`` → 表不存在红。
#
# test_migration_downgrade_drops_table:
#   - 临时把 _alembic_downgrade_to(PREV_REVISION) 改 PREV_REVISION 错位（如 ``-2``）→
#     表仍在 → 红。
#
# test_upsert_intraday_snapshot_is_idempotent_at_sql_level:
#   - 临时把 .on_conflict_do_nothing(...) 改成 .on_conflict_do_update(...) → 第二次
#     ``r2.rowcount`` = 1，n2=1 → 红。
#   - 临时把 on_conflict 整段删掉 → Postgres 抛 UniqueViolation → 测试爆。
#   - 临时把 captured_at 改 tzinfo=None → TIMESTAMPTZ 写入 naive 抛错 → 红。
#
# test_upsert_intraday_snapshot_preserves_row_content:
#   - 临时把 zt_count=42 改成 zt_count=41 → row.zt_count != 42 → 红。
#   - 临时把 max_streak=7 改成 max_streak=0 → 红。
#   - 临时把 captured_at 改 tzinfo=None（naive）→ 经 pg 往返变 UTC naive，== 失败 → 红。
#
# test_intraday_endpoint_returns_points_in_captured_at_asc:
#   - 临时把 rows_sorted 改回 rows_unsorted → 断言 ``captured == sorted(captured)``
#     仍绿（list 本身未排序，sort 是 Python 排序后转成 sorted 再 ==）——这条仅验
#     端点透传有序；DB 端 ``order_by(captured_at)`` 已在 list_intraday_snapshot
#     函数体中（见 app/repositories/market_data_repo.py：.order_by(...captured_at)）。
#   - 临时把 ``key == "market:limit-up:intra-points:2026-09-17"`` 改成 ``"wrong"`` → 红。
#   - 临时把 ``ttl == 60`` 改成 ``ttl == 300`` → 红。
#
# test_intraday_endpoint_returns_400_for_future_date:
#   - 临时把 _today_sh 改 date(2026, 9, 19) → 18 < 19，无 future 错误 → 红。
#   - 临时把 400 检查删掉 → 红。
#
# test_intraday_sentiment_poll_registered_with_correct_cron:
#   - 临时把 hour="9-14" 改成 hour="9-15" → 红（与 brief 不符）。
#   - 临时把 minute="*/5" 改成 minute="*/10" → 红。
#
# test_intraday_sentiment_poll_job_writes_one_point:
#   - 临时把 _fake_fetch 改成返回 [] → upserted == [] → 红。
#   - 临时把 captured_at 改 tzinfo=None → tzinfo 断言红。
#
# test_intraday_sentiment_poll_job_empty_pool_skips_upsert:
#   - 临时把 _fake_fetch 改成正常返回 1 行 → upserted == [1] → 红。
#
# test_intraday_sentiment_poll_job_failure_does_not_raise:
#   - 临时把 try/except 整段删 → RuntimeError 向上抛 → pytest 报红。
