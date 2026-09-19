"""概念板块 repo：成分 diff 语义（默认门禁，纯函数）+ 真库聚合/计划守卫（`-m e2e`）。

本文件刻意**不**使用模块级 `pytestmark = pytest.mark.e2e`：diff 语义是本任务的核心不变量
（抓取失败绝不能表现为"成分清空"），必须留在默认 `uv run pytest` 门禁里，而不是躲在
需要真库的 `-m e2e` 之后。异步/真库用例逐个打 `@pytest.mark.e2e`。

`-m e2e` 用例直连 `backend/.env` 指向的 dev DB（`stock_bot`），自建自清的 `TESTBK*`
假板块/成分行进库，结束后（含失败路径）按前缀删除，不触碰任何真实板块数据。
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, text

from app.core.database import async_session_factory, engine
from app.models.quote import DailyQuote
from app.repositories import concept_repo
from app.repositories.concept_repo import diff_members

TODAY = date(2026, 9, 18)

# 假板块一律以 TESTBK 开头，清理/断言都靠这个前缀，避免误伤真实东财板块码（BKxxxx）。
BOARD_PREFIX = "TESTBK"


# ---------------------------------------------------------------------------
# 默认门禁：diff_members 纯函数（不触 DB）
# ---------------------------------------------------------------------------


def test_diff_member_rows_adds_removes_and_keeps() -> None:
    """纯函数形态的 diff 语义（不触 DB）：新增写 add、消失写 remove、留存只刷 last_seen_on。"""
    existing = {"600000": "浦发银行", "600001": "邯郸钢铁"}
    seen = {"600000": "浦发银行", "600002": "东北证券"}
    plan = diff_members(existing, seen, today=TODAY)
    assert plan.added == [{"symbol": "600002", "stock_name": "东北证券"}]
    assert plan.removed == ["600001"]
    assert plan.kept == ["600000"]  # 只 refresh，不写 change
    assert plan.degraded is False


def test_diff_members_empty_seen_is_not_a_wipe() -> None:
    """抓取失败/空页绝不能表现为"成分清空"：调用方据此跳过该板。"""
    plan = diff_members({"600000": "浦发银行"}, seen={}, today=TODAY)
    assert plan.removed == ["600000"] and plan.degraded is True


def test_diff_members_kept_rows_are_refreshed_not_re_added() -> None:
    """留存成分（含改名）只进 kept：不得出现在 added（否则重复 INSERT 撞唯一键）。"""
    plan = diff_members({"600000": "浦发银行"}, {"600000": "浦发银行新名"}, today=TODAY)
    assert plan.added == [] and plan.removed == []
    assert plan.kept == ["600000"]


def test_diff_members_added_carries_stock_name_from_seen() -> None:
    """新增行的 stock_name 取本轮抓到的名字（seen），不是库里的旧值。"""
    plan = diff_members(
        {"600000": "浦发银行"}, {"600000": "浦发银行", "601091": "C沈鼓"}, today=TODAY
    )
    assert plan.added == [{"symbol": "601091", "stock_name": "C沈鼓"}]
    assert plan.kept == ["600000"]


# ---------------------------------------------------------------------------
# `-m e2e`：真库聚合 / 写入语义 / 计划形状守卫
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _cleanup_seeded_rows_and_dispose(
    request: pytest.FixtureRequest,
) -> AsyncGenerator[None, None]:
    """每个 **e2e** 用例后按前缀清掉假数据，并释放引擎（沿用 test_limit_up_repo 的收尾方式）。

    autouse 但按 marker 短路：默认门禁里的 diff 纯函数用例**不许连库**——`uv run pytest`
    在 dev DB 未启动时也必须全绿，否则核心不变量会被环境问题挡住。
    """
    try:
        yield
    finally:
        if request.node.get_closest_marker("e2e") is None:
            return
        async with async_session_factory() as db:
            for table in ("concept_member_changes", "concept_members", "concept_boards"):
                await db.execute(
                    text(f"DELETE FROM {table} WHERE board_code LIKE :prefix"),  # noqa: S608
                    {"prefix": f"{BOARD_PREFIX}%"},
                )
            await db.commit()
        await engine.dispose()


async def _latest_quote_date(db: Any) -> date:
    as_of = (await db.execute(select(func.max(DailyQuote.trade_date)))).scalar_one()
    assert as_of is not None, "daily_quotes 为空，先跑 quotes 回补"
    return as_of


async def _insert_board(db: Any, code: str, name: str, is_active: bool = True) -> None:
    await db.execute(
        text(
            "INSERT INTO concept_boards (board_code, board_name, source, member_count, is_active) "
            "VALUES (:code, :name, 'test', :mc, :active) "
            "ON CONFLICT (board_code) DO UPDATE SET board_name = excluded.board_name, "
            "is_active = excluded.is_active"
        ),
        {"code": code, "name": name, "mc": 0, "active": is_active},
    )


async def _insert_member(
    db: Any, code: str, symbol: str, stock_name: str, stock_id: int | None, seen_on: date
) -> None:
    await db.execute(
        text(
            "INSERT INTO concept_members "
            "(board_code, symbol, stock_name, stock_id, first_seen_on, last_seen_on) "
            "VALUES (:code, :symbol, :name, :sid, :seen, :seen)"
        ),
        {"code": code, "symbol": symbol, "name": stock_name, "sid": stock_id, "seen": seen_on},
    )


async def _quote_by_sign(db: Any, as_of: date, sign: str) -> Any:
    """取当日某一涨跌方向的真实行情行（顺序钉死，跨跑稳定）。"""
    op = {"up": "> 0", "flat": "= 0", "down": "< 0"}[sign]
    order = "DESC" if sign == "up" else "ASC"
    return (
        await db.execute(
            text(
                f"SELECT stock_id, pct_chg FROM daily_quotes "  # noqa: S608 — 枚举白名单
                f"WHERE trade_date = :as_of AND pct_chg {op} "
                f"ORDER BY pct_chg {order}, stock_id LIMIT 1"
            ),
            {"as_of": as_of},
        )
    ).one()


@pytest.mark.e2e
async def test_aggregate_boards_counts_buckets_and_deterministic_order() -> None:
    """§2.3 口径：member_count 含未解析、unresolved 只数 stock_id IS NULL、均价只算有行情家数。"""
    async with async_session_factory() as db:
        as_of = await _latest_quote_date(db)
        up = await _quote_by_sign(db, as_of, "up")
        flat = await _quote_by_sign(db, as_of, "flat")
        down = await _quote_by_sign(db, as_of, "down")  # 当日跌幅最大票 → TESTBKB 均价
        unpriced_id = (
            await db.execute(
                text(
                    "SELECT s.id FROM stocks s WHERE NOT EXISTS ("
                    "  SELECT 1 FROM daily_quotes q WHERE q.stock_id = s.id "
                    "AND q.trade_date = :as_of"
                    ") ORDER BY s.id LIMIT 1"
                ),
                {"as_of": as_of},
            )
        ).scalar_one()
        assert unpriced_id is not None, "找不到当日无行情的股票，用例前置不成立"

        # TESTBK：1 未解析 + 1 已解析但当日无行情 + 涨/平/跌各 1 → 涨跌家数与均价可逐项对拍。
        await _insert_board(db, "TESTBK", "用例主板块")
        await _insert_member(db, "TESTBK", "XNULL", "未解析", None, TODAY)
        await _insert_member(db, "TESTBK", "XUNPRICED", "无行情", unpriced_id, TODAY)
        await _insert_member(db, "TESTBK", "XUP", "涨", up.stock_id, TODAY)
        await _insert_member(db, "TESTBK", "XFLAT", "平", flat.stock_id, TODAY)
        await _insert_member(db, "TESTBK", "XDOWN", "跌", down.stock_id, TODAY)
        # 排序锚点：最高均价 / 最低均价 / 两个均价为 NULL 的板（测 board_code 上升序 tiebreak）。
        await _insert_board(db, "TESTBKA", "最高")
        await _insert_member(db, "TESTBKA", "XTOP", "最高", up.stock_id, TODAY)
        await _insert_board(db, "TESTBKB", "最低")
        await _insert_member(db, "TESTBKB", "XLOW", "最低", down.stock_id, TODAY)
        await _insert_board(db, "TESTBKC", "全未解析")
        await _insert_member(db, "TESTBKC", "XN1", "未解析", None, TODAY)
        await _insert_board(db, "TESTBKD", "全未解析2")
        await _insert_member(db, "TESTBKD", "XN2", "未解析", None, TODAY)
        # 停用板：is_active=false 必须被 WHERE b.is_active 过滤掉（成分保留但不参与聚合）。
        await _insert_board(db, "TESTBKX", "停用板", is_active=False)
        await _insert_member(db, "TESTBKX", "XOFF", "停用", up.stock_id, TODAY)

        rows = await concept_repo.aggregate_boards(db, as_of, limit=1000, offset=0)

    by_code = {r["board_code"]: r for r in rows}
    assert "TESTBKX" not in by_code, "is_active=false 的板块不得出现在聚合结果里"

    main = by_code["TESTBK"]
    assert main["board_name"] == "用例主板块"
    assert main["member_count"] == 5, "member_count 是全部成分行数（含未解析/无行情）"
    assert main["unresolved_count"] == 1, "unresolved 只数 stock_id IS NULL"
    assert main["priced_count"] == 3, "priced_count 只数当日有非空 pct_chg 的成分"
    assert (main["up_count"], main["flat_count"], main["down_count"]) == (1, 1, 1)
    assert main["avg_pct"] == pytest.approx(
        (float(up.pct_chg) + 0.0 + float(down.pct_chg)) / 3, abs=1e-9
    )

    order = [r["board_code"] for r in rows if r["board_code"].startswith(BOARD_PREFIX)]
    # avg_pct DESC NULLS LAST + board_code ASC tiebreak 的确定性顺序：
    # 最高(涨幅最大票) > 主板块(涨/平/跌均值) > 最低(跌幅最大票) > 两个 NULL(按码升序)。
    assert order == ["TESTBKA", "TESTBK", "TESTBKB", "TESTBKC", "TESTBKD"]


@pytest.mark.e2e
async def test_upsert_members_writes_diff_and_degrades_safely() -> None:
    """整板应用：新增/刷新/删除一次到位；空 seen（抓取失败）不动任何行、不写 change。"""
    tomorrow = TODAY + timedelta(days=1)
    async with async_session_factory() as db:
        await _insert_board(db, "TESTBKUP", "写入用例")

        # 首次：两个新成分（客户端形状用 name，仓库层归一化为 stock_name）→ 2 add。
        counts = await concept_repo.upsert_members(
            db,
            "TESTBKUP",
            [{"symbol": "600000", "name": "浦发银行"}, {"symbol": "000001", "name": "平安银行"}],
            TODAY,
        )
        assert counts == (2, 0, 0)
        await db.commit()

        # 二次：留存一只（刷新 last_seen_on）+ 新增一只未收录（stock_id 必须为 NULL）+ 剔除一只。
        counts = await concept_repo.upsert_members(
            db,
            "TESTBKUP",
            [{"symbol": "000001", "name": "平安银行"}, {"symbol": "999999", "name": "未收录"}],
            tomorrow,
        )
        assert counts == (1, 1, 1)
        await db.commit()

        rows = (
            (
                await db.execute(
                    text(
                        "SELECT symbol, stock_name, stock_id, first_seen_on, last_seen_on "
                        "FROM concept_members WHERE board_code = 'TESTBKUP' ORDER BY symbol"
                    )
                )
            )
            .mappings()
            .all()
        )
        assert [r["symbol"] for r in rows] == ["000001", "999999"], "600000 必须被删除"
        kept, added = rows
        assert kept["first_seen_on"] == TODAY and kept["last_seen_on"] == tomorrow, (
            "留存只刷 last_seen_on"
        )
        assert kept["stock_id"] is not None, "名录命中的成分必须解析出 stock_id"
        assert added["first_seen_on"] == tomorrow and added["last_seen_on"] == tomorrow
        assert added["stock_id"] is None, "名录未收录 → stock_id 留 NULL，不阻塞成分采集"

        changes = (
            (
                await db.execute(
                    text(
                        "SELECT observed_on, symbol, change_type FROM concept_member_changes "
                        "WHERE board_code = 'TESTBKUP' ORDER BY observed_on, symbol"
                    )
                )
            )
            .mappings()
            .all()
        )
        # 首次 2 行 add；二次 1 行 add + 1 行 remove（留存不写 change；唯一键含 observed_on）。
        assert sorted((c["observed_on"], c["symbol"], c["change_type"]) for c in changes) == sorted(
            [
                (TODAY, "600000", "add"),
                (TODAY, "000001", "add"),
                (tomorrow, "999999", "add"),
                (tomorrow, "600000", "remove"),
            ]
        )

        # 三次：抓取失败（空 seen）→ degraded，绝不当成"全成分剔除"。
        counts = await concept_repo.upsert_members(db, "TESTBKUP", [], TODAY)
        assert counts == (0, 0, 0)
        await db.commit()

        survivors = (
            (
                await db.execute(
                    text(
                        "SELECT symbol, last_seen_on FROM concept_members "
                        "WHERE board_code = 'TESTBKUP' ORDER BY symbol"
                    )
                )
            )
            .mappings()
            .all()
        )
    assert [(s["symbol"], s["last_seen_on"]) for s in survivors] == [
        ("000001", tomorrow),
        ("999999", tomorrow),
    ], "空 seen 不得删除成分、不得刷新 last_seen_on"


@pytest.mark.e2e
async def test_aggregate_plan_has_no_whole_table_probe() -> None:
    """计划形状守卫：聚合不得全表扫 daily_quotes/stocks，也不得逐票 probe 整表。

    只断言**形状**（节点类型 / 索引条件包含的列），不断言具体索引名——同列等价索引
    （uq_/idx_）规划器按 OID 任选，钉名字会在换索引时误红（test_limit_up_repo 的教训）。
    """
    async with async_session_factory() as db:
        as_of = await _latest_quote_date(db)
        raw = (
            await db.execute(
                text("EXPLAIN (FORMAT JSON) " + concept_repo.build_aggregate_sql_for_explain()),
                {"as_of": as_of, "limit": 50, "offset": 0},
            )
        ).scalar_one()
    plan = json.loads(raw) if isinstance(raw, str) else raw
    nodes: list[dict[str, Any]] = []
    _walk(plan[0]["Plan"], nodes)

    seq_scans = {n.get("Relation Name") for n in nodes if n["Node Type"] == "Seq Scan"}
    assert "stocks" not in seq_scans, f"stocks 全表扫描会随名录增长退化：{seq_scans}"
    assert "daily_quotes" not in seq_scans, f"daily_quotes 全表扫描：{seq_scans}"

    # daily_quotes 只能按日收敛（4.37M 行表，任何访问都必须带 trade_date 条件）。
    for node in nodes:
        if node.get("Relation Name") == "daily_quotes" and node["Node Type"].startswith("Index"):
            assert "trade_date" in node.get("Index Cond", ""), node

    # Nested Loop 的内侧不得是全表扫描；若它 probe daily_quotes，必须是 (stock_id, trade_date)
    # 唯一键点查（"逐票 probe" 只有在拿唯一键点时才是可接受的最坏情况）。
    for node in nodes:
        if node["Node Type"].startswith("Nested Loop"):
            for inner in node.get("Plans", [])[1:]:
                assert inner["Node Type"] != "Seq Scan", f"Nested Loop 内侧全表扫描：{inner}"
                if inner.get("Relation Name") == "daily_quotes":
                    assert "stock_id" in inner.get("Index Cond", ""), inner


@pytest.mark.e2e
async def test_member_history_stats_keeps_unknown_never_broken_as_none() -> None:
    """限价缺失 → `never_broken is None`（不可判），绝不能被写成 False（"已开板"假信号）。"""
    board = "TESTBKHIST"
    async with async_session_factory() as db:
        # "全限价"样本 = **行情历史整体落在限价覆盖窗口内**的股票（本库 stock_price_limits
        # 只有近 28 个交易日，老股票的早期行情行永远缺限价，不可能"全限价"）。
        # 原写法（对整个 4.37M 行 daily_quotes 做 NOT EXISTS 相关子查询）实测 >15 分钟不收敛，
        # 改为：先在限价窗口内比计数（走 trade_date 索引，仅 15 万行），再排除窗口外有行情的票。
        complete_id = (
            await db.execute(
                text(
                    "WITH b AS (SELECT min(trade_date) AS lo, max(trade_date) AS hi "
                    "FROM stock_price_limits), "
                    "lc AS (SELECT stock_id, count(*) AS n FROM stock_price_limits "
                    "GROUP BY stock_id), "
                    "qc AS (SELECT q.stock_id, count(*) AS n FROM daily_quotes q, b "
                    "WHERE q.trade_date BETWEEN b.lo AND b.hi GROUP BY q.stock_id) "
                    "SELECT lc.stock_id FROM lc JOIN qc USING (stock_id) CROSS JOIN b "
                    "WHERE qc.n = lc.n AND NOT EXISTS (SELECT 1 FROM daily_quotes q2 "
                    "WHERE q2.stock_id = lc.stock_id "
                    "AND (q2.trade_date < b.lo OR q2.trade_date > b.hi)) "
                    "ORDER BY lc.n DESC, lc.stock_id LIMIT 1"
                )
            )
        ).scalar_one_or_none()
        missing_id = (
            await db.execute(
                text(
                    "SELECT q.stock_id FROM daily_quotes q "
                    "LEFT JOIN stock_price_limits l ON l.stock_id = q.stock_id "
                    "  AND l.trade_date = q.trade_date "
                    "WHERE l.id IS NULL GROUP BY q.stock_id "
                    "ORDER BY count(*) DESC, q.stock_id LIMIT 1"
                )
            )
        ).scalar_one_or_none()
        if complete_id is None or missing_id is None:
            pytest.skip("dev DB 缺全限价/缺限价样本，用例前置不成立")

        await _insert_board(db, board, "历史统计用例")
        await _insert_member(db, board, "XCOMPLETE", "全限价", complete_id, TODAY)
        await _insert_member(db, board, "XMISSING", "缺限价", missing_id, TODAY)
        await _insert_member(db, board, "XNULLID", "未解析", None, TODAY)

        stats = await concept_repo.member_history_stats(db, board)

        ref = (
            await db.execute(
                text(
                    "SELECT count(*) AS days, "
                    "(array_agg(q.open ORDER BY q.trade_date))[1] AS first_open, "
                    "bool_and(q.close >= l.up_limit - 0.005) AS all_lu "
                    "FROM daily_quotes q JOIN stock_price_limits l ON l.stock_id = q.stock_id "
                    "  AND l.trade_date = q.trade_date WHERE q.stock_id = :sid"
                ),
                {"sid": complete_id},
            )
        ).one()

    assert set(stats) == {"XCOMPLETE", "XMISSING"}, "stock_id IS NULL 的成分不进历史统计"
    assert stats["XCOMPLETE"]["listed_trade_days"] == ref.days, (
        "listed_trade_days = 有行情的交易日数"
    )
    if ref.first_open is None:
        assert stats["XCOMPLETE"]["first_open"] is None
    else:
        assert stats["XCOMPLETE"]["first_open"] == pytest.approx(float(ref.first_open))
    assert stats["XCOMPLETE"]["never_broken"] == ref.all_lu
    assert stats["XCOMPLETE"]["never_broken"] is False, "有非涨停交易日 → 确定已开板（false）"
    assert stats["XMISSING"]["never_broken"] is None, "任一行缺限价 → 不可判，必须是 None"


@pytest.mark.e2e
async def test_upsert_boards_refreshes_and_deactivates_missing() -> None:
    """名录 upsert 幂等（刷 name/member_count、复活 is_active）；未出现的板置 false 且保留成分。"""
    async with async_session_factory() as db:
        got = await concept_repo.upsert_boards(
            db,
            [
                {"board_code": "TESTBKA", "board_name": "用例甲", "member_total": 3},
                {"board_code": "TESTBKB", "board_name": "用例乙", "member_total": None},
            ],
            TODAY,
        )
        assert got == 2
        await db.commit()

        got = await concept_repo.upsert_boards(
            db, [{"board_code": "TESTBKA", "board_name": "用例甲改", "member_total": 5}], TODAY
        )
        assert got == 1, "已存在的板块再次 upsert 仍回 rowcount（覆盖刷新）"

        # 只把 TESTBKA 留在本轮名单里：真实在用的板块全部并入 keep，避免误停用线上板块。
        active = set(
            (
                await db.execute(
                    text(
                        "SELECT board_code FROM concept_boards "
                        "WHERE is_active AND board_code NOT LIKE :p"
                    ),
                    {"p": f"{BOARD_PREFIX}%"},
                )
            )
            .scalars()
            .all()
        )
        got = await concept_repo.deactivate_missing_boards(db, active | {"TESTBKA"})
        await db.commit()
        assert got == 1, "本轮未出现的只有 TESTBKB → 恰停用一个"

        state = dict(
            (
                await db.execute(
                    text(
                        "SELECT board_code, (board_name, member_count, is_active) "
                        "FROM concept_boards WHERE board_code IN ('TESTBKA','TESTBKB')"
                    )
                )
            ).all()
        )
    assert state["TESTBKA"] == ("用例甲改", 5, True)
    assert state["TESTBKB"] == ("用例乙", None, False), "未出现 → 停用但保留行（成分也不删）"


def _walk(node: dict[str, Any], out: list[dict[str, Any]]) -> None:
    out.append(node)
    for child in node.get("Plans", []):
        _walk(child, out)
