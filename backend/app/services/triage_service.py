"""自选体检：批量取数（固定查询数）+ 组合（单股合并 / 排序 / ≤8）.

**为什么读时算而不是落表**：自选 ≤50 只、每只 ≤``WINDOW_DAYS`` 行窗口，固定几条批量查询即可；
落表要新增表 + cron + 回填（见 spec §S8.3 的"零新表"约束）。

**反 N+1**：所有查询都按 ``symbols`` 一次性批量取，循环里**不查库**；探测器拿到的
``TriageContext`` 已经是全量内存对象。查询数由 ``tests/test_triage_service.py`` 的计数器
锁死为常数（不随自选数增长）。

**窗口为什么不按交易日算**：库内没有持久化交易日历（``docstring`` 见
``limit_up_repo.list_recent_trade_dates``），"哪些日子真有行情"只能靠 ``daily_quotes``
自身。本模块用**自然日下界**取行情（120 天 ≈ 80 交易日 > 探测器需要的 61 行），
再在内存里截每只股票最近 ``WINDOW_DAYS`` 行；龙虎榜的"近 3 个交易日"同样取
**行情窗口里实际出现过的最近 3 个日期**，不推算日历。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market_data import Announcement, DragonTigerEntry, StockPriceLimit
from app.models.quote import DailyQuote
from app.models.stock import Stock
from app.models.watchlist import UserWatchlist, UserWatchlistItem
from app.services import triage_detectors as td
from app.services.market_day_service import resolve_latest_complete_day

#: 一次请求最多算这么多只（spec §S8.3：>50 → 422，**不静默截断**）
MAX_SYMBOLS = 50
#: 每只股票保留的交易日行数。
#:
#: spec §S8.3 写的是"窗口 ≤60 交易日"，但 ``new_high_low`` 的判据是"今日收盘 > 前 60 日最高"，
#: 需要 **61 行**（今日 + 前 60 日）；只留 60 行会让该探测器**永不触发**（静默死掉）。
#: 故多留 5 行做缓冲：≤50 只 × 65 行 = 3250 行，成本可忽略，换来探测器真的活着。
WINDOW_DAYS = 65
#: 行情窗口的自然日下界（120 天 ≈ 80 个交易日，> 探测器需要的 61 行）
WINDOW_NATURAL_DAYS = 120
#: 公告：近 N 个自然日
ANNOUNCEMENT_DAYS = 3
#: 龙虎榜：近 N 个交易日
DRAGON_TIGER_TRADING_DAYS = 3
#: 龙虎榜兜底自然日窗口（行情窗口为空、拿不到交易日时用）
DRAGON_TIGER_NATURAL_DAYS = 7
#: 单页上限（spec §S8.4）
LIMIT = 8

_SH = ZoneInfo("Asia/Shanghai")

#: 申万一级当日涨跌幅 + 该行业在**全市场 L1 里**的涨幅绝对值分位。
#:
#: 为什么必须算全市场 L1 而不只看自选所属板块：spec §S8.2 第 7 行的判据是"进全市场
#: 前 10%"，分位离开全市场分母就没有意义。代价是**当日全市场行情的一次聚合**
#: （与 ``/market/sw-industry/performance`` 同一口径、同量级），但查询条数仍与自选数
#: 无关；不这么做就只能改成绝对阈值，那等于把探测器调成"永不触发"。
#:
#: 涨跌幅口径：``close/pre_close - 1``（**不用** ``pct_chg`` 列、不用 ``adj_factor``）。
#: 成分归属取 ``sw_industry_members`` 当前态快照（本 spec 不做 PIT 还原，v2 再说）。
_SECTOR_SQL = text(
    """
    WITH l1_members AS (
        SELECT c1.industry_code AS l1_code, c1.industry_name AS l1_name, m.symbol
        FROM sw_industry_members m
        JOIN sw_industry_classes c3
          ON c3.industry_code = m.industry_code AND c3.level = 3
        JOIN sw_industry_classes c2 ON c2.industry_code = c3.parent_code
        JOIN sw_industry_classes c1 ON c1.industry_code = c2.parent_code
    ),
    day_pct AS (
        SELECT lm.l1_code, lm.l1_name, lm.symbol,
               (q.close / NULLIF(q.pre_close, 0) - 1) * 100 AS pct
        FROM l1_members lm
        JOIN stocks s ON s.symbol = lm.symbol
        JOIN daily_quotes q ON q.stock_id = s.id AND q.trade_date = :as_of
        WHERE q.pre_close IS NOT NULL AND q.pre_close > 0
    ),
    l1 AS (
        SELECT l1_code, l1_name, avg(pct) AS pct FROM day_pct GROUP BY l1_code, l1_name
    ),
    ranked AS (
        SELECT l1_code, l1_name, pct, percent_rank() OVER (ORDER BY abs(pct)) AS pr FROM l1
    )
    SELECT d.symbol, r.l1_code, r.l1_name, r.pct, r.pr
    FROM ranked r
    JOIN (SELECT DISTINCT l1_code, symbol FROM day_pct WHERE symbol = ANY(:symbols)) d
      ON d.l1_code = r.l1_code
    """
)


@dataclass
class TriageResult:
    """一次体检的最终结果（API 直接投影成 ``TriageOut``）。"""

    as_of: date
    items: list[td.TriageItem] = field(default_factory=list)
    quiet_count: int = 0
    folded: int = 0
    unavailable: list[str] = field(default_factory=list)


def _today_sh() -> date:
    return datetime.now(_SH).date()


def _recent_trade_days(quotes: dict[str, list[td.QuoteRow]], limit: int) -> list[date]:
    """行情窗口里**实际出现过**的最近 ``limit`` 个交易日（升序）。

    真源是 ``daily_quotes`` 自身，不推算 weekday / ``trade_cal``（与
    ``limit_up_repo.list_recent_trade_dates`` 同一口径）。
    """
    seen = {row.trade_date for rows in quotes.values() for row in rows}
    return sorted(seen)[-limit:]


async def load_context(db: AsyncSession, symbols: list[str], as_of: date) -> td.TriageContext:
    """批量装载探测器输入 —— **固定查询数**（与自选数无关）。

    六条查询：① 代码 → id/名称 ② 行情窗口 ③ 涨停价+当日高低 ④ 公告 ⑤ 龙虎榜 ⑥ 板块。
    （spec 说的"5 条固定查询"是五个**数据面**；①是为把 symbol 映射成 ``stock_id`` 的
    一行解析查询，不随自选数增长，总数落在 spec §S8.3 的"≤6"预算内。）
    """
    codes = list(dict.fromkeys(symbols))
    ctx = td.TriageContext(as_of=as_of, names={}, symbols_order=list(codes))
    if not codes:
        return ctx

    # ① 代码 → stock_id / 名称
    stock_rows = (
        await db.execute(select(Stock.id, Stock.symbol, Stock.name).where(Stock.symbol.in_(codes)))
    ).all()
    id_of: dict[str, int] = {}
    for stock_id, symbol, name in stock_rows:
        id_of.setdefault(str(symbol), int(stock_id))
        ctx.names[str(symbol)] = str(name)
    ids = list(id_of.values())
    if not ids:
        # 自选代码在 stocks 里一个都查不到：**不静默当作"今天没事"**，全部探测器标缺数据
        ctx.unavailable.update(spec.key for spec in td.TRIAGE_DETECTORS)
        return ctx
    symbol_of = {stock_id: symbol for symbol, stock_id in id_of.items()}

    # ② 行情窗口（close/pre_close 一律原值，涨跌幅在探测器里算）
    window_start = as_of - timedelta(days=WINDOW_NATURAL_DAYS)
    quote_rows = (
        await db.execute(
            select(
                DailyQuote.stock_id,
                DailyQuote.trade_date,
                DailyQuote.close,
                DailyQuote.pre_close,
                DailyQuote.amount,
            )
            .where(
                DailyQuote.stock_id.in_(ids),
                DailyQuote.trade_date <= as_of,
                DailyQuote.trade_date >= window_start,
            )
            .order_by(DailyQuote.stock_id, DailyQuote.trade_date)
        )
    ).all()
    last_close: dict[int, float] = {}
    for stock_id, trade_date, close, pre_close, amount in quote_rows:
        if close is None:
            continue
        sid = int(stock_id)
        close_f = float(close)
        # pre_close 缺失的历史行：退化为"上一行收盘"（跨除权日会偏，但比丢行更可复核）；
        # 首行仍缺 → 用自身收盘（当日涨跌幅记 0%），绝不用 adj_factor 推断。
        base = float(pre_close) if pre_close is not None else last_close.get(sid, close_f)
        symbol = symbol_of.get(sid)
        if symbol is None:
            continue
        ctx.quotes.setdefault(symbol, []).append(
            td.QuoteRow(
                trade_date=trade_date,
                close=close_f,
                pre_close=base,
                amount=float(amount or 0),
            )
        )
        last_close[sid] = close_f
    for symbol, rows in ctx.quotes.items():
        ctx.quotes[symbol] = rows[-WINDOW_DAYS:]

    # ③ 涨停价 + 当日最高（炸板需要 high；涨停价唯一权威源 = stock_price_limits）
    limit_rows = (
        await db.execute(
            select(
                StockPriceLimit.stock_id,
                StockPriceLimit.up_limit,
                DailyQuote.high,
                DailyQuote.close,
                DailyQuote.pre_close,
            )
            .join(
                DailyQuote,
                (DailyQuote.stock_id == StockPriceLimit.stock_id)
                & (DailyQuote.trade_date == StockPriceLimit.trade_date),
            )
            .where(StockPriceLimit.stock_id.in_(ids), StockPriceLimit.trade_date == as_of)
        )
    ).all()
    for stock_id, up_limit, high, close, pre_close in limit_rows:
        symbol = symbol_of.get(int(stock_id))
        if symbol is None or up_limit is None:
            continue
        ctx.limits[symbol] = td.LimitRow(
            up_limit=float(up_limit),
            high=float(high or 0),
            close=float(close or 0),
            pre_close=float(pre_close or 0),
        )

    # ④ 公告（近 N 个自然日；equity_change 由探测器优先展示）
    ann_cutoff = datetime.combine(as_of - timedelta(days=ANNOUNCEMENT_DAYS), time.min)
    ann_rows = (
        await db.execute(
            select(
                Announcement.sec_code,
                Announcement.announcement_id,
                Announcement.title,
                Announcement.category,
                Announcement.announce_time,
            )
            .where(
                Announcement.sec_code.in_(codes),
                Announcement.announce_time >= ann_cutoff,
            )
            .order_by(Announcement.announce_time.desc())
        )
    ).all()
    for sec_code, announcement_id, title, category, announce_time in ann_rows:
        ctx.announcements.setdefault(str(sec_code), []).append(
            td.AnnRow(
                announcement_id=str(announcement_id),
                title=str(title or ""),
                category=str(category or ""),
                announcement_time=announce_time,
            )
        )

    # ⑤ 龙虎榜（近 N 个交易日；ts_code 形如 600000.SH，按 "<symbol>." 前缀批量匹配）
    stmt = select(
        DragonTigerEntry.ts_code,
        DragonTigerEntry.trade_date,
        DragonTigerEntry.net_amount,
    ).where(
        or_(*[DragonTigerEntry.ts_code.startswith(f"{code}.") for code in codes]),
    )
    recent_days = _recent_trade_days(ctx.quotes, DRAGON_TIGER_TRADING_DAYS)
    if recent_days:
        stmt = stmt.where(DragonTigerEntry.trade_date.in_(recent_days))
    else:
        stmt = stmt.where(
            DragonTigerEntry.trade_date >= as_of - timedelta(days=DRAGON_TIGER_NATURAL_DAYS)
        )
    dt_rows = (await db.execute(stmt)).all()
    for ts_code, trade_date, net_amount in dt_rows:
        symbol = str(ts_code).split(".", 1)[0]
        if symbol not in ctx.names:
            continue
        ctx.dragon_tiger.setdefault(symbol, []).append(
            td.DtRow(trade_date=trade_date, net_amount=float(net_amount or 0))
        )

    # ⑥ 板块（申万一级当日涨跌幅 + 全市场分位）
    sector_rows = (
        (await db.execute(_SECTOR_SQL, {"as_of": as_of, "symbols": codes})).mappings().all()
    )
    for row in sector_rows:
        symbol = str(row["symbol"])
        code = str(row["l1_code"])
        ctx.sector_of[symbol] = code
        ctx.sector_name[code] = str(row["l1_name"] or code)
        ctx.sector_pct[code] = float(row["pct"])
        ctx.sector_rank[code] = float(row["pr"])
    if not ctx.sector_of:
        # 缺板块归属/缺当日行情 → 明示不可用，不静默当作"板块没动"
        ctx.unavailable.add("sector_move")

    return ctx


def _sort_key(item: td.TriageItem) -> tuple[int, float, str]:
    """排序：severity 固定次序 → |当日涨跌幅| 降序 → symbol（确定性尾巴）。"""
    return (td.SEVERITY_RANK[item.severity], -abs(item.day_pct or 0.0), item.symbol)


def _merge_group(group: list[td.TriageItem]) -> td.TriageItem:
    """单股合并：保留最高 severity 的那条，其余并入 facts，headline 标注「另有 N 项」。"""
    group = sorted(group, key=_sort_key)
    primary = group[0]
    others = group[1:]
    if not others:
        return primary
    extra: tuple[td.Fact, ...] = tuple(fact for item in others for fact in item.facts)
    return replace(
        primary,
        headline=f"{primary.headline}（另有 {len(others)} 项）",
        facts=primary.facts + extra,
    )


def compose(items: list[td.TriageItem], limit: int = LIMIT) -> tuple[list[td.TriageItem], int]:
    """单股合并 + 排序 + 折叠；返回 ``(展示条目, 被折叠数)``。

    ``limit`` 只裁剪**展示**，不丢数据：被裁掉的条数由第二个返回值（→ ``folded``）带出去。
    """
    groups: dict[str, list[td.TriageItem]] = {}
    for item in items:
        groups.setdefault(item.symbol, []).append(item)
    merged = [_merge_group(group) for group in groups.values()]
    merged.sort(key=_sort_key)
    cap = max(0, limit)
    return merged[:cap], max(0, len(merged) - cap)


async def user_symbols(
    db: AsyncSession,
    user_id: uuid.UUID | str,
    watchlist_id: uuid.UUID | None = None,
) -> list[str]:
    """用户自选代码，按 ``sort_order``（同一用户可有多张自选表）。

    只读：**不**顺手创建默认自选表（GET 不改数据）。无自选 → ``[]``，
    由前端给"去添加自选"入口（spec §S8.1 三态之一）。
    ``user_id`` 接受 ``str`` 是因为 ``Principal.user_id`` 是字符串（见 ``api/deps.py``）。
    """
    uid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    stmt = (
        select(UserWatchlistItem.symbol)
        .join(UserWatchlist, UserWatchlist.id == UserWatchlistItem.watchlist_id)
        .where(UserWatchlist.user_id == uid)
        .order_by(
            UserWatchlist.is_default.desc(),
            UserWatchlistItem.sort_order,
            UserWatchlistItem.created_at,
        )
    )
    if watchlist_id is not None:
        stmt = stmt.where(UserWatchlist.id == watchlist_id)
    rows = (await db.execute(stmt)).scalars().all()
    return list(dict.fromkeys(str(symbol) for symbol in rows))


async def get_triage(
    db: AsyncSession,
    symbols: list[str],
    limit: int = LIMIT,
) -> TriageResult:
    """一次体检：解析最近完整日 → 批量装载 → 跑探测器 → 合并/排序/折叠。"""
    codes = list(dict.fromkeys(symbols))
    day = await resolve_latest_complete_day(db)
    if day is None:
        # 库内没有可用交易日：**不**报"今天都平静"，而是全部探测器标缺数据
        return TriageResult(
            as_of=_today_sh(),
            unavailable=sorted(spec.key for spec in td.TRIAGE_DETECTORS),
        )
    ctx = await load_context(db, codes, day.day)
    found = td.run_detectors(ctx)
    items, folded = compose(found, limit=limit)
    hit = {item.symbol for item in found}
    # "平静"只对**有行情**的自选成立：没有行情（停牌/新入库/代码错误）等于"未被判定"，
    # 把它算进 quiet_count 会把"没查"读成"没事"（spec §S8.4 的同一条禁令）。
    judgeable = set(ctx.quotes)
    quiet = len({code for code in codes if code in judgeable and code not in hit})
    return TriageResult(
        as_of=day.day,
        items=items,
        quiet_count=quiet,
        folded=folded,
        unavailable=sorted(ctx.unavailable),
    )
