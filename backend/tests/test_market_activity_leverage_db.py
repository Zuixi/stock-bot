"""spec §S1.9 + §S1.11 —— **真 PG** 用例（`TEST_DATABASE_URL`，默认 `stock_bot_test`）。

纯函数不变量在 `tests/test_market_activity_leverage.py`；本文件只锁需要真实底座的六条：

1. **落库形状 + 幂等**：`upsert_metric_range` 写 `industry_key='market'` 的行，
   每个交易日恰好 13 行（= 11 个 §S1.9 + 2 个 §S1.11）；重跑**不新增行**。
2. **PIT（三档）**：`published_at` 非空、带时区，且按产地分档（15:00 / 18:00 / 19:00
   Asia/Shanghai）。**绝不回退 `period`**（ADR 0011 / Ruling 34-35）。
3. **`erp_10y` 可手算核对**：`erp_10y == 1/PE × 100 − 10Y` 逐行相等（两列都在库里）。
4. **换手率口径可查 + 与源一致**：直取指数的落库值 == 上游
   `index_dailybasic.turnover_rate_f`（**独立重算**，不用服务内部结构）。
5. **自建换手率可独立复算**：北证50 / 科创综指 == 成分股
   `daily_basic.turnover_rate_f` 按 `index_weight` 权重的加权平均
   （**独立写法的 SQL + Python**，与服务的实现互相对拍）。
6. **缺数不静默为 0**：库里 `value IS NULL` 的行必须带 `extra.missing_reason`
   （有行 ≠ 有值，ADR 0014「产地决定可信度」）。

**不污染库**（Ruling 17 的做法）：所有写入都包在 `rollback` 的事务里；测试库无数据或
上游不可达时 skip 而不是红。测试前后 `industry_key='market'` 的行数与内容不变。

**联网说明**：本文件会真调 TuShare/AKShare（回补的 B 类属性要求"重跑"真的跑一遍）。
上游不可达时对应用例 skip（不假装通过）。
"""

from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
import pandas as pd
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

_CN = ZoneInfo("Asia/Shanghai")


def _tushare_token() -> str:
    """找一个可用的 `TUSHARE_TOKEN`（**不打印其值**）。

    本 worktree 的 `backend/.env` 是 git-ignored（常不存在）→ 回落到主 checkout 的
    `backend/.env`（与 `scripts/spike_*.py` 同规矩）。两者都没有时，联网用例 **skip**，
    不因为"本机缺 token"把整套测试判红。
    """
    from pathlib import Path as _Path

    token = os.environ.get("TUSHARE_TOKEN") or ""
    if token:
        return token
    for path in (
        _Path(__file__).resolve().parents[1] / ".env",
        _Path("/Users/wqz/Developer/stock-bot/backend/.env"),
    ):
        if path.exists():
            try:
                from dotenv import dotenv_values
            except ImportError:  # pragma: no cover
                break
            value = dotenv_values(path).get("TUSHARE_TOKEN") or ""
            if value:
                return value
    return ""


_TUSHARE_TOKEN = _tushare_token()


def _tushare_client() -> Any:
    """**显式用 token 构造** `TuShareClient`，不走 `get_tushare_client()` 单例。

    为什么不走单例：单例读 `settings.tushare_token`，而 `settings` 在**任何**测试模块
    首次 `import app.config` 时就定型了（此时 `TUSHARE_TOKEN` 可能还没进环境）。
    实测：单跑本文件绿，混在整套里红（`ValueError: TuShare token is required`）——
    依赖 import 次序的测试等于不确定。显式构造让本文件与运行顺序无关。
    """
    from app.core.providers.tushare_client import TuShareClient

    return TuShareClient(_TUSHARE_TOKEN)


def _akshare_client() -> Any:
    from app.core.providers.akshare_client import AkShareClient

    return AkShareClient()


from app.models.industry_research import IndustryMetric  # noqa: E402
from app.models.quote import DailyQuote  # noqa: E402
from app.services import market_activity_service as svc  # noqa: E402
from app.services.industry_registry import (  # noqa: E402
    MARKET_ACTIVITY_CALIBER_VERSION,
)

_DEFAULT_ASYNC = "postgresql+asyncpg://stock_user:stock_pass@localhost:5433/stock_bot_test"
ASYNC_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or _DEFAULT_ASYNC
SYNC_URL = ASYNC_URL.replace("+asyncpg", "")


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
requires_tushare = pytest.mark.skipif(
    not _TUSHARE_TOKEN, reason="no TUSHARE_TOKEN (env or backend/.env) — 联网用例跳过"
)


async def _with_rollback(coro_factory: Any) -> Any:
    """在一个**回滚**的事务里跑：测试不向库里留任何行。"""
    engine = create_async_engine(ASYNC_URL)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            session = AsyncSession(bind=conn)
            try:
                return await coro_factory(session)
            finally:
                await session.close()
                await trans.rollback()
    finally:
        await engine.dispose()


async def _latest_quote_day(session: AsyncSession) -> date:
    day = (await session.execute(select(func.max(DailyQuote.trade_date)))).scalar_one_or_none()
    if day is None:
        pytest.skip("库内 daily_quotes 为空（本 spec 依赖行情底座）")
    return day


async def _existing_rows(session: AsyncSession) -> list[IndustryMetric]:
    """库里**已有**的本批次行（不联网、不写入）—— 供只读对账用例使用。

    只读用例的输入来自回补脚本（`scripts/backfill_market_activity.py`）；本任务约定
    **回填与覆盖率演示用 dev 库 `stock_bot`**，故 `stock_bot_test` 没有这些行时
    对应用例 **skip 而不是红**（与 `test_market_sentiment_breadth_db.py` 同规矩）。
    """
    return list(
        (
            await session.execute(
                select(IndustryMetric).where(
                    IndustryMetric.industry_key == "market",
                    IndustryMetric.stock_id == 0,
                    IndustryMetric.metric_key.in_(svc.INGEST_METRIC_KEYS),
                )
            )
        ).scalars()
    )


async def _stored_rows(session: AsyncSession, day_lo: date, day_hi: date) -> list[IndustryMetric]:
    return list(
        (
            await session.execute(
                select(IndustryMetric).where(
                    IndustryMetric.industry_key == "market",
                    IndustryMetric.stock_id == 0,
                    IndustryMetric.metric_key.in_(svc.INGEST_METRIC_KEYS),
                    IndustryMetric.period >= day_lo,
                    IndustryMetric.period <= day_hi,
                )
            )
        ).scalars()
    )


# ── 1) 落库形状 + 幂等 ────────────────────────────────────────────────


@requires_pg
@requires_tushare
def test_upsert_writes_thirteen_rows_per_day_and_is_idempotent() -> None:
    """每交易日 13 行（§S1.9 的 11 + §S1.11 的 2）；重跑不新增行。"""

    async def _go(session: AsyncSession) -> tuple[int, int, int, int]:
        day_hi = await _latest_quote_day(session)
        all_days = await svc._quote_days(session, day_hi - timedelta(days=14), day_hi)
        if not all_days:
            pytest.skip("最近 14 天窗口内无行情行")
        # 只取最后 2 个交易日：本用例验**行数形状与幂等**，联网调用数 ∝ 交易日数
        days = all_days[-2:]
        day_lo = days[0]
        stats = await svc.upsert_metric_range(
            session, day_lo, day_hi, tushare=_tushare_client(), akshare=_akshare_client()
        )
        assert stats.days_written == len(days)
        before = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(IndustryMetric)
                    .where(
                        IndustryMetric.industry_key == "market",
                        IndustryMetric.metric_key.in_(svc.INGEST_METRIC_KEYS),
                    )
                )
            ).scalar_one()
        )
        assert before == len(days) * len(svc.INGEST_METRIC_KEYS), "每交易日必须恰好 13 行"
        stats2 = await svc.upsert_metric_range(
            session, day_lo, day_hi, tushare=_tushare_client(), akshare=_akshare_client()
        )
        after = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(IndustryMetric)
                    .where(
                        IndustryMetric.industry_key == "market",
                        IndustryMetric.metric_key.in_(svc.INGEST_METRIC_KEYS),
                    )
                )
            ).scalar_one()
        )
        return before, after, stats2.rows_upserted, len(days)

    before, after, rerun, _ = asyncio.run(_with_rollback(_go))
    assert rerun > 0, "ON CONFLICT DO UPDATE 会更新既有行（rowcount 非 0）"
    assert after == before, "重跑不得新增任何行（唯一键 + upsert 幂等）"


@requires_pg
@requires_tushare
def test_rows_carry_registry_projection_and_pit() -> None:
    """source/tier/freq/unit/caliber 来自 registry；`published_at` 带时区且非空。"""

    async def _go(session: AsyncSession) -> list[IndustryMetric]:
        day_hi = await _latest_quote_day(session)
        days = (await svc._quote_days(session, day_hi - timedelta(days=14), day_hi))[-2:]
        if not days:
            pytest.skip("最近 14 天窗口内无行情行")
        await svc.upsert_metric_range(
            session, days[0], day_hi, tushare=_tushare_client(), akshare=_akshare_client()
        )
        return await _stored_rows(session, days[0], day_hi)

    rows = asyncio.run(_with_rollback(_go))
    if not rows:
        pytest.skip("回补窗口内没有落库行（上游不可达？）")
    for row in rows:
        assert row.freq == "daily"
        assert row.caliber_version == MARKET_ACTIVITY_CALIBER_VERSION
        assert row.source_tier in {"official", "derived"}
        assert row.published_at is not None, row.metric_key
        assert row.published_at.tzinfo is not None, row.metric_key
        assert row.unit == svc.UNITS[row.metric_key] or row.unit is None


# ── 2) PIT 三档 ──────────────────────────────────────────────────────


@requires_pg
def test_published_at_follows_per_source_bands() -> None:
    """同一交易日：行情/ERP 15:00、两融 18:00、国债 19:00（Asia/Shanghai）。"""

    expected = {
        "market_turnover_amount": (15, 0),
        "margin_financing_balance": (18, 0),
        "margin_financing_buy_ratio": (18, 0),
        "margin_short_balance": (18, 0),
        "cn_treasury_yield_10y": (19, 0),
        "erp_10y": (15, 0),
    }

    async def _go(session: AsyncSession) -> dict[str, tuple[date, datetime]]:
        # 只读（不联网）：每个 key 取库里最新一天，验时点分档
        rows = await _existing_rows(session)
        latest: dict[str, IndustryMetric] = {}
        for r in rows:
            cur = latest.get(r.metric_key)
            if cur is None or r.period > cur.period:
                latest[r.metric_key] = r
        return {k: (v.period, v.published_at) for k, v in latest.items()}

    got = asyncio.run(_with_rollback(_go))
    for key, (hour, minute) in expected.items():
        if key not in got:
            pytest.skip(f"{key} 未落库（上游不可达？）")
        period, published = got[key]
        want = datetime(period.year, period.month, period.day, hour, minute, tzinfo=_CN)
        assert published == want, f"{key}: {published} != {want}"


@requires_pg
def test_published_at_never_falls_back_to_period_midnight() -> None:
    """`published_at` 不得等于 `period` 的 00:00（那就是"静默回退 period"的形态）。"""

    async def _go(session: AsyncSession) -> list[tuple[str, date, datetime]]:
        rows = await _existing_rows(session)
        return [(r.metric_key, r.period, r.published_at) for r in rows]

    rows = asyncio.run(_with_rollback(_go))
    if not rows:
        pytest.skip("回补窗口内没有落库行")
    for key, period, published in rows:
        assert published.astimezone(_CN).time() != datetime.min.time(), key


# ── 3) erp_10y 可手算核对 ──────────────────────────────────────────────


@requires_pg
def test_erp_equals_earnings_yield_minus_treasury_for_every_row() -> None:
    """逐行核对 `erp_10y == 1/PE × 100 − 10Y`（两列都在库里，可独立重算）。"""

    async def _go(session: AsyncSession) -> list[tuple[date, float, float, float]]:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT e.period,
                           e.value  AS erp,
                           p.value  AS pe,
                           t.value  AS treasury
                    FROM industry_metrics e
                    JOIN industry_metrics p
                      ON p.industry_key = 'market' AND p.stock_id = 0
                     AND p.metric_key = 'pe_csi300' AND p.period = e.period
                    JOIN industry_metrics t
                      ON t.industry_key = 'market' AND t.stock_id = 0
                     AND t.metric_key = 'cn_treasury_yield_10y' AND t.period = e.period
                    WHERE e.industry_key = 'market' AND e.stock_id = 0
                      AND e.metric_key = 'erp_10y'
                      AND e.value IS NOT NULL AND p.value IS NOT NULL
                      AND t.value IS NOT NULL AND p.value <> 0
                    ORDER BY e.period
                    """
                )
            )
        ).all()
        if not rows:
            pytest.skip("库里没有 erp_10y + PE + 国债三列齐全的行（先跑回补脚本）")
        return [(r.period, float(r.erp), float(r.pe), float(r.treasury)) for r in rows]

    rows = asyncio.run(_with_rollback(_go))
    for period, erp, pe, treasury in rows:
        assert erp == pytest.approx(100.0 / pe - treasury, abs=1e-3), period


# ── 4) 换手率口径与源一致 ─────────────────────────────────────────────


@requires_pg
@requires_tushare
def test_direct_turnover_matches_upstream_free_float_column() -> None:
    """直取指数：落库值 == 上游 `index_dailybasic.turnover_rate_f`（独立重取对拍）。"""

    async def _go(session: AsyncSession) -> dict[str, float]:
        row = (
            await session.execute(
                text(
                    """
                    SELECT period, value FROM industry_metrics
                    WHERE industry_key='market' AND stock_id=0
                      AND metric_key='idx_turnover_ratio_sse' AND value IS NOT NULL
                    ORDER BY period DESC LIMIT 1
                    """
                )
            )
        ).first()
        if row is None:
            pytest.skip("库里没有 idx_turnover_ratio_sse 的行（先跑回补脚本）")
        return {"period": row.period, "value": float(row.value)}  # type: ignore[dict-item]

    stored = asyncio.run(_with_rollback(_go))
    try:
        client = _tushare_client()
        df = asyncio.run(
            client.fetch_index_dailybasic(
                ts_code="000001.SH",
                start_date=stored["period"].strftime("%Y%m%d"),  # type: ignore[union-attr]
                end_date=stored["period"].strftime("%Y%m%d"),  # type: ignore[union-attr]
            )
        )
    except Exception as exc:  # noqa: BLE001 — 上游不可达时 skip，不假装通过
        pytest.skip(f"TuShare 不可达/无权限: {exc}")
    assert len(df) == 1, f"上游对 {stored['period']} 应恰好返回 1 行"
    upstream = float(df.iloc[0]["turnover_rate_f"])
    assert stored["value"] == pytest.approx(upstream, abs=1e-4)
    # 口径判据：落库的不是总股本口径那一列（两列实测不同值，取错了会撞上）
    total = float(df.iloc[0]["turnover_rate"])
    assert total != upstream, "样本日两列同值 ⇒ 这条对拍无法区分口径，请换样本日"


@requires_pg
def test_turnover_rows_declare_free_float_caliber_in_extra() -> None:
    """换手率行的 `extra.caliber` 必须是自由流通口径（spec §S1.9 验收：口径可查）。"""

    async def _go(session: AsyncSession) -> list[tuple[str, str, dict[str, Any]]]:
        rows = (
            await session.execute(
                select(IndustryMetric).where(
                    IndustryMetric.industry_key == "market",
                    IndustryMetric.metric_key.in_(list(svc.TURNOVER_RATIO_KEYS)),
                    IndustryMetric.value.is_not(None),
                )
            )
        ).scalars()
        return [(r.metric_key, r.source, dict(r.extra or {})) for r in rows]

    rows = asyncio.run(_with_rollback(_go))
    if not rows:
        pytest.skip("库里没有换手率行（先跑回补脚本）")
    for key, _source, extra in rows:
        spec = next(s for s in svc.INDEX_TURNOVER_SPECS if s.metric_key == key)
        if spec.source == svc.SRC_INDEX_DAILYBASIC:
            assert extra["caliber"] == "free_float", key
            assert extra["source_column"] == "turnover_rate_f", key
        else:
            assert extra["caliber"] == "free_float_weighted", key
            assert extra["attribution"] == "current_index_weight_snapshot", key


# ── 5) 自建换手率可独立复算 ───────────────────────────────────────────


@requires_pg
@requires_tushare
def test_self_built_turnover_matches_independent_weighted_average() -> None:
    """北证50 落库值 == 独立重算（成分股 `turnover_rate_f` 按快照权重加权）。"""

    async def _go(session: AsyncSession) -> list[tuple[date, float, str]]:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT period, value, extra
                    FROM industry_metrics
                    WHERE industry_key='market' AND stock_id=0
                      AND metric_key='idx_turnover_ratio_bse50' AND value IS NOT NULL
                    ORDER BY period DESC LIMIT 3
                    """
                )
            )
        ).all()
        if not rows:
            pytest.skip("库里没有北证50 换手率的行（先跑回补脚本）")
        return [(r.period, float(r.value), str(r.extra.get("weight_period"))) for r in rows]

    stored = asyncio.run(_with_rollback(_go))
    try:
        client = _tushare_client()

        async def _independent(day: date, weight_period: str) -> float:
            """**独立写法**：逐成分取权重与当日 `turnover_rate_f`，自己加权。"""
            w = await client.fetch_index_weight(
                index_code="899050.BJ",
                trade_date=weight_period.replace("-", ""),
            )
            basic = await client.fetch_daily_basic_by_trade_date(day.strftime("%Y%m%d"))
            quotes = {
                str(r["ts_code"]): float(r["turnover_rate_f"])
                for _, r in basic.iterrows()
                if r.get("turnover_rate_f") is not None
                and r["turnover_rate_f"] == r["turnover_rate_f"]
            }
            num = 0.0
            den = 0.0
            for _, r in w.iterrows():
                v = quotes.get(str(r["con_code"]))
                if v is None:
                    continue
                num += float(r["weight"]) * v
                den += float(r["weight"])
            assert den > 0
            return num / den

        got = asyncio.run(asyncio.gather(*[_independent(d, wp) for d, _, wp in stored]))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"TuShare 不可达/无权限: {exc}")
    for (period, value, _wp), expected in zip(stored, got, strict=True):
        assert value == pytest.approx(float(expected), rel=1e-6), period


# ── 6) 缺数不静默为 0 ─────────────────────────────────────────────────


@requires_pg
def test_null_rows_always_carry_missing_reason() -> None:
    """`value IS NULL` 的行必须带 `extra.missing_reason`（有行 ≠ 有值）。"""

    async def _go(session: AsyncSession) -> tuple[int, list[str]]:
        rows = (
            await session.execute(
                select(IndustryMetric.metric_key, IndustryMetric.extra).where(
                    IndustryMetric.industry_key == "market",
                    IndustryMetric.stock_id == 0,
                    IndustryMetric.metric_key.in_(svc.INGEST_METRIC_KEYS),
                    IndustryMetric.value.is_(None),
                )
            )
        ).all()
        bad = [r.metric_key for r in rows if not (r.extra or {}).get("missing_reason")]
        return len(rows), bad

    total, bad = asyncio.run(_with_rollback(_go))
    assert bad == [], f"{len(bad)} 行 NULL 没有 missing_reason，例如 {sorted(set(bad))[:5]}"
    assert total >= 0  # 0 行也合法（全都有值）


@requires_pg
def test_coverage_helper_reports_all_thirteen_metrics() -> None:
    """`metric_coverage` 对 13 个 spec 指标逐条给 covered/null/missing。"""

    async def _go(session: AsyncSession) -> list[svc.MetricCoverage]:
        day_hi = await _latest_quote_day(session)
        return await svc.metric_coverage(session, day_lo=day_hi - timedelta(days=30), day_hi=day_hi)

    coverage = asyncio.run(_with_rollback(_go))
    assert {c.metric_key for c in coverage} == set(svc.INGEST_METRIC_KEYS)
    for c in coverage:
        assert c.rows == c.covered_days + c.null_days


# ── 7) Ruling 59 fix round 1：ERP 的落库天锚在自身输入上（不依赖 daily_quotes）──


class _EmptyTushare:
    """只回空表的假上游：本用例的落点是 ERP 的锚，不是行情类指标。"""

    async def fetch_margin(self, **_: Any) -> pd.DataFrame:
        return pd.DataFrame()

    async def fetch_index_dailybasic(self, **_: Any) -> pd.DataFrame:
        return pd.DataFrame()

    async def fetch_index_weight(self, **_: Any) -> pd.DataFrame:
        return pd.DataFrame()

    async def fetch_daily_basic_by_trade_date(self, *_: Any, **__: Any) -> pd.DataFrame:
        return pd.DataFrame()


class _FakeTreasury:
    def __init__(self, values: dict[date, float]) -> None:
        self._values = values

    async def fetch_china_bond_yield(self, start_date: str, end_date: str) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"曲线名称": "中债国债收益率曲线", "日期": day, "10年": value}
                for day, value in sorted(self._values.items())
            ]
        )


@requires_pg
def test_erp_is_anchored_on_its_own_inputs_not_on_daily_quotes() -> None:
    """PE ∩ 10Y 的日子**即使不是 `daily_quotes` 交易日**也要落 `erp_10y` / 10Y 行。

    Ruling 59 Job 3b：v1 把 ERP 锚在行情底座上，实测只有 829 天（3.4 年 < spec §S1.11
    的 5 年）。ERP = 1/PE − 10Y 与 `daily_quotes` 无关，锚必须落在它自己的两个输入上。
    本用例用一个**不在行情底座里**的交易日，锁住"改锚真的生效"。
    """

    async def _go(
        session: AsyncSession,
    ) -> tuple[date, set[date], dict[str, IndustryMetric], Any]:
        day_hi = await _latest_quote_day(session)
        pe_day = day_hi - timedelta(days=100)
        quote_days = set(await svc._quote_days(session, pe_day, day_hi))
        assert pe_day not in quote_days, "本用例要求 PE 日不在行情底座里（否则证明不了改锚）"
        # 代价：往库里写一行 PE 底座（同事务回滚，不落库）
        session.add(
            IndustryMetric(
                industry_key="market",
                stock_id=0,
                metric_key=svc.ERP_PE_METRIC_KEY,
                source="tushare_index_dailybasic",
                source_tier="official",
                freq="daily",
                period=pe_day,
                value=10.0,
                unit="倍",
                published_at=svc.published_at_for(pe_day),
                caliber_version=MARKET_ACTIVITY_CALIBER_VERSION,
            )
        )
        await session.flush()
        stats = await svc.upsert_metric_range(
            session,
            pe_day,
            day_hi,
            tushare=_EmptyTushare(),
            akshare=_FakeTreasury({pe_day: 2.0, day_hi: 1.6864}),
        )
        rows = (
            await session.execute(
                select(IndustryMetric).where(
                    IndustryMetric.industry_key == "market",
                    IndustryMetric.stock_id == 0,
                    IndustryMetric.period == pe_day,
                    IndustryMetric.metric_key.in_(svc.ERP_EXTENSION_KEYS),
                )
            )
        ).scalars()
        return pe_day, quote_days, {r.metric_key: r for r in rows}, stats

    pe_day, quote_days, by_key, stats = asyncio.run(_with_rollback(_go))
    assert set(by_key) == set(svc.ERP_EXTENSION_KEYS), "非行情交易日的 ERP 与 10Y 两侧都要落行"
    erp, treasury = by_key["erp_10y"], by_key["cn_treasury_yield_10y"]
    assert float(treasury.value) == pytest.approx(2.0)
    assert float(erp.value) == pytest.approx(100.0 / 10.0 - 2.0), "1/PE×100 − 10Y 必须成立"
    assert erp.published_at is not None and erp.caliber_version == MARKET_ACTIVITY_CALIBER_VERSION
    assert stats.per_metric_covered["erp_10y"] >= 1
    assert pe_day not in quote_days  # 前提复核：这天的行**不可能**来自行情循环
