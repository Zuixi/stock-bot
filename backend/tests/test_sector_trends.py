"""Task 6b：`GET /market/sector-trends`（读时计算，不物化）+ 完整日闸门。

覆盖点（按 brief 与 controller rulings）：

1. **`eod_full` 完整日闸门**（Ruling 21）：窗口内**只有 `poll` 行**的日子不得进序列，
   必须落进 `missing_days`；`available_from` = 该维度最早的 `eod_full` 日（可为 None：
   表里根本没有完整日时**不得**假装有）。
2. **缺日 ≠ 0 流入**（Ruling 7）：缺日不进任何聚合，`missing_days` 里的日子既不算
   "净流入 0"也不打断/续接连击 —— 只在请求窗口与完整日**交集**上统计。
3. **归属显式**（Ruling 22）：`industry`/`region` → `attribution="current_sw_snapshot"`
   （UI 文案"按当前分类回溯，非当时成分"）；`concept` → `attribution="point_in_time"`
   （成分由 `concept_members` 当前态 + `concept_member_changes` **反推**时点集合，
   绝不用当前成分冒充历史成分）。
4. **`top_n` 显式**（Ruling 6）：默认 20、有界；上榜榜序 `main_net_inflow DESC,
   board_code ASC`（确定性 tiebreak）；`连续上榜天数` 在**完整日**上计算。
5. **窗口守恒**：`days` 缺省 60 / 上限 120；`series` 升序；缓存键含
   `dimension`/`days`/`top_n`；空结果不缓存。

**离线**：默认门禁用例全部打桩（repo 替身 / 假 session），不碰 PG、Redis、网络。
真库用例标 ``e2e``（哨兵日 1999-01-04 自建自清，且**不碰任何真实交易日**）。
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from app.api.deps import get_cache
from app.main import app
from app.repositories import market_data_repo
from app.services import market_data_service as mds

# 1999-01-04 是**哨兵日**：早于任何真实快照（顺带不改变 `max(trade_date)`，
# 于是不影响 `/market/sector-moneyflow` 的"最近日"语义），真库用例自建自清。
SENTINEL = date(1999, 1, 4)
SENTINEL_2 = date(1999, 1, 5)
# 工作窗口（与真实历史日期无关，桩数据自洽即可）
D1, D2, D3, D4 = date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 9)


def _snap(
    day: date,
    code: str,
    *,
    name: str | None = None,
    inflow: float | None = 1.0e9,
    pct: float | None = 1.0,
    up: int | None = 10,
    down: int | None = 3,
    capture_kind: str = market_data_repo.EOD_FULL_CAPTURE,
    dimension: str = "industry",
) -> SimpleNamespace:
    """一行 ``sector_moneyflow_snapshots``（ORM 行替身：直读属性）。"""
    return SimpleNamespace(
        trade_date=day,
        dimension=dimension,
        board_code=code,
        board_name=name or f"板块{code}",
        pct_change=pct,
        main_net_inflow=inflow,
        super_large_net=None,
        large_net=None,
        main_net_ratio=None,
        up_count=up,
        down_count=down,
        lead_stock_name="龙头",
        lead_stock_code="600000",
        lead_stock_pct=1.0,
        amount=None,
        flat_count=1,
        capture_kind=capture_kind,
    )


class _RecordingCache:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.gets: list[str] = []
        self.sets: list[tuple[str, Any, int | None]] = []

    async def get(self, key: str) -> Any:
        self.gets.append(key)
        return self.store.get(key)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self.sets.append((key, value, ttl))
        self.store[key] = value


def _days(offsets: list[date]) -> list[date]:
    return list(offsets)


# ---------------------------------------------------------------------------
# 1. 完整日闸门 + 缺日语义（纯函数，不触 DB）
# ---------------------------------------------------------------------------


def test_available_from_is_earliest_eod_full_day_not_earliest_row() -> None:
    """`available_from` = 最早**完整日**；一个完整日都没有 → `None`（不假装）。"""
    assert mds.resolve_available_from([D1, D2], [D1, D2, D3]) == D1
    assert mds.resolve_available_from([], [D1, D2]) is None


def test_missing_days_lists_poll_only_days_and_excludes_them_from_samples() -> None:
    """`poll`-only 日必须落进 `missing_days`，且**不得**出现在序列的 samples 里。"""
    trading_days = [D1, D2, D3]
    rows = [
        _snap(D1, "BK0001"),  # eod_full
        _snap(D2, "BK0001", capture_kind=market_data_repo.POLL_CAPTURE),  # 只有 poll
        # D3 完全无行
    ]
    complete = mds.eod_full_days_from_rows(rows)
    assert complete == {D1}
    assert mds.missing_eod_days(trading_days, complete) == [D2.isoformat(), D3.isoformat()]
    assert mds.intersect_complete(trading_days, complete) == [D1]


def test_missing_day_is_not_zero_inflow() -> None:
    """缺日**不进**聚合：D2 缺 → 不产生 `main_net_inflow=0` 的伪行，连击也不被它打断。

    构造：D1/D3 有完整日（BK0001 连续两日净流入），D2 只有 poll 行（缺）。
    正确口径下 BK0001 = 2 天净流入连击（D1→D3 在**完整日序列**上相邻）；
    "缺日当 0"会把它算成 0 连击（或 1 天）。
    """
    rows = [
        _snap(D1, "BK0001", inflow=2.0e9),
        _snap(D2, "BK0001", inflow=-9.0e9, capture_kind=market_data_repo.POLL_CAPTURE),
        _snap(D3, "BK0001", inflow=1.0e9),
    ]
    complete_days = [D1, D3]
    payload = mds.sector_trends_payload(
        rows=rows,
        complete_days=complete_days,
        all_complete_days=[D1, D3],
        trading_days=[D1, D2, D3],
        dimension="industry",
        days=60,
        top_n=20,
    )

    assert payload["available_from"] == D1
    assert payload["window"]["samples"] == 2
    # D2 只有 poll 行 → 缺完整日；D1/D3 都在 complete_days 里 → 不缺
    assert payload["missing_days"] == [D2.isoformat()]
    board = payload["boards"][0]
    assert board["board_code"] == "BK0001"
    assert board["main_net_inflow"] == 1.0e9  # **最后完整日**的值，不是缺日的 0
    assert board["streak_inflow"] == 2
    assert board["streak_outflow"] == 0
    assert [p["trade_date"] for p in board["series"]] == [D1, D3]


def test_top_n_bounded_and_ranking_includes_only_complete_days() -> None:
    """上榜榜序 = `main_net_inflow DESC, board_code ASC`；`top_n` 之外的板连击为 0。"""
    rows = [
        _snap(D1, "BK0003", inflow=3.0e9),
        _snap(D1, "BK0001", inflow=5.0e9),
        _snap(D1, "BK0002", inflow=5.0e9),  # 与 BK0001 同额 → 板码升序定序
    ]
    payload = mds.sector_trends_payload(
        rows=rows,
        complete_days=[D1],
        all_complete_days=[D1],
        trading_days=[D1],
        dimension="industry",
        days=60,
        top_n=2,
    )
    ranked = mds.rank_boards(rows, D1, top_n=2)
    assert [r.board_code for r in ranked] == ["BK0001", "BK0002"]
    assert payload["top_n"] == 2
    stats = {b["board_code"]: b for b in payload["boards"]}
    assert stats["BK0001"]["streak_on_top_n"] == 1
    assert stats["BK0002"]["streak_on_top_n"] == 1
    assert stats["BK0003"]["streak_on_top_n"] == 0  # 只有 1 天且不在 Top-2 → 未上榜


def test_leader_change_count_and_attribution_fields() -> None:
    """龙头更替次数 = 完整日相邻两天榜首变化次数；`attribution` 必填且随维度变化。"""
    rows = [
        _snap(D1, "BK0001", inflow=5.0e9),
        _snap(D1, "BK0002", inflow=1.0e9),
        _snap(D2, "BK0001", inflow=1.0e9),
        _snap(D2, "BK0002", inflow=5.0e9),
        _snap(D3, "BK0002", inflow=6.0e9),
    ]
    payload = mds.sector_trends_payload(
        rows=rows,
        complete_days=[D1, D2, D3],
        all_complete_days=[D1, D2, D3],
        trading_days=[D1, D2, D3],
        dimension="industry",
        days=60,
        top_n=20,
    )
    # D1 龙头 BK0001 → D2 BK0002（1 次）→ D3 仍 BK0002（0 次）
    assert payload["leader_change_count"] == 1
    assert payload["leader_changes"] == [
        {"trade_date": D2, "board_code": "BK0002", "board_name": "板块BK0002"}
    ]
    assert payload["attribution"] == "current_sw_snapshot"

    concept = mds.sector_trends_payload(
        rows=[_snap(D1, "BK0896", dimension="concept")],
        complete_days=[D1],
        all_complete_days=[D1],
        trading_days=[D1],
        dimension="concept",
        days=60,
        top_n=20,
    )
    assert concept["attribution"] == "point_in_time"


def test_point_in_time_concept_membership_reconstruction() -> None:
    """概念时点成分**反推**：当前态 - 之后的 add + 之后的 remove。

    构造（BK0896）：S1 从 09-20 起在板；S2 于 09-22 被 **add**；S3 于 09-22 被 **remove**。
    - 09-21 时点成分 = {S1, S3}
    - 09-22 时点成分 = {S1, S2}
    用当前成分（{S1,S2}）冒充 09-21 就会把 S2 的涨停算进 09-21。
    """
    current = {"BK0896": {"S1", "S2"}}
    d20, d21, d22 = date(2026, 9, 20), date(2026, 9, 21), date(2026, 9, 22)
    changes = [
        {"observed_on": d20, "board_code": "BK0896", "symbol": "S1", "change_type": "add"},
        {"observed_on": d22, "board_code": "BK0896", "symbol": "S2", "change_type": "add"},
        {"observed_on": d22, "board_code": "BK0896", "symbol": "S3", "change_type": "remove"},
    ]
    sets = mds.concept_membership_snapshots(current, changes, on_days=[d21, d22])
    assert sets[d21] == {"BK0896": {"S1", "S3"}}
    assert sets[d22] == {"BK0896": {"S1", "S2"}}


def test_concept_lu_count_uses_point_in_time_membership() -> None:
    """板块涨停家数按**时点成分**归属：09-21 的 S2 涨停不计入（当时 S2 还不在板）。"""
    facts = [
        {"trade_date": date(2026, 9, 21), "symbol": "S1"},
        {"trade_date": date(2026, 9, 21), "symbol": "S2"},
        {"trade_date": date(2026, 9, 22), "symbol": "S2"},
    ]
    membership = {
        date(2026, 9, 21): {"BK0896": {"S1", "S3"}},
        date(2026, 9, 22): {"BK0896": {"S1", "S2"}},
    }
    counts = mds.limit_up_counts_by_board(facts, membership)
    assert counts[(date(2026, 9, 21), "BK0896")] == 1  # 只有 S1
    assert counts[(date(2026, 9, 22), "BK0896")] == 1  # 只有 S2（S1 当日未涨停）


def test_no_eod_full_day_means_no_available_from_and_never_zero_inflow() -> None:
    """表里没有任何 `eod_full` 行 → `available_from=None`、序列空、全窗口缺日。

    **不得**把 poll 行当成有数据、更不得给 `main_net_inflow=0` 的伪行（当前真库正是
    这个状态：15:05 EOD job 上线前 `capture_kind='eod_full'` 行数为 0）。
    """
    rows = [
        _snap(D1, "BK0001", capture_kind=market_data_repo.POLL_CAPTURE),
        _snap(D2, "BK0001", capture_kind=market_data_repo.POLL_CAPTURE),
    ]
    payload = mds.sector_trends_payload(
        rows=rows,
        complete_days=[],
        all_complete_days=[],
        trading_days=[D1, D2],
        dimension="industry",
        days=60,
        top_n=20,
    )
    assert payload["available_from"] is None
    assert payload["boards"] == []
    assert payload["window"]["samples"] == 0
    assert payload["window"]["from"] is None
    assert payload["window"]["to"] is None
    assert payload["as_of"] is None
    assert payload["missing_days"] == [D1.isoformat(), D2.isoformat()]


async def test_empty_result_is_never_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """空结果不得进缓存（`boards=[]` 不能被冻结成 300s 的"暂无数据"）。"""
    _patch_sector_db(monkeypatch, rows=[], trading=[])

    cache = _RecordingCache()
    payload = await mds.get_sector_trends(cache, dimension="industry", days=60, top_n=20)

    assert payload["boards"] == []
    assert payload["available_from"] is None
    assert payload["window"]["observed_days"] == 0
    assert payload["missing_days"] == []
    assert cache.sets == []


async def test_empty_dimension_early_exit_never_touches_daily_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**空维度必须早退且不缓存**：不发起任何 `daily_quotes` 查询，也不把它冻成空结果。

    Task 6 评审 Important #3 的被测性质是"**不发起任何 `daily_quotes` 查询**"——**不是**
    "一条 SQL 都不发"（锚点聚合 `max(trade_date)` 仍会跑，真库是一条 `Index Only Scan`）。
    Task 8 在这里同时恢复"空结果不缓存"的**专用**断言（`boards=[]` 不得被冻结 300s），
    并把测试名改成实际断言的两件事。

    原实现会走 `list_trading_days(db, date.min, date.min)` —— `trade_date >= 0001-01-01
    AND <= 0001-01-01` 在 `daily_quotes` 上是非 sargable 的全表扫；这条路径恰是"该维度
    尚无数据"的诚实空态（真库用户可随时请求一个空维度）。
    """
    factory = _FakeFactory()
    _patch_sector_db(monkeypatch, rows=[], trading=[D1, D2, D3], snapshot_days=[], factory=factory)

    # 直接给"行情表读"装上绊线：早退若被去掉，这里立刻炸（比断言"没执行到"更强：
    # 它不依赖替身恰好返回什么）
    async def _no_market_read(*a: Any, **k: Any) -> list[date]:
        raise AssertionError("空维度不得读 daily_quotes（Review Important #3）")

    monkeypatch.setattr(market_data_repo, "list_trading_days", _no_market_read)

    cache = _RecordingCache()
    payload = await mds.get_sector_trends(cache, dimension="industry", days=60)

    # 早退：执行过的语句里**没有** `daily_quotes`（原实现会发
    # `trade_date >= 0001-01-01 AND <= 0001-01-01` 的非 sargable 全表扫）。
    # 注意：锚点聚合在本用例里被 `_patch_sector_db` 的 repo 替身顶掉，所以它不会出现在
    # `factory.statements` 里 —— 断言"锚点查询发生过"会是在断言替身的实现，不是断言服务。
    assert "daily_quotes" not in _sql_tables(factory.statements)
    # 空维度是"没有可统计对象"，不是"有对象但缺完整日"：两种形状必须可区分
    assert payload == mds._empty_sector_trends("industry", 60, 20)
    # 空结果不缓存（专用断言，Task 6 重写时丢失）：第一行落库后下次请求必须看得见
    assert cache.sets == []


async def test_window_never_exceeds_requested_days_when_snapshot_lags_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**快照落后行情时窗口必须截断到 `days`**（Task 6 评审 Important #2）。

    构造：行情有 100 个交易日（D1…D100），该维度快照只到 D80（采集断了 20 天），
    `days=60` → 窗口只能是最后 60 个交易日 `[D21, D80]`：
    - 不变量 `len(series) + len(missing_days) <= days`（原实现会给 D81…D100 这 20 天
      造出**假缺日**，`len(missing_days)` 变 80）；
    - `window.days` 回请求值 60、`window.observed_days` = 实际跨度 60；
    - `window.from` 不得早于请求窗口下界（D21），`missing_days` 里不得出现窗口外的日子。
    """
    day = [D1 + timedelta(days=i) for i in range(100)]
    snapshots = day[:80]  # 快照落后行情 20 天
    rows = [_snap(d, "BK0001", capture_kind=market_data_repo.POLL_CAPTURE) for d in snapshots]
    _patch_sector_db(monkeypatch, rows=rows, trading=day, snapshot_days=snapshots)

    payload = await mds.get_sector_trends(_RecordingCache(), dimension="industry", days=60)

    window = payload["window"]
    assert window["days"] == 60, window
    assert window["observed_days"] == 60, window
    assert window["samples"] == 0  # 全是 poll 行 → 没有完整日
    assert len(payload["missing_days"]) <= 60, payload["missing_days"]
    assert len(payload["boards"]) + len(payload["missing_days"]) <= 60
    assert payload["window"]["from"] is None and payload["window"]["to"] is None
    forbidden = {d.isoformat() for d in day[80:]}  # 快照完全没覆盖的那 20 天
    assert forbidden & set(payload["missing_days"]) == set()
    assert payload["missing_days"][0] >= day[20].isoformat()
    assert payload["missing_days"][-1] <= day[79].isoformat()


# ---------------------------------------------------------------------------
# 2. repo 查询：完整日谓词 / 区间过滤
# ---------------------------------------------------------------------------


async def test_list_sector_moneyflow_complete_days_filters_capture_kind() -> None:
    """完整日行的 WHERE 必须含 `capture_kind='eod_full'` 与维度（Ruling 21 的 SQL 面）。"""
    from sqlalchemy.dialects import postgresql

    captured: list[Any] = []

    class _Db:
        async def execute(self, stmt: Any, params: Any = None) -> Any:
            captured.append(stmt)
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    await market_data_repo.list_sector_moneyflow_complete_days(
        _Db(),
        "industry",
        D1,
        D4,  # type: ignore[arg-type]
    )
    compiled = captured[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "capture_kind" in sql
    assert "ORDER BY" in sql.upper()
    # `eod_full` 是**绑定参数**（不是内联字面量）：断言参数值，别只 grep SQL 文本
    assert market_data_repo.EOD_FULL_CAPTURE in compiled.params.values(), compiled.params


async def test_list_concept_membership_changes_is_ascending_by_observed_on() -> None:
    captured: list[Any] = []

    class _Db:
        async def execute(self, stmt: Any, params: Any = None) -> Any:
            captured.append(stmt)
            return SimpleNamespace(all=lambda: [])

    await market_data_repo.list_concept_membership_changes(_Db(), D1, D4)  # type: ignore[arg-type]
    from sqlalchemy.dialects import postgresql

    sql = str(captured[0].compile(dialect=postgresql.dialect()))
    assert "concept_member_changes" in sql
    assert "ASC" in sql.upper()


# ---------------------------------------------------------------------------
# 3. 端点级：200 + 形状 + 契约（与 /market/sector-moneyflow 同日同值）
# ---------------------------------------------------------------------------


@pytest.fixture
async def _api_cache() -> Any:

    cache = _RecordingCache()

    async def _cache_dep() -> Any:
        return cache

    app.dependency_overrides[get_cache] = _cache_dep
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, cache
    finally:
        app.dependency_overrides.clear()


class _FakeFactory:
    """`async_session_factory()` 的替身；记录每条执行过的语句（表名断言用）。"""

    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def __aenter__(self) -> Any:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def __call__(self) -> _FakeFactory:
        return self

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        self.statements.append(stmt)
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))


def _sql_tables(statements: list[Any]) -> set[str]:
    """从执行过的语句里解析出被 FROM 的表名（`daily_quotes` 是否被碰过的判据）。"""
    from sqlalchemy.dialects import postgresql

    out: set[str] = set()
    for stmt in statements:
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        assert "FROM " in sql, sql
        out.add(sql.split("FROM ", 1)[1].split()[0].strip(","))
    return out


def _patch_sector_db(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[SimpleNamespace],
    trading: list[date],
    *,
    snapshot_days: list[date] | None = None,
    factory: Any = None,
) -> Any:
    """把 `get_sector_trends` 的读路径换成桩（service 内联 import 的是模块属性）。

    `snapshot_days` 默认 = `trading`（快照与行情同步）；显式传入可构造"快照落后行情"。
    """
    snapshots = list(trading if snapshot_days is None else snapshot_days)

    async def _earliest_day(db: Any, dimension: str) -> date | None:
        return min(snapshots) if snapshots else None

    async def _latest_day(db: Any, dimension: str) -> date | None:
        return max(snapshots) if snapshots else None

    async def _market_days(
        db: Any, start: date, end: date, *, limit: int | None = None
    ) -> list[date]:
        # 扫描下界必须仍能覆盖**窗口**所需的交易日（Task 8 给生产加了"有界下探"：
        # 上界 = 锚点 - (days * 2 + 14) 自然日）；否则"窗口守恒"等用例会因为扫描截断而
        # 静默变样。窗口长度由生产的 `trailing_window_days(days=60)` 决定 —— 桩数据里
        # 最老的**切片点**必须不早于 start（该用例本身断言的是"扫描下界被上界钳住"，
        # 它允许夹具刻意铺更老的历史）。
        required_from = start if len(trading) <= 60 else trading[-(60 + 1)]
        assert required_from >= start, (
            f"bounded scan starts at {start}, truncating the 60-day window from {required_from}"
        )
        window = [d for d in trading if start <= d <= end]
        return window[-limit:] if limit is not None else window

    async def _snapshot_days(db: Any, dimension: str, *, limit: int) -> list[date]:
        return snapshots[-limit:]

    async def _earliest_complete(db: Any, dimension: str, **kw: Any) -> date | None:
        days = [
            r.trade_date
            for r in rows
            if r.trade_date in set(snapshots)
            and r.capture_kind == market_data_repo.EOD_FULL_CAPTURE
        ]
        return min(days) if days else None

    async def _complete_rows(
        db: Any, dimension: str, start: date, end: date, **kw: Any
    ) -> list[Any]:
        # 契约：窗口读**只回完整日**（`poll` 行不得混进来 —— Ruling 21 的 SQL 面）
        assert (
            kw.get("capture_kind", market_data_repo.EOD_FULL_CAPTURE)
            == market_data_repo.EOD_FULL_CAPTURE
        )
        return [
            r
            for r in rows
            if r.dimension == dimension
            and r.capture_kind == market_data_repo.EOD_FULL_CAPTURE
            and start <= r.trade_date <= end
        ]

    fake_factory = factory if factory is not None else _FakeFactory()
    monkeypatch.setattr(market_data_repo, "earliest_sector_moneyflow_day", _earliest_day)
    monkeypatch.setattr(market_data_repo, "latest_sector_moneyflow_day", _latest_day)
    monkeypatch.setattr(market_data_repo, "list_trading_days", _market_days)
    monkeypatch.setattr(market_data_repo, "list_sector_moneyflow_trading_days", _snapshot_days)
    monkeypatch.setattr(
        market_data_repo, "earliest_sector_moneyflow_complete_day", _earliest_complete
    )
    monkeypatch.setattr(market_data_repo, "list_sector_moneyflow_complete_days", _complete_rows)
    monkeypatch.setattr(mds, "async_session_factory", fake_factory, raising=False)
    import app.core.database as _db

    monkeypatch.setattr(_db, "async_session_factory", fake_factory)
    return fake_factory


async def test_sector_trends_endpoint_shape(
    monkeypatch: pytest.MonkeyPatch, _api_cache: Any
) -> None:
    """端点 200；`available_from`/`attribution`/`top_n`/`missing_days` 齐备；天数越界 422。"""
    client, cache = _api_cache
    rows = [
        _snap(D1, "BK0001", inflow=5.0e9),
        _snap(D2, "BK0001", capture_kind=market_data_repo.POLL_CAPTURE, inflow=1.0e9),
        _snap(D3, "BK0001", inflow=2.0e9),
    ]
    _patch_sector_db(monkeypatch, rows, [D1, D2, D3])

    async def _no_concept(db: Any, start: date, end: date) -> list[Any]:
        return []

    monkeypatch.setattr(market_data_repo, "list_concept_membership_changes", _no_concept)
    monkeypatch.setattr(market_data_repo, "list_concept_members_current", _no_concept)

    resp = await client.get("/api/v1/market/sector-trends", params={"dimension": "industry"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dimension"] == "industry"
    assert body["available_from"] == D1.isoformat()
    assert body["attribution"] == "current_sw_snapshot"
    assert body["attribution_note"] == "按当前分类回溯，非当时成分"
    assert body["lu_count_source"] == "unavailable"
    assert body["top_n"] == 20
    # `window.days` 回**请求值** 60；实际交易日跨度（桩里 3 天）走 `observed_days`
    assert body["window"]["days"] == 60
    assert body["window"]["observed_days"] == 3
    assert body["window"]["samples"] + len(body["missing_days"]) <= body["window"]["days"]
    assert body["missing_days"] == [D2.isoformat()]
    assert [b["board_code"] for b in body["boards"]] == ["BK0001"]
    assert body["as_of"] == D3.isoformat()
    assert any(k.endswith(":industry:60:20") for k in cache.gets), cache.gets

    resp_over = await client.get(
        "/api/v1/market/sector-trends", params={"dimension": "industry", "days": 121}
    )
    assert resp_over.status_code == 422
    assert (
        await client.get("/api/v1/market/sector-trends", params={"dimension": "nope"})
    ).status_code == 422
    assert (
        await client.get(
            "/api/v1/market/sector-trends", params={"dimension": "industry", "top_n": 0}
        )
    ).status_code == 422


@pytest.mark.parametrize("dimension", ["industry", "concept", "region"])
async def test_sector_trends_cache_key_includes_every_dimension(
    monkeypatch: pytest.MonkeyPatch, _api_cache: Any, dimension: str
) -> None:
    """缓存键必须含 `dimension` + `days` + `top_n`（漏一个就会串味）。"""
    client, cache = _api_cache
    rows = [_snap(D1, "BK0001", dimension=dimension)]
    _patch_sector_db(monkeypatch, rows, [D1])

    async def _empty(db: Any, *a: Any, **k: Any) -> list[Any]:
        return []

    monkeypatch.setattr(market_data_repo, "list_concept_membership_changes", _empty)
    monkeypatch.setattr(market_data_repo, "list_concept_members_current", _empty)

    resp = await client.get(
        "/api/v1/market/sector-trends",
        params={"dimension": dimension, "days": 42, "top_n": 7},
    )
    assert resp.status_code == 200, resp.text
    assert any(f":{dimension}:42:7" in k for k in cache.gets), cache.gets


# ---------------------------------------------------------------------------
# 4. 真库（`-m e2e`）：哨兵日自建自清 + 与 `/market/sector-moneyflow` 同日同值
# ---------------------------------------------------------------------------


@pytest.mark.e2e
async def test_sector_trends_real_db_gate_and_consistency() -> None:
    """真库：完整日闸门如实 + 最后完整日的板值与 `/market/sector-moneyflow` 同源同值。

    - 真库当前（T2 的 15:05 EOD job 上线前）`capture_kind='eod_full'` 行数为 **0**
      → `available_from=None`、`window.samples=0`、`boards=[]`（**绝不**用 poll 行冒充、
      绝不把缺日算成 0 流入）。
    - 再往**该维度表内最近的交易日**（= `/market/sector-moneyflow` 的 `as_of` 日）写一行
      `board_code='BK9999'` 的 `eod_full` 哨兵行，自建自清：该日成为 `available_from`
      与"最后完整日"，且该行的值与 `get_sector_moneyflow(cache=None)` 返回的**同板**行
      逐字相等（同源、同映射）。哨兵板码不与任何真实板块冲突，清理只删自己那一行。
    """
    from sqlalchemy import text

    from app.core.database import async_session_factory, engine

    cache = _RecordingCache()
    sentinel_rows = [
        {
            "board_code": "BK9999",
            "board_name": "哨兵板块",
            "pct_change": 2.5,
            "main_net_inflow": 1.5e9,
            "up_count": 12,
            "down_count": 3,
            "flat_count": 1,
            "amount": None,
            "super_large_net": None,
            "large_net": None,
            "main_net_ratio": None,
            "lead_stock_name": None,
            "lead_stock_code": None,
            "lead_stock_pct": None,
        }
    ]

    async def _cleanup() -> None:
        async with async_session_factory() as db:
            await db.execute(
                text(
                    "DELETE FROM sector_moneyflow_snapshots "
                    "WHERE board_code = 'BK9999' AND board_name = '哨兵板块'"
                )
            )
            await db.commit()

    # 上一批 `engine.dispose()` 已释放其事件循环上的连接池；本用例自己的循环里再 dispose
    # 一次（幂等）确保没有跨循环连接被复用（全量 suite 实测过 `Event loop is closed`）。
    await engine.dispose()
    try:
        await _cleanup()

        # 闸门如实：真库没有任何 eod_full 行 → 无 available_from、无样本、无板块行
        honest = await mds.get_sector_trends(cache, dimension="industry", days=60)
        assert honest["available_from"] is None, honest
        assert honest["window"]["samples"] == 0
        assert honest["window"]["days"] == 60  # 回请求值；实际跨度走 observed_days
        assert honest["window"]["observed_days"] > 0
        assert honest["window"]["from"] is None and honest["window"]["to"] is None
        assert honest["boards"] == []
        assert len(honest["missing_days"]) == honest["window"]["observed_days"]
        assert honest["leader_change_count"] == 0 and honest["leader_changes"] == []

        # 哨兵：写在该维度**窗口内最早**的交易日（真库里该日只有 poll 行，哨兵板码
        # BK9999 不与任何真实板块冲突），自建 → 实测 → 自清。
        async with async_session_factory() as db:
            days_in_window = await market_data_repo.list_sector_moneyflow_trading_days(
                db, "industry", limit=60
            )
            trading = await market_data_repo.list_trading_days(
                db, days_in_window[0], days_in_window[-1]
            )
            day = trading[0]
            reference_before = await mds.get_sector_moneyflow(None, "industry", 100)  # type: ignore[arg-type]
            assert reference_before["as_of"] is not None
            await market_data_repo.upsert_sector_moneyflow(
                db,
                day,
                "industry",
                sentinel_rows,
                capture_kind=market_data_repo.EOD_FULL_CAPTURE,
            )
            await db.commit()

        payload = await mds.get_sector_trends(cache, dimension="industry", days=60)
        assert payload["available_from"] == day, payload["available_from"]
        assert payload["as_of"] == day
        assert payload["window"]["samples"] == 1
        assert payload["window"]["from"] == day and payload["window"]["to"] == day
        assert payload["attribution"] == "current_sw_snapshot"
        assert payload["attribution_note"] == "按当前分类回溯，非当时成分"
        assert payload["leader_change_count"] == 0  # 只有一个完整日 → 没有"更替"
        sentinel_board = next(b for b in payload["boards"] if b["board_code"] == "BK9999")
        assert sentinel_board["main_net_inflow"] == 1.5e9
        assert sentinel_board["pct_change"] == 2.5
        assert sentinel_board["up_count"] == 12
        assert sentinel_board["streak_on_top_n"] == 1
        assert sentinel_board["streak_inflow"] == 1
        # 只有哨兵那天是完整日 → 窗口内其余交易日全部点名（缺日 ≠ 0 流入）
        assert day.isoformat() not in payload["missing_days"]
        assert len(payload["missing_days"]) == payload["window"]["observed_days"] - 1

        # 同源一致性①：`/market/sector-moneyflow` 这条生产读路径读的就是表里那一行
        envelope = await mds.get_sector_moneyflow(None, "industry", 100)  # type: ignore[arg-type]
        assert envelope["as_of"] is not None
        async with async_session_factory() as db:
            physical = {
                r.board_code: r
                for r in await market_data_repo.list_sector_moneyflow(
                    db, date.fromisoformat(str(envelope["as_of"])), "industry", 100
                )
            }
        latest = envelope["items"][0]
        assert latest["main_net_inflow"] == physical[latest["board_code"]].main_net_inflow
        assert latest["pct_change"] == physical[latest["board_code"]].pct_change
        assert latest["up_count"] == physical[latest["board_code"]].up_count
        assert latest["down_count"] == physical[latest["board_code"]].down_count

        # 同源一致性②：6b 的哨兵行与它写在表里的那一行（`list_sector_moneyflow`
        # 的生产读路径）逐字相等 —— 两条路径读的是同一行，不是两份口径
        async with async_session_factory() as db:
            stored = {
                r.board_code: r
                for r in await market_data_repo.list_sector_moneyflow(db, day, "industry", 100)
            }["BK9999"]
        assert sentinel_board["main_net_inflow"] == stored.main_net_inflow
        assert sentinel_board["pct_change"] == stored.pct_change
        assert sentinel_board["up_count"] == stored.up_count
        assert sentinel_board["down_count"] == stored.down_count
    finally:
        await _cleanup()
        await engine.dispose()


def test_lu_count_is_rendered_only_where_point_in_time_membership_exists() -> None:
    """`lu_count` 进序列只在有时点成分的日子；没有成分的日子是 `None`（不是 0）。

    0 是"当日该板一只涨停都没有"这一**事实断言**；`None` 是"这天算不出来"。
    两者混用会把"还没积累成分差分"的历史日显示成"零涨停"。
    """
    rows = [_snap(D1, "BK0896", dimension="concept"), _snap(D2, "BK0896", dimension="concept")]
    payload = mds.sector_trends_payload(
        rows=rows,
        complete_days=[D1, D2],
        all_complete_days=[D1, D2],
        trading_days=[D1, D2],
        dimension="concept",
        days=60,
        top_n=20,
        lu_counts={(D1, "BK0896"): 3},
    )
    board = next(b for b in payload["boards"] if b["board_code"] == "BK0896")
    assert [p["lu_count"] for p in board["series"]] == [3, None]
    assert payload["lu_count_source"] == "point_in_time_membership"

    industry = mds.sector_trends_payload(
        rows=rows,
        complete_days=[D1, D2],
        all_complete_days=[D1, D2],
        trading_days=[D1, D2],
        dimension="industry",
        days=60,
        top_n=20,
    )
    assert all(p["lu_count"] is None for b in industry["boards"] for p in b["series"])
    assert industry["lu_count_source"] == "unavailable"


def _record_scan_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[date, date]]:
    """`list_trading_days` 的调用记录（`(start, end)`）。必须**在**
    :func:`_patch_sector_db` 之后调用 —— 它包住后者装的桩，而不是替换桩的行为。"""
    real = market_data_repo.list_trading_days
    calls: list[tuple[date, date]] = []

    async def _spy(db: Any, start: date, end: date, **kw: Any) -> list[date]:
        calls.append((start, end))
        return await real(db, start, end, **kw)

    monkeypatch.setattr(market_data_repo, "list_trading_days", _spy)
    return calls


async def test_daily_quotes_scan_window_is_bounded_not_history_wide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`daily_quotes` 扫描范围必须**两端有界**（Task 6 评审 Minor：原实现从该维度最早行扫起）。

    构造：该维度快照横跨 240 个自然日（`days=60` 只取末尾 60 个交易日），锚点为最后一天
    → 下界应是"锚点 - (60*2+14) 自然日"，而不是 240 天前的 `min(trade_date)`。
    只断言"不比上界更早"（上界公式本身就是被测实现的一部分），并另用一条断言把
    "不再横跨整部维度历史"钉死。
    """
    # 维度历史 ≈ 2 年（720 自然日）；行情只在这些日子里稀疏存在（真实 A 股约 0.67
    # 交易日/自然日），最老的行情在 200 自然日前 —— 远早于有界下探上界（134 自然日）。
    anchor = D1 + timedelta(days=719)
    trading = [anchor - timedelta(days=2 * i) for i in range(220)]  # 每 2 天一个交易日
    trading.reverse()
    _patch_sector_db(
        monkeypatch,
        rows=[_snap(d, "BK0001") for d in trading],
        trading=trading,
        snapshot_days=trading,
    )
    scans = _record_scan_calls(monkeypatch)

    await mds.get_sector_trends(_RecordingCache(), dimension="industry", days=60)

    start, end = max(scans)  # 只有一次；`max` 使断言在"多扫一次"的实现下也不假绿
    assert end == anchor
    assert start == anchor - timedelta(days=134)
    assert start > trading[0], "不得从该维度的最早行开始扫（横跨整部维度历史）"
    assert start <= anchor - timedelta(days=2 * 59), "下探上界必须仍装得下 60 个交易日"
