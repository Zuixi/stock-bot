"""对账收敛器：数据面完整性由对账保证，不依赖"任务准点跑"（level-triggered）。

期望集 = trade_cal 最近 N 个交易日（截止昨日，T-1 语义）；完整判据 = 行数量级
（≥ 0.8×全市场数，防 partial 行挡住补齐的死锁）。cron 只负责 timeliness，
任何停摆（宿主睡眠/容器挂起/任务异常）在下一个对账触发点自动收敛。
设计与事故时间线见 plans/2026-09-17-data-sync-self-healing.md。

两套域，语义不同，**不得混淆**（E1：把只读域塞进补漏循环 = 假绿）：

- **可自愈域（有 ``_refetch_*``）** = :data:`DOMAINS`：``daily_quotes`` /
  ``daily_basic`` / ``price_limits`` / ``sentiment``。只有这些域进
  :func:`reconcile_market_data` 的 apply 补漏循环（3 域底座 + sentiment 特例）。
- **只读巡检域（永不参与 apply 补漏）** = :data:`INSPECT_DOMAINS`：东财快照/盘中聚合/
  个股事实/数据面四表。它们**没有**回补器（A 类无历史接口，或历史靠独立回补脚本），
  本模块对它们只做覆盖率聚合（:func:`coverage_report`），绝不写库。新加域前先问：
  这个域有 ``_refetch_*`` 吗？没有就只能进巡检列表。

补灌副作用：重拉某天时新插入的行只有 TuShare ``daily`` 的 OHLC（该接口不带因子），
``adj_factor`` 为 NULL，而 upsert 的 COALESCE 只保已有值、懒加载只认最新行——中间
交易日的洞两条路径都不会再碰。因此重拉 ``daily_quotes`` 后，对**本次重拉的日子**补
一次因子缺口（见 :func:`_repair_adj_factors`），把缺陷消灭在产生点。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, NamedTuple

from sqlalchemy import func, select
from sqlalchemy.exc import ProgrammingError

from app.models.daily_basic import DailyBasicIndicator
from app.models.market_data import (
    BlockTrade,
    DragonTigerEntry,
    MarketLimitUpStockDaily,
    MarketSentimentDaily,
    MarketSentimentIntraday,
    SectorMoneyflowSnapshot,
    ShareFloat,
    StockPriceLimit,
    StockRepurchase,
)
from app.models.quote import DailyQuote
from app.models.stock import Stock
from app.services.market_data_service import _d, _get_tushare, _today_sh

logger = logging.getLogger(__name__)

COMPLETENESS_RATIO = 0.8
DEFAULT_WINDOW_DAYS = 10
DEFAULT_COVERAGE_WINDOW_DAYS = 30

# 对账域（顺序即依赖序：sentiment 依赖 quotes + price_limits 完备）
DOMAINS: tuple[str, ...] = ("daily_quotes", "daily_basic", "price_limits", "sentiment")

_ROW_MODEL: dict[str, type[Any]] = {
    "daily_quotes": DailyQuote,
    "daily_basic": DailyBasicIndicator,
    "price_limits": StockPriceLimit,
    "sentiment": MarketSentimentDaily,
}


class _CoverageSource(NamedTuple):
    """巡检域的数据来源（唯一真源；``domain`` 键即 API 里的 ``domain`` 字段）。"""

    table: str
    date_column: str  # 业务日期列（不是自然日，也不是 created_at）
    complete_kind: tuple[str, str] | None = None  # (列, 值)：只有"完整日"才计入的产地标记


#: 交易日历的数据源（并集）：`daily_quotes` 是主源，`stock_price_limits` 是**补漏源**。
#: 为什么不止用 daily_quotes：它自己会缺日（真库实测 2026-09 缺 09-09..09-17 共 7 个
#: 交易日），拿它当分母会把"行情自己缺"算成"巡检域缺"——sector_moneyflow 的 14 天会被
#: 判成 12 天。限价表是涨停判定的权威口径且逐日 100% 覆盖，并进来即可对齐。
#: 并集会**高估新表的可用交易日**（2025-09-16 之前 daily_quotes 才有数据），所以窗口
#: 下界取 domain 自身区间 + `window_days` + 主源起点三者之较晚者。
TRADING_DAY_SOURCES: tuple[str, ...] = ("daily_quotes", "stock_price_limits")

#: 交易日历的权威来源列（报告里透出，便于解释分母构成）；主源缺失时状态会降级
TRADING_DAY_PRIMARY = "daily_quotes"


# 域 → (表, 业务日期列 [, 完整日产地产地列])。日期列逐域实测确定，不猜：
#   - `share_floats` 的业务日期是 `float_date`（`ann_date` 可空且是公告日，不是解禁日）
#   - `stock_repurchases` 的采集/去重键是 `ann_date`（`end_date`/`exp_date` 常为 NaN）
#   - `sector_moneyflow` 的"完整日"只认 `capture_kind='eod_full'`（Ruling 11/13）：
#     盘中 `poll` 行是 inflow-Top-100 子集，有行 ≠ 完整
_COVERAGE_SOURCES: dict[str, _CoverageSource] = {
    "sector_moneyflow": _CoverageSource(
        "sector_moneyflow_snapshots", "trade_date", ("capture_kind", "eod_full")
    ),
    # 注意：这里叫 `sentiment_daily` —— 可自愈域里的 `sentiment` 指同一张表但语义是
    # "补漏单元"。两套域名必须可区分，否则"巡检域 == 补漏域"的边界又糊了。
    "sentiment_daily": _CoverageSource("market_sentiment_daily", "trade_date"),
    "sentiment_intraday": _CoverageSource("market_sentiment_intraday", "trade_date"),
    "limit_up_stock_daily": _CoverageSource("market_limit_up_stock_daily", "trade_date"),
    "dragon_tiger": _CoverageSource("dragon_tiger_entries", "trade_date"),
    "block_trade": _CoverageSource("block_trades", "trade_date"),
    "share_float": _CoverageSource("share_floats", "float_date"),
    "repurchase": _CoverageSource("stock_repurchases", "ann_date"),
}

# 只读巡检域（顺序即报告顺序）。**永不**参与 apply 补漏——没有 `_refetch_*`，
# 塞进 `DOMAINS` 只会得到"看着被纳入、永不补漏"的假绿（E1）。
INSPECT_DOMAINS: tuple[str, ...] = (
    "sector_moneyflow",
    "sentiment_daily",
    "sentiment_intraday",
    "limit_up_stock_daily",
    "dragon_tiger",
    "block_trade",
    "share_float",
    "repurchase",
)

assert not set(INSPECT_DOMAINS) & set(DOMAINS), "巡检域与可自愈域不得重名（边界守卫）"

# SQLSTATE：未定义表 / 未定义列。迁移未应用时覆盖率必须降级标注而不是 500。
_SQLSTATE_UNDEFINED_TABLE = "42P01"
_SQLSTATE_UNDEFINED_COLUMN = "42703"


async def expected_trade_dates(
    client: Any, *, window_days: int = DEFAULT_WINDOW_DAYS, today: date | None = None
) -> tuple[list[date], bool]:
    """最近 window_days 个交易日（截止**昨日**，升序）。

    当日 TuShare 数据未出全，拉了会写 partial（9/16 事故）——当日不属于期望集。
    trade_cal 不可用时降级为工作日启发式并返回 degraded=True（节假日会多试几个
    空日，ingest 空结果无害）。
    """
    today = today or _today_sh()
    end = today - timedelta(days=1)
    start = end - timedelta(days=window_days * 2 + 5)  # 覆盖节假日的日历跨度
    try:
        df = await client.fetch_trade_cal(
            start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"), is_open="1"
        )
        days = sorted({_d(rec["cal_date"]) for rec in df.to_dict("records") if rec.get("cal_date")})
        degraded = False
    except Exception:
        logger.warning("trade_cal unavailable — degrading to weekday heuristic", exc_info=True)
        days = [
            start + timedelta(days=i)
            for i in range((end - start).days + 1)
            if (start + timedelta(days=i)).weekday() < 5
        ]
        degraded = True
    return days[-window_days:], degraded


async def _stock_universe_count(db: Any) -> int:

    result = await db.execute(select(func.count()).select_from(Stock))
    return int(result.scalar() or 0)


async def _row_counts(db: Any, domain: str, dates: list[date]) -> dict[date, int]:
    """各期望日的行数（域 → 表模型分派；sentiment 一行即一天）。"""
    model = _ROW_MODEL[domain]
    stmt = (
        select(model.trade_date, func.count())
        .where(model.trade_date.in_(dates))
        .group_by(model.trade_date)
    )
    rows = (await db.execute(stmt)).all()
    return {d: int(c) for d, c in rows}  # type: ignore[call-overload]


async def _refetch_daily_quotes(db: Any, day: date) -> dict[str, Any]:
    from app.services.tushare_ingest import TuShareIngestService  # noqa: PLC0415

    return await TuShareIngestService().ingest_daily_quotes(db, day.strftime("%Y%m%d"))


async def _refetch_daily_basic(db: Any, day: date) -> dict[str, Any]:
    from app.services.tushare_ingest import TuShareIngestService  # noqa: PLC0415

    return await TuShareIngestService().ingest_daily_basic(db, day.strftime("%Y%m%d"))


async def _refetch_price_limits(db: Any, day: date) -> dict[str, Any]:
    from app.services import market_data_service  # noqa: PLC0415

    # 显式 trade_date → todo=[day]：partial 日也会被强制重拉（upsert 合并）
    return await market_data_service.ingest_stock_price_limits(db, trade_date=day)


async def _refetch_sentiment(db: Any, day: date) -> dict[str, Any]:
    from app.services import limit_up_service  # noqa: PLC0415

    return await limit_up_service.persist_snapshot(db, None, as_of=day)


async def _commit(db: Any) -> None:
    """sentiment 的 get_snapshot 走独立会话——底座补数必须先提交才可见。"""
    await db.commit()


def _contiguous_runs(days: list[date]) -> list[tuple[date, date]]:
    """把升序日期聚成连续区间：``[10, 11, 16]`` → ``[(10, 11), (16, 16)]``。

    补洞服务按 ``[start, end]`` 区间发现缺口；为了只碰"本次重拉的日子"不能直接取
    ``min``/``max``（中间未重拉的日子会被顺带扫到）。连续段分组后每个区间恰好等于
    一组重拉日，且保留"每股一次外呼覆盖整段"的批处理（非逐行、非逐日外呼）。
    """
    runs: list[tuple[date, date]] = []
    for day in days:
        if runs and day == runs[-1][1] + timedelta(days=1):
            runs[-1] = (runs[-1][0], day)
        else:
            runs.append((day, day))
    return runs


async def _repair_adj_factors(db: Any, days: list[date]) -> dict[str, Any]:
    """对本次重拉的交易日补 ``adj_factor`` 缺口（best-effort，绝不抛）。

    为什么在这里：重拉插入的新行因子为 NULL，懒加载的判据是"最新行有无因子"，
    补中间日的洞永远不会被它触发（Task 5c 的根因）。

    失败可见性：本模块不持有 scheduler 的 ``job_id``（``record_job_failure`` 由
    jobs.py 的 except 块调用），故按本模块既有约定记 WARNING（带 traceback），并把
    失败写进结果的 ``adj_factor_repaired.error``。该结果随 worker 回执与 scheduler
    日志外露；``/market/data-freshness`` 恒以 ``apply=False`` 调用，本字段在那里
    恒为 ``null``（只读巡检不补洞），不要指望巡检端点看到它。
    失败后主动 ``rollback`` 清会话：否则中断的事务会让随后的 sentiment 补数报
    PendingRollbackError，把"补因子失败"升级成"对账失败"，违背非致命契约。
    """
    outcome: dict[str, Any] = {
        "days": [d.isoformat() for d in days],
        "rows": 0,
        "failed": 0,
        "unfilled": 0,
        "remaining": 0,
        "error": None,
    }
    try:
        from app.services.quote_service import backfill_missing_adj_factors  # noqa: PLC0415

        for start, end in _contiguous_runs(days):
            stats = await backfill_missing_adj_factors(db, start=start, end=end)
            outcome["rows"] += int(stats.get("rows", 0))
            outcome["failed"] += int(stats.get("failed", 0))
            outcome["unfilled"] += int(stats.get("unfilled", 0))
            # 预算截断：本次没修完的股票数，透出到结果，避免"看起来全修完了"
            outcome["remaining"] += int(stats.get("remaining", 0))
        if outcome["failed"] or outcome["unfilled"] or outcome["remaining"]:
            logger.warning("RECONCILE adj_factor repair partial: %s", outcome)
    except Exception as exc:  # noqa: BLE001 — 加法式修复，失败不得反噬对账
        outcome["error"] = f"{type(exc).__name__}: {exc}"
        logger.warning("RECONCILE adj_factor repair failed (kept best-effort)", exc_info=True)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001,S110 — 会话清理失败也只能记日志
            logger.warning("RECONCILE adj_factor repair: session cleanup failed", exc_info=True)
    return outcome


async def _domain_status(
    counts: dict[date, int], expected: list[date], threshold: int
) -> dict[str, Any]:
    missing = [d for d in expected if counts.get(d, 0) == 0]
    partial = [d for d in expected if 0 < counts.get(d, 0) < threshold]
    latest = max((d for d in expected if counts.get(d, 0) > 0), default=None)
    return {"latest_in_db": latest, "missing_days": missing, "partial_days": partial}


async def reconcile_market_data(
    db: Any,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    apply: bool = True,
    only: set[str] | None = None,
    client: Any = None,
    today: date | None = None,
) -> dict[str, Any]:
    """对账并（默认）补齐窗口内缺口；apply=False 为只读巡检（freshness 端点复用）。

    幂等可重放：所有回补走既有 upsert/DO-NOTHING ingest，任意重跑收敛到同一结果。
    """
    scope = set(only) if only is not None else set(DOMAINS)
    unknown = scope - set(DOMAINS)
    if unknown:
        raise ValueError(f"unknown reconcile domains: {sorted(unknown)}")
    client = client or _get_tushare()
    expected, degraded = await expected_trade_dates(client, window_days=window_days, today=today)
    universe = await _stock_universe_count(db)
    threshold = int(universe * COMPLETENESS_RATIO)

    result: dict[str, Any] = {
        "as_of": (today or _today_sh()).isoformat(),
        "expected_latest": expected[-1].isoformat() if expected else None,
        "expected_days": len(expected),
        "universe": universe,
        "quotes_symbols_latest": None,  # 下方按 daily_quotes 实际行数回填
        "degraded_calendar": degraded,
        "apply": apply,
        # 补灌的副作用修复：本次重拉日的 adj_factor 缺口（apply=False 恒为 None）
        "adj_factor_repaired": None,
        "domains": {},
    }

    # ── 底座域（quotes → basic → price_limits），sentiment 前统一 commit ──
    refetched_any = False
    quotes_refetched: list[date] = []
    for domain in ("daily_quotes", "daily_basic", "price_limits"):
        counts = await _row_counts(db, domain, expected)
        status = await _domain_status(counts, expected, threshold)
        todo = sorted(status["missing_days"] + status["partial_days"])
        refetched: list[date] = []
        if apply and todo and domain in scope:
            for day in todo:
                await globals()[f"_refetch_{domain}"](db, day)
                refetched.append(day)
            refetched_any = True
            status["latest_in_db"] = max(
                (d for d in expected if d in refetched or counts.get(d, 0) > 0), default=None
            )
        if domain == "daily_quotes":
            # 只取 daily_quotes 域本次真正重拉的日子（其余域与未重拉的日不补因子）
            quotes_refetched = list(refetched)
        result["domains"][domain] = {
            "latest_in_db": status["latest_in_db"].isoformat() if status["latest_in_db"] else None,
            "missing_days": [d.isoformat() for d in status["missing_days"]],
            "partial_days": [d.isoformat() for d in status["partial_days"]],
            "refetched": [d.isoformat() for d in refetched],
            "status": _status_word(apply, bool(refetched), bool(todo)),
        }

    # 底座补数必须先提交：persist_snapshot → get_snapshot 走独立会话，
    # 未提交的 upsert 对它不可见（READ COMMITTED），会把刚补的日子误判 partial。
    if apply and (refetched_any or "sentiment" in scope):
        await _commit(db)

    # 因子补洞放在底座 commit 之后：补洞函数内部逐股 commit/rollback，若在重拉数据
    # 尚未提交时运行，单股失败的 rollback 会把刚补灌的行情一起回滚掉。
    if apply and quotes_refetched:
        result["adj_factor_repaired"] = await _repair_adj_factors(db, quotes_refetched)

    # ── 派生域（sentiment）：仅 quotes + price_limits 完备的期望日才补 ──
    if expected:
        quotes_counts = await _row_counts(db, "daily_quotes", expected)
        limits_counts = await _row_counts(db, "price_limits", expected)
        base_complete = {
            d
            for d in expected
            if quotes_counts.get(d, 0) >= threshold and limits_counts.get(d, 0) >= threshold
        }
        # 名录 vs 行情活跃数：上面的 threshold 用 stocks 表行数当分母，而该表可能长期
        # 冻结（实测停在 5 月，65 只次新股的行被日线采集静默丢弃却始终过阈值）。两个数
        # 并排暴露，"名录滞后"才能在巡检端点看见，而非只留一个 skipped 计数。
        result["quotes_symbols_latest"] = quotes_counts.get(expected[-1], 0)
    else:
        base_complete = set()
        result["quotes_symbols_latest"] = None
    sent_counts = await _row_counts(db, "sentiment", expected)
    sent_status = await _domain_status(sent_counts, expected, threshold=1)
    sent_todo = [d for d in sent_status["missing_days"] if d in base_complete]
    sent_refetched: list[date] = []
    if apply and sent_todo and "sentiment" in scope:
        for day in sent_todo:
            await _refetch_sentiment(db, day)
            sent_refetched.append(day)
        if sent_refetched:
            await _commit(db)  # 情绪行落库 + persist 内已失效 calendar 缓存
    result["domains"]["sentiment"] = {
        "latest_in_db": sent_status["latest_in_db"].isoformat()
        if sent_status["latest_in_db"]
        else None,
        "missing_days": [d.isoformat() for d in sent_status["missing_days"]],
        "partial_days": [],
        "refetched": [d.isoformat() for d in sent_refetched],
        "status": _status_word(apply, bool(sent_refetched), bool(sent_status["missing_days"])),
    }

    _log_summary(result)
    return result


def _status_word(apply: bool, refetched: bool, has_gaps: bool) -> str:
    if refetched:
        return "refetched"
    if has_gaps and not apply:
        return "stale"
    return "ok"


def _log_summary(result: dict[str, Any]) -> None:
    gaps = {
        dom: v["missing_days"] + v["partial_days"]
        for dom, v in result["domains"].items()
        if v["missing_days"] or v["partial_days"]
    }
    if gaps:
        logger.warning(
            "RECONCILE gaps=%s apply=%s degraded_calendar=%s",
            gaps,
            result["apply"],
            result["degraded_calendar"],
        )
    else:
        logger.info("RECONCILE all domains ok (expected_latest=%s)", result["expected_latest"])


def _as_date(value: Any) -> Any:
    """日期归一：DB 可能回 ``date``/``datetime``/ISO 字符串，纯函数只认 ``date``。"""
    return value.date() if hasattr(value, "date") and not isinstance(value, date) else value


def _normalized_day_set(days: Any) -> set[date]:
    return {d for d in (_as_date(v) for v in days) if isinstance(d, date)}


def compute_coverage(
    trading_days: list[date],
    have_days: set[date],
    complete_days: set[date] | None = None,
    *,
    window: tuple[date, date] | None = None,
) -> dict[str, Any]:
    """单域覆盖率（纯函数，无 DB 无 IO）。

    分母恒为 **交易日**（``daily_quotes`` 的 distinct ``trade_date``），且只取该域
    自己数据的 ``[first_day, last_day]`` 内的交易日——域内缺日才算洞，域开始记录
    之前的空白不算缺失。

    ``have_days``（有任何行）与 ``complete_days``（完整日）**必须分开**（Ruling 13）：
    ``sector_moneyflow`` 盘中 ``poll`` 行只是 inflow-Top-100 子集，"有行"不等于"完整"。
    ``cover_pct`` 基于 ``complete_days``，``have_days`` 原样带回供与计划 Why-now 表对拍。

    ``complete_days=None``（其余域）→ ``complete_days = have_days``（不是 0）。
    分母为空 → ``cover_pct=None``（绝不用 0/0 渲染成 0%）。

    ``window`` 显式给出测量窗口 ``(from, to)``（生产路径传"最近 window_days、末端锚定
    日历末日"，见 :func:`_coverage_window`）；缺省则取 ``have_days`` 的极值（纯函数
    调用方只关心"数据自身跨度"）。返回的 ``have_days`` 是**窗口内交易日**的行数——
    分母是交易日，分子就必须同口径（窗口外/非交易日的行既不是"有数据"也不是"缺"，
    它们只出现在 ``extra_days`` 里，不静默丢弃）。
    """
    trading = sorted(_normalized_day_set(trading_days))
    have_all = _normalized_day_set(have_days)
    if complete_days is None:
        complete_all = set(have_all)
    else:
        # 完整日必须是"有行"的子集：脏数据不得造出 complete > have
        complete_all = have_all & _normalized_day_set(complete_days)

    if window is None:
        span = have_all | complete_all
        if span:
            window = (min(span), max(span))
    if window is not None:
        first, last = _as_date(window[0]), _as_date(window[1])
        trading = [d for d in trading if first <= d <= last]
    # 分子 = 窗口内**且是交易日**的行数：周末/未来脏行既不算"有数据"也不算"缺"
    have = {d for d in have_all if d in set(trading)}
    complete = complete_all & have
    missing = sorted(d for d in trading if d not in have)
    # 窗口外/非交易日的行：单列，不静默丢弃。**不叫"脏行"** —— 当日盘中行只要落在
    # 窗口外就会进这里（窗口外 ≠ 这行是错的），它们只是不参与覆盖率计分
    extra = sorted(have_all - have)
    cover_pct = round(100.0 * len(complete) / len(trading), 2) if trading else None
    return {
        "have_days": len(have),
        "trading_days": len(trading),
        "complete_days": len(complete),
        "cover_pct": cover_pct,
        "missing_days": [d.isoformat() for d in missing],
        "extra_days": [d.isoformat() for d in extra],
    }


def _coverage_row_days_stmt(src: _CoverageSource, *, complete_only: bool = False) -> Any:
    """各交易日行数（``have`` 与 ``row_count`` 的唯一来源；``row_count`` 不另发查询）。

    ``complete_only=True`` 只统计"完整日"的产地（如 ``capture_kind='eod_full'``）——
    这是 **``complete_days`` 专用**查询，绝不能拿来算 ``have_days``/``row_count``：
    sector_moneyflow 的 pre-T2 历史只有 ``poll`` 行，混用会让 ``have_days`` 归零
    （真库实测踩过）。
    """
    model = _row_model_for_table(src.table)
    col = getattr(model, src.date_column)
    stmt = select(col, func.count()).group_by(col)
    if complete_only:
        if src.complete_kind is None:  # pragma: no cover — 调用方只对有声明的域传 True
            raise ValueError(f"{src.table} has no complete_kind")
        stmt = stmt.where(getattr(model, src.complete_kind[0]) == src.complete_kind[1])
    return stmt


def _coverage_days_stmt(src: _CoverageSource) -> Any:
    """该域数据的 ``[last_day, first_day]``（区间界定窗口，不是自然日窗口）。"""
    col = getattr(_row_model_for_table(src.table), src.date_column)
    return select(func.max(col), func.min(col))


async def _trading_calendar(
    db: Any, *, window_days: int, since: date | None
) -> tuple[list[date], bool]:
    """交易日历（``TRADING_DAY_SOURCES`` 的并集，只取最近 ``window_days``）+ 是否降级。

    取并集是因为**任何单源都可能缺日**：真库实测 ``daily_quotes`` 2026-09 缺
    09-09..09-17 共 7 个交易日，拿它当分母会把行情自己的洞算成巡检域的洞。
    ``stock_price_limits`` 是涨停判定的权威口径且逐日 100% 覆盖，并进来即可对齐。

    查询形状：内层 `distinct` 走 `trade_date` 索引，外层自连接取 `max` 再按日历区间
    过滤 → 有界扫描（不把整部行情史拉回来）。

    ``degraded`` 的语义 = **并集提供了主源缺失的交易日**（`union_days != primary_days`），
    或主源表不可用。只按"表是否存在"判降级会漏掉真实状态（主源在库但缺日、由次源补上），
    那种情况下分母口径已经变了却标 ok —— 评审 Important #1。
    """
    all_days: set[date] = set()
    primary_days: set[date] = set()
    primary_broken = False
    for name in TRADING_DAY_SOURCES:
        model = _row_model_for_table(name)
        distinct_days = select(model.trade_date).distinct().subquery()
        col = distinct_days.c.trade_date
        stmt = select(col).where(col >= select(func.max(col)).scalar_subquery() - window_days)
        try:
            days = {_as_date(d) for (d,) in (await db.execute(stmt)).all()}
        except ProgrammingError:
            if name != TRADING_DAY_PRIMARY:
                raise
            primary_broken = True
            logger.warning("COVERAGE trading calendar: %s unavailable", name, exc_info=True)
            continue
        all_days |= days
        if name == TRADING_DAY_PRIMARY:
            primary_days = days
    if since is not None:
        all_days = {d for d in all_days if d >= since}
    degraded = primary_broken or all_days != primary_days
    return sorted(all_days), degraded


def _row_model_for_table(table: str) -> Any:
    """表名 → ORM 模型（覆盖率查询按列取值；表名与 `_COVERAGE_SOURCES` 同源）。"""
    for model in (
        DailyQuote,
        DailyBasicIndicator,
        StockPriceLimit,
        MarketSentimentDaily,
        SectorMoneyflowSnapshot,
        MarketLimitUpStockDaily,
        MarketSentimentIntraday,
        DragonTigerEntry,
        BlockTrade,
        ShareFloat,
        StockRepurchase,
    ):
        if model.__tablename__ == table:
            return model
    raise KeyError(f"no mapped model for coverage table {table!r}")


def _coverage_window(
    range_row: Any, *, since: date | None, cal_end: date | None
) -> tuple[date | None, date | None, date | None, date | None]:
    """该域的数据跨度（SQL 的 max/min）与 **有效测量窗口**。

    返回 ``(first_day, last_day, win_from, win_to)``：``first_day``/``last_day`` 是
    原始数据跨度（报告里如实展示，停更事实由此可见）；``win_from``/``win_to`` 是分子
    分母**共用**的测量窗口。

    窗口**末端锚定交易日历的末日**（``cal_end``）而不是该域自己的最后一行：夹到域末日
    会让整域停更的域分母随之缩小（极端情况退化成单日），于是 `cover_pct=100`、
    `missing_days=[]` —— 对"这个域整体停更"完全盲（评审 Important #2；真库实测
    dragon_tiger/block_trade 的 trading_days=16 而邻域 17 就是这个夹取）。
    窗口下界 = max(域首行, `since`)：历史上限 + 域自身起点，谁更晚取谁。
    日历为空（全库无行情）时退回域末日，避免窗口末端为 None。
    """
    last, first = (range_row or (None, None))[0], (range_row or (None, None))[1]
    first, last = _as_date(first), _as_date(last)
    win_from = first
    if win_from is not None and since is not None:
        win_from = max(win_from, since)
    win_to = cal_end if cal_end is not None else last
    return first, last, win_from, win_to


def _normalize_coverage_row(
    domain: str,
    src: _CoverageSource,
    counts: dict[date, int],
    window: tuple[date | None, date | None] | None,
    quotes: list[date],
    *,
    complete_days: set[date] | None = None,
) -> dict[str, Any]:
    """SQL 行 → 报告 dict（纯函数，便于单测与真实查询共用同一形状）。

    ``counts`` = 该域**有任何行**的交易日行数（``have_days``/``row_count`` 的来源）；
    ``complete_days`` = 逐域单独查出的"完整日"集合（``sector_moneyflow`` 的
    ``capture_kind='eod_full'``）；``None`` 表示该域没有产地之分 → 完整日 = 有行的日子。
    ``window`` 是**有效测量窗口**（见 :func:`_coverage_window`，末端锚定日历末日）：
    窗口外的行只进 ``extra_days``，不抬高 `have_days`。空域（新表上线前）由调用方传入
    "最近 window_days"窗口，否则窗口无从取极值、分母会退化成整部行情史
    （真库实测：空域曾报 0/829 交易日）。
    """
    win_from, win_to = window if window else (None, None)
    have_raw = {d for d in counts if isinstance(d, date)}
    trading = [
        d for d in quotes if (win_from is None or d >= win_from) and (win_to is None or d <= win_to)
    ]
    # 空集不能写成 `complete_raw or None`：那会把"完整日 0 天"重新解释成"该域没有
    # 产地之分"，于是 complete_days 又等于 have_days（Ruling 13 的核心形状被吃掉）
    complete_raw = have_raw if complete_days is None else (complete_days & have_raw)
    out = compute_coverage(
        trading,
        have_raw,
        None if complete_days is None else complete_raw,
        window=(win_from, win_to) if win_from and win_to else None,
    )
    first = min(have_raw) if have_raw else None
    last = max(have_raw) if have_raw else None
    out.update(
        {
            "domain": domain,
            "table": src.table,
            "date_column": src.date_column,
            # 行数只算窗口内：否则"行数"与"天数"各自一个窗口，读的人会对不上
            "row_count": int(sum(counts.get(d, 0) for d in have_raw if d in set(trading))),
            "first_day": first.isoformat() if first else None,
            "last_day": last.isoformat() if last else None,
            "status": "ok",
        }
    )
    return out


def _empty_coverage_row(
    domain: str, src: _CoverageSource, *, status: str, degraded_calendar: bool = False
) -> dict[str, Any]:
    """缺表/缺列/无交易日时的一致形状：计数字段为 0、``cover_pct`` 为 ``None``。"""
    return {
        "domain": domain,
        "table": src.table,
        "date_column": src.date_column,
        "row_count": 0,
        "have_days": 0,
        "complete_days": 0,
        "trading_days": 0,
        "cover_pct": None,
        "missing_days": [],
        "extra_days": [],
        "first_day": None,
        "last_day": None,
        "status": status,
        "degraded_trading_calendar": degraded_calendar,
    }


def _undefined_kind(exc: BaseException) -> str | None:
    """把"迁移没跑 / 只跑了一半"从一般 SQL 错误里挑出来（其余异常照旧上抛）。

    ``42P01`` = 表缺失 → ``missing_table``；``42703`` = 列缺失（如 ``capture_kind``
    还没加）→ ``missing_column``。asyncpg 有时不带 ``sqlstate``，故再按消息兜底。
    """
    sqlstate = getattr(exc, "sqlstate", None)
    if sqlstate in (_SQLSTATE_UNDEFINED_TABLE, _SQLSTATE_UNDEFINED_COLUMN):
        return "missing_table" if sqlstate == _SQLSTATE_UNDEFINED_TABLE else "missing_column"
    text = str(getattr(exc, "orig", exc)).lower()
    if "undefinedcolumn" in text or "undefined_column" in text:
        return "missing_column"
    if "undefinedtable" in text or "undefined_table" in text:
        return "missing_table"
    if "does not exist" in text or "no such table" in text:
        return "missing_column" if "column" in text else "missing_table"
    return None


async def coverage_report(
    db: Any, *, window_days: int = DEFAULT_COVERAGE_WINDOW_DAYS
) -> list[dict]:
    """各巡检域的覆盖率（**只读**聚合；绝不回补、绝不写库）。

    分母 = **交易日**（``TRADING_DAY_SOURCES`` 的并集，见 :func:`_trading_calendar`），
    再逐域收窄到该域自身数据区间内（见 :func:`compute_coverage`）。``window_days`` 是
    最近**自然日**上限：域数据比它更长时只测最近这一段——历史长度不改变"最近有没有洞"
    的答案，也让单域固定为常数级查询。注意它**不是分母**（分母恒是交易日，避免周末把
    覆盖率稀释）。

    缺表/缺列 → 该域标 ``missing_table`` / ``missing_column``，其余域照常渲染（不 500）；
    每个域的读包在 SAVEPOINT 里，一个域失败不污染会话。

    这里**不**调用 `job_alert_service`：本函数由只读端点每次请求调用，登记表
    （`job:failures`，1 job 1 条）会被巡检刷成假告警。缺口的告警出口仍是 scheduler
    的 `_alert_job_failure`；本报告负责把洞**可见**（`cover_pct` / `missing_days`）。
    """
    if window_days <= 0:
        raise ValueError("window_days must be positive")

    # 交易日历 = 多源并集（`TRADING_DAY_SOURCES`）。**基准日不能取主源自己的 max**：
    # 主源缺最近几天时（真库实测 2026-09 缺 7 个交易日）会把补漏源的新日子截掉。
    # 这里直接取并集的 max，主源缺失则标降级。
    # 先取"粗略的一段"定位日历末日，再用它算 `since`，最后用 `since` 收窄（避免
    # 把整部行情史拉回来，也让窗口口径只依赖日历、不依赖任何单个巡检域）。
    rough, _ = await _trading_calendar(db, window_days=window_days, since=None)
    last_quote = rough[-1] if rough else None
    since = last_quote - timedelta(days=window_days) if last_quote else None
    calendar, calendar_degraded = await _trading_calendar(db, window_days=window_days, since=since)

    out: list[dict[str, Any]] = []
    for domain in INSPECT_DOMAINS:
        src = _COVERAGE_SOURCES[domain]
        try:
            async with db.begin_nested():
                # `have`（有任何行）与 `complete`（完整日）是**两次查询**：
                # 用 complete 的谓词算 have 会让 sector_moneyflow 的 pre-T2 历史
                # （只有 poll 行）在报告里变成 0（真库实测踩过，别合并这两条语句）
                days_rows = (await db.execute(_coverage_row_days_stmt(src))).all()
                complete_days: set[date] | None = None
                if src.complete_kind is not None:
                    complete_rows = (
                        await db.execute(_coverage_row_days_stmt(src, complete_only=True))
                    ).all()
                    complete_days = {
                        d for d in (_as_date(v) for v, _ in complete_rows) if d is not None
                    }
                range_row = (await db.execute(_coverage_days_stmt(src))).all()
        except ProgrammingError as exc:
            kind = _undefined_kind(exc)
            if kind is None:
                raise
            logger.warning(
                "COVERAGE %s degraded (%s) — migration not applied?", domain, kind, exc_info=True
            )
            out.append(
                _empty_coverage_row(domain, src, status=kind, degraded_calendar=calendar_degraded)
            )
            continue

        first_day, last_day, win_from, win_to = _coverage_window(
            range_row[0] if range_row else None, since=since, cal_end=last_quote
        )
        if first_day is None:
            # 该域一行都没有（新表上线前）：窗口退回"最近 window_days"（同样锚定日历末日）；
            # 若连日历都空，退化成"已观测到的交易日两端"（不取全史）
            win_from, win_to = since, last_quote
            if win_from is None and calendar:
                win_from, win_to = calendar[0], calendar[-1]
        # 分母 = 窗口内的交易日（并集，见 TRADING_DAY_SOURCES；主源降级会被标注）
        quotes = [
            d
            for d in calendar
            if (win_from is None or d >= win_from) and (win_to is None or d <= win_to)
        ]
        counts = {_as_date(d): int(n or 0) for d, n in days_rows}
        row = _normalize_coverage_row(
            domain,
            src,
            counts,
            (win_from, win_to),
            quotes,
            complete_days=complete_days,
        )
        row["degraded_trading_calendar"] = calendar_degraded
        if row["cover_pct"] != 100.0:
            logger.info(
                "COVERAGE %s have=%s complete=%s trading=%s pct=%s missing=%s",
                domain,
                row["have_days"],
                row["complete_days"],
                row["trading_days"],
                row["cover_pct"],
                len(row["missing_days"]),
            )
        # 不变式：有数据的域不可能整天都没有行。踩过的真坑是把"完整日"的产地谓词
        # （`capture_kind='eod_full'`）误用到 `have` 查询上 → pre-T2 只有 poll 行的域
        # 在报告里变成 0 天（缺表是 missing_table，不是这里）。WARNING 而不是抛错：
        # 只读巡检宁可少报一个域，也不能把新鲜度端点打成 500。
        if last_day is not None and row["have_days"] == 0:
            logger.warning(
                "COVERAGE %s invariant violated: rows exist up to %s but have_days=0 "
                "(完整日谓词串进了 have 查询?)",
                domain,
                last_day,
            )
        out.append(row)
    return out


# 域 → 回补器的启动期守卫：`DOMAINS` 里的每个域都必须真的能被补（E1：不然"看着被纳入、
# 永不补漏"）。反向（`_refetch_x` 存在但不在 DOMAINS）由只读巡检兜住——那正是巡检域的形态。
for _domain in DOMAINS:
    assert f"_refetch_{_domain}" in globals(), f"DOMAINS 里的 {_domain!r} 没有 _refetch_{_domain}"
