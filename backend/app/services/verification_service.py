"""Independent cross-source verification (R4 的另一半).

**为什么需要它**：`data_expectations` 的断言全部在**本库内**求值（行数、非空率、
区间），它们能发现"没到/明显坏"，但**发现不了上游给错数**——如果 TuShare 把某只
股票收盘价写错，本地所有断言依旧全绿。要发现这类错误，只能拿一个**与本库无血缘**
的源去对同一事实。

本模块的设计约束（对应 ADR 0001 的"不做多源降级链"）：

* **只读比对，绝不写业务表**：校验源只回答"这两个数一样吗"，不参与补数
  （多源降级会让"哪个源对"变成隐式选择，最终没人能解释一个数字的来历）。
* **抽样而非全量**：全市场逐只外呼会打爆限频，且收益递减；固定抽 N 只 +
  按 ``md5(symbol)`` 排序保证同一天抽样可复现（不是随机，便于复盘）。
* **不通过也绝不自动修**：结果写进 ``data_quality_runs`` 并暴露到
  ``/ops/verification``；修数据是人的决定（自动覆盖会把错误固化）。
* **源不可用 ≠ 通过**：全部取不到时状态是 ``error``，不是 ``pass``。宁可红。

比对口径：

* ``close``：相对误差 ≤ 0.5%（不复权口径与 ``daily_quotes.close`` 同源语义，
  容忍极小浮点/四舍五入差异）；
* ``pct_chg``：绝对差 ≤ 0.3 个百分点（同口径的百分比）；
* 一致率 ≥95% → ``pass``；≥85% → ``warn``；否则 ``fail``。

已知边界（诚实声明）：只校验**当日收盘价与涨跌幅**，不校验成交量/成交额
（两个源对"手/股"和"元/千元"的单位约定不同，比对会得到假阳性）；只覆盖
沪深主板/创业板/科创板（北交所 secid 前缀不同，暂不抽）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.data_quality import DataQualityRun

logger = logging.getLogger(__name__)

SOURCE = "eastmoney_push2his"
EXPECTATION_KEY = "verify.daily_quotes.independent"
DEFAULT_SAMPLE = 12
PRICE_TOLERANCE = 0.005  # 0.5% 相对误差
PCT_TOLERANCE = 0.3  # 绝对差 0.3 个百分点
PASS_RATIO = 0.95
WARN_RATIO = 0.85

#: 交易所 → 东财 secid 前缀（沪 1 / 深 0）。北交所不在抽样范围。
_SECID_PREFIX = {"Shanghai_Stocks": "1", "Shenzen_Stocks": "0"}


@dataclass
class Comparison:
    symbol: str
    local_close: float | None = None
    remote_close: float | None = None
    local_pct: float | None = None
    remote_pct: float | None = None
    matched: bool = False
    reason: str | None = None


@dataclass
class VerificationResult:
    day: date
    status: str
    checked: int
    matched: int
    ratio: float
    comparisons: list[Comparison] = field(default_factory=list)
    source_error: str | None = None

    @property
    def detail(self) -> dict[str, Any]:
        return {
            "source": SOURCE,
            "sample_size": self.checked,
            "matched": self.matched,
            "ratio": self.ratio,
            "source_error": self.source_error,
            "mismatches": [
                {
                    "symbol": c.symbol,
                    "local_close": c.local_close,
                    "remote_close": c.remote_close,
                    "local_pct": c.local_pct,
                    "remote_pct": c.remote_pct,
                    "reason": c.reason,
                }
                for c in self.comparisons
                if not c.matched
            ],
        }


def compare_row(
    *,
    symbol: str,
    local_close: float | None,
    local_pct: float | None,
    remote_close: float | None,
    remote_pct: float | None,
) -> Comparison:
    """纯函数：单只股票的比对判定（单测直接覆盖，不需要网络/库）."""
    out = Comparison(
        symbol=symbol,
        local_close=local_close,
        remote_close=remote_close,
        local_pct=local_pct,
        remote_pct=remote_pct,
    )
    if local_close is None or remote_close is None:
        out.reason = "missing_value"
        return out
    if remote_close <= 0 or local_close <= 0:
        out.reason = "non_positive_price"
        return out
    price_diff = abs(local_close - remote_close) / remote_close
    if price_diff > PRICE_TOLERANCE:
        out.reason = f"close_diff={price_diff:.4f}"
        return out
    if local_pct is not None and remote_pct is not None:
        if abs(local_pct - remote_pct) > PCT_TOLERANCE:
            out.reason = f"pct_diff={abs(local_pct - remote_pct):.4f}"
            return out
    out.matched = True
    return out


def summarize(
    day: date, comparisons: list[Comparison], source_error: str | None
) -> VerificationResult:
    checked = len(comparisons)
    matched = sum(1 for c in comparisons if c.matched)
    ratio = (matched / checked) if checked else 0.0
    if source_error is not None and checked == 0:
        status = "error"
    elif checked == 0:
        status = "error"
    elif ratio >= PASS_RATIO:
        status = "pass"
    elif ratio >= WARN_RATIO:
        status = "warn"
    else:
        status = "fail"
    return VerificationResult(
        day=day,
        status=status,
        checked=checked,
        matched=matched,
        ratio=ratio,
        comparisons=comparisons,
        source_error=source_error,
    )


async def _sample(db: AsyncSession, day: date, limit: int) -> list[dict[str, Any]]:
    rows = (
        (
            await db.execute(
                text(
                    """
                SELECT s.symbol, s.exchange, q.close, q.pct_chg
                FROM daily_quotes q
                JOIN stocks s ON s.id = q.stock_id
                WHERE q.trade_date = :day
                  AND q.close > 0
                  AND s.exchange IN ('Shanghai_Stocks', 'Shenzen_Stocks')
                  AND s.symbol ~ '^(60|00|30|68)'
                ORDER BY md5(s.symbol)
                LIMIT :lim
                """
                ),
                {"day": day, "lim": limit},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def verify_day(
    db: AsyncSession,
    *,
    day: date,
    client: Any | None = None,
    sample: int = DEFAULT_SAMPLE,
) -> VerificationResult:
    """抽样比对某一天的收盘价/涨跌幅；不写业务表，只返回结果（调用方决定是否落账）."""
    rows = await _sample(db, day, sample)
    if not rows:
        return summarize(day, [], "no_local_rows")

    if client is None:
        from app.core.providers.eastmoney_client import (  # noqa: PLC0415
            get_eastmoney_client,
        )

        client = get_eastmoney_client()

    comparisons: list[Comparison] = []
    errors: list[str] = []
    beg = day.strftime("%Y%m%d")
    for row in rows:
        prefix = _SECID_PREFIX.get(row["exchange"])
        if prefix is None:
            continue
        secid = f"{prefix}.{row['symbol']}"
        try:
            klines = await client.fetch_stock_daily_kline(secid, beg, beg)
        except Exception as exc:  # 网络/限频/格式错误都算"这只查不了"，不中断整轮
            errors.append(f"{row['symbol']}:{type(exc).__name__}")
            continue
        remote = next((k for k in klines if k["trade_date"] == day), None)
        if remote is None:
            errors.append(f"{row['symbol']}:no_row")
            continue
        comparisons.append(
            compare_row(
                symbol=row["symbol"],
                local_close=float(row["close"]) if row["close"] is not None else None,
                local_pct=float(row["pct_chg"]) if row["pct_chg"] is not None else None,
                remote_close=remote["close"],
                remote_pct=remote.get("pct_chg"),
            )
        )

    source_error = None
    if not comparisons:
        source_error = "; ".join(errors[:5]) or "no_comparable_rows"
    elif errors:
        source_error = f"partial:{len(errors)}"
    return summarize(day, comparisons, source_error)


async def persist(db: AsyncSession, result: VerificationResult, *, severity: str = "warn") -> Any:
    """落 ``data_quality_runs``（severity 默认 warn：跨源差异不阻断发布，但必须可见）."""
    run_id = uuid4()
    db.add(
        DataQualityRun(
            run_id=run_id,
            expectation_key=EXPECTATION_KEY,
            domain="daily_quotes",
            business_date=result.day,
            severity=severity,
            status=result.status,
            observed=result.ratio,
            threshold=PASS_RATIO,
            detail={
                "description": "抽样与独立源（东财 push2his）比对当日收盘价/涨跌幅",
                **result.detail,
            },
        )
    )
    await db.commit()
    return run_id


async def latest(db: AsyncSession, day: date | None = None) -> dict[str, Any] | None:
    """最近一次跨源校验结果（读面用；不触发外呼）."""
    stmt = text(
        """
        SELECT business_date, status, observed, threshold, detail, checked_at
        FROM data_quality_runs
        WHERE expectation_key = :key
          AND (CAST(:day AS date) IS NULL OR business_date = CAST(:day AS date))
        ORDER BY checked_at DESC
        LIMIT 1
        """
    )
    row = (await db.execute(stmt, {"key": EXPECTATION_KEY, "day": day})).first()
    if row is None:
        return None
    return {
        "date": row[0].isoformat() if row[0] else None,
        "status": row[1],
        "ratio": float(row[2]) if row[2] is not None else None,
        "threshold": float(row[3]) if row[3] is not None else None,
        "detail": row[4],
        "checked_at": row[5].isoformat() if row[5] else None,
    }
