#!/usr/bin/env python3
"""生成「自选体检」规则的标定统计（``app/services/triage_rule_stats.json``）。

当前 ``TRIAGE_DETECTORS`` 有 9 条规则，其中 8 条的判定式已复刻进 ``_sql()``；
``unlock_soon``（S11 新增）尚未纳入 → 产物里写明确的"未纳入"原因（见 :data:`UNCALIBRATED`），
**不**落成"窗口内无信号"。

**可复跑、只读**：只对真实 PG 发 ``SELECT``（会话显式 ``SET TRANSACTION READ ONLY``），
产出随仓库入库（它不是缓存：规则口径的"历史上长什么样"必须可评审、可 diff）。

口径（与 ``docs/design/signal-rule-calibration.md`` 第 1 节一致，改动前先看那份报告）：

1. **窗口**：``daily_quotes`` 最大交易日往前 ``WINDOW_MONTHS`` 个月；
2. **收益**：``close/pre_close - 1`` 按交易日**连乘**（``sum(ln(1+r))`` 再 ``exp``），
   **交易日历对齐**——T 与 T+N 都必须是市场交易日（``cal_idx`` 用 ``daily_quotes`` 的
   去重交易日，T+N 当天个股停牌则不计收益，绝不用 ``lead(close)`` 跨停牌期）；
3. **winsorize** 到 ±50%：未复权极端值会把"超额"放大数倍；
4. ``tN_excess_med`` = 每个信号日「信号组中位收益 − 当日全市场中位收益」的**跨日中位数**；
   ``t5_p05`` = 信号组 5 日收益的 **5% 分位**；
5. ``per_day`` = 信号总数 / 有信号的天数，``days`` = 有信号的天数，``signals`` = 信号总数；
6. **阈值只从 ``TRIAGE_DETECTORS`` 读**，SQL 里不写死任何阈值数字——脚本复刻的是判定式，
   不是第二份配置。规则窗口的结构性行数（如 drop_sharp 的 5 个交易日）也集中在本文件顶部。

数据不足（源表覆盖不足 12 个月 / 窗口内无信号）→ 该规则 ``stats=null`` + ``stats_note``
写原因：**半窗样本比没有统计更危险**（它看起来像全窗口结论）。

用法::

    cd backend && UV_CACHE_DIR=.uv-cache uv run python scripts/build_triage_rule_stats.py
"""

from __future__ import annotations

import asyncio
import calendar
import json
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory, engine
from app.services import triage_detectors as td

#: 统计窗口（月）——契约里的 ``window_months``
WINDOW_MONTHS = 12

#: 窗口前多取的自然日：覆盖最长的回看（61 个交易日 ≈ 88 自然日）
LOOKBACK_NATURAL_DAYS = 130

#: 产出路径（``triage_rules.STATS_PATH`` 读的就是它）
OUT_PATH = Path(__file__).resolve().parents[1] / "app" / "services" / "triage_rule_stats.json"

#: drop_sharp 的「5 日跌幅」= 与 5 个交易日前的收盘比（``_detect_drop_sharp`` 的 rows[-6]）
#: 它不是阈值（阈值 -7% / -12% 在 spec.thresholds 里），而是规则的**行数结构**。
DROP_SHARP_ROWS = 5

#: 收益换算成百分数、统计量保留位数
PCT = 100
EXCESS_DIGITS = 3
PER_DAY_DIGITS = 2

BASIS = (
    "收益 = close/pre_close 按交易日连乘（T 与 T+N 均为市场交易日，停牌无 T+N 行情则不计），"
    "winsorize ±50%；超额 = 信号组中位收益 − 当日全市场中位收益；"
    "tN_excess_med 为跨日中位数（%），t5_p05 为信号组 5 日收益 5% 分位（%）；"
    "tN 仅含已有 T+N 行情的信号日"
)

#: 各来源表的日期列（用于覆盖度判断）；空串 = 快照表（没有历史口径）
#:
#: **新增探测器必须同时在这里登记来源表**，否则 ``build()`` 会 KeyError（S12.3 把
#: 本脚本接进 reconcile 尾步后，这个缺口会变成每轮一条告警而不是"没人跑就没人发现"）。
#: ``share_floats`` 是 S11 的 ``unlock_soon`` 带来的：日期列是**解禁日** ``float_date``
#: （不是公告日；覆盖度要按"未来解禁计划能覆盖多久"判）。
DATE_COLUMN: dict[str, str] = {
    "daily_quotes": "trade_date",
    "stock_price_limits": "trade_date",
    "announcements": "announce_time",
    "dragon_tiger_entries": "trade_date",
    "sw_industry_members": "",
    "share_floats": "float_date",
}

#: 尚未纳入 ``_sql()`` 的探测器 → 必须写进产物的**原因**（``stats=null`` 的语义）。
#:
#: ``unlock_soon``（S11 新增，第 9 条规则）的判定式还没有在下面的标定 SQL 里复刻。
#: 不登记在这里的话它会落进 ``NO_SIGNAL_NOTE``（"窗口内无信号"）—— 那是**假话**：
#: 有信号，只是没统计。产物是给人看的证据，宁可写"未纳入"也不能写"没有"。
UNCALIBRATED: dict[str, str] = {
    "unlock_soon": "该规则（S11 unlock_soon）尚未纳入标定 SQL，stats=null 不代表窗口内无信号",
}

#: 没有信号时的兜底说明（统计口径本身没问题，是样本为空）
NO_SIGNAL_NOTE = "窗口内无信号（signals=0），样本不足"

#: 快照表参与标定的口径提示（不阻断统计，但必须让读的人知道）
SNAPSHOT_NOTE = "板块成分用 sw_industry_members 当前快照（非 PIT），历史回放会把今天的归属套到过去"


def _spec(key: str) -> td.DetectorSpec:
    for spec in td.TRIAGE_DETECTORS:
        if spec.key == key:
            return spec
    raise KeyError(key)


def _minus_months(day: date, months: int) -> date:
    """``day`` 往前 ``months`` 个自然月（月末截断到目标月最后一天）。"""
    total = day.year * 12 + (day.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last))


def _ret_expr(horizon: int) -> str:
    """T+horizon 的复权收益表达式：缺失（停牌无 T+N 行）必须是 NULL。

    **不能**直接写 ``least(greatest(exp(...) - 1, -0.5), 0.5)``：PostgreSQL 的
    ``greatest`` 会忽略 NULL，缺失收益会被 winsorize **变成 -50%**——那是最坏的一种
    "把没数据当成最差数据"。故先用 ``CASE`` 把缺失判成 NULL。
    """
    return (
        f"CASE WHEN c{horizon} IS NULL THEN NULL"
        f" ELSE least(greatest(exp(c{horizon} - c) - 1, -0.5), 0.5) END AS r{horizon}"
    )


def _sql() -> str:
    """一次性回放 8 条规则的判定式，按 (规则, 信号日) 输出统计。

    为什么一条大 SQL 而不是 8 条小 SQL：``feat``/``cum``/``ret`` 是 8 条规则共用的
    同一份窗口特征，跑 8 次等于把 130 万行窗口函数算 8 遍；共用 CTE 只算一遍。
    """
    return f"""
    WITH cal_idx AS (
        SELECT trade_date, row_number() OVER (ORDER BY trade_date) AS idx
        FROM (SELECT DISTINCT trade_date FROM daily_quotes WHERE trade_date <= :as_of) c
    ),
    base AS (
        SELECT q.stock_id, q.trade_date, q.close, q.pre_close, q.high, q.amount
        FROM daily_quotes q
        WHERE q.trade_date >= :lookback_start
          AND q.trade_date <= :as_of
          AND q.close > 0
          AND q.pre_close > 0
    ),
    feat AS (
        SELECT b.stock_id, b.trade_date, b.close, b.pre_close, b.high, b.amount,
               row_number() OVER w AS rn,
               lag(b.close, 1) OVER w AS prev_close,
               lag(b.close, :drop_rows) OVER w AS base_close,
               avg(b.close) OVER (
                   PARTITION BY b.stock_id ORDER BY b.trade_date
                   ROWS BETWEEN :ma_prev PRECEDING AND CURRENT ROW
               ) AS ma_today,
               avg(b.close) OVER (
                   PARTITION BY b.stock_id ORDER BY b.trade_date
                   ROWS BETWEEN :ma_full PRECEDING AND 1 PRECEDING
               ) AS ma_before,
               avg(b.amount) OVER (
                   PARTITION BY b.stock_id ORDER BY b.trade_date
                   ROWS BETWEEN :avg_days PRECEDING AND 1 PRECEDING
               ) AS amount_avg,
               max(b.close) OVER (
                   PARTITION BY b.stock_id ORDER BY b.trade_date
                   ROWS BETWEEN :hl_days PRECEDING AND 1 PRECEDING
               ) AS high_before,
               min(b.close) OVER (
                   PARTITION BY b.stock_id ORDER BY b.trade_date
                   ROWS BETWEEN :hl_days PRECEDING AND 1 PRECEDING
               ) AS low_before
        FROM base b
        WINDOW w AS (PARTITION BY b.stock_id ORDER BY b.trade_date)
    ),
    cum AS (
        SELECT stock_id, trade_date,
               sum(ln(close / pre_close)) OVER (
                   PARTITION BY stock_id ORDER BY trade_date
               ) AS c
        FROM base
    ),
    fwd AS (
        SELECT a.stock_id, a.trade_date, a.c,
               b1.c AS c1, b5.c AS c5, b20.c AS c20
        FROM cum a
        JOIN cal_idx c0 ON c0.trade_date = a.trade_date
        LEFT JOIN cal_idx f1 ON f1.idx = c0.idx + 1
        LEFT JOIN cal_idx f5 ON f5.idx = c0.idx + 5
        LEFT JOIN cal_idx f20 ON f20.idx = c0.idx + 20
        LEFT JOIN cum b1 ON b1.stock_id = a.stock_id AND b1.trade_date = f1.trade_date
        LEFT JOIN cum b5 ON b5.stock_id = a.stock_id AND b5.trade_date = f5.trade_date
        LEFT JOIN cum b20 ON b20.stock_id = a.stock_id AND b20.trade_date = f20.trade_date
        WHERE a.trade_date >= :window_start
    ),
    ret AS (
        SELECT stock_id, trade_date, {_ret_expr(1)}, {_ret_expr(5)}, {_ret_expr(20)}
        FROM fwd
    ),
    sector_l1 AS (
        SELECT m.symbol, c1.industry_code AS l1_code
        FROM sw_industry_members m
        JOIN sw_industry_classes c3
          ON c3.industry_code = m.industry_code AND c3.level = 3
        JOIN sw_industry_classes c2 ON c2.industry_code = c3.parent_code
        JOIN sw_industry_classes c1 ON c1.industry_code = c2.parent_code
    ),
    sector_day AS (
        SELECT f.trade_date, l1.l1_code,
               avg((f.close / f.pre_close - 1) * {PCT}) AS pct
        FROM feat f
        JOIN stocks s ON s.id = f.stock_id
        JOIN sector_l1 l1 ON l1.symbol = s.symbol
        WHERE f.trade_date >= :window_start
        GROUP BY f.trade_date, l1.l1_code
    ),
    sector_rank AS (
        SELECT trade_date, l1_code, pct,
               percent_rank() OVER (PARTITION BY trade_date ORDER BY abs(pct)) AS pr
        FROM sector_day
    ),
    sig AS (
        SELECT 'break_ma20'::text AS rule, f.stock_id, f.trade_date
        FROM feat f
        WHERE f.rn >= :ma_need
          AND f.trade_date >= :window_start
          AND f.close < f.ma_today
          AND f.prev_close >= f.ma_before
        UNION ALL
        SELECT 'drop_sharp', f.stock_id, f.trade_date
        FROM feat f
        WHERE f.rn >= :drop_need
          AND f.trade_date >= :window_start
          AND (
              (f.close / f.pre_close - 1) * {PCT} <= :day_pct
              OR (f.close / f.base_close - 1) * {PCT} <= :five_day_pct
          )
        UNION ALL
        SELECT 'volume_spike', f.stock_id, f.trade_date
        FROM feat f
        WHERE f.rn >= :volumes_need
          AND f.trade_date >= :window_start
          AND f.amount_avg > 0
          AND f.amount >= :amount_ratio * f.amount_avg
          AND abs((f.close / f.pre_close - 1) * {PCT}) >= :abs_pct
        UNION ALL
        SELECT 'limit_up_state', f.stock_id, f.trade_date
        FROM feat f
        JOIN stock_price_limits l
          ON l.stock_id = f.stock_id AND l.trade_date = f.trade_date
        WHERE f.trade_date >= :window_start
          AND l.up_limit > 0
          AND (f.close >= l.up_limit - :touch_tolerance
               OR f.high >= l.up_limit - :touch_tolerance)
        UNION ALL
        SELECT 'new_announcement', f.stock_id, f.trade_date
        FROM feat f
        JOIN stocks s ON s.id = f.stock_id
        WHERE f.trade_date BETWEEN :ann_lo AND :ann_hi
          AND EXISTS (
              SELECT 1 FROM announcements a
              WHERE a.sec_code = s.symbol
                AND a.announce_time >= f.trade_date - make_interval(days => :ann_days)
                AND a.announce_time < f.trade_date + INTERVAL '1 day'
          )
        UNION ALL
        SELECT 'dragon_tiger', s.id, c2.trade_date
        FROM dragon_tiger_entries d
        JOIN stocks s ON s.symbol = split_part(d.ts_code, '.', 1)
        JOIN cal_idx c1 ON c1.trade_date = d.trade_date
        JOIN cal_idx c2 ON c2.idx BETWEEN c1.idx AND c1.idx + :dt_days
        WHERE c2.trade_date <= :as_of
        UNION ALL
        SELECT 'new_high_low', f.stock_id, f.trade_date
        FROM feat f
        WHERE f.rn >= :hl_need
          AND f.trade_date >= :window_start
          AND (f.close > f.high_before OR f.close < f.low_before)
        UNION ALL
        SELECT 'sector_move', f.stock_id, f.trade_date
        FROM feat f
        JOIN stocks s ON s.id = f.stock_id
        JOIN sector_l1 l1 ON l1.symbol = s.symbol
        JOIN sector_rank r
          ON r.trade_date = f.trade_date AND r.l1_code = l1.l1_code
        WHERE f.trade_date >= :window_start
          AND r.pr >= 1 - :top_ratio
          AND abs(r.pct) >= :min_abs_pct
    ),
    sig_ret AS (
        SELECT s.rule, s.trade_date, r.r1, r.r5, r.r20
        FROM sig s
        LEFT JOIN ret r ON r.stock_id = s.stock_id AND r.trade_date = s.trade_date
        WHERE s.trade_date >= :window_start
    ),
    mkt AS (
        SELECT trade_date,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY r1) AS m1,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY r5) AS m5,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY r20) AS m20
        FROM ret
        GROUP BY trade_date
    ),
    per_day AS (
        SELECT rule, trade_date,
               count(*)::int AS n,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY r1) AS s1,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY r5) AS s5,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY r20) AS s20
        FROM sig_ret
        GROUP BY rule, trade_date
    ),
    excess AS (
        SELECT p.rule, p.trade_date, p.n,
               (p.s1 - m.m1) * {PCT} AS e1,
               (p.s5 - m.m5) * {PCT} AS e5,
               (p.s20 - m.m20) * {PCT} AS e20
        FROM per_day p
        LEFT JOIN mkt m ON m.trade_date = p.trade_date
    ),
    agg AS (
        SELECT rule,
               sum(n)::int AS signals,
               count(*)::int AS days,
               round(sum(n)::numeric / greatest(count(*), 1), {PER_DAY_DIGITS}) AS per_day,
               round(percentile_cont(0.5) WITHIN GROUP (ORDER BY e1)::numeric,
                     {EXCESS_DIGITS}) AS t1,
               round(percentile_cont(0.5) WITHIN GROUP (ORDER BY e5)::numeric,
                     {EXCESS_DIGITS}) AS t5,
               round(percentile_cont(0.5) WITHIN GROUP (ORDER BY e20)::numeric,
                     {EXCESS_DIGITS}) AS t20
        FROM excess
        GROUP BY rule
    ),
    worst AS (
        SELECT rule,
               round((percentile_cont(0.05) WITHIN GROUP (ORDER BY r5) * {PCT})::numeric,
                     {EXCESS_DIGITS}) AS t5_p05
        FROM sig_ret
        WHERE r5 IS NOT NULL
        GROUP BY rule
    )
    SELECT a.rule, a.signals, a.days, a.per_day, a.t1, a.t5, a.t20, w.t5_p05
    FROM agg a
    LEFT JOIN worst w ON w.rule = a.rule
    """


def _params(
    *,
    as_of: date,
    window_start: date,
    lookback_start: date,
    ann_lo: date,
    ann_hi: date,
) -> dict[str, Any]:
    """SQL 参数：**全部取自 registry**（阈值禁止在脚本里再写一遍）。"""
    drop = _spec("drop_sharp").thresholds
    volume = _spec("volume_spike").thresholds
    limit_up = _spec("limit_up_state").thresholds
    sector = _spec("sector_move").thresholds
    ma_need = int(_spec("break_ma20").thresholds["ma_days"]) + 1
    avg_days = int(volume["avg_days"])
    hl_days = int(_spec("new_high_low").thresholds["days"])
    return {
        "as_of": as_of,
        "window_start": window_start,
        "lookback_start": lookback_start,
        "drop_rows": DROP_SHARP_ROWS,
        "drop_need": DROP_SHARP_ROWS + 1,
        "ma_need": ma_need,
        "ma_prev": ma_need - 2,
        "ma_full": ma_need - 1,
        "avg_days": avg_days,
        "volumes_need": avg_days + 1,
        "hl_days": hl_days,
        "hl_need": max(hl_days, td.MIN_HISTORY_DAYS) + 1,
        "day_pct": drop["day_pct"],
        "five_day_pct": drop["five_day_pct"],
        "amount_ratio": volume["amount_ratio"],
        "abs_pct": volume["abs_pct"],
        "touch_tolerance": limit_up["touch_tolerance"],
        "ann_days": int(_spec("new_announcement").thresholds["days"]),
        "ann_lo": ann_lo,
        "ann_hi": ann_hi,
        "dt_days": int(_spec("dragon_tiger").thresholds["days"]) - 1,
        "top_ratio": sector["top_ratio"],
        "min_abs_pct": sector["min_abs_pct"],
    }


async def _read_source_span(session: AsyncSession, table: str) -> tuple[date | None, date | None]:
    """来源表的覆盖区间；表不存在或空表返回 ``(None, None)``。"""
    column = DATE_COLUMN.get(table)
    if column is None:
        raise KeyError(f"{table} 没有登记日期列，新增来源表请先补 DATE_COLUMN")
    exists = (await session.execute(text("SELECT to_regclass(:name)"), {"name": table})).scalar()
    if exists is None:
        return None, None
    if column == "":
        # 快照表（sw_industry_members）：没有时间列，只有"有没有数据"
        has_rows = (await session.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))).scalar()
        return (date.min, date.min) if has_rows else (None, None)
    # 表名/列名来自本文件的固定映射（非外部输入），故可安全拼进 SQL
    row = (
        await session.execute(text(f"SELECT min({column})::date, max({column})::date FROM {table}"))
    ).one()
    return row[0], row[1]


def _coverage_note(table: str, start: date | None, window_start: date, as_of: date) -> str | None:
    """来源表覆盖不足 12 个月时返回原因（否则 ``None``）。"""
    if start is None:
        return f"{table} 表不存在或为空，无法统计"
    if start == date.min:  # 快照表
        return None
    if start > window_start:
        months = (as_of - start).days / 30.44
        return (
            f"{table} 仅覆盖自 {start.isoformat()} 起的 {months:.1f} 个月，"
            f"不足 {WINDOW_MONTHS} 个月窗口，样本不足"
        )
    return None


async def build(session: AsyncSession) -> dict[str, Any]:
    """读库 → 规则标定统计文档（**纯**：只用传入的会话读，不改事务属性、不写文件）。

    会话由调用方给：脚本自建并显式 ``SET TRANSACTION READ ONLY``；reconcile 尾步
    （``artifacts_service``）复用对账会话（同一事务里还有写操作，故只读不在此处设置）。
    """
    as_of = (await session.execute(text("SELECT max(trade_date) FROM daily_quotes"))).scalar()
    if as_of is None:
        raise RuntimeError("daily_quotes 为空：先入库行情再跑本脚本")
    window_start = _minus_months(as_of, WINDOW_MONTHS)
    lookback_start = window_start - timedelta(days=LOOKBACK_NATURAL_DAYS)

    spans: dict[str, tuple[date | None, date | None]] = {}
    for table in DATE_COLUMN:
        spans[table] = await _read_source_span(session, table)

    ann_start, ann_end = spans["announcements"]
    if ann_start is None:
        ann_lo, ann_hi = as_of + timedelta(days=1), as_of  # 空区间：一条都不判
    else:
        ann_lo = min(max(ann_start - timedelta(days=1), lookback_start), as_of)
        ann_hi = min(ann_end + timedelta(days=1), as_of) if ann_end else as_of

    params = _params(
        as_of=as_of,
        window_start=window_start,
        lookback_start=lookback_start,
        ann_lo=ann_lo,
        ann_hi=ann_hi,
    )
    rows = (await session.execute(text(_sql()), params)).all()

    by_rule: dict[str, dict[str, Any]] = {}
    for rule, signals, days, per_day, t1, t5, t20, t5_p05 in rows:
        by_rule[str(rule)] = {
            "signals": int(signals),
            "days": int(days),
            "per_day": float(per_day),
            "t1_excess_med": float(t1) if t1 is not None else None,
            "t5_excess_med": float(t5) if t5 is not None else None,
            "t20_excess_med": float(t20) if t20 is not None else None,
            "t5_p05": float(t5_p05) if t5_p05 is not None else None,
        }

    rules: dict[str, Any] = {}
    for spec in td.TRIAGE_DETECTORS:
        note: str | None = None
        for table in spec.source_tables:
            note = _coverage_note(table, spans[table][0], window_start, as_of)
            if note is not None:
                break
        if note is None:
            # 覆盖度够但判定式没进 SQL：写"未纳入"，不能伪装成"窗口内无信号"
            note = UNCALIBRATED.get(spec.key)
        stats = by_rule.get(spec.key)
        if note is None and not stats:
            note = NO_SIGNAL_NOTE
        if note is None and stats is not None and stats["t5_p05"] is None:
            note = "窗口内没有可用的 T+5 收益（全部信号日都缺 T+5 行情），样本不足"
            stats = None
        if note is not None:
            stats = None
        elif "sw_industry_members" in spec.source_tables:
            note = SNAPSHOT_NOTE
        rules[spec.key] = {"stats": stats, "stats_note": note}

    return {
        "as_of": as_of.isoformat(),
        "window_months": WINDOW_MONTHS,
        "basis": BASIS,
        "rules": rules,
    }


def _pad(text: str, width: int) -> str:
    """按**显示宽度**补齐（中文占 2 列，``str.ljust`` 会让表错行）。"""
    shown = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)
    return text + " " * max(0, width - shown)


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:+.2f}"


def _print_table(doc: dict[str, Any]) -> None:
    header = (
        _pad("规则", 22)
        + _pad("信号数", 9)
        + _pad("天数", 7)
        + _pad("每日", 8)
        + _pad("T+5 超额中位", 15)
        + _pad("T+5 最差 5%", 13)
    )
    print(header)
    print("-" * 76)
    for spec in td.TRIAGE_DETECTORS:
        entry = doc["rules"][spec.key]
        stats = entry["stats"]
        if stats is None:
            print(_pad(f"{spec.key}（{spec.label}）", 22) + f"stats=null：{entry['stats_note']}")
            continue
        print(
            _pad(f"{spec.key}（{spec.label}）", 22)
            + _pad(str(stats["signals"]), 9)
            + _pad(str(stats["days"]), 7)
            + _pad(f"{stats['per_day']:.2f}", 8)
            + _pad(_fmt(stats["t5_excess_med"]), 15)
            + _pad(_fmt(stats["t5_p05"]), 13)
        )


def write_document(doc: dict[str, Any], path: Path = OUT_PATH) -> Path:
    """写产物（**确定性**：固定缩进、结尾一个换行；两次运行逐字节一致）。"""
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


async def main() -> int:
    try:
        async with async_session_factory() as session:
            # 只读会话：这条纪律是结构性的，不靠"我们只写了 SELECT"的自觉
            await session.execute(text("SET TRANSACTION READ ONLY"))
            doc = await build(session)
    finally:
        await engine.dispose()
    write_document(doc)
    start = _minus_months(date.fromisoformat(doc["as_of"]), WINDOW_MONTHS)
    print(
        f"窗口：{start.isoformat()} → {doc['as_of']}"
        f"（{WINDOW_MONTHS} 个月，as_of = daily_quotes 最大交易日）"
    )
    print()
    _print_table(doc)
    print()
    print(f"已写入 {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
