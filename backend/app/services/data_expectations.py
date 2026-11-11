"""声明式数据质量期望（P5）.

十一个 SQL 断言，覆盖"行数够但数据错了"的典型形态。设计上刻意**不引入** dbt /
Great Expectations：断言就是一段返回 ``(observed, threshold)`` 的 SQL，纯函数式比较，
跑在既有 session 上，零新基础设施。

比较方向（``compare``）：

- ``gte``：observed ≥ threshold（覆盖率类）
- ``lte``：observed ≤ threshold（违规计数类）
- ``eq``：observed == threshold（不变量类）

严重级：

- ``block``：失败 ⇒ 该分区**不封存**，下一轮对账自动重拉，派生指标不推进。
- ``warn``：失败只记录（停牌日、ST、上游迟到等合法偏差）。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.data_quality import DataQualityRun

logger = logging.getLogger(__name__)

Compare = Literal["gte", "lte", "eq"]
Severity = Literal["block", "warn"]
Status = Literal["pass", "warn", "fail", "error"]


@dataclass(frozen=True)
class Expectation:
    """一条可对单日执行的断言。SQL 必须返回一行 ``(observed, threshold)``。"""

    key: str
    domain: str
    severity: Severity
    compare: Compare
    description: str
    sql: str
    #: 该断言的"预期有数据"的前提域；为空表示总是执行。用于非交易日/未计算日跳过。
    requires_domain: str | None = None


@dataclass(frozen=True)
class ExpectationResult:
    key: str
    domain: str
    severity: Severity
    status: Status
    observed: float | None
    threshold: float | None
    business_date: date | None
    description: str
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "domain": self.domain,
            "severity": self.severity,
            "status": self.status,
            "observed": self.observed,
            "threshold": self.threshold,
            "business_date": self.business_date.isoformat() if self.business_date else None,
            "description": self.description,
            "detail": self.detail,
        }


#: 11 条基线断言。改动任何一条都要同时在 ``tests/test_data_expectations.py`` 里
#: 锁定语义（否则"门禁"就变成了随代码一起漂移的装饰）。
EXPECTATIONS: tuple[Expectation, ...] = (
    Expectation(
        key="dq.daily_quotes.rows_ratio",
        domain="daily_quotes",
        severity="block",
        compare="gte",
        description="日线行数 ≥ 90% 在市标的数",
        sql="""
            SELECT count(*)::numeric AS observed,
                   0.9 * (SELECT count(*) FROM stocks)::numeric AS threshold
            FROM daily_quotes WHERE trade_date = :day
        """,
    ),
    Expectation(
        key="dq.daily_quotes.pct_chg_nonnull",
        domain="daily_quotes",
        severity="block",
        compare="gte",
        description="pct_chg 非空率 ≥ 99%",
        sql="""
            SELECT (count(pct_chg)::numeric / NULLIF(count(*), 0)) AS observed,
                   0.99::numeric AS threshold
            FROM daily_quotes WHERE trade_date = :day
        """,
    ),
    Expectation(
        key="dq.daily_quotes.ohlc_sane",
        domain="daily_quotes",
        severity="block",
        compare="lte",
        description="high ≥ low 且 close > 0（违规行数 = 0）",
        sql="""
            SELECT count(*)::numeric AS observed, 0::numeric AS threshold
            FROM daily_quotes
            WHERE trade_date = :day AND (high < low OR close <= 0)
        """,
    ),
    Expectation(
        key="dq.daily_quotes.price_identity",
        domain="daily_quotes",
        severity="block",
        compare="lte",
        description="close / pre_close 与 pct_chg 的算术恒等式（违规率 ≤ 0.1%）",
        # 独立于上游的第二道判据：同一行的 close、pre_close、pct_chg 必须自洽。
        # 上游把 close 写错（而 pct_chg 仍按真实行情算）时，行数/非空率/区间全部达标，
        # 只有这条恒等式会红。容差 0.05 个百分点吸收四舍五入。
        sql="""
            SELECT (count(*) FILTER (
                       WHERE pre_close > 0 AND pct_chg IS NOT NULL
                         AND abs((close / pre_close - 1) * 100 - pct_chg) > 0.05
                   )::numeric / NULLIF(count(*), 0)) AS observed,
                   0.001::numeric AS threshold
            FROM daily_quotes WHERE trade_date = :day
        """,
    ),
    Expectation(
        key="dq.daily_quotes.pct_chg_bounds",
        domain="daily_quotes",
        severity="warn",
        compare="lte",
        description="pct_chg 超出 ±44%（违规行数 = 0；停牌/新股/ST 允许 warn）",
        sql="""
            SELECT count(*)::numeric AS observed, 0::numeric AS threshold
            FROM daily_quotes
            WHERE trade_date = :day AND (pct_chg < -44 OR pct_chg > 44)
        """,
    ),
    Expectation(
        key="dq.price_limits.coverage",
        domain="price_limits",
        severity="block",
        compare="gte",
        description="涨跌停价行数 ≥ 90% 在市标的数",
        sql="""
            SELECT count(*)::numeric AS observed,
                   0.9 * (SELECT count(*) FROM stocks)::numeric AS threshold
            FROM stock_price_limits WHERE trade_date = :day
        """,
    ),
    Expectation(
        key="dq.sentiment.zt_le_total",
        domain="sentiment",
        severity="block",
        compare="lte",
        description="涨停家数 ≤ 当日行情行数（不可能有更多涨停）",
        sql="""
            SELECT s.zt_count::numeric AS observed, q.cnt::numeric AS threshold
            FROM market_sentiment_daily s
            CROSS JOIN (SELECT count(*) AS cnt FROM daily_quotes WHERE trade_date = :day) q
            WHERE s.trade_date = :day
        """,
    ),
    Expectation(
        key="dq.sentiment.broken_rate_range",
        domain="sentiment",
        severity="block",
        compare="lte",
        description="炸板率 ∈ [0,1]（越界行数 = 0）",
        sql="""
            SELECT count(*)::numeric AS observed, 0::numeric AS threshold
            FROM market_sentiment_daily
            WHERE trade_date = :day AND (broken_rate < 0 OR broken_rate > 1)
        """,
    ),
    Expectation(
        key="dq.sw.level1_count",
        domain="sw_industry",
        severity="warn",
        compare="eq",
        description="申万一级行业数 = 31",
        sql="""
            SELECT count(*)::numeric AS observed, 31::numeric AS threshold
            FROM sw_industry_classes WHERE level = 1
        """,
    ),
    Expectation(
        key="dq.sector_moneyflow.eod_full_present",
        domain="sector_moneyflow",
        severity="block",
        compare="gte",
        description="当日存在收盘全量板块快照（eod_full 行数 ≥ 1）",
        sql="""
            SELECT count(*)::numeric AS observed, 1::numeric AS threshold
            FROM sector_moneyflow_snapshots
            WHERE trade_date = :day AND capture_kind = 'eod_full'
        """,
        requires_domain="sector_moneyflow",
    ),
    Expectation(
        key="dq.market_breadth.partition_invariant",
        domain="market_breadth",
        severity="warn",
        compare="eq",
        description="广度表 up+down+flat 与当日行情行数的差 = 0",
        sql="""
            SELECT (COALESCE(b.total, 0) - COALESCE(q.cnt, 0))::numeric AS observed,
                   0::numeric AS threshold
            FROM (SELECT up_count + down_count + flat_count AS total
                  FROM market_breadth_daily WHERE trade_date = :day) b
            CROSS JOIN (SELECT count(*) AS cnt FROM daily_quotes WHERE trade_date = :day) q
        """,
        requires_domain="market_breadth",
    ),
)


def _status_for(
    expectation: Expectation, observed: float | None, threshold: float | None
) -> Status:
    if observed is None or threshold is None:
        # observed 为 NULL 表示"断言的前提不成立"（如当日无行情、无派生行）。
        # 对 block 断言这是**缺数据**，仍判 fail；对 warn 断言判 warn。
        return "fail" if expectation.severity == "block" else "warn"
    if expectation.compare == "gte":
        ok = observed >= threshold
    elif expectation.compare == "lte":
        ok = observed <= threshold
    else:
        ok = observed == threshold
    if ok:
        return "pass"
    return "fail" if expectation.severity == "block" else "warn"


async def evaluate(
    db: AsyncSession,
    *,
    day: date,
    only: Sequence[str] | None = None,
) -> list[ExpectationResult]:
    """只读评估（不落库）；供 API 与 job 共用。"""
    wanted = set(only) if only is not None else None
    results: list[ExpectationResult] = []
    for expectation in EXPECTATIONS:
        if wanted is not None and expectation.key not in wanted:
            continue
        try:
            row = (await db.execute(text(expectation.sql), {"day": day})).first()
        except Exception as exc:  # noqa: BLE001 — 单条断言失败不得吞掉其他断言
            await db.rollback()
            results.append(
                ExpectationResult(
                    key=expectation.key,
                    domain=expectation.domain,
                    severity=expectation.severity,
                    status="error",
                    observed=None,
                    threshold=None,
                    business_date=day,
                    description=expectation.description,
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
            continue

        if row is None:
            observed = threshold = None
        else:
            observed = float(row[0]) if row[0] is not None else None
            threshold = float(row[1]) if row[1] is not None else None
        results.append(
            ExpectationResult(
                key=expectation.key,
                domain=expectation.domain,
                severity=expectation.severity,
                status=_status_for(expectation, observed, threshold),
                observed=observed,
                threshold=threshold,
                business_date=day,
                description=expectation.description,
                detail=None if row is not None else "no_row",
            )
        )
    return results


async def persist(
    db: AsyncSession,
    results: Sequence[ExpectationResult],
    *,
    run_id: Any | None = None,
) -> Any:
    """把一次评估写入 ``data_quality_runs``（幂等靠 run_id，不靠 key）。"""
    run = run_id or uuid4()
    db.add_all(
        [
            DataQualityRun(
                run_id=run,
                expectation_key=item.key,
                domain=item.domain,
                business_date=item.business_date,
                severity=item.severity,
                status=item.status,
                observed=item.observed,
                threshold=item.threshold,
                detail={"description": item.description, "detail": item.detail},
            )
            for item in results
        ]
    )
    await db.commit()
    return run


async def run_for_day(db: AsyncSession, *, day: date) -> list[ExpectationResult]:
    """评估 + 落库；供对账 job 尾巴调用。"""
    results = await evaluate(db, day=day)
    failed = [r.key for r in results if r.status in ("fail", "error")]
    if failed:
        logger.warning("DATA QUALITY day=%s failed=%s", day, failed)
    else:
        logger.info("DATA QUALITY day=%s all expectations pass", day)
    await persist(db, results)
    return results


async def latest_for_day(db: AsyncSession, *, day: date) -> list[ExpectationResult]:
    """读最近一次落库结果（无记录返回空列表，由调用方决定展示口径）。"""
    stmt = (
        select(DataQualityRun)
        .where(DataQualityRun.business_date == day)
        .order_by(DataQualityRun.id.desc())
        .limit(len(EXPECTATIONS) * 4)
    )
    rows = (await db.execute(stmt)).scalars().all()
    seen: set[str] = set()
    out: list[ExpectationResult] = []
    for row in rows:
        if row.expectation_key in seen:
            continue
        seen.add(row.expectation_key)
        out.append(
            ExpectationResult(
                key=row.expectation_key,
                domain=row.domain,
                severity=row.severity,  # type: ignore[arg-type]
                status=row.status,  # type: ignore[arg-type]
                observed=float(row.observed) if row.observed is not None else None,
                threshold=float(row.threshold) if row.threshold is not None else None,
                business_date=row.business_date,
                description=str((row.detail or {}).get("description", "")),
                detail=(row.detail or {}).get("detail"),  # type: ignore[union-attr]
            )
        )
    return out


def has_blocking_failure(results: Sequence[ExpectationResult]) -> bool:
    return any(item.severity == "block" and item.status in ("fail", "error") for item in results)
