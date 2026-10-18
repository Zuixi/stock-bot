"""spec §S9.6 —— **同类历史**（分桶 / L1 自我历史 / L2 全市场同桶 / PIT / 降级）。

七条不变量（每条对应 spec 的一句验收判据）：

1. **分桶边界逐值**（§S9.6.1）：情绪 19.9/20/40/60/80/80.1、板块 ±2、MA20 ±5；
   ``unknown`` **单列**（缺数不并进任何桶、也不当成 0）；
2. **两层分开**（§S9.6.2）：``self``（本人已复盘）与 ``market``（全市场同桶）各自成立，
   不合并成一个"胜率"；L1 口径 = 三类归因算错，``right_for_wrong_reason`` / ``luck`` 单列；
3. **L2 样本闸**（§S9.6.2）：``n = 29`` 不给数字、``n = 30`` 给；产物缺失 → ``no_stats_product``；
4. **PIT**（§S9.6.3）：L1 只看 ``created_at < 本条 created_at`` 的已复盘条目（把未来塞进
   候选集 → 输出必须不变，且把上界往后挪必须变）；L2 产物 ``as_of >= 快照日`` → ``stale_guard``；
5. **落快照**（§S9.6.4）：``snapshot.peer_history`` 与响应里那份逐字一致；
6. **降级不阻断创建**（§S9.6.4）：任何异常 → 创建仍成功，``notes`` 写 ``pipeline_error``；
7. **产物可复跑**（§S9.6.7）：脚本两次运行**逐字节一致**（真 PG；短窗口加速）。

所有写入包在**回滚**事务里（Ruling 17）；库不可达时 skip 而非红。合成数据用库外代码
（``T960xx``）与固定日期，避免与真实行情/申万成分互相污染。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.index_daily import IndexDaily
from app.models.journal import DecisionJournalEntry, DecisionJournalReview
from app.models.quote import DailyQuote
from app.models.stock import Stock
from app.schemas.journal import JournalEntryCreate
from app.services import journal_service as svc
from app.services import peer_history_service as ph

_DEFAULT_ASYNC = "postgresql+asyncpg://stock_user:stock_pass@localhost:5433/stock_bot_test"
ASYNC_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or _DEFAULT_ASYNC
SYNC_URL = ASYNC_URL.replace("+asyncpg", "")

AS_OF = date(2026, 10, 16)
SYMBOL = "T96001"
OTHER = "T96002"
CHECKED_AT = datetime(2026, 10, 20, 15, 0)


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
    not _PG_OK, reason=f"PostgreSQL unavailable at {SYNC_URL} (set TEST_DATABASE_URL)"
)


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


async def _with_rollback(factory: Any) -> Any:
    engine = create_async_engine(ASYNC_URL)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            session = AsyncSession(bind=conn)
            try:
                return await factory(session)
            finally:
                await session.close()
                await trans.rollback()
    finally:
        await engine.dispose()


async def _seed(session: AsyncSession) -> None:
    """最小底座：两只股票各两天行情 + 沪深300（够 ``create_entry`` 抓快照）。"""
    await session.execute(
        insert(Stock),
        [
            {
                "exchange": "Shanghai_Stocks",
                "symbol": symbol,
                "name": f"测试{symbol}",
                "category": "A股",
                "asof": datetime(2026, 10, 16, 18, 0),
            }
            for symbol in (SYMBOL, OTHER)
        ],
    )
    id_of = {
        str(symbol): int(stock_id)
        for stock_id, symbol in (
            await session.execute(
                select(Stock.id, Stock.symbol).where(Stock.symbol.in_((SYMBOL, OTHER)))
            )
        ).all()
    }
    quotes: list[dict[str, Any]] = []
    for symbol in (SYMBOL, OTHER):
        for trade_date, close in ((AS_OF, 100.0), (AS_OF + timedelta(days=5), 110.0)):
            quotes.append(
                {
                    "stock_id": id_of[symbol],
                    "trade_date": trade_date,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "pre_close": close,
                    "amount": 1_000_000.0,
                }
            )
    await session.execute(insert(DailyQuote), quotes)
    await session.execute(
        insert(IndexDaily),
        [
            {"ts_code": svc.HS300_TS_CODE, "trade_date": AS_OF, "close": 4000.0},
            {
                "ts_code": svc.HS300_TS_CODE,
                "trade_date": AS_OF + timedelta(days=5),
                "close": 4040.0,
            },
        ],
    )
    await session.flush()


def _snapshot(
    *,
    sentiment: float | None = 50.0,
    sector_pct: float | None = 0.0,
    ma20: float | None = 0.0,
) -> dict[str, Any]:
    """一份"创建时"的快照（只填分桶用得到的三项 + 最小元数据）。"""
    return {
        "version": svc.SNAPSHOT_VERSION,
        "as_of": AS_OF.isoformat(),
        "quote_as_of": AS_OF.isoformat(),
        "symbol": SYMBOL,
        "price": 100.0,
        "sentiment_percentile": sentiment,
        "sector_flow_state": (
            None if sector_pct is None else {"code": "T96L1", "name": "测试一级", "pct": sector_pct}
        ),
        "ma20_dist_pct": ma20,
        "missing_reason": {},
    }


def _entry(
    *,
    user_id: uuid.UUID,
    created_at: datetime,
    symbol: str | None = SYMBOL,
    snapshot: dict[str, Any] | None = None,
    kind: str = "watch",
) -> DecisionJournalEntry:
    return DecisionJournalEntry(
        user_id=user_id,
        symbol=symbol,
        kind=kind,
        bet_type="direction",
        position_size="none",
        thesis="猪价见底，产能去化加速",
        invalidation={"kind": "price_below", "value": 50.0},
        horizon_days=7,
        snapshot=snapshot if snapshot is not None else _snapshot(),
        created_at=created_at,
        due_on=created_at.date() + timedelta(days=7),
    )


async def _review(session: AsyncSession, entry: DecisionJournalEntry, verdict: str) -> None:
    session.add(entry)
    await session.flush()
    session.add(
        DecisionJournalReview(
            entry_id=entry.id,
            outcome_pct=1.0,
            bench_pct=0.5,
            excess_pct=0.5,
            invalidation_hit=False,
            verdict=verdict,
        )
    )
    await session.flush()


def _write_product(
    path: Path, *, as_of: str, n: int, med5: float | None, med20: float | None
) -> Path:
    """写一份最小产物（只有当前快照那一个桶），用来测样本闸与 PIT。"""
    key = ph.bucket_key(ph.bucket_of(_snapshot()))
    path.write_text(
        json.dumps(
            {
                "version": ph.PEER_HISTORY_VERSION,
                "as_of": as_of,
                "window_months": 12,
                "min_sample": 30,
                "basis": "test fixture",
                "buckets": {
                    key: {"n": n, "t20_n": n, "t5_excess_med": med5, "t20_excess_med": med20}
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# 1) 分桶边界（纯函数，逐值）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "unknown"),
        (19.9, "lt_20"),
        (20.0, "20_40"),
        (39.9, "20_40"),
        (40.0, "40_60"),
        (60.0, "60_80"),
        (79.9, "60_80"),
        (80.0, "ge_80"),
        (80.1, "ge_80"),
        (100.0, "ge_80"),
    ],
)
def test_sentiment_bucket_boundaries(value: float | None, expected: str) -> None:
    """spec §S9.6.1 逐字：``<20 / 20–40 / 40–60 / 60–80 / ≥80``（边界含下侧）+ unknown。"""
    assert ph.sentiment_label(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "unknown"),
        (-2.1, "below_-2"),
        (-2.0, "below_-2"),
        (-1.9, "mid"),
        (0.0, "mid"),
        (1.9, "mid"),
        (2.0, "above_2"),
        (9.9, "above_2"),
    ],
)
def test_sector_bucket_boundaries(value: float | None, expected: str) -> None:
    """spec §S9.6.1：``≤−2%`` / ``(−2%, 2%)`` / ``≥2%``。"""
    assert ph.sector_label(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "unknown"),
        (-5.1, "neg_5"),
        (-5.0, "mid"),
        (-0.1, "mid"),
        (5.0, "mid"),
        (5.1, "pos_5"),
    ],
)
def test_ma20_bucket_boundaries(value: float | None, expected: str) -> None:
    """spec §S9.6.1：``<−5%`` / ``[−5%, 5%]`` / ``>5%``。"""
    assert ph.ma20_label(value) == expected


def test_bucket_of_keeps_unknown_separate() -> None:
    """缺数写 ``unknown``：**不并进**任何桶、也不当 0（spec §S9.6.1）。"""
    assert ph.bucket_of(_snapshot(sentiment=None, sector_pct=None, ma20=None)) == {
        "sentiment": "unknown",
        "sector": "unknown",
        "ma20": "unknown",
    }
    # 三个维度互不干扰：只有一个缺数时只有那一格是 unknown
    assert ph.bucket_of(_snapshot(sentiment=None, sector_pct=1.0, ma20=-9.0)) == {
        "sentiment": "unknown",
        "sector": "mid",
        "ma20": "neg_5",
    }
    # 快照为空 → 整体 unknown（不崩、不补 0）
    assert ph.bucket_of(None) == {
        "sentiment": "unknown",
        "sector": "unknown",
        "ma20": "unknown",
    }
    assert ph.bucket_key({"sentiment": "unknown", "sector": "unknown", "ma20": "unknown"}) == (
        "unknown|unknown|unknown"
    )


# ---------------------------------------------------------------------------
# 2) L1 自我历史：只用本人 + 只用过去 + 两层分开 + 样本闸
# ---------------------------------------------------------------------------


@requires_pg
async def test_self_history_counts_only_reviewed_entries() -> None:
    """L1 的 ``n`` 只数**已复盘**：未复盘的不计入（spec §S9.6.2）。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed(session)
        user = uuid.uuid4()
        base = datetime(2026, 10, 10, 15, 0)
        for offset, verdict in enumerate(("timing_wrong", "luck", "discipline_break")):
            await _review(
                session,
                _entry(user_id=user, created_at=base + timedelta(days=offset)),
                verdict,
            )
        session.add(_entry(user_id=user, created_at=base + timedelta(days=5)))  # 未复盘
        await session.flush()
        return await ph.self_history(
            session,
            user,
            bucket=ph.bucket_of(_snapshot()),
            symbol=SYMBOL,
            before=CHECKED_AT,
        )

    block = await _with_rollback(_go)
    same_symbol = block["same_symbol"]
    assert same_symbol["n"] == 3
    assert same_symbol["wrong"] == 2  # timing_wrong + discipline_break
    assert same_symbol["right"] == 1  # luck = "没有被判成错"
    assert same_symbol["wrong_by"] == {"timing_wrong": 1, "discipline_break": 1}
    assert same_symbol["luck"] == 1
    assert same_symbol["insufficient"] is True, "n=3 < 5 必须标样本不足（不给比例）"


@requires_pg
async def test_self_history_pit_ignores_future_and_moves_with_the_bound() -> None:
    """**PIT 对照**：未来条目不得进入 L1；把上界往后挪才允许它进来（红/绿）。"""

    async def _go(session: AsyncSession) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        await _seed(session)
        user = uuid.uuid4()
        boundary = datetime(2026, 10, 12, 15, 0)
        await _review(
            session,
            _entry(user_id=user, created_at=datetime(2026, 10, 10, 15, 0)),
            "timing_wrong",
        )
        params: dict[str, Any] = {
            "bucket": ph.bucket_of(_snapshot()),
            "symbol": SYMBOL,
        }
        baseline = await ph.self_history(session, user, before=boundary, **params)
        # 一条"未来"的已复盘条目（10-13 > 上界 10-12）
        await _review(
            session,
            _entry(user_id=user, created_at=datetime(2026, 10, 13, 15, 0)),
            "thesis_wrong",
        )
        unchanged = await ph.self_history(session, user, before=boundary, **params)
        widened = await ph.self_history(
            session, user, before=datetime(2026, 10, 14, 15, 0), **params
        )
        return baseline, unchanged, widened

    baseline, unchanged, widened = await _with_rollback(_go)
    assert baseline["same_symbol"]["n"] == 1
    assert unchanged == baseline, "未来条目不得影响 L1（PIT：不泄露自己）"
    assert widened["same_symbol"]["n"] == 2, "上界往后挪后它才该进来（证明过滤真的在起作用）"


@requires_pg
async def test_self_history_splits_same_symbol_and_same_bucket() -> None:
    """两层分开：``same_symbol`` 只数同标的，``same_bucket`` 只数同桶（别人的一条都不数）。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed(session)
        user = uuid.uuid4()
        base = datetime(2026, 10, 10, 15, 0)
        await _review(session, _entry(user_id=user, created_at=base), "timing_wrong")
        await _review(
            session,
            _entry(user_id=user, symbol=OTHER, created_at=base + timedelta(days=1)),
            "luck",
        )
        await _review(
            session,
            _entry(
                user_id=user,
                created_at=base + timedelta(days=2),
                snapshot=_snapshot(ma20=-9.0),
            ),
            "thesis_wrong",
        )
        # 别人的条目：两层都不该数进去
        await _review(
            session,
            _entry(user_id=uuid.uuid4(), created_at=base + timedelta(days=3)),
            "thesis_wrong",
        )
        return await ph.self_history(
            session,
            user,
            bucket=ph.bucket_of(_snapshot()),
            symbol=SYMBOL,
            before=CHECKED_AT,
        )

    block = await _with_rollback(_go)
    assert block["same_symbol"]["n"] == 2, "同标的 2 条（第三条桶不同也仍算同标的）"
    assert block["same_bucket"]["n"] == 2, "同桶 2 条（换标的但同桶要算）"
    assert block["same_symbol"]["wrong"] == 2
    assert block["min_sample"] == ph.MIN_SELF_SAMPLE


@requires_pg
async def test_self_history_insufficient_flag_at_four_and_five() -> None:
    """L1 样本闸：``n=4`` → ``insufficient``；``n=5`` → 可给比例（spec §S9.6.2/§S9.6.7）。"""

    async def _go(session: AsyncSession) -> tuple[dict[str, Any], dict[str, Any]]:
        await _seed(session)
        user = uuid.uuid4()
        base = datetime(2026, 10, 1, 15, 0)
        for index in range(4):
            await _review(
                session,
                _entry(user_id=user, created_at=base + timedelta(days=index)),
                "luck",
            )
        four = await ph.self_history(
            session, user, bucket=ph.bucket_of(_snapshot()), symbol=SYMBOL, before=CHECKED_AT
        )
        await _review(
            session,
            _entry(user_id=user, created_at=base + timedelta(days=4)),
            "thesis_wrong",
        )
        five = await ph.self_history(
            session, user, bucket=ph.bucket_of(_snapshot()), symbol=SYMBOL, before=CHECKED_AT
        )
        return four, five

    four, five = await _with_rollback(_go)
    assert four["same_symbol"]["n"] == 4
    assert four["same_symbol"]["insufficient"] is True
    assert five["same_symbol"]["n"] == 5
    assert five["same_symbol"]["insufficient"] is False


# ---------------------------------------------------------------------------
# 3) L2 全市场同桶：样本闸 / 产物缺失 / PIT（stale_guard）
# ---------------------------------------------------------------------------


def test_market_history_gates_on_min_sample(tmp_path: Path) -> None:
    """L2 样本闸逐值：``n=29`` 不给数字（``insufficient``），``n=30`` 给（spec §S9.6.2）。"""
    bucket = ph.bucket_of(_snapshot())
    day = date(2026, 10, 20)

    small = _write_product(tmp_path / "small.json", as_of="2026-10-16", n=29, med5=-0.4, med20=-1.1)
    block, note = ph.market_history(bucket, day, path=small)
    assert note is None
    assert block is not None and block["n"] == 29
    assert block["t5_excess_med"] is None and block["t20_excess_med"] is None
    assert block["insufficient"] is True

    big = _write_product(tmp_path / "big.json", as_of="2026-10-16", n=30, med5=-0.4, med20=-1.1)
    block, note = ph.market_history(bucket, day, path=big)
    assert note is None
    assert block is not None and block["n"] == 30
    assert block["t5_excess_med"] == pytest.approx(-0.4)
    assert block["t20_excess_med"] == pytest.approx(-1.1)
    assert block["insufficient"] is False


def test_market_history_refuses_stale_product(tmp_path: Path) -> None:
    """PIT：产物 ``as_of >= 快照日`` → 拒绝使用（``stale_guard``，spec §S9.6.3）。"""
    bucket = ph.bucket_of(_snapshot())
    path = _write_product(tmp_path / "p.json", as_of="2026-10-16", n=1000, med5=-0.4, med20=-1.1)
    # 快照日 = 2026-10-16（同日）→ 产物可能含快照日之后的数据 → 拒绝
    assert ph.market_history(bucket, date(2026, 10, 16), path=path) == (None, ph.NOTE_STALE)
    # 快照日晚于产物 as_of → 可以用
    block, note = ph.market_history(bucket, date(2026, 10, 17), path=path)
    assert note is None and block is not None and block["n"] == 1000


def test_market_history_returns_none_for_missing_product(tmp_path: Path) -> None:
    """产物不存在 → ``(None, no_stats_product)``：不给数字，也不阻断创建。"""
    block, note = ph.market_history(
        ph.bucket_of(_snapshot()), date(2026, 10, 20), path=tmp_path / "missing.json"
    )
    assert (block, note) == (None, ph.NOTE_NO_PRODUCT)


# ---------------------------------------------------------------------------
# 4) 装配 + 落快照 + 降级
# ---------------------------------------------------------------------------


@requires_pg
async def test_create_entry_installs_peer_history_in_snapshot() -> None:
    """创建 → ``snapshot.peer_history`` 落库，且桶与快照自洽（spec §S9.6.4）。"""

    async def _go(session: AsyncSession) -> DecisionJournalEntry:
        await _seed(session)
        payload = JournalEntryCreate(
            symbol=SYMBOL,
            kind="watch",
            bet_type="direction",
            thesis="猪价见底，产能去化加速",
            invalidation={"kind": "price_below", "value": 50.0},
            horizon_days=7,
        )
        return await svc.create_entry(
            session, uuid.uuid4(), payload, now=datetime(2026, 10, 16, 15, 0)
        )

    entry = await _with_rollback(_go)
    block = entry.snapshot["peer_history"]
    assert block["version"] == ph.PEER_HISTORY_VERSION
    # 桶必须与**这份快照**一致（不重新按今天的数据分桶）
    assert block["bucket"] == ph.bucket_of(entry.snapshot)
    assert len(ph.bucket_key(block["bucket"]).split("|")) == 3
    assert ph.NOTE_PAST_ONLY in block["notes"]
    assert block["self"]["same_symbol"]["n"] == 0
    assert block["self"]["same_bucket"]["n"] == 0
    # 真产物 as_of=2026-10-16 与快照日同日 → stale_guard 是**正确**行为（不放行未来行情）
    assert block["market"] is None
    assert ph.NOTE_STALE in block["notes"]


@requires_pg
async def test_peer_history_failure_never_blocks_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    """任何异常 → 创建照样成功，``notes=["pipeline_error"]``（spec §S9.6.4）。"""

    async def _boom(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("boom")

    async def _go(session: AsyncSession) -> DecisionJournalEntry:
        await _seed(session)
        payload = JournalEntryCreate(
            symbol=SYMBOL,
            thesis="猪价见底，产能去化加速",
            invalidation={"kind": "price_below", "value": 50.0},
            horizon_days=7,
        )
        return await svc.create_entry(
            session, uuid.uuid4(), payload, now=datetime(2026, 10, 16, 15, 0)
        )

    monkeypatch.setattr(ph, "peer_history", _boom)
    entry = await _with_rollback(_go)
    block = entry.snapshot["peer_history"]
    assert block["self"] is None and block["market"] is None
    assert block["notes"] == [ph.NOTE_PIPELINE]
    assert entry.id is not None, "降级不得阻断创建（必须真的写进去了）"


@requires_pg
async def test_peer_history_uses_the_injected_product_path(tmp_path: Path) -> None:
    """产物路径可注入：换一份产物 → 数字跟着换；``as_of`` 同日 → ``stale_guard``。"""

    async def _go(session: AsyncSession) -> tuple[dict[str, Any], dict[str, Any]]:
        await _seed(session)
        user = uuid.uuid4()
        snapshot = _snapshot()
        fresh = await ph.peer_history(
            session,
            user,
            snapshot=snapshot,
            symbol=SYMBOL,
            computed_at=datetime(2026, 10, 16, 15, 0),
            path=_write_product(
                tmp_path / "fresh.json", as_of="2026-10-15", n=100, med5=1.5, med20=2.5
            ),
        )
        stale = await ph.peer_history(
            session,
            user,
            snapshot=snapshot,
            symbol=SYMBOL,
            computed_at=datetime(2026, 10, 16, 15, 0),
            path=_write_product(
                tmp_path / "stale.json", as_of="2026-10-16", n=100, med5=9.9, med20=9.9
            ),
        )
        return fresh, stale

    fresh, stale = await _with_rollback(_go)
    assert fresh["market"]["t5_excess_med"] == pytest.approx(1.5)
    assert fresh["market"]["t20_excess_med"] == pytest.approx(2.5)
    assert fresh["market"]["as_of"] == "2026-10-15"
    assert stale["market"] is None
    assert ph.NOTE_STALE in stale["notes"]


# ---------------------------------------------------------------------------
# 5) 产物：随仓库入库 + 自描述 + 可复跑（真 PG）
# ---------------------------------------------------------------------------


def test_product_file_is_committed_and_self_describing() -> None:
    """产物随仓库入库，且自带 ``as_of`` / ``window_months`` / ``min_sample`` / ``basis``。"""
    doc = ph.load_stats()
    assert doc is not None, f"缺产物：{ph.STATS_PATH}（跑 scripts/build_peer_history_stats.py）"
    assert doc["version"] == ph.PEER_HISTORY_VERSION
    assert doc["window_months"] == 12
    assert doc["min_sample"] == ph.MIN_MARKET_SAMPLE
    for key in ("as_of", "basis", "buckets"):
        assert doc.get(key) is not None, f"产物缺 {key}"
    assert doc["buckets"], "产物桶表为空（样本为 0，读面会一直显示样本不足）"
    for key, bucket in doc["buckets"].items():
        assert len(key.split("|")) == 3, f"桶键必须是 维度|维度|维度：{key}"
        assert int(bucket["n"]) >= 0
        for field in ("t5_excess_med", "t20_excess_med"):
            value = bucket[field]
            assert value is None or -100.0 <= float(value) <= 100.0


def _load_builder() -> Any:
    """按路径加载离线脚本（``scripts/`` 不是包；不 import 会触发 ``if __name__``）。"""
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_peer_history_stats.py"
    spec = importlib.util.spec_from_file_location("build_peer_history_stats", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@requires_pg
async def test_builder_is_deterministic_byte_for_byte(tmp_path: Path) -> None:
    """spec §S9.6.7：离线脚本可复跑、两次产物**逐字节一致**（1 个月窗口加速）。"""
    builder = _load_builder()
    builder.WINDOW_MONTHS = 1
    builder.LOOKBACK_NATURAL_DAYS = 40
    engine = create_async_engine(ASYNC_URL)
    try:
        async with AsyncSession(bind=engine) as session:
            first = await builder._build(session)
            second = await builder._build(session)
    finally:
        await engine.dispose()
    out_a = builder.write_document(first, tmp_path / "a.json")
    out_b = builder.write_document(second, tmp_path / "b.json")
    assert out_a.read_bytes() == out_b.read_bytes(), "两次运行产物不一致（混进了随机/时间因素）"
    assert out_a.read_bytes().endswith(b"\n"), "产物必须以一个换行结尾（diff 友好）"
    assert first["as_of"] and first["buckets"], "短窗口也必须能算出桶（否则用例证明不了确定性）"
