"""spec §S1.4 的 SCD2 数据契约（真 PG，目标库 = **`stock_bot_test`**）。

**为什么不照抄 `test_intraday_snapshot_table.py`**：那个文件把 dev 库 `stock_bot`
硬编码在 ``:58-59``（Ruling 17），在共享环境下必然失败。本文件从
``TEST_DATABASE_URL`` 取 URL、默认落到 ``stock_bot_test``（端口 5433），
库不可达时 skip 而不是红。

**本文件不依赖库内既有数据**（Fix round 1 · 评审 I4）：所有用例要么纯函数、
要么真 PG + **事务内 ROLLBACK**，并且**自己在事务里建最小分类树**——
不再 ``pytest.skip`` 于"树为空"（那会让 CI 上的断言静默消失、套件照样绿）。

覆盖：
1. 迁移：SCD2 两表存在；``effective_from`` 为 NOT NULL、``effective_to`` 可空；
   **既有 ``sw_industry_members`` 一列未动**（不给既有行硬塞 NOT NULL）
2. 迁移干净往返（降一级只回退覆盖列）
3. 对照表（``data/sw_index_mapping.csv``）511 行 = 31/134/346，代码唯一；
   默认灌全部三级
4. ``normalize_member_rows``：``in_date`` 缺失的行被丢弃；``is_current`` 取 ``is_new``
5. 重跑同一份上游行 → **新增行 0**，且 ``rows_inserted/updated`` 能区分两种情形
6. **SCD2 区间还原**：同一 (行业, 股票) 多段区间下，任取两个历史日各取到"当日有效"
   的那一段（spec §S1.4「任取一个历史交易日…与当日实际分类快照一致」的机制层验证）
7. ``as_of`` 上界：只认 ``fully_ingested AND status='ok'``；**``--limit`` 冒烟运行
   与 ``status='partial'`` 都不抬高它**（Fix round 1 · 评审 I1）
8. **成员取自节点自身层级**（Fix round 1 · 评审 C1）：自身层级优先、缺失才上卷，
   且上卷**明确标注不等价**；末级 ``chain`` 每级带真实成分数（不是 0 占位）
9. 31 个一级行业**全部可下钻到三级**：机械版本**在事务里自建 31 个 L1 的树与 as_of**，
   断言每 L1 都有 L2/L3/当日成分，**不会静默跳过**；库内真有申万树时另按真实树核对

**运维顺序（重要）**：``tests/test_industry_metric_pit_roundtrip.py`` 的破坏性
``downgrade`` 已改为 **opt-in**（``SC_DESTRUCTIVE_MIGRATION_TESTS=1``），故默认
pytest 不再 drop 本任务的两张表。顺序仍是"先跑测试、再回填"，见
``docs/deployment/index.md``。
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

REVISION = "c5d6e7f8a9b0"
PREV_REVISION = "b4c5d6e7f8a9"

_DEFAULT_ASYNC = "postgresql+asyncpg://stock_user:stock_pass@localhost:5433/stock_bot_test"
ASYNC_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or _DEFAULT_ASYNC
SYNC_URL = ASYNC_URL.replace("+asyncpg", "")

ALEMBIC_BIN = str(Path(__file__).resolve().parents[1] / ".venv" / "bin" / "alembic")
BACKEND_DIR = str(Path(__file__).resolve().parents[1])

# 本文件自建的测试树（99xxxx 段，与其他测试文件错开，避免唯一键互撞）
L1 = "992000"
L2 = "992100"
L3 = "992101"
ASOF = date(2026, 10, 12)


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

    async def _go() -> tuple[dict[str, str], list[str], list[str], str]:
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
        uq = await _fetch(
            "SELECT indexdef AS def FROM pg_indexes "
            "WHERE tablename = 'sw_industry_member_periods' AND indexname = $1",
            "uq_sw_member_period_code_stock_from_to",
        )
        return (
            state,
            [r["column_name"] for r in member_cols],
            [r["indexname"] for r in indexes],
            uq[0]["def"] if uq else "",
        )

    state, member_cols, indexes, uq_def = asyncio.run(_go())

    assert state["effective_from"] == "NO", "新表的 effective_from 必须 NOT NULL"
    assert state["effective_to"] == "YES", "effective_to 必须可空（NULL = 至今仍在成分）"
    assert state["is_current"] == "NO"
    assert state["source"] == "NO"
    # Ruling 30 / 硬性约束 1：既有行不得被硬塞 NOT NULL，故既有表一列未加
    #
    # ⚠️ 用**集合**比较，不比顺序（2026-10-19 修）：本用例读的是**运行环境里那张表**的
    # `information_schema.columns`。列顺序取决于该库是怎么长出来的 —— 全新建库走
    # `b4c5d6e7f8a9` 的 CREATE TABLE 是一种顺序，早期建库再被 ALTER 追加 `stock_name`
    # 是另一种（本机 dev 库即后者，会假红）。列顺序不是任何契约的一部分，
    # 这里要钉的是"列一个不多一个不少"。
    assert set(member_cols) == {
        "id",
        "industry_code",
        "stock_code",
        "symbol",
        "stock_name",
        "updated_at",
    }, f"既有表列集合变了：{member_cols}"
    assert "uq_sw_member_period_code_stock_from_to" in indexes
    assert "idx_sw_member_period_industry_from" in indexes
    assert "idx_sw_member_period_open" in indexes
    # Fix round 1：幂等键必须是**表达式**唯一索引，且 NULL 终点被 COALESCE 折叠
    # （普通 UNIQUE 视 NULL 彼此不同 → NULL 终点行永不冲突 → 幂等失效）
    assert "effective_to" in uq_def, f"幂等键未含 effective_to：{uq_def}"
    assert "COALESCE" in uq_def, f"幂等键未折叠 NULL 终点（幂等会失效）：{uq_def}"
    assert "UNIQUE" in uq_def.upper(), uq_def


def test_migration_file_contract() -> None:
    """只建新表 / 只加列，不碰既有 `sw_industry_members`；down_revision 逐级正确。"""
    path = (
        Path(BACKEND_DIR)
        / "app"
        / "migrations"
        / "versions"
        / "b4c5d6e7f8a9_add_sw_industry_member_scd2.py"
    )
    text_src = path.read_text()
    assert 'down_revision: str | None = "f1a2b3c4d5e6"' in text_src
    assert "add_column" not in text_src, "不得给既有表加列（会导致 NOT NULL 回填问题）"
    assert text_src.count("op.create_table") == 2
    assert text_src.count("op.drop_table") == 2

    path2 = (
        Path(BACKEND_DIR)
        / "app"
        / "migrations"
        / "versions"
        / f"{REVISION}_sw_member_ingest_coverage.py"
    )
    text2 = path2.read_text()
    assert f'down_revision: str | None = "{PREV_REVISION}"' in text2
    assert "fully_ingested" in text2 and "rows_inserted" in text2
    # 只允许动这两张新表；不得出现对既有 sw_industry_members 的 DDL（注释里的提及不算）
    ddl_body = text2.split('"""', 2)[-1]
    assert "sw_industry_members\n" not in ddl_body.replace("sw_industry_member_periods", ""), (
        "不得对既有 sw_industry_members 做 DDL"
    )


@requires_pg
def test_migration_up_down_up_round_trip() -> None:
    """降一级（覆盖列消失）→ 升回 head（覆盖列回来）；失败也把库恢复到 head。"""

    async def _cols() -> list[str]:
        rows = await _fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'sw_member_ingest_runs'"
        )
        return [r["column_name"] for r in rows]

    _alembic("upgrade", "head")
    assert "fully_ingested" in asyncio.run(_cols())
    try:
        _alembic("downgrade", PREV_REVISION)
        assert "fully_ingested" not in asyncio.run(_cols())
        _alembic("upgrade", "head")
        assert "fully_ingested" in asyncio.run(_cols())
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


def test_default_levels_are_all_three() -> None:
    """默认必须灌全部三级 —— C1 的根因就是"只灌 L3 再上卷"。"""
    assert scd2.DEFAULT_LEVELS == (1, 2, 3)


# ── 4/5) 规范化 + 幂等 + 新增/更新计数 ─────────────────────────────────


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
def test_upsert_is_idempotent_and_counts_are_distinguishable() -> None:
    """同一份上游行跑两次 → 第二次新增行 0；`rows_inserted/updated` 能区分两种情形。"""
    ref = scd2.SwIndexRef("993001", "999903.SI", 3, "测试行业")
    df = _frame(
        [
            {
                "index_code": "999903.SI",
                "con_code": "600000.SH",
                "in_date": "20200102",
                "out_date": None,
                "is_new": "Y",
            },
        ]
    )
    rows = scd2.normalize_member_rows(df, ref)

    async def _scenario(db: AsyncSession) -> tuple[int, int, int, int, int]:
        first_ins, first_upd = await scd2.upsert_period_rows(db, rows)
        await db.flush()
        count1 = (
            await db.execute(
                text("SELECT count(*) FROM sw_industry_member_periods WHERE industry_code='993001'")
            )
        ).scalar_one()
        second_ins, _second_upd = await scd2.upsert_period_rows(db, rows)
        await db.flush()
        dup = (
            await db.execute(
                text(
                    "SELECT count(*) FROM (SELECT industry_code, stock_code, effective_from "
                    "FROM sw_industry_member_periods WHERE industry_code='993001' "
                    "GROUP BY 1,2,3 HAVING count(*) > 1) d"
                )
            )
        ).scalar_one()
        return first_ins, first_upd, count1, second_ins, dup

    first_ins, first_upd, count1, second_ins, dup = asyncio.run(_with_rollback(_scenario))
    assert (first_ins, first_upd) == (1, 0), "首次写入应记为 inserted"
    assert count1 == 1
    assert second_ins == 0, "重跑同一份数据不得新增行（inserted=0，而不是 len(rows)）"
    assert dup == 0, "唯一键上不得出现重复区间"


# ── 6/7) SCD2 区间还原 + as_of 上界 ─────────────────────────────────────


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
        _period("993002", "600001.SH", date(2007, 7, 3), date(2009, 6, 1), False),
        _period("993002", "600001.SH", date(2015, 7, 1), date(2017, 6, 30), False),
        _period("993002", "600001.SH", date(2021, 12, 13), None, True),
        _period("993002", "600002.SH", date(2015, 7, 1), date(2017, 6, 30), False),
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
            ("mid", date(2016, 1, 4)),
            ("now", date(2022, 1, 4)),
            ("early", date(2008, 1, 3)),
        ):
            got = (await db.execute(query, {"code": "993002", "d": day})).all()
            out[label] = [tuple(r) for r in got]
        return out

    out = asyncio.run(_with_rollback(_scenario))
    assert out["mid"] == [
        ("600001.SH", date(2015, 7, 1), date(2017, 6, 30)),
        ("600002.SH", date(2015, 7, 1), date(2017, 6, 30)),
    ]
    assert out["now"] == [("600001.SH", date(2021, 12, 13), None)]
    # 2008-01-03 落在第一段内（2007-07-03 ~ 2009-06-01）
    assert out["early"] == [("600001.SH", date(2007, 7, 3), date(2009, 6, 1))]


@requires_pg
def test_member_as_of_only_counts_fully_ingested_ok_runs() -> None:
    """**只认** ``fully_ingested=True AND status='ok'``（Fix round 1 · 评审 I1）。

    ``status='partial'`` 与 ``--limit`` 截断的运行都**不得**抬高上界 ——
    这是 C1 当初没被发现的直接原因。
    """

    async def _scenario(db: AsyncSession) -> tuple[Any, ...]:
        await db.execute(text("DELETE FROM sw_member_ingest_runs"))
        await db.flush()
        without = await scd2.get_member_as_of(db)
        db.add(
            SwMemberIngestRun(
                as_of=date(2026, 6, 1),
                index_codes=1,
                rows_upserted=1,
                rows_inserted=1,
                fully_ingested=False,
                status="partial",
            )
        )
        await db.flush()
        only_partial = await scd2.get_member_as_of(db)
        db.add(
            SwMemberIngestRun(
                as_of=date(2026, 9, 1),
                index_codes=20,
                rows_upserted=20,
                rows_inserted=20,
                fully_ingested=False,
                status="partial",
            )
        )
        await db.flush()
        with_limit = await scd2.get_member_as_of(db)
        db.add(
            SwMemberIngestRun(
                as_of=date(2026, 10, 12),
                index_codes=511,
                rows_upserted=100,
                rows_inserted=100,
                fully_ingested=True,
                status="ok",
            )
        )
        await db.flush()
        full_ok = await scd2.get_member_as_of(db)
        return without, only_partial, with_limit, full_ok

    without, only_partial, with_limit, full_ok = asyncio.run(_with_rollback(_scenario))
    assert without is None
    assert only_partial is None, "status='partial' 不得抬高口径日"
    assert with_limit is None, "limit 截断的运行不得抬高口径日（C1 未被发现的根因）"
    assert full_ok == date(2026, 10, 12)


def test_floor_assertions_flag_zero_and_collapse() -> None:
    """下界断言的绿/红输入（Fix round 1 · 评审 I5；破坏验证见报告）。"""
    # 绿：三层都有指数、都有行
    assert scd2._check_level_floors({1: (31, 7740), 2: (134, 8170), 3: (346, 7624)}, {}) == []
    # 红 1：某层 0 个成功指数 / 0 行
    bad1 = scd2._check_level_floors({1: (0, 0), 3: (346, 7624)}, {})
    assert any("L1 成功指数为 0" in r for r in bad1)
    assert any("L1 区间行总数为 0" in r for r in bad1)
    # 红 2：单层行数塌方（-33% 超过 20% 阈值）
    bad2 = scd2._check_level_floors({1: (31, 3500)}, {1: 5211})
    assert any("跌" in r for r in bad2), bad2
    # 绿：跌幅在阈值内（-10%）
    assert scd2._check_level_floors({1: (31, 4690)}, {1: 5211}) == []


@requires_pg
def test_detail_round_trip_feeds_previous_level_totals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**写入端 → 解析端 round-trip**（re-review · NEW-A 的回归锁）。

    旧版解析端按 ``";"`` 分割、要求 ``"="``，而写入端产出 ``" | "`` join、
    ``L1:指数 31/31,行 7740`` 形态的 ``detail``——解析恒 ``{}``，
    规则 2（单层 20% 塌方 floor）**在生产中从未生效**。纯函数单测测不出这类
    "写入/解析两端不一致"，所以本用例**真跑两次 ingest**（stub 上游）：
    第一轮各层 1 行、完整成功；第二轮上游塌成 0 行——**生产代码**必须靠
    第一轮写下的 detail 把第二轮判成 ``partial``（20% floor 真正转红）。
    旧解析器读不回 previous，第二轮照样 ``ok``，这条断言必然红。
    """
    from app.services import sw_member_scd2_service as svc

    class _ToggleStub:
        """可切换返回内容的假上游：第一轮 1 行/指数，第二轮 0 行。"""

        def __init__(self) -> None:
            self.frame = pd.DataFrame(
                [
                    {
                        "index_code": "x",
                        "con_code": "600000.SH",
                        "in_date": "20200102",
                        "out_date": None,
                        "is_new": "Y",
                    }
                ]
            )

        async def fetch_index_member(self, index_code: str = "") -> pd.DataFrame:
            return self.frame

    stub = _ToggleStub()

    async def _fake_names(_db: Any) -> dict[str, str]:
        return {}

    monkeypatch.setattr(svc, "_stock_names", _fake_names)
    import app.core.providers.tushare_client as tc

    monkeypatch.setattr(tc, "get_tushare_client", lambda: stub)

    async def _scenario(db: AsyncSession) -> tuple[dict[int, int], Any, str]:
        await db.execute(text("DELETE FROM sw_member_ingest_runs"))
        await db.execute(
            text(
                "INSERT INTO sw_industry_classes "
                "(industry_code, level, industry_name, parent_code) VALUES "
                "('994011', 1, '往返一级', NULL), ('994012', 3, '往返三级', NULL)"
            )
        )
        await db.flush()
        refs = [
            scd2.SwIndexRef("994011", "999911.SI", 1, "往返一级"),
            scd2.SwIndexRef("994012", "999912.SI", 3, "往返三级"),
        ]
        # 第一轮：完整成功（2 个指数、各 1 行）→ detail 落逐层计数
        first = await svc.ingest_sw_member_periods(db, levels=(1, 3), refs=refs)
        assert first.fully_ingested and first.status == "ok"
        previous = await svc._previous_level_totals(db)
        # 第二轮：上游塌成 0 行——上一轮写下的逐层总数必须让生产代码判 partial
        stub.frame = pd.DataFrame()
        second = await svc.ingest_sw_member_periods(db, levels=(1, 3), refs=refs)
        last = await svc.get_last_run(db) or {}
        return previous, second, str(last.get("detail"))

    previous, second, second_detail = asyncio.run(_with_rollback(_scenario))
    # **round-trip 的核心断言**：写入端记下的逐层总数（各 1 行）被解析端原样读回。
    # 旧解析器对同一 detail 返回 {}，这条断言必然红。
    assert previous == {1: 1, 3: 1}, (
        f"写入端 → 解析端 round-trip 失败：previous={previous}"
        "（{{}} = 旧解析器对写入格式的返回）"
    )
    # previous 真的参与生产判定（不是摆设）：第二轮 0 行 = 较上次跌 100% → partial，
    # 且运行记录写明塌方原因（20% floor 在生产路径上转红）
    assert second.fully_ingested is False
    assert second.status == "partial"
    assert "下界未过" in second_detail and "跌 100%" in second_detail, second_detail


class _FakeTuShare:
    """只回一行成分的假上游（用于验证"截断运行不抬高上界"的行为层）。"""

    async def fetch_index_member(self, index_code: str = "") -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "index_code": index_code,
                    "con_code": "600000.SH",
                    "in_date": "20200102",
                    "out_date": None,
                    "is_new": "Y",
                }
            ]
        )


@requires_pg
def test_limit_run_is_recorded_as_not_fully_ingested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--limit`` 冒烟运行必须落 ``fully_ingested=False`` + ``status='partial'``（I1 行为层）。"""
    from app.services import sw_member_scd2_service as svc

    async def _fake_names(_db: Any) -> dict[str, str]:
        return {}

    monkeypatch.setattr(svc, "_stock_names", _fake_names)
    import app.core.providers.tushare_client as tc

    monkeypatch.setattr(tc, "get_tushare_client", lambda: _FakeTuShare())

    async def _scenario(db: AsyncSession) -> tuple[Any, Any, dict[str, Any]]:
        await db.execute(text("DELETE FROM sw_member_ingest_runs"))
        # 先把它放进分类树，否则 ingest 会把它当"孤儿代码"记 failed 而不是截断
        await db.execute(
            text(
                "INSERT INTO sw_industry_classes "
                "(industry_code, level, industry_name, parent_code) VALUES "
                "('994001', 3, '限流测试', NULL), ('994002', 3, '限流测试2', NULL)"
            )
        )
        await db.flush()
        refs = [
            scd2.SwIndexRef("994001", "999904.SI", 3, "限流测试"),
            scd2.SwIndexRef("994002", "999905.SI", 3, "限流测试2"),
        ]
        res = await svc.ingest_sw_member_periods(db, levels=(3,), limit=1, refs=refs)
        after = await svc.get_member_as_of(db)
        last = await svc.get_last_run(db) or {}
        return res, after, last

    res, after, last = asyncio.run(_with_rollback(_scenario))
    assert res.fully_ingested is False
    assert res.status == "partial"
    assert after is None, "截断运行的 as_of 不得成为读面口径日"
    assert last["status"] == "partial"
    assert "limit 截断" in str(last["detail"]), f"detail 应写明截断：{last['detail']}"


def test_scd2_query_day_falls_back_to_today() -> None:
    """`as_of` 与行情日都为空时，SCD2 查询日兜底为具体日期（绝不拿 NULL 去比区间）。"""
    from app.services.sw_drilldown_service import _scd2_query_day

    explicit = date(2020, 1, 2)
    assert _scd2_query_day(explicit, date(2026, 1, 1)) == explicit
    assert _scd2_query_day(None, date(2026, 1, 1)) == date(2026, 1, 1)
    fallback = _scd2_query_day(None, None)
    assert isinstance(fallback, date)
    assert abs((date.today() - fallback).days) <= 1


def test_scd2_interval_semantics_are_half_open() -> None:
    """区间是 **[effective_from, effective_to)**：`effective_to` 当天已不算成分。"""
    from datetime import timedelta as _td

    start, end = date(2015, 7, 1), date(2017, 6, 30)

    def in_period(day: date) -> bool:
        return start <= day and (end is None or day < end)

    assert in_period(start)
    assert in_period(end - _td(days=1))
    assert not in_period(end)
    assert not in_period(start - _td(days=1))


def test_assemble_quote_never_substitutes_zero() -> None:
    """空聚合行（LEFT JOIN 全空）→ 所有字段 `None`，**没有一个是 0**。"""
    from app.services.sw_drilldown_service import assemble_quote

    empty: dict[str, Any] = dict.fromkeys(
        (
            "median_open",
            "median_high",
            "median_low",
            "median_close",
            "pct_chg_weighted",
            "turnover_rate_weighted",
            "sum_volume",
            "volume_ratio_weighted",
            "amplitude",
            "sum_amount_all",
        )
    )
    empty.update(dict.fromkeys(("member_count", "quoted_count", "up_count", "down_count"), 0))
    empty.update(
        dict.fromkeys(
            (
                "pct_chg_covered",
                "turnover_rate_covered",
                "volume_covered",
                "volume_ratio_covered",
                "amplitude_covered",
                "amount_covered",
            ),
            0,
        )
    )
    q = assemble_quote(empty, code="993003", name="测试三级", level=3, parent_code="993002")
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
    assert q.member_count == 0 and q.amount_covered == 0


# ── 8/9) 自身层级优先 + 31/31 无断链（自建树，不静默跳过）────────────────

_TREE_ROWS = [
    (L1, 1, "测试一级", None),
    (L2, 2, "测试二级", L1),
    (L3, 3, "测试三级", L2),
]


async def _seed_tree(db: AsyncSession, as_of: date) -> None:
    """在**事务内**建最小分类树 + 两层各自的 SCD2 成员。

    L1 自身层级有 2 只（``992001/992002``），L3 只有 1 只（``992001``）——
    这样"自身层级 vs 上卷"必然不同，C1 的回归就能被测到（旧实现返回 1）。
    """
    for code, level, name, parent in _TREE_ROWS:
        await db.execute(
            text(
                "INSERT INTO sw_industry_classes "
                "(industry_code, level, industry_name, parent_code) VALUES (:c, :l, :n, :p)"
            ),
            {"c": code, "l": level, "n": name, "p": parent},
        )
    await _insert_periods(
        db,
        [
            _period(L1, "992001.SH", as_of, None, True),
            _period(L1, "992002.SH", as_of, None, True),
        ],
    )
    await _insert_periods(db, [_period(L3, "992001.SH", as_of, None, True)])


@requires_pg
def test_member_source_prefers_own_level_then_rollup() -> None:
    """C1 回归：L1 用**自身层级**（2 只），L3 用自身层级（1 只）；两者不等价且如实标注。"""
    from app.services.sw_drilldown_service import (
        MEMBER_SOURCE_LEVEL,
        get_level_items,
        get_members,
    )

    async def _scenario(db: AsyncSession) -> tuple[Any, Any, Any]:
        await _seed_tree(db, ASOF)
        l1_items = await get_level_items(db, None, as_of=ASOF)
        l1_node = next(i for i in l1_items.items if i.code == L1)
        l3_list = await get_level_items(db, None, level1_code=L1, level2_code=L2, as_of=ASOF)
        l3_node = next(i for i in l3_list.items if i.code == L3)
        members = await get_members(
            db, None, level1_code=L1, level2_code=L2, level3_code=L3, as_of=ASOF
        )
        return l1_node, l3_node, members

    l1_node, l3_node, members = asyncio.run(_with_rollback(_scenario))

    assert l1_node.metrics is not None
    assert l1_node.metrics.member_source == MEMBER_SOURCE_LEVEL
    assert l1_node.metrics.member_count == 2, (
        "L1 必须取自身层级的 2 只；旧实现（硬钉 level=3 上卷）会返回 1 只 —— 这就是 C1"
    )
    assert l1_node.metrics.member_source_note is None

    assert l3_node.metrics is not None
    assert l3_node.metrics.member_source == MEMBER_SOURCE_LEVEL
    assert l3_node.metrics.member_count == 1

    # 末级页：chain 的每一级都带**真实**成分数（不是 0 占位）。
    # L1 = 自身层级 2 只；L2 自身层级无记录 → 上卷（其唯一 L3 只有 1 只）；L3 = 1 只
    assert [c.stock_count for c in members.chain] == [2, 1, 1]
    assert members.metrics is not None and members.metrics.member_count == 1


@requires_pg
def test_rollup_fallback_is_labelled_not_claimed_equivalent() -> None:
    """节点自身层级无记录时退回上卷，并**明确标注**不等价（不静默、不冒充等价）。"""
    from app.services.sw_drilldown_service import (
        MEMBER_SOURCE_ROLLUP,
        ROLLUP_NOTE,
        get_level_items,
    )

    async def _scenario(db: AsyncSession) -> Any:
        for code, level, name, parent in _TREE_ROWS:
            await db.execute(
                text(
                    "INSERT INTO sw_industry_classes "
                    "(industry_code, level, industry_name, parent_code) VALUES (:c,:l,:n,:p)"
                ),
                {"c": code, "l": level, "n": name, "p": parent},
            )
        await _insert_periods(db, [_period(L3, "992001.SH", ASOF, None, True)])
        return await get_level_items(db, None, as_of=ASOF)

    out = asyncio.run(_with_rollback(_scenario))
    node = next(i for i in out.items if i.code == L1)
    assert node.metrics is not None
    assert node.metrics.member_source == MEMBER_SOURCE_ROLLUP
    assert node.metrics.member_count == 1
    assert node.metrics.member_source_note == ROLLUP_NOTE, "上卷必须带「不等于上游」的明确说明"


@requires_pg
def test_all_31_l1_industries_drill_to_l3_with_members() -> None:
    """31 个一级行业**每个**都有 L2、每 L2 有 L3、每 L1 至少 1 个当日成分。

    **自建树 + 自建 as_of，不 skip**（Fix round 1 · 评审 I4）：旧版本在
    ``sw_industry_classes`` 为空时 ``pytest.skip``，而 conftest 从不灌 seed ——
    CI 上这条断言会静默消失、套件照样绿。这里在事务内**真造** 31 个一级行业
    （每个 1 个 L2 + 1 个 L3 + 1 只当日成分），断言 31/31 无断链。
    """

    async def _scenario(db: AsyncSession) -> list[tuple[str, int, int, int]]:
        for i in range(31):
            l1 = f"99{i:02d}000"
            l2 = f"99{i:02d}100"
            l3 = f"99{i:02d}101"
            sym = f"99{i:02d}01"
            for code, level, name, parent in (
                (l1, 1, f"合成一级{i}", None),
                (l2, 2, f"合成二级{i}", l1),
                (l3, 3, f"合成三级{i}", l2),
            ):
                await db.execute(
                    text(
                        "INSERT INTO sw_industry_classes "
                        "(industry_code, level, industry_name, parent_code) VALUES (:c,:l,:n,:p)"
                    ),
                    {"c": code, "l": level, "n": name, "p": parent},
                )
            await _insert_periods(db, [_period(l3, f"{sym}.SH", ASOF, None, True)])
        rows = (
            await db.execute(
                text(
                    """
                    SELECT c1.industry_code AS l1,
                           count(DISTINCT c2.industry_code) AS l2_count,
                           count(DISTINCT c3.industry_code) AS l3_count,
                           count(DISTINCT m.symbol) AS members
                    FROM sw_industry_classes c1
                    LEFT JOIN sw_industry_classes c2
                           ON c2.parent_code = c1.industry_code AND c2.level = 2
                    LEFT JOIN sw_industry_classes c3
                           ON c3.parent_code = c2.industry_code AND c3.level = 3
                    LEFT JOIN sw_industry_member_periods m
                           ON m.industry_code = c3.industry_code
                          AND m.effective_from <= :d
                          AND (m.effective_to IS NULL OR :d < m.effective_to)
                    WHERE c1.level = 1 AND c1.industry_code LIKE '99%'
                    GROUP BY c1.industry_code
                    ORDER BY c1.industry_code
                    """
                ),
                {"d": ASOF},
            )
        ).all()
        return [(r[0], int(r[1]), int(r[2]), int(r[3])) for r in rows]

    rows = asyncio.run(_with_rollback(_scenario))
    assert len(rows) == 31, f"一级行业应为 31 个，实得 {len(rows)}"
    broken = [r for r in rows if r[1] == 0 or r[2] == 0 or r[3] == 0]
    assert broken == [], f"以下一级行业断链（L2/L3/当日成分）：{broken}"


async def _has_real_tree() -> bool:
    rows = await _fetch(
        "SELECT count(*) AS n FROM sw_industry_classes WHERE level = 1 AND industry_code LIKE '1%'"
    )
    return bool(rows and rows[0]["n"])


@requires_pg
def test_seeded_tree_chain_when_available() -> None:
    """库内**真有**申万树时按真实树核对 31/134/346。

    树为空时直接返回（无断言）——"无树"是环境事实，而"31/31 无断链"这条不变量
    已由 ``test_all_31_l1_industries_drill_to_l3_with_members`` 用合成树锁住，
    故本用例不需要 skip 也不会假绿。
    """

    async def _go() -> asyncpg.Record:
        rows = await _fetch(
            """
            SELECT count(*) FILTER (WHERE level = 1) AS l1,
                   count(*) FILTER (WHERE level = 2) AS l2,
                   count(*) FILTER (WHERE level = 3) AS l3
            FROM sw_industry_classes
            WHERE industry_code ~ '^[1-7][0-9]{2}'
            """
        )
        return rows[0]

    row = asyncio.run(_go())
    if not row["l1"]:
        return
    assert int(row["l1"]) == 31, f"真实树 L1 = {row['l1']}，期望 31"
    assert int(row["l2"]) == 134, f"真实树 L2 = {row['l2']}，期望 134"
    assert int(row["l3"]) == 346, f"真实树 L3 = {row['l3']}，期望 346"


@requires_pg
def test_real_tree_l3_all_roll_up_via_parent_code() -> None:
    """真实树里 346 个 L3 **逐个**沿 ``parent_code`` 上卷到某个 L1（读 DB，不是字符串前缀）。"""
    if not asyncio.run(_has_real_tree()):
        return

    async def _go() -> list[asyncpg.Record]:
        # 从每个 L3 出发，递归沿 parent_code 往上走，必须至少命中一个 level=1。
        # 递归项必须**重新取一遍分类树行**（不能把锚点里的 level=3 带上去，
        # 否则向上走到的祖先会一直标着 level=3，`filter (level=1)` 永远为空）
        return await _fetch(
            """
            WITH RECURSIVE up AS (
                SELECT industry_code, parent_code, level,
                       industry_code AS root
                FROM sw_industry_classes WHERE level = 3
              UNION ALL
                SELECT c.industry_code, c.parent_code, c.level, up.root
                FROM sw_industry_classes c JOIN up ON c.industry_code = up.parent_code
            )
            SELECT root AS l3
            FROM up
            GROUP BY root
            HAVING count(*) FILTER (WHERE level = 1) = 0
            ORDER BY root
            """
        )

    unresolved = asyncio.run(_go())
    assert unresolved == [], (
        f"以下 L3 沿真实 parent_code 链上卷不到 L1：{[r['l3'] for r in unresolved[:10]]}"
    )


@requires_pg
def test_chain_validation_rejects_mismatched_pair() -> None:
    """层级不构成父子关系时必须 404（LookupError），而不是返回 0 成员节点。"""
    from app.services.sw_drilldown_service import get_members

    async def _scenario(db: AsyncSession) -> None:
        await _seed_tree(db, ASOF)
        await db.execute(
            text(
                "INSERT INTO sw_industry_classes "
                "(industry_code, level, industry_name, parent_code) "
                "VALUES ('992999', 2, '野二级', NULL)"
            )
        )
        await get_members(
            db, None, level1_code=L1, level2_code="992999", level3_code=L3, as_of=ASOF
        )

    with pytest.raises(LookupError, match="不属于"):
        asyncio.run(_with_rollback(_scenario))


@requires_pg
def test_duplicate_period_key_is_rejected() -> None:
    """唯一键含 ``effective_to`` 后，同键重复行仍必须被数据库拒绝。"""

    async def _scenario(db: AsyncSession) -> None:
        row = [_period("995001", "600009.SH", date(2020, 1, 1), date(2021, 1, 1), False)]
        await _insert_periods(db, row)
        await _insert_periods(db, row)

    with pytest.raises(Exception) as exc_info:
        asyncio.run(_with_rollback(_scenario))
    assert "uq_sw_member_period_code_stock_from_to" in str(exc_info.value)
