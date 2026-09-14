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

from app.models.market_data import StockPriceLimit
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
        (await db.execute(
            select(StockPriceLimit.trade_date)
            .where(StockPriceLimit.trade_date.in_(dates))
            .group_by(StockPriceLimit.trade_date)
        )).scalars().all()
    )
    return [d for d in dates if d not in present]


async def has_price_limits(db: AsyncSession, as_of: date) -> bool:
    stmt = select(func.count()).select_from(StockPriceLimit).where(
        StockPriceLimit.trade_date == as_of
    ).limit(1)
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
        stmt = pg_insert(StockPriceLimit).values(batch).on_conflict_do_update(
            constraint="uq_price_limit_date_stock",
            set_={
                "pre_close": pg_insert(StockPriceLimit).excluded.pre_close,
                "up_limit": pg_insert(StockPriceLimit).excluded.up_limit,
                "down_limit": pg_insert(StockPriceLimit).excluded.down_limit,
            },
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


async def fetch_limit_up_window(
    db: AsyncSession, as_of: date, as_of_prev: date, window_start: date
) -> list[dict[str, Any]]:
    """候选股在窗口内的**逐日**行（含 is_lu / touched / streak_upto / 申万 L1+L3）。

    返回整段窗口（16 个交易日约 2,400 行），不只 as_of/as_of_prev 两天：
    `n_day_m_board`（M = 板次数）与 `missing_days`（停牌披露）都要吃窗口里其余交易日。
    调用方（`limit_up_calculator`）自己按日期筛选。
    """
    rows = (
        await db.execute(
            text(_WINDOW_SQL),
            {"as_of": as_of, "as_of_prev": as_of_prev, "window_start": window_start},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def fetch_day_breadth(db: AsyncSession, as_of: date) -> dict[str, int]:
    """当日全市场涨停/跌停/炸板家数与有行情家数。"""
    row = (await db.execute(text(_BREADTH_SQL), {"as_of": as_of})).mappings().one()
    return {k: int(v or 0) for k, v in dict(row).items()}
