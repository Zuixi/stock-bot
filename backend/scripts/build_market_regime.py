#!/usr/bin/env python3
"""生成市场状态机历史产物（``app/services/market_regime_history.json``）。

**可复跑、只读**：只对真实 PG 发 ``SELECT``（会话显式 ``SET TRANSACTION READ ONLY``），
产出随仓库入库——状态机的"历史上长什么样、按哪套阈值算的"必须可评审、可 diff、可复跑。
两次运行**逐字节一致**（产物里没有生成时间戳，``as_of`` 只取数据自身的最大交易日）。

口径（与 ``docs/specs/S10-market-regime.md`` §S10.1 一致，改动前先看那份契约）：

1. **只用 ``daily_quotes``**：``amount`` / ``close`` / ``pre_close``。零新表、零新源；
   涨停/连板族（``stock_price_limits`` 只有 259 天）**不参与**特征；
2. **涨跌幅** ``close / 前收 - 1``：前收取 ``pre_close``，缺失时退化到**该股上一行收盘**
   （``pct_chg`` 列实测 308 万行为 NULL，当唯一来源等于把 2023–2024 整段算不出来）；
3. **新高**：前 60 个交易日（该股自己的最近 60 行）最高收盘 < 今日收盘，且该股有 ≥60 日历史；
   分母 = 有 ≥60 日历史的股票数（分母过小由服务层判 ``None``）；
4. **同态后续收益**的交易日历 = 本文产出行的日期集（首个交易日没有前收、算不出全市场
   收益 → 不进日历）。这把"未来的日子"从构造上排除，不可能前视；
5. 本文件只做**按日聚合**（一条 SQL），阈值/分位/滚动波动/迟滞/统计全在
   ``app.services.market_regime_service``——脚本里不写第二份阈值。

用法::

    cd backend && UV_CACHE_DIR=.uv-cache uv run python scripts/build_market_regime.py
"""

from __future__ import annotations

import asyncio
import unicodedata
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory, engine
from app.services import market_regime_service as svc

#: 产出路径（``market_regime_service.HISTORY_PATH`` 读的就是它）
OUT_PATH: Path = svc.HISTORY_PATH

#: 日级原语：一条 SQL 出全部（``amount``/``close``/``pre_close`` 三列就能算五维特征）。
#:
#: 为什么一条大 SQL 而不是 834 次查询：``ret`` 与 ``high_before`` 都是**按股票**的窗口，
#: 共用同一个 ``PARTITION BY stock_id ORDER BY trade_date`` 排序只排一遍；
#: 逐日查等于把 445 万行重排 834 次。
#: 两个窗口帧的 ``PRECEDING`` 都绑到 ``:nh_days``（阈值唯一真相在 registry）。
_SQL = text(
    """
    WITH base AS (
        SELECT q.stock_id,
               q.trade_date,
               q.close,
               q.amount,
               CASE WHEN q.pre_close IS NOT NULL AND q.pre_close > 0 THEN q.pre_close END
                   AS pre_close,
               lag(q.close) OVER (
                   PARTITION BY q.stock_id ORDER BY q.trade_date
               ) AS prev_close,
               count(*) OVER (
                   PARTITION BY q.stock_id ORDER BY q.trade_date
                   ROWS BETWEEN :nh_days PRECEDING AND 1 PRECEDING
               ) AS history_rows,
               max(q.close) OVER (
                   PARTITION BY q.stock_id ORDER BY q.trade_date
                   ROWS BETWEEN :nh_days PRECEDING AND 1 PRECEDING
               ) AS high_before
        FROM daily_quotes q
        WHERE q.close IS NOT NULL AND q.close > 0
    ),
    feat AS (
        SELECT stock_id,
               trade_date,
               close,
               amount,
               history_rows,
               high_before,
               CASE
                   WHEN pre_close IS NOT NULL THEN close / pre_close - 1
                   WHEN prev_close IS NOT NULL AND prev_close > 0 THEN close / prev_close - 1
               END AS ret
        FROM base
    )
    SELECT trade_date,
           sum(amount) AS total_amount,
           count(*) FILTER (WHERE ret > 0) AS advancers,
           count(*) FILTER (WHERE ret < 0) AS decliners,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY ret) * 100 AS median_pct,
           count(*) FILTER (
               WHERE history_rows >= :nh_days AND close > high_before
           ) AS new_highs,
           count(*) FILTER (WHERE history_rows >= :nh_days) AS new_high_universe
    FROM feat
    GROUP BY trade_date
    ORDER BY trade_date
    """
)


async def _load(session: AsyncSession) -> tuple[list[svc.DailyRaw], dict[date, float]]:
    """读全历史按日聚合 → ``(日级原语, {交易日: 全市场中位收益（小数）})``。

    没有可用数据的交易日（首个交易日无前收 → 全市场收益中位为 NULL）**不进**：
    它既算不出特征也当不了"入场日"，把它当 0 收益会污染 ``forward`` 的最差 5%。
    """
    nh_days = int(svc.REGIME_THRESHOLDS["new_high_window"])
    rows = (await session.execute(_SQL, {"nh_days": nh_days})).all()
    dailies: list[svc.DailyRaw] = []
    returns: dict[date, float] = {}
    for trade_date, amount, advancers, decliners, median_pct, new_highs, universe in rows:
        if median_pct is None:
            continue
        day = trade_date if isinstance(trade_date, date) else date.fromisoformat(str(trade_date))
        dailies.append(
            svc.DailyRaw(
                trade_date=day,
                total_amount=float(amount or 0.0),
                advancers=int(advancers),
                decliners=int(decliners),
                median_pct_chg=float(median_pct),
                new_highs=int(new_highs),
                new_high_universe=int(universe),
            )
        )
        returns[day] = float(median_pct) / 100.0
    return dailies, returns


def _pad(text: str, width: int) -> str:
    """按**显示宽度**补齐（中文占 2 列，``str.ljust`` 会让表错行）。"""
    shown = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)
    return text + " " * max(0, width - shown)


def _print_distribution(document: dict[str, Any]) -> None:
    """五态天数与占比（spec §S10.5 的"先标定再上线"就靠这张表核对）。"""
    counts = Counter(point["state"] for point in document["series"])
    total = len(document["series"])
    print(_pad("状态", 10) + _pad("天数", 8) + "占比")
    print("-" * 30)
    for state in svc.STATES:
        days = counts.get(state, 0)
        print(_pad(state, 10) + _pad(str(days), 8) + f"{days / total:.1%}")
    print("-" * 30)
    print(_pad("合计", 10) + _pad(str(total), 8) + "100.0%")


async def build(session: AsyncSession) -> dict[str, Any]:
    """读库 → 产物文档（**纯**：只用传入的会话读，不改事务属性、不写文件）。

    会话由调用方给：脚本自建并显式 ``SET TRANSACTION READ ONLY``；reconcile 尾步
    （``artifacts_service``）**复用对账会话**，那边同一事务里还有写操作，
    因此"只读"只能是脚本这条轨道的纪律，不能塞进本函数（否则会打断对账事务）。
    """
    dailies, returns = await _load(session)
    if not dailies:
        raise RuntimeError("daily_quotes 为空：先入库行情再跑本脚本")
    return svc.build_document(dailies, returns)


def write_document(document: dict[str, Any], path: Path = OUT_PATH) -> Path:
    """把文档写成产物文件（复用 ``svc.dump_document`` 的确定性序列化）。"""
    path.write_text(svc.dump_document(document), encoding="utf-8")
    return path


async def main() -> int:
    try:
        async with async_session_factory() as session:
            # 只读会话：这条纪律是结构性的，不靠"我们只写了 SELECT"的自觉
            await session.execute(text("SET TRANSACTION READ ONLY"))
            document = await build(session)
    finally:
        await engine.dispose()
    written = write_document(document)
    series = document["series"]
    print(f"版本：{document['version']}（阈值快照见产物 thresholds）")
    print(
        f"覆盖：daily_quotes {len(series)} 个可判定交易日 "
        f"{series[0]['date']} → {document['as_of']}"
        f"（特征窗口 {int(svc.REGIME_THRESHOLDS['amount_pctile_window'])} 日，前 "
        f"{int(svc.REGIME_THRESHOLDS['amount_pctile_min_days'])} 日不出分位 → 序列前缀不入内）"
    )
    print()
    _print_distribution(document)
    print()
    print(f"已写入 {written}（{written.stat().st_size} 字节）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
