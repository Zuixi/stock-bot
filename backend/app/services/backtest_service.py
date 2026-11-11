"""Single-rule event-study backtest (spec §S2.3).

**这不是策略回测**：不构建组合、不调仓、不扣成本，只回答"历史上该信号出现后
N 日，行业等权篮子的远期收益相对基准如何"。因此：

* 资产 = 信号日 SCD2 成分股（``sw_industry_member_periods``）的**等权收益中位数**；
* 基准 = 配置的指数（默认沪深 300）同窗口收益；
* 超额 = 篮子收益 − 基准收益；
* ``split`` 必填（样本内 / 样本外），防止全样本调参后全样本报收益；
* ``pit_enforced`` 为假时**拒绝落库**——没有通过 PIT 对照的回测不允许展示。

已知边界（诚实声明，不粉饰）：

* 用未复权 ``close`` 计算，不含分红再投资；行业篮子等权，不按市值加权；
* 事件日若不是交易日，取其后第一个交易日为 T0；
* 样本 <30 仍落库，但 ``insufficient_sample=true``，前端必须显示；
* 只支持行业 scope（``IndustryConfig.sw_l3_codes`` 非空），全市场/主题不适用。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import mean, median
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.research_backtest import BacktestRun
from app.services.industry_registry import INDUSTRIES

MIN_SAMPLE = 30
MAX_HORIZON = 250
DEFAULT_BENCHMARK = "000300.SH"


class BacktestError(ValueError):
    """回测请求不合法（路由映射为 422）."""


@dataclass(frozen=True)
class Event:
    day: date
    created_at: datetime


def _as_date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def validate_request(
    *,
    scope_key: str,
    window_from: date,
    window_to: date,
    horizons: list[int],
    split: dict[str, Any],
) -> None:
    """参数校验：范围、期限、切分。纯函数，单测直接覆盖."""
    cfg = INDUSTRIES.get(scope_key)
    if cfg is None or not cfg.is_industry:
        raise BacktestError(f"scope {scope_key!r} 不是已登记的行业 scope")
    if window_from > window_to:
        raise BacktestError("window_from 不能晚于 window_to")
    if (window_to - window_from).days > 3650:
        raise BacktestError("窗口最长 10 年")
    if not horizons or any(h < 1 or h > MAX_HORIZON for h in horizons):
        raise BacktestError(f"horizons 必须在 1..{MAX_HORIZON}")
    if len(set(horizons)) != len(horizons):
        raise BacktestError("horizons 不能重复")

    def _range(name: str) -> tuple[date, date]:
        raw = split.get(name) or {}
        if not raw.get("from") or not raw.get("to"):
            raise BacktestError(f"split.{name} 必须同时给 from/to")
        lo, hi = _as_date(raw["from"]), _as_date(raw["to"])
        if lo > hi:
            raise BacktestError(f"split.{name} 的 from 不能晚于 to")
        if lo < window_from or hi > window_to:
            raise BacktestError(f"split.{name} 必须落在回测窗口内")
        return lo, hi

    in_lo, in_hi = _range("in_sample")
    out_lo, out_hi = _range("out_of_sample")
    if max(in_lo, out_lo) <= min(in_hi, out_hi):
        raise BacktestError("样本内与样本外区间不能重叠")
    if not (in_lo <= in_hi < out_lo <= out_hi or out_lo <= out_hi < in_lo <= in_hi):
        raise BacktestError("样本内/样本外必须首尾相接（先内后外或先外后内）")


def summarize(returns: list[float], excess: list[float]) -> dict[str, Any]:
    """逐期限统计；空样本返回 n=0 与空值，不伪造 0."""
    n = len(returns)
    if n == 0:
        return {
            "n": 0,
            "mean": None,
            "median": None,
            "win_rate": None,
            "excess_mean": None,
            "excess_median": None,
            "excess_win_rate": None,
            "insufficient_sample": True,
        }
    return {
        "n": n,
        "mean": mean(returns),
        "median": median(returns),
        "win_rate": sum(r > 0 for r in returns) / n,
        "excess_mean": mean(excess) if excess else None,
        "excess_median": median(excess) if excess else None,
        "excess_win_rate": (sum(r > 0 for r in excess) / len(excess)) if excess else None,
        "insufficient_sample": n < MIN_SAMPLE,
    }


async def _events(
    db: AsyncSession, *, scope_key: str, rule_key: str, window_from: date, window_to: date
) -> list[Event]:
    rows = (
        await db.execute(
            text(
                """
                SELECT effective_date, created_at
                FROM industry_signals
                WHERE industry_key = :scope
                  AND signal_type = :rule
                  AND effective_date BETWEEN :lo AND :hi
                ORDER BY effective_date
                """
            ),
            {"scope": scope_key, "rule": rule_key, "lo": window_from, "hi": window_to},
        )
    ).all()
    return [Event(day=_as_date(r[0]), created_at=r[1]) for r in rows]


def _pit_violations(events: list[Event]) -> list[date]:
    """信号必须在生效日前后 1 天内生成；更晚 = 回填/补算，判 PIT 不通过."""
    return [e.day for e in events if e.created_at.date() > e.day + timedelta(days=1)]


async def _trading_days(db: AsyncSession, start: date, horizon: int) -> tuple[date, date] | None:
    rows = (
        (
            await db.execute(
                text(
                    """
                SELECT DISTINCT trade_date FROM daily_quotes
                WHERE trade_date >= :start
                ORDER BY trade_date
                LIMIT :lim
                """
                ),
                {"start": start, "lim": horizon + 1},
            )
        )
        .scalars()
        .all()
    )
    if len(rows) < horizon + 1:
        return None
    return rows[0], rows[-1]


async def _basket_return(db: AsyncSession, *, codes: list[str], d0: date, dn: date) -> float | None:
    rows = (
        await db.execute(
            text(
                """
                SELECT q0.close AS c0, qn.close AS cn
                FROM sw_industry_member_periods p
                JOIN stocks s ON s.symbol = split_part(p.stock_code, '.', 1)
                JOIN daily_quotes q0 ON q0.stock_id = s.id AND q0.trade_date = :d0
                JOIN daily_quotes qn ON qn.stock_id = s.id AND qn.trade_date = :dn
                WHERE p.industry_code = ANY(:codes)
                  AND p.effective_from <= :d0
                  AND (p.effective_to IS NULL OR p.effective_to > :d0)
                  AND q0.close > 0 AND qn.close > 0
                """
            ),
            {"codes": codes, "d0": d0, "dn": dn},
        )
    ).all()
    returns = [float(r[1]) / float(r[0]) - 1 for r in rows]
    return median(returns) if returns else None


async def _benchmark_return(db: AsyncSession, ts_code: str, d0: date, dn: date) -> float | None:
    rows = (
        await db.execute(
            text(
                """
                SELECT trade_date, close FROM index_dailies
                WHERE ts_code = :code AND trade_date IN (:d0, :dn)
                """
            ),
            {"code": ts_code, "d0": d0, "dn": dn},
        )
    ).all()
    by_day = {_as_date(r[0]): float(r[1]) for r in rows}
    if d0 not in by_day or dn not in by_day or by_day[d0] <= 0:
        return None
    return by_day[dn] / by_day[d0] - 1


async def run_backtest(
    db: AsyncSession,
    *,
    scope_key: str,
    rule_key: str,
    rule_version: str,
    window_from: date,
    window_to: date,
    horizons: list[int],
    benchmark: str,
    split: dict[str, Any],
) -> BacktestRun:
    validate_request(
        scope_key=scope_key,
        window_from=window_from,
        window_to=window_to,
        horizons=horizons,
        split=split,
    )
    has_benchmark = await db.scalar(
        text("SELECT 1 FROM index_dailies WHERE ts_code = :code LIMIT 1"), {"code": benchmark}
    )
    if not has_benchmark:
        raise BacktestError(f"基准 {benchmark!r} 在 index_dailies 中没有数据")
    events = await _events(
        db, scope_key=scope_key, rule_key=rule_key, window_from=window_from, window_to=window_to
    )
    if not events:
        raise BacktestError("窗口内没有该规则的信号，无法回测")
    violations = _pit_violations(events)
    if violations:
        raise BacktestError(
            f"PIT 未通过：{len(violations)} 个信号的生成时间晚于生效日（如 {violations[0]}），"
            "拒绝落库/展示"
        )

    cfg = INDUSTRIES[scope_key]
    in_lo, in_hi = _as_date(split["in_sample"]["from"]), _as_date(split["in_sample"]["to"])
    out_lo, out_hi = (
        _as_date(split["out_of_sample"]["from"]),
        _as_date(split["out_of_sample"]["to"]),
    )

    per_horizon: dict[str, dict[str, dict[str, Any]]] = {}
    samples: dict[str, list[dict[str, Any]]] = {}
    for horizon in horizons:
        buckets: dict[str, tuple[list[float], list[float]]] = {
            "all": ([], []),
            "in_sample": ([], []),
            "out_of_sample": ([], []),
        }
        detail: list[dict[str, Any]] = []
        for event in events:
            days = await _trading_days(db, event.day, horizon)
            if days is None:
                continue
            d0, dn = days
            basket = await _basket_return(db, codes=cfg.sw_l3_codes, d0=d0, dn=dn)
            bench = await _benchmark_return(db, benchmark, d0, dn)
            if basket is None or bench is None:
                continue
            excess = basket - bench
            detail.append({"date": d0.isoformat(), "return": basket, "excess": excess})
            for key, hit in (
                ("all", True),
                ("in_sample", in_lo <= event.day <= in_hi),
                ("out_of_sample", out_lo <= event.day <= out_hi),
            ):
                if hit:
                    buckets[key][0].append(basket)
                    buckets[key][1].append(excess)
        per_horizon[str(horizon)] = {k: summarize(*v) for k, v in buckets.items()}
        samples[str(horizon)] = detail

    stats = {
        "events": len(events),
        "horizons": per_horizon,
        "samples": samples,
        "benchmark": benchmark,
        "asset": "equal_weight_median",
        "costs": "none",
        "note": "未复权 close；等权中位数；不扣成本",
    }
    run = BacktestRun(
        rule_key=rule_key,
        rule_version=rule_version,
        scope_type="industry",
        scope_keys=[scope_key],
        window_from=window_from,
        window_to=window_to,
        horizons=horizons,
        benchmark=benchmark,
        split=split,
        pit_enforced=True,
        stats=stats,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run
