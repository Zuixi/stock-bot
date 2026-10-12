"""连板/情绪数据访问：权威限价、候选窗口、当日广度、情绪聚合。

口径与实测证据见 docs/design/limit-up-sentiment.md。
"""

from __future__ import annotations

from datetime import date
from typing import Any, cast

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market_data import (
    MarketLimitUpStockDaily,
    MarketSentimentDaily,
    StockPriceLimit,
)
from app.models.quote import DailyQuote


async def latest_quote_date(db: AsyncSession) -> date | None:
    return (await db.execute(select(func.max(DailyQuote.trade_date)))).scalar_one_or_none()


async def list_recent_trade_dates(db: AsyncSession, as_of: date, limit: int) -> list[date]:
    """窗口内交易日（升序）。

    真源是 daily_quotes 实际存在的日期，而不是 trade_cal 推算或 weekday()——库内没有
    持久化交易日历，而"哪些日子真有行情"本身就是最准的可用性判据（节假日后的永久
    缺口也是靠这个口径暴露的）。
    """
    stmt = (
        select(DailyQuote.trade_date)
        .where(DailyQuote.trade_date <= as_of)
        .group_by(DailyQuote.trade_date)
        .order_by(DailyQuote.trade_date.desc())
        .limit(limit)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return sorted(rows)


async def missing_price_limit_dates(db: AsyncSession, dates: list[date]) -> list[date]:
    """给定交易日中，stock_price_limits 尚无任何行的那些（补漏判据）。"""
    if not dates:
        return []
    present = set(
        (
            await db.execute(
                select(StockPriceLimit.trade_date)
                .where(StockPriceLimit.trade_date.in_(dates))
                .group_by(StockPriceLimit.trade_date)
            )
        )
        .scalars()
        .all()
    )
    return [d for d in dates if d not in present]


async def has_price_limits(db: AsyncSession, as_of: date) -> bool:
    stmt = (
        select(func.count())
        .select_from(StockPriceLimit)
        .where(StockPriceLimit.trade_date == as_of)
        .limit(1)
    )
    return bool((await db.execute(stmt)).scalar_one())


# asyncpg 单条语句 bind 参数上限 32767；本表 INSERT 每行 6 个绑定列（id 自增不参与），
# 单日约 5,499 行会一次绑定 6*5499=32994 个参数越界（实测 InterfaceError），故分批执行。
_UPSERT_CHUNK = 4000


async def upsert_price_limits(db: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """幂等写入限价；同批内先按唯一键去重（pg 的 ON CONFLICT 不处理同批自冲突）。

    去重键必须是完整的唯一键 (trade_date, stock_id)——只按 stock_id 去重会静默
    丢掉其他日期的行（调用方目前按日分批，错只错在"以后有人合并多日"）。
    单日约 5,499 行超过 asyncpg 单语句 32767 绑定参数上限，按 _UPSERT_CHUNK 分批。
    """
    if not rows:
        return 0
    deduped = list({(r["trade_date"], r["stock_id"]): r for r in rows}.values())
    total = 0
    for i in range(0, len(deduped), _UPSERT_CHUNK):
        batch = deduped[i : i + _UPSERT_CHUNK]
        stmt = (
            pg_insert(StockPriceLimit)
            .values(batch)
            .on_conflict_do_update(
                constraint="uq_price_limit_date_stock",
                set_={
                    "pre_close": pg_insert(StockPriceLimit).excluded.pre_close,
                    "up_limit": pg_insert(StockPriceLimit).excluded.up_limit,
                    "down_limit": pg_insert(StockPriceLimit).excluded.down_limit,
                },
            )
        )
        result = cast("CursorResult[Any]", await db.execute(stmt))
        total += int(result.rowcount)
    await db.flush()
    return total


# 查询形状即性能契约（实测，2026-09-14 复测：16 交易日窗口 2026-08-18~09-08，
# 等价索引临时表）：整窗回 2,411 行 / ~48ms；只取末日+前日回 302 行 / ~47ms。
# 计划形状为 Filter/Join + `Bitmap Heap Scan daily_quotes` + `Index Scan
# idx_daily_quotes_stock_date`（uq_ 与 idx_ 同列，规划器按 OID 任选其一，**不要
# 断言具体是哪一个**）。若让 stock_price_limits 驱动、逐股 probe daily_quotes，
# 同一逻辑要 620ms（Nested Loop 5,499 次 probe）。改这条 SQL 前先跑计划守卫测试。
#
# 四条硬性注意：
# 1) cand 必须 DISTINCT —— 两日都涨停的票会出现两次，扇出后涨停数 75→94 并造出假连板。
# 2) is_lu 必须 COALESCE(false) —— LEFT JOIN 到缺失限价的交易日会得到 NULL，
#    而 NULL 会让 gaps-and-islands 的 grp 分组错乱（NULL 与 false 不同组）。
# 3) 停牌 policy A：gaps-and-islands 按「该股实际有行情的行」分段，停牌日既不计数
#    也不截断连板（对齐东财 lbc）。不要加"缺行即断"的守卫——那会与用户对照的口径不符。
# 4) streaked 的 PARTITION 必须带 is_lu 与 grp 两个键。`grp = rn_all - rn_by_val` 只保证
#    **组内常量**，不保证**组间唯一**：一段 false 岛与紧接的 true 岛会撞同一 grp，
#    被合并后 true 行的 streak 继承 false 行的计数。实测本窗口内 4 行 is_lu 虚高
#    （600127 @08-27/09-01 报 3 实为 1；*ST艾艾、002365 @08-20 报 2 实为 1），
#    恰好不落在 09-08/09-07，所以黄金数字会漏过它，但 promotion_rate 读的正是
#    as_of_prev 的 streak——换个日子就静默错。
_WINDOW_SQL = """
WITH limits AS (
    SELECT l.stock_id, l.trade_date, l.pre_close, l.up_limit, l.down_limit
    FROM stock_price_limits l
    WHERE l.trade_date BETWEEN :window_start AND :as_of
),
cand AS (
    SELECT DISTINCT q.stock_id
    FROM daily_quotes q
    JOIN limits l ON l.stock_id = q.stock_id AND l.trade_date = q.trade_date
    WHERE q.trade_date IN (:as_of, :as_of_prev)
      AND q.close >= l.up_limit - 0.005
),
win AS (
    SELECT q.stock_id, q.trade_date, q.close, q.open, q.high, q.amount,
           l.pre_close, l.up_limit, l.down_limit,
           COALESCE(q.close >= l.up_limit - 0.005, false) AS is_lu,
           COALESCE(q.high  >= l.up_limit - 0.005, false) AS touched
    FROM daily_quotes q
    JOIN cand c ON c.stock_id = q.stock_id
    LEFT JOIN limits l ON l.stock_id = q.stock_id AND l.trade_date = q.trade_date
    WHERE q.trade_date BETWEEN :window_start AND :as_of
),
island AS (
    SELECT w.*,
           row_number() OVER (PARTITION BY w.stock_id ORDER BY w.trade_date)
         - row_number() OVER (PARTITION BY w.stock_id, w.is_lu ORDER BY w.trade_date) AS grp
    FROM win w
),
streaked AS (
    SELECT i.*,
           row_number() OVER (
               PARTITION BY i.stock_id, i.is_lu, i.grp ORDER BY i.trade_date
           ) AS streak_upto
    FROM island i
)
SELECT s.stock_id, st.symbol, st.name, s.trade_date, s.close, s.open, s.high, s.amount,
       s.pre_close, s.up_limit, s.down_limit, s.is_lu, s.touched, s.streak_upto,
       c1.industry_code AS sw_l1_code, c1.industry_name AS sw_l1_name,
       c3.industry_code AS sw_l3_code, c3.industry_name AS sw_l3_name
FROM streaked s
JOIN stocks st ON st.id = s.stock_id
LEFT JOIN sw_industry_members m ON m.symbol = st.symbol
LEFT JOIN sw_industry_classes c3 ON c3.industry_code = m.industry_code AND c3.level = 3
LEFT JOIN sw_industry_classes c2 ON c2.industry_code = c3.parent_code
LEFT JOIN sw_industry_classes c1 ON c1.industry_code = c2.parent_code
-- 不过滤 trade_date：整窗都要回（streak 只是窗口副产品），N天M板 / missing_days
-- 还靠窗口其余交易日；只回 as_of 与 as_of_prev 会把 boards_in_window 锁死在 2、
-- 并把每只票的 missing_days 算成「窗口天数 - 2」（实测 16 窗口 → 14）。
ORDER BY s.stock_id, s.trade_date
"""

# 全市场当日广度：口径与候选窗口不同，不能合并——候选集只有"今日或昨日涨停"的票，
# 炸板的票不在其中，实测会把 09-08 的炸板数从 39 算成 12（约 3 倍偏差）。
_BREADTH_SQL = """
SELECT count(*) FILTER (WHERE q.close >= l.up_limit  - 0.005) AS zt_count,
       count(*) FILTER (WHERE q.close <= l.down_limit + 0.005) AS dt_count,
       count(*) FILTER (WHERE q.high >= l.up_limit - 0.005
                          AND q.close < l.up_limit - 0.005) AS zb_count,
       count(*) AS quoted
FROM daily_quotes q
JOIN stock_price_limits l ON l.stock_id = q.stock_id AND l.trade_date = q.trade_date
WHERE q.trade_date = :as_of
"""


def build_window_sql_for_explain() -> str:
    """给计划守卫测试用的同源 SQL（避免测试里复制一份会漂移的 SQL 文本）。"""
    return _WINDOW_SQL


# 当日炸板选行：谓词与 `_BREADTH_SQL` 的 `zb_count` **逐字一致**（`_BREADTH_SQL` 只回
# 计数、`_WINDOW_SQL` 的 `cand` 又不含炸板票——实测 09-08 的 39 只炸板里只有 12 只在
# 候选窗口内，靠窗口回不出另外 27 只）。同款 `JOIN stock_price_limits` 保证缺限价的行
# 被排除，与 breadth 口径同源。
_DAY_BROKEN_SQL = """
SELECT q.stock_id, st.symbol, st.name
FROM daily_quotes q
JOIN stock_price_limits l ON l.stock_id = q.stock_id AND l.trade_date = q.trade_date
JOIN stocks st ON st.id = q.stock_id
WHERE q.trade_date = :as_of
  AND q.high >= l.up_limit - 0.005
  AND q.close < l.up_limit - 0.005
ORDER BY q.stock_id
"""


def build_day_broken_sql_for_audit() -> str:
    """给测试用的同源 SQL（守卫"炸板谓词 == breadth 谓词"，避免两处口径漂移）。"""
    return _DAY_BROKEN_SQL


async def fetch_day_broken_board_stocks(db: AsyncSession, as_of: date) -> list[dict[str, Any]]:
    """当日炸板股（盘中触板但收盘未封住）逐股行；`stock_id/symbol/name`。

    与 `fetch_day_breadth` 的 `zb_count` 同谓词 → `len(rows) == breadth["zb_count"]`。
    """
    rows = (await db.execute(text(_DAY_BROKEN_SQL), {"as_of": as_of})).mappings().all()
    return [dict(r) for r in rows]


async def fetch_limit_up_window(
    db: AsyncSession, as_of: date, as_of_prev: date, window_start: date
) -> list[dict[str, Any]]:
    """候选股在窗口内的**逐日**行（含 is_lu / touched / streak_upto / 申万 L1+L3）。

    返回整段窗口（16 个交易日约 2,400 行），不只 as_of/as_of_prev 两天：
    `n_day_m_board`（M = 板次数）与 `missing_days`（停牌披露）都要吃窗口里其余交易日。
    调用方（`limit_up_calculator`）自己按日期筛选。
    """
    rows = (
        (
            await db.execute(
                text(_WINDOW_SQL),
                {"as_of": as_of, "as_of_prev": as_of_prev, "window_start": window_start},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def fetch_day_breadth(db: AsyncSession, as_of: date) -> dict[str, int]:
    """当日全市场涨停/跌停/炸板家数与有行情家数。"""
    row = (await db.execute(text(_BREADTH_SQL), {"as_of": as_of})).mappings().one()
    return {k: int(v or 0) for k, v in dict(row).items()}


# 事实表单行 16 列 → asyncpg 单语句 32767 个绑定参数上限下最多约 2000 行；取 1000 留余量
# （单日行集实测 178–450 行，正常不会触发分批，分批只为防御"以后有人合并多日再写"）。
_FACT_UPSERT_CHUNK = 1000

# 事实表 upsert 只覆写**本地判定 + 窗口派生**列。东财独有列（seal_time/seal_fund/
# break_count/enrich_source）必须留在 set_ 之外：17:20 的 facts 重跑不得把同 job
# 后段刚写好的封板明细抹回 NULL（口径冻结：东财只填它独有的列，本地只填判定列）。
_FACT_UPDATE_COLUMNS = (
    "symbol",
    "is_lu",
    "touched",
    "is_zb",
    "prev_is_lu",
    "streak",
    "days_span",
    "boards_in_window",
    "missing_days",
    "fact_source",
)

# 回补行标记（Ruling 18）。**写入方与守卫必须同一个字面量**：回补脚本以它标记新行，
# upsert 的 `DO UPDATE ... WHERE <stored> = BACKFILL_SOURCE` 以它识别"可修复的行"。
# 两处各写一份 'backfill' 就会静默退化成"回补能改实时行"。
BACKFILL_SOURCE = "backfill"


async def upsert_limit_up_stock_daily(
    db: AsyncSession, rows: list[dict[str, Any]], *, backfill_only: bool = False
) -> int:
    """幂等写入个股事实；同批内先按唯一键去重（pg 的 ON CONFLICT 不处理同批自冲突）。

    `backfill_only=True`（Ruling 18）：**回补只能修回补行**。`DO UPDATE` 带
    `WHERE market_limit_up_stock_daily.fact_source = 'backfill'`（看**已存储**的列，
    不是 `excluded`），于是当天实时写下的 `local_calc` 行——连同 `enrich_source`/
    `seal_*` 增强列——对回补**完全不可见**。这让"黄金日既有行字节不变"是**保证**，
    不是"恰好没撞上"。

    `backfill_only=True` 还**拒绝** `fact_source != 'backfill'` 的行（最终评审 Important 2）：
    `ON CONFLICT` 的守卫只管 `DO UPDATE` 分支，插入分支不受约束 —— 一次"回补"批里若某行
    把 `fact_source` 写错，就会 **INSERT 出 `local_calc` 行**，从而永久免疫后续回补修复
    （守卫只修 `backfill` 行）。这里提前 fail-fast，不静默插脏行。
    """
    if not rows:
        return 0
    if backfill_only:
        offenders = sorted({str(r.get("fact_source")) for r in rows} - {BACKFILL_SOURCE})
        if offenders:
            raise ValueError(
                "upsert_limit_up_stock_daily(backfill_only=True) 收到非回补来源的行："
                f"fact_source={offenders}（只允许 {BACKFILL_SOURCE!r}）—— 否则插入分支会写出"
                "永久免疫 Ruling 18 修复的实时行"
            )
    deduped = list({(r["trade_date"], r["stock_id"]): r for r in rows}.values())
    total = 0
    for i in range(0, len(deduped), _FACT_UPSERT_CHUNK):
        batch = deduped[i : i + _FACT_UPSERT_CHUNK]
        ins = pg_insert(MarketLimitUpStockDaily)
        stmt = ins.values(batch).on_conflict_do_update(
            constraint="uq_limit_up_stock_daily_date_stock",
            set_={
                **{c: getattr(ins.excluded, c) for c in _FACT_UPDATE_COLUMNS},
                "observed_at": func.now(),
            },
            where=(
                MarketLimitUpStockDaily.fact_source == BACKFILL_SOURCE if backfill_only else None
            ),
        )
        result = cast("CursorResult[Any]", await db.execute(stmt))
        total += int(result.rowcount)
    await db.flush()
    return total


async def list_limit_up_stock_daily(db: AsyncSession, as_of: date) -> list[MarketLimitUpStockDaily]:
    """某交易日的个股事实行（涨停优先、连板数降序、再按代码升序；排序确定性可测）。"""
    stmt = (
        select(MarketLimitUpStockDaily)
        .where(MarketLimitUpStockDaily.trade_date == as_of)
        .order_by(
            MarketLimitUpStockDaily.is_lu.desc(),
            MarketLimitUpStockDaily.streak.desc(),
            MarketLimitUpStockDaily.symbol,
        )
    )
    return list((await db.execute(stmt)).scalars().all())


async def limit_up_stock_daily_missing_dates(db: AsyncSession, dates: list[date]) -> list[date]:
    """给定交易日中，`market_limit_up_stock_daily` 尚无任何行的那些（回补判据）。"""
    if not dates:
        return []
    present = set(
        (
            await db.execute(
                select(MarketLimitUpStockDaily.trade_date)
                .where(MarketLimitUpStockDaily.trade_date.in_(dates))
                .group_by(MarketLimitUpStockDaily.trade_date)
            )
        )
        .scalars()
        .all()
    )
    return [d for d in dates if d not in present]


async def upsert_sentiment_daily(
    db: AsyncSession, row: dict[str, Any], *, backfill_only: bool = False
) -> int:
    """幂等写情绪聚合行（唯一键 `trade_date`）。

    `backfill_only=True`（Ruling 18）：回补 upsert 只修回补行 —— `DO UPDATE` 带
    `WHERE market_sentiment_daily.source = 'backfill'`（看**已存储**的 `source`，
    不是 `excluded`）。实时路径（`backfill_only=False`）语义**不变**：仍是覆写，
    且 SET 依旧排除 `source`，永不重标。

    `backfill_only=True` 还**拒绝** `source != 'backfill'` 的行（最终评审 Important 2）：
    与 :func:`upsert_limit_up_stock_daily` 同因 —— 插入分支不受 `ON CONFLICT` 守卫约束，
    写错来源的行会被 INSERT 成"实时行"并永久免疫回补修复。
    """
    if backfill_only and row.get("source") != BACKFILL_SOURCE:
        raise ValueError(
            "upsert_sentiment_daily(backfill_only=True) 收到非回补来源的行："
            f"source={row.get('source')!r}（只允许 {BACKFILL_SOURCE!r}）"
        )
    stmt = (
        pg_insert(MarketSentimentDaily)
        .values(**row)
        .on_conflict_do_update(
            constraint="uq_sentiment_daily_date",
            set_={k: v for k, v in row.items() if k not in ("trade_date", "source")},
            where=MarketSentimentDaily.source == BACKFILL_SOURCE if backfill_only else None,
        )
    )
    result = cast("CursorResult[Any]", await db.execute(stmt))
    await db.flush()
    return int(result.rowcount)


async def list_sentiment_calendar(db: AsyncSession, days: int) -> list[dict[str, Any]]:
    """近 N 个交易日情绪时序（升序），供周期图消费。"""
    stmt = select(MarketSentimentDaily).order_by(MarketSentimentDaily.trade_date.desc()).limit(days)
    rows = list((await db.execute(stmt)).scalars().all())
    return [
        {
            "trade_date": r.trade_date,
            "zt_count": r.zt_count,
            "dt_count": r.dt_count,
            "zb_count": r.zb_count,
            "broken_rate": r.broken_rate,
            "yzt_avg_pct": r.yzt_avg_pct,
            "promo_1to2": r.promo_1to2,
            "promo_2to3": r.promo_2to3,
            "max_streak": r.max_streak,
        }
        for r in reversed(rows)
    ]


# ----- Task 6a：情绪趋势窗口读（纯读，不物化）--------------------------------------
#
# 与 `list_sentiment_calendar` 的分工：那个只回"图表要的 9 个字段"、且**缺
# `promo_1to2_n`**——分位必须知道"这天该字段到底有没有值"（NULL ≠ 0，见 Ruling 4），
# 所以趋势读单独一条，回整行 + 有效样本判据列。不要合并：calendar 的字段集是前端
# 周期图契约，吃进 `promo_1to2_n` 会让两个消费方的口径糊在一起。


async def latest_sentiment_day(db: AsyncSession) -> date | None:
    """`market_sentiment_daily` 的最近交易日（趋势端点的 `as_of`，不是"今天"）。"""
    return (
        await db.execute(select(func.max(MarketSentimentDaily.trade_date)))
    ).scalar_one_or_none()


async def list_sentiment_days(db: AsyncSession, days: int) -> list[MarketSentimentDaily]:
    """近 N 个交易日情绪行（**升序**返回）。

    实现取"降序 limit days"再反转：跨月时序图从左到右，且窗口是"最近 N 天"而不是
    "最早 N 天"。`days <= 0` 时 SQL 的 `LIMIT 0` 回空（不抛），调用方的 samples=0。
    """
    stmt = (
        select(MarketSentimentDaily)
        .order_by(MarketSentimentDaily.trade_date.desc())
        .limit(max(days, 0))
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return list(reversed(rows))


async def sentiment_days_in_range(db: AsyncSession, start: date, end: date) -> list[date]:
    """`[start, end]` 闭区间内 `market_sentiment_daily` 实际持有的交易日（升序）。

    供趋势端点的"窗口内缺日"（有交易日行情、但没有情绪行）与日历对拍——分母是**情绪
    表自己持有的日子**，不是 weekday 推算（节假日工作日的脏日期不会被当成交易日）。
    """
    stmt = (
        select(MarketSentimentDaily.trade_date)
        .where(
            MarketSentimentDaily.trade_date >= start,
            MarketSentimentDaily.trade_date <= end,
        )
        .order_by(MarketSentimentDaily.trade_date.asc())
        .group_by(MarketSentimentDaily.trade_date)
    )
    return list((await db.execute(stmt)).scalars().all())
