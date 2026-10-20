"""启动自愈的**增量**契约：已覆盖的指数不许再发请求.

2026-10-21 线上形态：每次重启 api 都看到
``Index daily ingest done: ts_code=399001.SZ fetched=242 upserted=242`` × 6 个指数。
个股行情（`list_stocks_missing_daily_coverage`）与基本面（`existing_dates` 差集）早就
是「先查缺口再拉」，只有指数日线这一支是**无条件拉一年**再 upsert —— 白烧 TuShare 额度、
白写一年 JSONL 快照。本文件把「0 次调用」变成可红可绿的断言。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services import data_init

ASOF = date(2026, 10, 20)


def test_window_none_when_already_covered():
    # 已覆盖到上一交易日 → 不发请求
    assert data_init._index_fetch_window(ASOF, ASOF) is None
    # 库里有未来脏行也算已覆盖（不回头补）
    assert data_init._index_fetch_window(ASOF + timedelta(days=3), ASOF) is None


def test_window_first_run_is_one_year():
    assert data_init._index_fetch_window(None, ASOF) == ("20251020", "20261020")


def test_window_gap_has_small_overlap():
    # 落后 40 天 → 只从「最大日 - 重叠 7 天」拉，不是从一年前拉
    existing = ASOF - timedelta(days=40)
    start, end = data_init._index_fetch_window(existing, ASOF)
    assert end == "20261020"
    assert start == (existing - timedelta(days=data_init.INDEX_BACKFILL_OVERLAP_DAYS)).strftime(
        "%Y%m%d"
    )


def test_window_never_exceeds_one_year_lookback():
    # 库里很久没更新（超过一年）→ 窗口仍以东端一年为下限，不被撕成多年
    start, end = data_init._index_fetch_window(ASOF - timedelta(days=900), ASOF)
    assert start == (ASOF - timedelta(days=365)).strftime("%Y%m%d")
    assert end == "20261020"


@pytest.mark.asyncio
async def test_no_api_call_when_all_indices_covered(monkeypatch):
    """全覆盖时必须**零次** ingest_index_daily 调用（这就是重启烧额度的根因）。"""
    from app.repositories import index_repo

    calls: list[tuple[str, str, str]] = []

    class FakeService:
        async def ingest_index_daily(self, db, ts_code, start_date, end_date):  # noqa: ANN001, ANN201
            calls.append((ts_code, start_date, end_date))
            return {"upserted": 0}

    class FakeSession:
        async def __aenter__(self):  # noqa: ANN204
            return self

        async def __aexit__(self, *exc):  # noqa: ANN002, ANN204
            return False

        async def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
            class _Empty:
                def all(self):
                    return []

            return _Empty()

    async def covered(db, codes):  # noqa: ANN001, ANN201
        return {code: ASOF for code in codes}

    monkeypatch.setattr(data_init, "last_completed_trading_day", lambda: ASOF)
    monkeypatch.setattr(data_init, "async_session_factory", FakeSession)
    monkeypatch.setattr(index_repo, "max_trade_date_by_code", covered)

    await data_init._ensure_trailing_one_year_index_daily(FakeService())

    assert calls == []


@pytest.mark.asyncio
async def test_only_stale_index_is_fetched(monkeypatch):
    """只有落后的那个指数发请求，且窗口带着小幅重叠。"""
    from app.repositories import index_repo
    from app.services.market_service import _TARGET_INDICES

    calls: list[tuple[str, str, str]] = []

    class FakeService:
        async def ingest_index_daily(self, db, ts_code, start_date, end_date):  # noqa: ANN001, ANN201
            calls.append((ts_code, start_date, end_date))
            return {"upserted": 5}

    class FakeSession:
        async def __aenter__(self):  # noqa: ANN204
            return self

        async def __aexit__(self, *exc):  # noqa: ANN002, ANN204
            return False

        async def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
            class _Empty:
                def all(self):
                    return []

            return _Empty()

    stale = _TARGET_INDICES[0]["ts_code"]
    stale_max = ASOF - timedelta(days=10)

    async def mixed(db, codes):  # noqa: ANN001, ANN201
        return {code: (stale_max if code == stale else ASOF) for code in codes}

    monkeypatch.setattr(data_init, "last_completed_trading_day", lambda: ASOF)
    monkeypatch.setattr(data_init, "async_session_factory", FakeSession)
    monkeypatch.setattr(index_repo, "max_trade_date_by_code", mixed)

    await data_init._ensure_trailing_one_year_index_daily(FakeService())

    assert len(calls) == 1
    code, start, end = calls[0]
    assert code == stale
    assert end == ASOF.strftime("%Y%m%d")
    assert start == (stale_max - timedelta(days=data_init.INDEX_BACKFILL_OVERLAP_DAYS)).strftime(
        "%Y%m%d"
    )


# ── 个股覆盖回填：只补尾巴，不重拉三年 ──────────────────────────────────────


def test_quote_backfill_starts_at_gap_for_tail_only():
    """尾部缺口（停牌股）→ 从库里最大日 + 1 开始，不重拉三年.

    2026-10-21 实测：21 只 ST 停牌股每次重启都被这样重拉 13,543 行。
    """
    item = {
        "expected_start": ASOF - timedelta(days=1095),
        "current_min": ASOF - timedelta(days=1095),
        "current_max": ASOF - timedelta(days=112),  # 停牌 4 个月
        "asof_date": ASOF,
    }
    assert data_init._quote_backfill_start(item) == item["current_max"] + timedelta(days=1)


def test_quote_backfill_full_window_when_no_rows():
    item = {"expected_start": ASOF - timedelta(days=1095), "current_max": None}
    assert data_init._quote_backfill_start(item) == item["expected_start"]


def test_quote_backfill_full_window_when_head_missing():
    """头部也缺（次新股上市日后才有行情）→ 必须从 expected 拉."""
    item = {
        "expected_start": ASOF - timedelta(days=1095),
        "current_min": ASOF - timedelta(days=1095) + timedelta(days=40),
        "current_max": ASOF,
    }
    assert data_init._quote_backfill_start(item) == item["expected_start"]


@pytest.mark.asyncio
async def test_tail_gap_stock_fetches_only_the_gap(monkeypatch):
    """接线断言：真正的 ingest 调用必须用「缺口起点」，不是 expected_start.

    只测纯函数不够——变异「把 _quote_backfill_start 换回 expected_start」时纯函数测试仍绿，
    真正烧额度的是这条调用参数。
    """
    calls: list[dict] = []
    item = {
        "stock_id": 7,
        "exchange": "Shanghai_Stocks",
        "symbol": "600666",
        "expected_start": ASOF - timedelta(days=1095),
        "current_min": ASOF - timedelta(days=1095),
        "current_max": ASOF - timedelta(days=112),
        "asof_date": ASOF,
        "reason": "gap",
        "row_count": 600,
    }

    class FakeService:
        async def list_stocks_missing_daily_coverage(self, db, **kw):  # noqa: ANN001, ANN201
            return [item]

        async def ingest_daily_quotes_for_stock(self, db, **kw):  # noqa: ANN001, ANN201
            calls.append(kw)
            return {"upserted": 0}

    class FakeSession:
        async def __aenter__(self):  # noqa: ANN204
            return self

        async def __aexit__(self, *exc):  # noqa: ANN002, ANN204
            return False

        async def commit(self):  # noqa: ANN201
            return None

    monkeypatch.setattr(data_init, "async_session_factory", FakeSession)

    await data_init._ensure_trailing_three_year_daily_quotes(FakeService())

    assert len(calls) == 1
    assert calls[0]["start_date"] == item["current_max"] + timedelta(days=1)
    assert calls[0]["end_date"] == ASOF


# ── 最近 35 个交易日的**内部缺口**（max 已到 asof 也可能缺天） ────────────────


def test_recent_coverage_window_none_when_complete():
    recent = [ASOF - timedelta(days=i) for i in range(35)]
    assert data_init._recent_coverage_window(recent, set(recent), ASOF) is None


def test_recent_coverage_window_returns_gap_minus_overlap():
    recent = [ASOF - timedelta(days=i) for i in range(35)]
    missing = recent[10]
    start, end = data_init._recent_coverage_window(recent, set(recent) - {missing}, ASOF)
    assert end == ASOF.strftime("%Y%m%d")
    assert start == (missing - timedelta(days=data_init.INDEX_BACKFILL_OVERLAP_DAYS)).strftime(
        "%Y%m%d"
    )


def test_merge_windows_takes_earlier_start_and_later_end():
    assert data_init._merge_windows(("20261001", "20261010"), None) == ("20261001", "20261010")
    assert data_init._merge_windows(None, ("20260901", "20261020")) == ("20260901", "20261020")
    assert data_init._merge_windows(("20261001", "20261010"), ("20260901", "20261005")) == (
        "20260901",
        "20261010",
    )


@pytest.mark.asyncio
async def test_internal_recent_gap_triggers_small_repair(monkeypatch):
    """max 已到 asof、但最近 35 天里缺一天 → 只补「缺口起点 - 7 天 … asof」，不拉一年."""
    from app.repositories import index_repo
    from app.services.market_service import _TARGET_INDICES

    recent = [ASOF - timedelta(days=i) for i in range(35)]
    broken = _TARGET_INDICES[0]["ts_code"]
    missing = recent[10]
    calls: list[tuple[str, str, str]] = []

    class FakeService:
        async def ingest_index_daily(self, db, ts_code, start_date, end_date):  # noqa: ANN001, ANN201
            calls.append((ts_code, start_date, end_date))
            return {"upserted": 1}

    class FakeSession:
        async def __aenter__(self):  # noqa: ANN204
            return self

        async def __aexit__(self, *exc):  # noqa: ANN002, ANN204
            return False

    async def all_covered(db, codes):  # noqa: ANN001, ANN201
        return {code: ASOF for code in codes}

    async def recent_days(db, asof, limit):  # noqa: ANN001, ANN201
        assert limit == data_init.INDEX_RECENT_COVERAGE_DAYS
        return recent

    async def covered(db, codes, start, end):  # noqa: ANN001, ANN201
        out = {code: set(recent) for code in codes}
        out[broken] = set(recent) - {missing}
        return out

    monkeypatch.setattr(data_init, "last_completed_trading_day", lambda: ASOF)
    monkeypatch.setattr(data_init, "async_session_factory", FakeSession)
    monkeypatch.setattr(index_repo, "max_trade_date_by_code", all_covered)
    monkeypatch.setattr(index_repo, "list_recent_quote_days", recent_days)
    monkeypatch.setattr(index_repo, "trade_dates_by_code", covered)

    await data_init._ensure_trailing_one_year_index_daily(FakeService())

    assert len(calls) == 1
    code, start, end = calls[0]
    assert code == broken
    assert end == ASOF.strftime("%Y%m%d")
    assert start == (missing - timedelta(days=data_init.INDEX_BACKFILL_OVERLAP_DAYS)).strftime(
        "%Y%m%d"
    )
