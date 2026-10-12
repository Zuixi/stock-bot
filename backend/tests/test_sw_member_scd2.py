"""spec §S1.4 的 SCD2 数据契约（真 PG，目标库 = **`stock_bot_test`**）。

**为什么不照抄 `test_intraday_snapshot_table.py`**：那个文件把 dev 库 `stock_bot`
硬编码在 ``:58-59``（Ruling 17），在共享环境下必然失败。本文件从
``TEST_DATABASE_URL`` 取 URL、默认落到 ``stock_bot_test``（端口 5433），
库不可达时 skip 而不是红。

覆盖：
1. 迁移：SCD2 两表存在；``effective_from`` 为 NOT NULL、``effective_to`` 可空；
   **既有 ``sw_industry_members`` 一列未动**（不给既有行硬塞 NOT NULL）
2. ``upgrade head → downgrade -1 → upgrade head`` 干净往返
3. 对照表（``data/sw_index_mapping.csv``）511 行 = 31/134/346，代码唯一
4. ``normalize_member_rows``：``in_date`` 缺失的行被丢弃；``is_current`` 取 ``is_new``
5. 重跑同一份上游行 → **新增行 0**（唯一键幂等）
6. **SCD2 区间还原**：同一 (行业, 股票) 多段区间下，任取两个历史日各取到"当日有效"
   的那一段（这是 spec §S1.4「任取一个历史交易日…与当日实际分类快照一致」的机制层验证）
7. ``as_of`` 上界：读面返回的 ``scd2_as_of`` = 最近一次**成功** ingest，且**无 ingest 记录
   时为 None**（不得把"没采过"说成"当日无成分"）
8. 31 个一级行业**全部可下钻到三级且无断链**（L1→L2→L3 每级都有映射）

**运维顺序（重要）**：本文件与 ``tests/test_industry_metric_pit_roundtrip.py`` 都跑
``alembic downgrade → upgrade``；后者 downgrade 到的 ``f1a2b3c4d5e6`` 在本任务之前，
会**连带 drop 掉 ``sw_industry_member_periods`` / ``sw_member_ingest_runs``**，
故整套 pytest 跑完后 ``sw_member_ingest_runs`` 会回到空表。所有用例因此都必须
自建 fixture（本文件全部如此，事务内 ROLLBACK），**不得**假设"库里有 SCD2 数据"。
运营侧的顺序是：**先跑测试，再跑回填**（``scripts/ingest_sw_member_periods.py``）。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

import asyncpg
import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.sw_industry import SwMemberIngestRun
from app.services import sw_member_scd2_service as scd2

REVISION = "b4c5d6e7f8a9"
PREV_REVISION = "f1a2b3c4d5e6"

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
        [ALEMBIC_BIN, *args], capture_output=True, text=True, cwd=BACKEND_DIR, env=env
    )
    if result.returncode != 0:
        raise AssertionError(
            f"alembic {' '.join(args)} failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


async def _fetch(sql: str, *args: Any) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(SYNC_URL)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


async def _with_rollback(fn: Any) -> Any:
    """在一个事务里跑 fn，结束一律 ROLLBACK —— 测试不污染库内容。"""
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


# ── 1) 迁移：SCD2 两表 + 既有表零改动 ───────────────────────────────────


@requires_pg
def test_migration_scd2_nullability_and_existing_table_untouched() -> None:
    """`effective_from` NOT NULL / `effective_to` 可空；既有 `sw_industry_members` 未加列。"""
    _alembic("upgrade", "head")

    async def _go() -> tuple[dict[str, str], list[str], list[str]]:
        rows = await _fetch(
            "SELECT column_name, is_nullable FROM information_schema.columns WHERE table_name = $1",
            "sw_industry_member_periods",
        )
        state = {r["column_name"]: r["is_nullable"] for r in rows}
        member_cols = await _fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = $1",
            "sw_industry_members",
        )
        indexes = await _fetch(
            "SELECT indexname FROM pg_indexes WHERE tablename = $1",
            "sw_industry_member_periods",
        )
        return state, [r["column_name"] for r in member_cols], [r["indexname"] for r in indexes]

    state, member_cols, indexes = asyncio.run(_go())

    assert state["effective_from"] == "NO", "新表的 effective_from 必须 NOT NULL"
    assert state["effective_to"] == "YES", "effective_to 必须可空（NULL = 至今仍在成分）"
    assert state["is_current"] == "NO"
    assert state["source"] == "NO"
    # Ruling 30 / 硬性约束 1：既有行不得被硬塞 NOT NULL，故既有表一列未加
    assert member_cols == [
        "id",
        "industry_code",
        "stock_code",
        "symbol",
        "stock_name",
        "updated_at",
    ]
    assert "uq_sw_member_period_code_stock_from" in indexes
    assert "idx_sw_member_period_industry_from" in indexes
    assert "idx_sw_member_period_open" in indexes


def test_migration_file_contract() -> None:
    """本迁移**只建新表**、不 add_column 到既有表；down_revision = 当时 head。"""
    path = (
        Path(BACKEND_DIR)
        / "app"
        / "migrations"
        / "versions"
        / f"{REVISION}_add_sw_industry_member_scd2.py"
    )
    text_src = path.read_text()
    assert f'down_revision: str | None = "{PREV_REVISION}"' in text_src
    assert "add_column" not in text_src, "不得给既有表加列（会导致 NOT NULL 回填问题）"
    assert text_src.count("op.create_table") == 2
    assert text_src.count("op.drop_table") == 2


@requires_pg
def test_migration_up_down_up_round_trip() -> None:
    """down 掉两表 → up 回来；失败也把库恢复到 head。"""

    async def _table_exists(name: str) -> bool:
        rows = await _fetch("SELECT to_regclass($1) AS t", f"public.{name}")
        return rows[0]["t"] is not None

    _alembic("upgrade", "head")
    assert asyncio.run(_table_exists("sw_industry_member_periods"))
    try:
        _alembic("downgrade", PREV_REVISION)
        assert not asyncio.run(_table_exists("sw_industry_member_periods"))
        assert not asyncio.run(_table_exists("sw_member_ingest_runs"))
        _alembic("upgrade", "head")
        assert asyncio.run(_table_exists("sw_industry_member_periods"))
    finally:
        _alembic("upgrade", "head")


# ── 3) 对照表覆盖 ──────────────────────────────────────────────────────


def test_index_mapping_covers_all_three_levels() -> None:
    """511 行 = L1 31 + L2 134 + L3 346；本地代码与上游代码各自唯一。"""
    refs = scd2.parse_sw_index_mapping()
    assert len(refs) == 511
    by_level = {lvl: sum(1 for r in refs if r.level == lvl) for lvl in (1, 2, 3)}
    assert by_level == {1: 31, 2: 134, 3: 346}
    assert len({r.industry_code for r in refs}) == 511
    assert len({r.index_code for r in refs}) == 511
    assert all(r.index_code.endswith(".SI") for r in refs)


# ── 4/5) 规范化 + 幂等 ─────────────────────────────────────────────────


def _frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(records, columns=list(scd2._MEMBER_COLUMNS))


def test_normalize_member_rows_drops_missing_in_date_and_maps_is_current() -> None:
    """``in_date`` 缺失 → 丢弃（effective_from 必填）；``is_current`` 取 ``is_new``。"""
    ref = scd2.SwIndexRef("110702", "850172.SI", 3, "生猪养殖")
    df = _frame(
        [
            {
                "index_code": "850172.SI",
                "con_code": "000876.SZ",
                "in_date": "20211213",
                "out_date": None,
                "is_new": "Y",
            },
            {
                "index_code": "850172.SI",
                "con_code": "000001.SZ",
                "in_date": None,
                "out_date": None,
                "is_new": "Y",
            },
            {
                "index_code": "850172.SI",
                "con_code": "000002.SZ",
                "in_date": "20070703",
                "out_date": "20090601",
                "is_new": "N",
            },
        ]
    )
    rows = scd2.normalize_member_rows(df, ref)
    assert [r["symbol"] for r in rows] == ["000876", "000002"]
    assert rows[0]["effective_from"] == date(2021, 12, 13)
    assert rows[0]["effective_to"] is None
    assert rows[0]["is_current"] is True
    assert rows[1]["effective_to"] == date(2009, 6, 1)
    assert rows[1]["is_current"] is False
    assert all(r["industry_code"] == "110702" for r in rows)


def test_normalize_member_rows_rejects_missing_columns() -> None:
    """上游列变了要**响亮报错**，不能静默产出空行（否则"上游改版"会伪装成"当日无成分"）。"""
    ref = scd2.SwIndexRef("110702", "850172.SI", 3, "生猪养殖")
    with pytest.raises(ValueError, match="缺列"):
        scd2.normalize_member_rows(pd.DataFrame([{"index_code": "x"}]), ref)


@requires_pg
def test_upsert_is_idempotent_on_unique_key() -> None:
    """同一份上游行跑两次 → 第二次新增行 0（唯一键生效）。"""
    ref = scd2.SwIndexRef("990001", "999901.SI", 3, "测试行业")
    df = _frame(
        [
            {
                "index_code": "999901.SI",
                "con_code": "600000.SH",
                "in_date": "20200102",
                "out_date": None,
                "is_new": "Y",
            },
        ]
    )
    rows = scd2.normalize_member_rows(df, ref)

    async def _scenario(db: AsyncSession) -> tuple[int, int, int]:
        await scd2.upsert_period_rows(db, rows)
        await db.flush()
        first = (
            await db.execute(
                text(
                    "SELECT count(*) FROM sw_industry_member_periods WHERE industry_code = '990001'"
                )
            )
        ).scalar_one()
        await scd2.upsert_period_rows(db, rows)
        await db.flush()
        second = (
            await db.execute(
                text(
                    "SELECT count(*) FROM sw_industry_member_periods WHERE industry_code = '990001'"
                )
            )
        ).scalar_one()
        dup = (
            await db.execute(
                text(
                    "SELECT count(*) FROM (SELECT industry_code, stock_code, effective_from "
                    "FROM sw_industry_member_periods WHERE industry_code = '990001' "
                    "GROUP BY 1,2,3 HAVING count(*) > 1) d"
                )
            )
        ).scalar_one()
        return first, second, dup

    first, second, dup = asyncio.run(_with_rollback(_scenario))
    assert first == 1
    assert second == 1, "重跑同一份数据不得新增行"
    assert dup == 0, "唯一键上不得出现重复区间"


# ── 6/7) SCD2 区间还原 + as_of 上界（纯 SQL，不依赖库内已灌数据）────────


_PERIOD_COLUMNS = (
    "industry_code, stock_code, symbol, stock_name, effective_from, effective_to, "
    "is_current, source"
)


async def _insert_periods(db: AsyncSession, rows: list[dict[str, Any]]) -> None:
    await db.execute(
        text(
            f"INSERT INTO sw_industry_member_periods ({_PERIOD_COLUMNS}) VALUES "
            "(:industry_code, :stock_code, :symbol, :stock_name, :effective_from, "
            ":effective_to, :is_current, :source)"
        ),
        rows,
    )
    await db.flush()


def _period(
    industry_code: str,
    stock_code: str,
    start: date,
    end: date | None,
    is_current: bool,
) -> dict[str, Any]:
    return {
        "industry_code": industry_code,
        "stock_code": stock_code,
        "symbol": stock_code.split(".")[0],
        "stock_name": "测试",
        "effective_from": start,
        "effective_to": end,
        "is_current": is_current,
        "source": "test",
    }


@requires_pg
def test_scd2_interval_reconstruction_picks_period_valid_at_query_day() -> None:
    """三段区间的同一只股票：两个历史日各取到"当日有效"的那一段（SCD2 机制）。"""
    rows = [
        # 600001 在 990002 三次进出：…2009 段 / 2015-2017 段 / 2021 至今段
        _period("990002", "600001.SH", date(2007, 7, 3), date(2009, 6, 1), False),
        _period("990002", "600001.SH", date(2015, 7, 1), date(2017, 6, 30), False),
        _period("990002", "600001.SH", date(2021, 12, 13), None, True),
        # 600002 只在中段：用来证明"当日不在成分"与"没有记录"不是一回事
        _period("990002", "600002.SH", date(2015, 7, 1), date(2017, 6, 30), False),
    ]
    query = text(
        "SELECT stock_code, effective_from, effective_to "
        "FROM sw_industry_member_periods "
        "WHERE industry_code = :code AND effective_from <= :d "
        "  AND (effective_to IS NULL OR :d < effective_to) "
        "ORDER BY stock_code"
    )

    async def _scenario(db: AsyncSession) -> dict[str, list[tuple[Any, ...]]]:
        await _insert_periods(db, rows)
        out: dict[str, list[tuple[Any, ...]]] = {}
        for label, day in (
            ("2016-01-04", date(2016, 1, 4)),
            ("2022-01-04", date(2022, 1, 4)),
            ("2008-01-02", date(2008, 1, 2)),
        ):
            got = (await db.execute(query, {"code": "990002", "d": day})).all()
            out[label] = [tuple(r) for r in got]
        return out

    out = asyncio.run(_with_rollback(_scenario))
    assert out["2016-01-04"] == [
        ("600001.SH", date(2015, 7, 1), date(2017, 6, 30)),
        ("600002.SH", date(2015, 7, 1), date(2017, 6, 30)),
    ]
    assert out["2022-01-04"] == [("600001.SH", date(2021, 12, 13), None)]
    # 2008-01-02 落在第一段内（2007-07-03 ~ 2009-06-01）
    assert out["2008-01-02"] == [("600001.SH", date(2007, 7, 3), date(2009, 6, 1))]


@requires_pg
def test_member_as_of_is_none_without_successful_run() -> None:
    """没有任何成功 ingest 时读面必须返回 None（= 读面声明"尚无 SCD2"，不是"当日无成分"）。"""

    async def _scenario(db: AsyncSession) -> tuple[date | None, date | None]:
        # 事务内看不到别人已提交的 run 记录吗？看得到——但为避免依赖库内既有数据，
        # 这里只断言"删掉全部成功记录后为 None"，故先在事务里 DELETE 成功记录。
        await db.execute(text("DELETE FROM sw_member_ingest_runs WHERE status = 'ok'"))
        await db.flush()
        without = await scd2.get_member_as_of(db)
        db.add(
            SwMemberIngestRun(as_of=date(2026, 10, 12), index_codes=1, rows_upserted=1, status="ok")
        )
        await db.flush()
        with_run = await scd2.get_member_as_of(db)
        return without, with_run

    without, with_run = asyncio.run(_with_rollback(_scenario))
    assert without is None
    assert with_run == date(2026, 10, 12)


@requires_pg
def test_member_as_of_ignores_partial_runs() -> None:
    """`status='partial'` 的运行不得抬高 as_of 上界（只认整批成功的）。"""

    async def _scenario(db: AsyncSession) -> date | None:
        await db.execute(text("DELETE FROM sw_member_ingest_runs"))
        db.add(
            SwMemberIngestRun(as_of=date(2026, 1, 1), index_codes=1, rows_upserted=1, status="ok")
        )
        db.add(
            SwMemberIngestRun(
                as_of=date(2026, 6, 1), index_codes=1, rows_upserted=1, status="partial"
            )
        )
        await db.flush()
        return await scd2.get_member_as_of(db)

    assert asyncio.run(_with_rollback(_scenario)) == date(2026, 1, 1)


# ── 8) 31/31 无断链（直连 SQL；不依赖库内既有 SCD2 数据）─────────────────


@requires_pg
def test_all_31_l1_industries_have_l2_and_l3_chain() -> None:
    """31 个一级行业**每个**都有 ≥1 个二级、且经二级有 ≥1 个三级（树层无断链）。

    覆盖两棵树：
    * ``sw_industry_classes``（本地分类树）——必须 31/134/346 且层次计数自洽；
    * ``data/sw_index_mapping.csv``（上游指数对照）——L3 必须 100% 能沿
      ``parent_code`` 上卷到 L1（这是 drill 读路 SQL 的 ``JOIN parent_code`` 前提）。
    """

    async def _go() -> list[asyncpg.Record]:
        return await _fetch(
            """
            SELECT c1.industry_code AS l1,
                   count(DISTINCT c2.industry_code) AS l2_count,
                   count(DISTINCT c3.industry_code) AS l3_count
            FROM sw_industry_classes c1
            LEFT JOIN sw_industry_classes c2
                   ON c2.parent_code = c1.industry_code AND c2.level = 2
            LEFT JOIN sw_industry_classes c3
                   ON c3.parent_code = c2.industry_code AND c3.level = 3
            WHERE c1.level = 1
            GROUP BY c1.industry_code
            ORDER BY c1.industry_code
            """
        )

    rows = asyncio.run(_go())
    if not rows:
        pytest.skip("sw_industry_classes 为空（需先导入 data/sw_seed.sql）")

    assert len(rows) == 31, f"一级行业应为 31 个，实得 {len(rows)}"
    broken = [r["l1"] for r in rows if r["l2_count"] == 0 or r["l3_count"] == 0]
    assert broken == [], f"以下一级行业断链（缺 L2 或 L3）：{broken}"
    assert sum(r["l3_count"] for r in rows) == 346


def test_index_mapping_l3_all_roll_up_to_l1() -> None:
    """纯函数层：346 个 L3 的 ``parent_code`` 前缀链必须能上卷到某个 L1 代码。"""
    refs = scd2.parse_sw_index_mapping()
    l1 = {r.industry_code for r in refs if r.level == 1}
    # 本地代码是层次编码：L1=前 2 位+0000，L2=前 4 位+00，L3=6 位
    unresolved = [
        r.industry_code for r in refs if r.level == 3 and f"{r.industry_code[:2]}0000" not in l1
    ]
    assert unresolved == [], f"以下 L3 无法上卷到 L1：{unresolved[:10]}"


def test_scd2_query_day_falls_back_to_today() -> None:
    """`as_of` 与行情日都为空时，SCD2 查询日兜底为具体日期（绝不拿 NULL 去比区间）。"""
    from app.services.sw_drilldown_service import _scd2_query_day

    explicit = date(2020, 1, 2)
    assert _scd2_query_day(explicit, date(2026, 1, 1)) == explicit
    assert _scd2_query_day(None, date(2026, 1, 1)) == date(2026, 1, 1)
    fallback = _scd2_query_day(None, None)
    assert isinstance(fallback, date)
    # 兜底日必须"不太旧"：与今天相差不超过 1 天（时区边界）
    assert abs((date.today() - fallback).days) <= 1


def test_scd2_interval_semantics_are_half_open() -> None:
    """区间是 **[effective_from, effective_to)**：`effective_to` 当天已不算成分。

    与上游 ``out_date`` 的语义一致（实测样例：``000034.SZ`` 段
    ``20070703 ~ 20090601``，2009-06-01 当天不在此行业）。
    """
    from datetime import timedelta as _td

    start, end = date(2015, 7, 1), date(2017, 6, 30)

    def in_period(day: date) -> bool:
        return start <= day and (end is None or day < end)

    assert in_period(start)
    assert in_period(end - _td(days=1))
    assert not in_period(end)
    assert not in_period(start - _td(days=1))
