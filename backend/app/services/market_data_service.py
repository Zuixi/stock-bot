"""Market-data face: ingest + read services.

数据源（字段/单位见 plans/2026-09-03-market-data-face.md「已验证数据源事实」）。
"""

from __future__ import annotations

import asyncio
import logging
import math
import sys
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import index_repo, market_data_repo

if TYPE_CHECKING:
    from app.core.redis import CacheClient
    from app.models.index_daily import IndexDaily

logger = logging.getLogger(__name__)

_SH = ZoneInfo("Asia/Shanghai")

GLOBAL_INDICES: list[dict[str, str]] = [
    {
        "ts_code": "000001.SH",
        "name": "上证指数",
        "market": "CN",
        "region": "asia",
        "em_secid": "1.000001",
        "source": "index_daily",
    },
    {
        "ts_code": "399001.SZ",
        "name": "深证成指",
        "market": "CN",
        "region": "asia",
        "em_secid": "0.399001",
        "source": "index_daily",
    },
    {
        "ts_code": "399006.SZ",
        "name": "创业板指",
        "market": "CN",
        "region": "asia",
        "em_secid": "0.399006",
        "source": "index_daily",
    },
    {
        "ts_code": "000300.SH",
        "name": "沪深300",
        "market": "CN",
        "region": "asia",
        "em_secid": "1.000300",
        "source": "index_daily",
    },
    {
        "ts_code": "000905.SH",
        "name": "中证500",
        "market": "CN",
        "region": "asia",
        "em_secid": "1.000905",
        "source": "index_daily",
    },
    {
        "ts_code": "000688.SH",
        "name": "科创50",
        "market": "CN",
        "region": "asia",
        "em_secid": "1.000688",
        "source": "index_daily",
    },
    {
        "ts_code": "000016.SH",
        "name": "上证50",
        "market": "CN",
        "region": "asia",
        "em_secid": "1.000016",
        "source": "index_daily",
    },
    {
        "ts_code": "899050.BJ",
        "name": "北证50",
        "market": "CN",
        "region": "asia",
        "em_secid": "0.899050",
        "source": "index_daily",
    },
    {
        "ts_code": "HSI",
        "name": "恒生指数",
        "market": "HK",
        "region": "asia",
        "em_secid": "100.HSI",
        "source": "index_global",
    },
    {
        "ts_code": "N225",
        "name": "日经225",
        "market": "JP",
        "region": "asia",
        "em_secid": "100.N225",
        "source": "index_global",
    },
    {
        "ts_code": "KS11",
        "name": "韩国KOSPI",
        "market": "KR",
        "region": "asia",
        "em_secid": "100.KS11",
        "source": "index_global",
    },
    {
        "ts_code": "DJI",
        "name": "道琼斯",
        "market": "US",
        "region": "americas",
        "em_secid": "100.DJIA",
        "source": "index_global",
    },
    {
        "ts_code": "SPX",
        "name": "标普500",
        "market": "US",
        "region": "americas",
        "em_secid": "100.SPX",
        "source": "index_global",
    },
    {
        "ts_code": "IXIC",
        "name": "纳斯达克",
        "market": "US",
        "region": "americas",
        "em_secid": "100.NDX",
        "source": "index_global",
    },
]


def _today_sh() -> date:
    return datetime.now(_SH).date()


async def _latest_snapshot_day(db: AsyncSession, model_day_col: Any, *filters: Any) -> date | None:
    """某快照表最近有数据的日期（表空/列脏 → ``None``，不抛）。

    读路径的"最近可用日"必须来自数据本身（该表实际持有的最大 ``trade_date``），
    不能拿"今天"去查：盘中轮询表（sector_moneyflow_snapshots / northbound_daily /
    market_moneyflow_daily）滞后时，用今天查会返回空数组，端点只能显示"暂无数据"。
    后续任务复用此 seam（`_latest_snapshot_day(db, Model.trade_date)`）。

    ``*filters`` 用于"最近日"本身带维度语义的表：``sector_moneyflow_snapshots``
    一天可能只有 industry 行，若取全表 max 再按 dimension 过滤明细，concept 请求会
    拿到 industry 的 as_of + 空 items（正是本次回落要消除的组合）。传
    ``Model.dimension == dimension`` 让 max 与明细同口径；无过滤时不得加 WHERE。
    """
    stmt = select(func.max(model_day_col))
    if filters:
        stmt = stmt.where(*filters)
    value = (await db.execute(stmt)).scalar()
    return value if isinstance(value, date) else None


def _stale_days(as_of: date | None) -> int | None:
    """自然日陈旧度 ``今天(上海) - as_of``；无 ``as_of`` → ``None``。

    派生量，**不是**缓存内容：payload 里的 ``stale_days`` 是按写入时刻算的，跨零点
    后会冻结成旧值。读取缓存时一律用本函数按**当下**重算（见三条读路径），
    避免"昨天写的 stale_days 一直报到 TTL 结束"。
    """
    return None if as_of is None else (_today_sh() - as_of).days


def _iso_date(value: str | None) -> date | None:
    """payload 里的 ISO 日期串 → ``date``；空/缺省 → ``None``。"""
    return date.fromisoformat(value) if value else None


def _f(v: Any) -> float | None:
    """tushare 返回 NaN 表示缺值。"""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return float(v)


def _d(v: str) -> date:
    return datetime.strptime(str(v), "%Y%m%d").date()


def _d_opt(v: Any) -> date | None:
    """可空日期字段（share_float.ann_date / repurchase.end_date、exp_date 常为 NaN）。"""
    if v is None or pd.isna(v):
        return None
    return _d(str(v))


def _map_index_global_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "ts_code": row["ts_code"],
        "trade_date": _d(row["trade_date"]),
        "open": _f(row.get("open")),
        "high": _f(row.get("high")),
        "low": _f(row.get("low")),
        "close": _f(row.get("close")),
        "volume": _f(row.get("vol")),
    }


def _get_tushare() -> Any:
    from app.core.providers.tushare_client import get_tushare_client  # noqa: PLC0415

    return get_tushare_client()


def _get_eastmoney() -> Any:
    from app.core.providers.eastmoney_client import get_eastmoney_client  # noqa: PLC0415

    return get_eastmoney_client()


# asyncpg 单条语句 bind 参数上限 32767；index_dailies 每行 9 列 → 单批最多 ~3600 行，
# 分批 upsert 以支持多年回补（见 upsert_index_dailies 的多行 INSERT）。
_UPSERT_CHUNK = 2000


async def _upsert_rows(db: AsyncSession, rows: list[IndexDaily]) -> int:
    upserted = 0
    for i in range(0, len(rows), _UPSERT_CHUNK):
        upserted += await index_repo.upsert_index_dailies(db, rows[i : i + _UPSERT_CHUNK])
    return upserted


async def _collect_index_rows(client: Any, start: str, end: str) -> list[IndexDaily]:
    """按注册表逐指数拉取并映射为 IndexDaily 行。

    单指数失败只告警并跳过（部分成功仍入库），避免一次抖动作废整个调度批次。
    """
    from app.models.index_daily import IndexDaily  # noqa: PLC0415

    rows: list[IndexDaily] = []
    for g in GLOBAL_INDICES:
        try:
            if g["source"] == "index_global":
                df: pd.DataFrame = await client.fetch_index_global(g["ts_code"], start, end)
            else:
                # fetch_index_daily 第二个位置参数是 trade_date，必须关键字传参
                df = await client.fetch_index_daily(
                    ts_code=g["ts_code"], start_date=start, end_date=end
                )
        except Exception:
            logger.warning("global index %s fetch failed", g["ts_code"], exc_info=True)
            continue
        for rec in df.to_dict("records"):
            rows.append(IndexDaily(**_map_index_global_row(rec), amount=None))
    return rows


async def ingest_global_index_daily(db: AsyncSession, lookback_days: int = 14) -> dict[str, int]:
    """全球指数 + A股三大指数近 N 日日线 → index_dailies（幂等 upsert）。"""
    client = _get_tushare()
    start = (_today_sh() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    end = _today_sh().strftime("%Y%m%d")
    rows = await _collect_index_rows(client, start, end)
    upserted = await _upsert_rows(db, rows)
    logger.info("ingest_global_index_daily lookback=%s upserted=%s", lookback_days, upserted)
    return {"upserted": upserted}


async def backfill_global_index_history(db: AsyncSession, years: int = 2) -> dict[str, int]:
    """一次性回补全球指数历史（供 spark30 与指数详情 K 线）。"""
    client = _get_tushare()
    start = (_today_sh() - timedelta(days=365 * years)).strftime("%Y%m%d")
    end = _today_sh().strftime("%Y%m%d")
    rows = await _collect_index_rows(client, start, end)
    upserted = await _upsert_rows(db, rows)
    logger.info("backfill_global_index_history years=%s upserted=%s", years, upserted)
    return {"upserted": upserted}


GLOBAL_INDICES_CACHE_KEY = "market:global-indices"
GLOBAL_INDICES_TTL = 60


async def get_global_index_cards(cache: CacheClient | None = None) -> list[dict[str, Any]]:
    """全球市场卡片：东财实时快照（60s 共享缓存）+ 近 30 日 spark + EOD 兜底。"""
    if cache is not None:
        cached = await cache.get(GLOBAL_INDICES_CACHE_KEY)
        if cached:
            cards_cached: list[dict[str, Any]] = cached
            return cards_cached

    quotes: dict[str, dict[str, Any]] = {}
    try:
        em = _get_eastmoney()
        snap = await em.fetch_index_snapshot([g["em_secid"] for g in GLOBAL_INDICES])
        # 按完整 secid 索引（不可用短码：沪/深同号段会互相覆盖）
        quotes = {q["secid"]: q for q in snap if q.get("secid")}
    except Exception:
        logger.warning("global index snapshot fetch failed, falling back to EOD", exc_info=True)

    cards: list[dict[str, Any]] = []
    from app.core.database import async_session_factory  # noqa: PLC0415

    async with async_session_factory() as db:
        for g in GLOBAL_INDICES:
            spark: list[float] = []
            last_close: float | None = None
            try:
                rows = await index_repo.get_kline(db, g["ts_code"])
                spark = [float(r.close) for r in rows[-30:] if r.close is not None]
                last_close = spark[-1] if spark else None
            except Exception:
                logger.warning("spark fetch failed for %s", g["ts_code"], exc_info=True)

            q = quotes.get(g["em_secid"])
            now = datetime.now(_SH).isoformat(timespec="seconds")
            if q and q.get("price") is not None:
                cards.append(
                    {
                        "ts_code": g["ts_code"],
                        "name": q.get("name") or g["name"],
                        "market": g["market"],
                        "region": g["region"],
                        "price": q["price"],
                        "change": q.get("change"),
                        "pct_change": q.get("pct_change"),
                        "spark": spark,
                        "updated_at": now,
                        "source": "realtime",
                    }
                )
            else:
                # 全球指数行 pre_close 为 NULL → 用相邻收盘价逐日差值算涨跌
                prev = spark[-2] if len(spark) >= 2 else None
                change = (
                    round(last_close - prev, 2)
                    if (last_close is not None and prev is not None)
                    else None
                )
                pct = round(change / prev * 100, 2) if (change is not None and prev) else None
                cards.append(
                    {
                        "ts_code": g["ts_code"],
                        "name": g["name"],
                        "market": g["market"],
                        "region": g["region"],
                        "price": last_close,
                        "change": change,
                        "pct_change": pct,
                        "spark": spark,
                        "updated_at": now,
                        "source": "eod",
                    }
                )

    if cache is not None and any(c["price"] is not None for c in cards):
        await cache.set(GLOBAL_INDICES_CACHE_KEY, cards, ttl=GLOBAL_INDICES_TTL)
    return cards


SECTOR_MONEYFLOW_CACHE_KEY = "market:sector-moneyflow:{dimension}:{as_of}"
SECTOR_MONEYFLOW_TTL = 60
SECTOR_MONEYFLOW_CACHE_LIMIT = 100  # 端点 limit 上限（le=100）：缓存全量再按请求切片

#: 热门板块读表分支一次取回的行数上限：与上游 ``pz=100`` 同量级（Top-10 截断在
#: ``market_service`` 侧做）。取回上限只作安全阀，不影响排序语义。
HOT_BOARD_SNAPSHOT_LIMIT = 100

#: 上海收盘时刻：``eod_full``（收盘全量捕获）的守卫边界，**含** 15:00 整。
SH_CLOSE = time(15, 0)


def _now_sh() -> datetime:
    """上海当下的 wall clock（**测试打桩点**：定时的 15:05 与手动 worker 两条轨道共用）。"""
    return datetime.now(_SH)


def is_after_close(now: datetime, *, close: time = SH_CLOSE) -> bool:
    """``now`` 是否已到/过了收盘时刻（默认上海 15:00）—— 纯函数。

    为什么需要这条谓词（评审 Important #1）：``eod_full`` 是"收盘后的全量捕获"标记，且在
    upsert 里**单向不可降级**（见 :func:`market_data_repo.upsert_sector_moneyflow` 的
    ``CASE``）。若盘中就能打上这个标记，读表闸门会从那一刻起**整天打开** —— 表里不在当前
    ``fid=f62`` Top-100 的行停在抓取时刻的值（5 分钟轮询只刷新它碰到的那 100 行），涨幅榜
    被冻结成"并集 + 陈旧行"，正是 Ruling 11 要消除的缺陷类别；跨日补跑（两个 job 都继承
    ``misfire_grace_time=None``，宿主睡醒后补跑）还会把**新的一天**提前写成"全量"。

    ``now`` 是 aware 时一律**换算到上海时钟**再比较（传 UTC/任何时区都判对）；naive 视作
    上海本地时间（与 :func:`_today_sh` 同约定）。``close`` 可注入（将来改收盘时间不用动
    调用点）。
    """
    if now.tzinfo is not None:
        now = now.astimezone(_SH)
    return now.time() >= close


async def ingest_sector_moneyflow(db: AsyncSession) -> dict[str, int]:
    """盘中轮询：industry/concept/region 三维当日快照 upsert（``capture_kind='poll'``）。

    每维只发**一次** ``clist``（``fid=f62`` 主力净流入 Top-100）—— 这是盘中新鲜的子集，
    **不是**当日完整态；完整态由 15:05 的 :func:`ingest_sector_moneyflow_eod_full` 分页全量
    补齐（读表闸门只认它）。
    """
    em = _get_eastmoney()
    today = _today_sh()
    result: dict[str, int] = {}
    for dimension in ("industry", "concept", "region"):
        rows = await em.fetch_sector_moneyflow(dimension)
        result[dimension] = await market_data_repo.upsert_sector_moneyflow(
            db, today, dimension, rows, capture_kind=market_data_repo.POLL_CAPTURE
        )
    logger.info("ingest_sector_moneyflow %s", result)
    return result


async def ingest_sector_moneyflow_eod_full(
    db: AsyncSession, *, now: datetime | None = None
) -> dict[str, Any]:
    """**收盘后**（上海 ≥15:00）的全量捕获：分页覆盖上游 ``data.total``。

    落库标记 ``capture_kind='eod_full'``。

    为什么必须全量（Ruling 11）：``clist`` 的 ``pz`` 上限是 100（实测传 1000 仍只回 100
    行），盘中 5 分钟轮询按主力净流入只拿得到 Top-100，当日表行因此是"每次轮询 Top-100 的
    并集、旧行永不刷新"（实测 industry 229 个 code 里 84 行停在 09:25 的开盘后第一轮），
    既不是完整当日态、也不是 ``/market/hot-boards`` 要的 f3（涨幅）全集。收盘后翻页全量
    （industry ≈5 页 / concept ≈6 页 / region 1 页 ≈12 次请求/日）把当日**所有**板块刷新一次，
    "并集 + 陈旧行"自然消解，**无需** DELETE 剪枝。

    **收盘守卫**（评审 Important #1）：``now < 15:00``（上海）时**跳过并返回**
    ``{"skipped": True, "reason": "before_close", ...}`` —— 不抛错、不写任何行、不外呼，
    告警由调用方（job/worker）走既有通道（:func:`eod_alert_reasons`）。因为 ``eod_full``
    单向不可降级，盘中打上它会让读表闸门提前整天打开（表里只有抓取时刻的子集），跨日补跑
    还会把新的一天提前写成"全量"。定时的 15:05 job 与手动 worker 都调本方法 → 两条触发路径
    同时关闭；收盘后的补跑（≥15:00）照常写入。``now`` 可注入（测试/运维复现）；
    ``None`` = 上海当下。

    返回 ``{dimension: {"fetched", "total", "upserted"}}``：``total`` 是上游 ``data.total``，
    覆盖不足由 :func:`eod_shortfalls` 判定（job 与 worker 两条轨道都要告警 —— **不许静默
    截断**：短列表看起来完全正常，但读表闸门会照开、T6 会拿它当完整日）。
    """
    now = now if now is not None else _now_sh()
    today = _today_sh()
    if not is_after_close(now):
        logger.warning(
            "board snapshot EOD capture skipped before close: now=%s (< %s SH)", now, SH_CLOSE
        )
        return {
            "skipped": True,
            "reason": "before_close",
            "trade_date": today.isoformat(),
            "now": now.isoformat(),
        }
    em = _get_eastmoney()
    result: dict[str, dict[str, int | None]] = {}
    for dimension in ("industry", "concept", "region"):
        rows, total = await em.fetch_sector_moneyflow_full(dimension)
        upserted = await market_data_repo.upsert_sector_moneyflow(
            db, today, dimension, rows, capture_kind=market_data_repo.EOD_FULL_CAPTURE
        )
        result[dimension] = {"fetched": len(rows), "total": total, "upserted": upserted}
    logger.info("ingest_sector_moneyflow_eod_full %s", result)
    return result


def eod_shortfalls(result: Mapping[str, Any]) -> list[str]:
    """全量捕获的"覆盖不足"原因（``fetched < total`` / 上游没报 total）→ 人可读列表。

    纯函数：job 与 worker 两条轨道共用同一判据（各自比一遍数就是第二份真相）。
    ``result`` 可能是 :func:`ingest_sector_moneyflow_eod_full` 的维度统计，也可能是
    ``ingest_sector_moneyflow_if_missing`` 的 ``{"skipped": ...}`` 形态（后者无短缺）。
    """
    out: list[str] = []
    for dimension, stats in result.items():
        if not isinstance(stats, Mapping) or "fetched" not in stats:
            continue
        fetched = int(stats["fetched"])
        total = stats.get("total")
        if total is None:
            out.append(f"{dimension}: upstream total unknown (fetched={fetched})")
        elif fetched < int(total):
            out.append(f"{dimension}: fetched={fetched} < total={int(total)}")
    return out


def eod_alert_reasons(result: Mapping[str, Any]) -> list[str]:
    """全量捕获需要**告警/登记 ``job:failures``** 的原因（job 与 worker 共用的唯一判据）。

    = 覆盖不足（:func:`eod_shortfalls`）+ **收盘前跳过**（:func:`is_after_close` 的
    ``before_close``）：后者本身就说明"这次 EOD 捕获没做"，必须可见（跨日补跑时操作者
    要能看出前一天的全量没抓到）。无告警 → 空列表。
    """
    reasons = eod_shortfalls(result)
    if result.get("skipped") and result.get("reason") == "before_close":
        reasons.append(f"skipped: before_close (now={result.get('now')}, close=15:00 SH)")
    return reasons


async def ingest_sector_moneyflow_if_missing(
    db: AsyncSession, *, now: datetime | None = None
) -> dict[str, Any]:
    """当日**没有 ``eod_full`` 行**才补做收盘全量捕获（15:35 容器重启兜底）。

    与 15:05 的 EOD job 共用 :func:`ingest_sector_moneyflow_eod_full`（同一 service 方法，
    双轨制，收盘守卫也在那里）。判据是"当日有没有**全量**捕获"而不是"有没有行"
    （Ruling 11）：只落了盘中 ``poll`` 行、错过 15:05 的日子也能补上收盘态 —— 这正是盘中
    Top-100 子集不该被当成当日完整态的另一面。

    返回值的键刻意不含 ``status``：worker 结果里 ``status`` 由 BaseWorker 拥有
    （``{"status": "completed", **result}`` 会被内层同名键覆盖）。
    """
    today = _today_sh()
    if await market_data_repo.sector_moneyflow_day_has_capture(
        db, today, market_data_repo.EOD_FULL_CAPTURE
    ):
        logger.info("board snapshot rewrite skipped: %s already fully captured", today)
        return {
            "skipped": True,
            "reason": "today_already_fully_captured",
            "trade_date": today.isoformat(),
        }
    counts = await ingest_sector_moneyflow_eod_full(db, now=now)
    if counts.get("skipped"):
        # 收盘前跳过（before_close）：零写入，原样上报 reason 让调用方登记 job:failures
        logger.info("board snapshot rewrite skipped %s for %s", counts.get("reason"), today)
        return counts
    logger.info("board snapshot rewrite captured %s for %s", counts, today)
    return {"skipped": False, "trade_date": today.isoformat(), **counts}


def _map_sector_moneyflow_row(snap: Any) -> dict[str, Any]:
    """快照行 → 响应 dict（直读属性：列被改名/拼错时必须炸，不静默降级成 None）。"""
    return {
        "board_code": snap.board_code,
        "board_name": snap.board_name,
        "pct_change": snap.pct_change,
        "main_net_inflow": snap.main_net_inflow,
        "super_large_net": snap.super_large_net,
        "large_net": snap.large_net,
        "main_net_ratio": snap.main_net_ratio,
        "up_count": snap.up_count,
        "down_count": snap.down_count,
        "lead_stock_name": snap.lead_stock_name,
        "lead_stock_code": snap.lead_stock_code,
        "lead_stock_pct": snap.lead_stock_pct,
    }


def _hot_board_snapshot_row(snap: Any) -> dict[str, Any]:
    """快照行 → ``/market/hot-boards`` 行 dict（与实时路径的行**同构**，供同一 mapper 消费）。

    相对 :func:`_map_sector_moneyflow_row` 的增量恰好是 Task 2 的两列
    （``amount``/``flat_count``，前端 ``HotBoardItem`` 契约消费）。``amount`` 是
    ``Numeric(20,4)`` → 出参统一转 ``float``：payload 进 Redis 时
    ``orjson.dumps(default=str)`` 会把 ``Decimal`` 编成**字符串**，缓存命中后前端拿到
    ``"123.45"`` 而不是数字 —— 实时路径给的是 float，两条路径必须同型（验收项
    "同 code 行资金口径一致"依赖这一点）。

    直读属性（缺列/列改名即 ``AttributeError``，不静默降级成 ``None``）：与
    :func:`_map_sector_moneyflow_row` 同一条约定 —— 拼错列名必须炸，而不是把"没有这一列"
    混成"这个板块没数据"。缺值（上游 ``-``/历史行）恒 ``None``，**绝不 0**。
    """
    return {
        "board_code": snap.board_code,
        "board_name": snap.board_name,
        "pct_change": snap.pct_change,
        "up_count": snap.up_count,
        "flat_count": snap.flat_count,
        "down_count": snap.down_count,
        "main_net_inflow": snap.main_net_inflow,
        "main_net_ratio": snap.main_net_ratio,
        "amount": None if snap.amount is None else float(snap.amount),
        "lead_stock_name": snap.lead_stock_name,
        "lead_stock_code": snap.lead_stock_code,
        "lead_stock_pct": snap.lead_stock_pct,
    }


async def load_hot_board_snapshot(
    db: AsyncSession, dimension: str, expected_day: date
) -> tuple[date, list[dict[str, Any]]] | None:
    """读表优先：表内该维度**最近有行的那一天** == ``expected_day`` 且当日已**全量捕获**
    时返回 ``(day, rows)``。

    否则 ``None``（含"表里完全没有该维度"、"最近日已陈旧"、"当日只落了盘中 poll 行"、
    "明细为空"四种）—— 调用方必须据此回落实时抓取，**绝不**把"表里没有"当成"今天没有
    热门板块"。

    为什么闸门是 ``eod_full`` 而不是"有行"（Ruling 11）：``clist`` 的 ``pz`` 上限 100，
    盘中 5 分钟轮询按 ``fid=f62``（主力净流入）只拿得到 Top-100，当日行集是"每次轮询
    Top-100 的并集、旧行永不刷新"（实测 industry 229 个 code 里 84 行停在开盘后第一轮）
    —— 拿它冒充 ``f3``（涨幅）全量榜是错的。只有 15:05 的分页全量捕获（覆盖上游
    ``data.total``、且 ``before_close`` 守卫保证只在 ≥15:00 写）才能保证"当日所有板块都在
    表里、且都是收盘后的值"。

    ``expected_day`` 由调用方按"最近**预期**交易日"给出（``last_weekday(_today_sh())``）；
    本函数只做"表内实际持有的最近日 vs 预期"这一条判据，不自己发明第二个"今天是哪天"。
    返回的 ``day`` 就是那批行的 ``trade_date``：调用方据此标 ``as_of``，**不得**改标成今天
    （陈旧日必须显式陈旧）。行序与产地过滤由 SQL 负责
    （``pct_change DESC, board_code ASC`` + ``capture_kind='eod_full'``）。
    """
    from app.models.market_data import SectorMoneyflowSnapshot  # noqa: PLC0415

    day = await _latest_snapshot_day(
        db,
        SectorMoneyflowSnapshot.trade_date,
        SectorMoneyflowSnapshot.dimension == dimension,
    )
    if day != expected_day:
        return None
    if not await market_data_repo.sector_moneyflow_day_has_capture(
        db, day, market_data_repo.EOD_FULL_CAPTURE
    ):
        return None
    snaps = await market_data_repo.list_sector_moneyflow_ranked(
        db,
        day,
        dimension,
        HOT_BOARD_SNAPSHOT_LIMIT,
        capture_kind=market_data_repo.EOD_FULL_CAPTURE,
    )
    rows = [_hot_board_snapshot_row(snap) for snap in snaps]
    if not rows:
        return None
    return day, rows


async def get_sector_moneyflow(
    cache: CacheClient | None, dimension: str = "industry", limit: int = 15
) -> dict[str, Any]:
    """最近可用日的板块主力资金流榜 + 陈旧度（Redis 60s 共享缓存，键含该快照日）。

    ``as_of`` 取 ``sector_moneyflow_snapshots`` 中**该 ``dimension``** 实际持有的最近
    ``trade_date``，而不是"今天"：东财源随时能返回 100 行，但落表只在盘中轮询时发生，
    用今天查会在该表滞后时返回空数组、前端只能显示"暂无数据"。按维度取最近日还保证
    ``as_of`` 与 ``items`` 同口径——否则某天只有 industry 行时，concept 请求会拿到该
    天的 ``as_of`` 配空 ``items``（"非空 as_of + 空列表"）。``stale_days`` 是**自然日**差
    ``(_today_sh() - as_of).days``——语义是"这批数据有多旧"，不是交易日计数。
    负值意味着表里存在未来日期的脏行（`trade_date` 无上界）：前端适配层应把负值当
    异常暴露，而不是当作"比今天还新=最新鲜"。

    元数据只描述**实际返回的 items**：明细为空时 ``as_of``/``stale_days`` 一并置
    ``None``（理论上"按维度取 max"后明细不可能为空，这里只是把不变量显式化）。
    表内一行都没有 → ``{"as_of": None, "stale_days": None, "items": []}``（不抛）。
    缓存命中时 ``stale_days`` **按当下重算**（``as_of`` 在 payload 里，陈旧度不在）。
    """
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.models.market_data import SectorMoneyflowSnapshot  # noqa: PLC0415

    async with async_session_factory() as db:
        as_of = await _latest_snapshot_day(
            db,
            SectorMoneyflowSnapshot.trade_date,
            SectorMoneyflowSnapshot.dimension == dimension,
        )
        key = SECTOR_MONEYFLOW_CACHE_KEY.format(
            dimension=dimension, as_of=as_of.isoformat() if as_of is not None else "none"
        )
        if cache is not None:
            cached = await cache.get(key)
            if cached:
                cached_as_of = _iso_date(cached["as_of"])
                if not cached["items"]:  # 不变量：无行则无元数据
                    return {"as_of": None, "stale_days": None, "items": []}
                return {
                    "as_of": cached["as_of"],
                    "stale_days": _stale_days(cached_as_of),  # 跨零点按当下重算
                    "items": cached["items"][:limit],
                }

        items: list[dict[str, Any]] = []
        if as_of is not None:
            for snap in await market_data_repo.list_sector_moneyflow(
                db, as_of, dimension, SECTOR_MONEYFLOW_CACHE_LIMIT
            ):
                items.append(_map_sector_moneyflow_row(snap))

    payload = _sector_moneyflow_payload(as_of, items)
    if cache is not None and items:
        await cache.set(key, payload, ttl=SECTOR_MONEYFLOW_TTL)
    return {**payload, "items": items[:limit]}


def _sector_moneyflow_payload(as_of: date | None, items: list[dict[str, Any]]) -> dict[str, Any]:
    """``as_of``/``stale_days`` 只描述**实际返回的** items；无行即无元数据。"""
    if not items:
        return {"as_of": None, "stale_days": None, "items": []}
    # items 只可能来自某个已解析的 as_of（见调用点），故此处 as_of 必非 None。
    return {
        "as_of": as_of.isoformat() if as_of else None,
        "stale_days": _stale_days(as_of),
        "items": items,
    }


MARKET_MONEYFLOW_CACHE_KEY = "market:market-moneyflow:{history_as_of}"
MARKET_MONEYFLOW_TTL = 60


# ----- Task 6b：板块趋势（读时计算，不物化）------------------------------------------
#
# 三条硬约束（Ruling 21/22 + 计划"缺日 ≠ 0"）：
# 1. **完整日闸门**：只统计 `capture_kind='eod_full'` 的日子。`poll`-only 日（盘中
#    `fid=f62` Top-100 子集）既不进序列、也不算 0 流入，只落进 `missing_days`；
# 2. **归属显式**：`industry`/`region` 只能按当前分类回溯（无成分差分表），
#    `concept` 走 `concept_member_changes` 反推的时点成分；
# 3. **不插值**：`series` 里没有的日子就是没有（不是 0），连击在完整日序列上连续。

SECTOR_TRENDS_CACHE_KEY = "market:sector-trends:{dimension}:{days}:{top_n}"
SECTOR_TRENDS_TTL = 300

#: 归属口径（Ruling 22）。两个字面量与 `app.schemas.market_data.Attribution` 对齐；
#: service 不 import schema（schema 只描述出参），故这里保留字面量并由端点断言兜住。
ATTRIBUTION_CURRENT_SW = "current_sw_snapshot"
ATTRIBUTION_POINT_IN_TIME = "point_in_time"

_ATTRIBUTION_NOTES = {
    ATTRIBUTION_CURRENT_SW: "按当前分类回溯，非当时成分",
    ATTRIBUTION_POINT_IN_TIME: "成分按 concept_member_changes 反推的时点口径",
}

#: 只有 concept 维度有"东财板块 → 个股"映射（`concept_members`）。industry/region 没有
#: 同源映射（`sw_industry_members` 是申万码、`stocks.province` 与"XX板块"不同名）——
#: 用相近名字硬凑会造出**看起来有**的错误归属，故显式不可用。
_LU_COUNT_UNAVAILABLE = "unavailable"
_LU_COUNT_POINT_IN_TIME = "point_in_time_membership"


def _as_date(value: Any) -> date:
    """日期归一：`date` 原样、ISO 字符串解析（缓存 JSON 回读会变成 str）。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def eod_full_days_from_rows(rows: list[Any]) -> set[date]:
    """行集里**完整日**的集合（Ruling 21：`capture_kind='eod_full'` 才计数）。

    直接读属性而不是 `getattr(..., 'eod_full')` 兜底：列改名/拼错必须炸，不能把
    "产地列没了"静默降级成"所有行都不是完整日"（那会把整个端点变成空响应）。
    """
    return {
        _as_date(r.trade_date) for r in rows if r.capture_kind == market_data_repo.EOD_FULL_CAPTURE
    }


def missing_eod_days(trading_days: list[date], complete_days: set[date]) -> list[str]:
    """请求窗口内**缺完整日**的交易日（isoformat 升序）。

    缺日 ≠ 0 流入：这些日子不出现在任何序列/连击里，只在这里被显式暴露（Ruling 21）。
    """
    return [d.isoformat() for d in trading_days if d not in complete_days]


def intersect_complete(trading_days: list[date], complete_days: set[date]) -> list[date]:
    """请求窗口内**有完整日**的交易日（升序）—— 统计只在它们上面做。"""
    return [d for d in trading_days if d in complete_days]


def resolve_available_from(complete_days: list[date], _trading_days: list[date]) -> date | None:
    """`available_from` = 该维度最早的**完整日**；一个都没有 → `None`（绝不假装有）。"""
    return min(complete_days) if complete_days else None


def concept_membership_snapshots(
    current: dict[str, set[str]],
    changes: list[dict[str, Any]],
    *,
    on_days: list[date],
) -> dict[date, dict[str, set[str]]]:
    """时点成分快照（**反推**：当前态 - 之后的 add + 之后的 remove）。

    `concept_members` 是**当前态**（每轮覆盖），`concept_member_changes` 是追加式差分
    （2026-09-20 起）。要把"今天谁属于谁"还原成"某天谁属于谁"，必须从当前态**倒着**应用
    **该日之后**的差分：

    - 在 `d` 之后被 `add` 的 symbol：`d` 时还不在板 → 从 `d` 的集合里**去掉**；
    - 在 `d` 之后被 `remove` 的 symbol：`d` 时还在板 → **加回** `d` 的集合。

    直接用当前成分冒充历史成分会把"后来才进板"的股票算进历史涨停家数（前视偏差）；
    这是 Ruling 22 允许 `attribution="point_in_time"` 的全部依据。

    变更日**当天**的差分不计入（`observed_on == d` 是当日观测到的变化，`d` 的成分应以
    此前状态为准），否则同日 add+remove 会自我抵消。
    """
    by_board: dict[str, list[tuple[date, str, str]]] = {}
    for change in changes:
        by_board.setdefault(str(change["board_code"]), []).append(
            (
                _as_date(change["observed_on"]),
                str(change["change_type"]),
                str(change["symbol"]),
            )
        )
    ordered = {
        board: sorted(events, key=lambda e: e[0], reverse=True)
        for board, events in by_board.items()
    }

    def state_at(day: date) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for board in set(current) | set(by_board):
            # 倒序（新→旧）应用"day 之后"的事件；遇到 `<= day` 即 break —— 剩下的都是
            # 更早的差分，与该时点无关（同一 symbol 的多个事件按新→旧逐个反转，语义正确）。
            symbols = set(current.get(board, set()))
            for observed_on, kind, symbol in ordered.get(board, []):
                if observed_on <= day:
                    break
                if kind == "add":
                    symbols.discard(symbol)
                elif kind == "remove":
                    symbols.add(symbol)
            out[board] = symbols
        return out

    return {day: state_at(day) for day in on_days}


def limit_up_counts_by_board(
    facts: list[dict[str, Any]],
    membership: dict[date, dict[str, set[str]]],
) -> dict[tuple[date, str], int]:
    """`(完整日, 板块码) → 当日该板涨停家数`（**时点成分**归属）。

    `facts` 是 `market_limit_up_stock_daily` 的 `is_lu` 行（`trade_date` + `symbol`）；
    `membership` 是 `concept_membership_snapshots` 的产物。没有时点成分的日子不参与
    （宁可少算，也不用当前成分冒充 —— Ruling 22）。
    """
    counts: dict[tuple[date, str], int] = {}
    for fact in facts:
        day = _as_date(fact["trade_date"])
        day_sets = membership.get(day)
        if not day_sets:
            continue
        symbol = str(fact["symbol"])
        for board, symbols in day_sets.items():
            if symbol in symbols:
                counts[(day, board)] = counts.get((day, board), 0) + 1
    return counts


def rank_boards(rows: list[Any], day: date, *, top_n: int) -> list[Any]:
    """某**完整日**按 `main_net_inflow DESC, board_code ASC` 排序取前 `top_n`。

    确定性 tiebreak（Ruling 6）：同额的两块板在两次请求间不得互换位置。缺净流入
    （`None`）的行排在**最后**（`NULLS LAST` 的服务层等价物）—— 缺值不是"0 流入"，
    更不该排在某额之上。
    """
    day_rows = [r for r in rows if _as_date(r.trade_date) == day]
    return sorted(
        day_rows,
        key=lambda r: (
            r.main_net_inflow is None,
            -(r.main_net_inflow if r.main_net_inflow is not None else 0.0),
            r.board_code,
        ),
    )[:top_n]


def _tail_streak(values: list[Any], pred: Any) -> int:
    """从**末位往前**数连续满足 `pred` 的个数（当前连击：截至窗口最后一天）。"""
    n = 0
    for value in reversed(values):
        if value is None or not pred(value):
            break
        n += 1
    return n


def _max_streak(values: list[Any], pred: Any) -> int:
    """窗口内最长连击（`None` 视为断点，**不**当作满足/不满足的观测）。"""
    best = 0
    current = 0
    for value in values:
        if value is not None and pred(value):
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def compute_board_stats(
    rows: list[Any],
    *,
    complete_days: list[date],
    top_n: int,
    lu_counts: dict[tuple[date, str], int] | None = None,
) -> list[dict[str, Any]]:
    """窗口内每块板的连续性统计 + 逐日序列（**只在 `complete_days` 上计算**）。

    返回的板按**末日榜序**（`main_net_inflow DESC, board_code ASC`，缺值最后）排列，
    与 `rank_boards` 同一 tiebreak。`series` 每个完整日一行；该板当日无行 → 该点
    `main_net_inflow/pct_change/up_count/down_count` 全 `None`（**不是 0**），
    `lu_count` 取 `lu_counts`（concept 才有；缺 → `None`）。
    """
    lu_counts = lu_counts or {}
    by_day: dict[date, dict[str, Any]] = {d: {} for d in complete_days}
    for row in rows:
        day = _as_date(row.trade_date)
        if day in by_day:
            by_day[day][row.board_code] = row

    codes: set[tuple[str, str | None]] = set()
    for day in complete_days:
        for code, row in by_day[day].items():
            codes.add((code, row.board_name))

    ranked_by_day = {
        day: [r.board_code for r in rank_boards(rows, day, top_n=top_n)] for day in complete_days
    }
    board_names = {code: name for code, name in codes}

    stats: list[dict[str, Any]] = []
    for code in sorted({c for c, _ in codes}):
        flows: list[float | None] = []
        series: list[dict[str, Any]] = []
        for day in complete_days:
            row = by_day[day].get(code)
            flow = row.main_net_inflow if row is not None else None
            flows.append(flow)
            series.append(
                {
                    "trade_date": day,
                    "main_net_inflow": flow,
                    "pct_change": row.pct_change if row is not None else None,
                    "up_count": row.up_count if row is not None else None,
                    "down_count": row.down_count if row is not None else None,
                    "lu_count": lu_counts.get((day, code)),
                }
            )
        on_top = [code in ranked_by_day[day] for day in complete_days]
        last = series[-1] if series else None
        stats.append(
            {
                "board_code": code,
                "board_name": board_names.get(code),
                "main_net_inflow": last["main_net_inflow"] if last else None,
                "pct_change": last["pct_change"] if last else None,
                "up_count": last["up_count"] if last else None,
                "down_count": last["down_count"] if last else None,
                "streak_on_top_n": _tail_streak(on_top, bool),
                "max_streak_on_top_n": _max_streak(on_top, bool),
                "streak_inflow": _tail_streak(flows, lambda v: v > 0),
                "max_streak_inflow": _max_streak(flows, lambda v: v > 0),
                "streak_outflow": _tail_streak(flows, lambda v: v < 0),
                "max_streak_outflow": _max_streak(flows, lambda v: v < 0),
                "series": series,
            }
        )

    # 末日榜序（与 rank_boards 同 tiebreak）→ 未上榜的板排在其后（仍可见，不静默丢）
    last_ranked = ranked_by_day[complete_days[-1]] if complete_days else []
    order = {code: i for i, code in enumerate(last_ranked)}
    stats.sort(
        key=lambda s: (
            order.get(s["board_code"], len(order)),
            s["board_code"],
        )
    )
    return stats


def leader_changes(
    rows: list[Any], complete_days: list[date], *, top_n: int
) -> list[dict[str, Any]]:
    """完整日相邻两天之间的**龙头更替**事件（首日不算更替）。

    龙头 = 当日榜序第一名（`main_net_inflow DESC, board_code ASC`）。某日一行都没有
    （该日仍在完整日集合里，但该维度没有行）→ 不产出事件，也不把"无行"当成新龙头。
    """
    out: list[dict[str, Any]] = []
    previous: str | None = None
    for day in complete_days:
        ranked = rank_boards(rows, day, top_n=top_n)
        current = ranked[0].board_code if ranked else None
        if current is None:
            continue
        if previous is not None and current != previous:
            out.append(
                {
                    "trade_date": day,
                    "board_code": current,
                    "board_name": next(
                        (r.board_name for r in ranked if r.board_code == current), None
                    ),
                }
            )
        previous = current
    return out


def sector_trends_payload(
    *,
    rows: list[Any],
    complete_days: list[date],
    all_complete_days: list[date],
    trading_days: list[date],
    dimension: str,
    days: int,
    top_n: int,
    lu_counts: dict[tuple[date, str], int] | None = None,
) -> dict[str, Any]:
    """组装 6b 响应（**纯函数**，无 IO；缓存由 :func:`get_sector_trends` 负责）。

    - `complete_days` = 请求窗口内的完整日（统计口径）；`all_complete_days` = 该维度
      **全部**完整日（`available_from` 的口径 —— 它描述"这类数据自哪日起可用"，不是
      "窗口里最早那天"）；
    - `trading_days` 必须**已经**被调用方截断到 `days` 个（见 :func:`trailing_window_days`）：
      这里只用它算 `missing_days` 与 `window`，不再二次裁剪 —— 双重截断会让"谁负责窗口
      守恒"变得说不清；
    - `window.days` 回**请求值**，`window.observed_days` 才是实际交易日跨度：
      两者不等时（数据起点附近/该维度行集更短）必须**可见**，不能悄悄拿观测值冒充请求值
      （Important #2 的收口）。不变量 `samples + len(missing_days) <= window.days`。
    """
    in_window = intersect_complete(trading_days, set(complete_days))
    attribution = ATTRIBUTION_POINT_IN_TIME if dimension == "concept" else ATTRIBUTION_CURRENT_SW
    boards = (
        compute_board_stats(rows, complete_days=in_window, top_n=top_n, lu_counts=lu_counts)
        if in_window
        else []
    )
    changes = leader_changes(rows, in_window, top_n=top_n) if in_window else []

    return {
        "dimension": dimension,
        "as_of": in_window[-1] if in_window else None,
        "available_from": resolve_available_from(all_complete_days, trading_days),
        "attribution": attribution,
        "attribution_note": _ATTRIBUTION_NOTES[attribution],
        "lu_count_source": (
            _LU_COUNT_POINT_IN_TIME if dimension == "concept" else _LU_COUNT_UNAVAILABLE
        ),
        "top_n": top_n,
        "window": {
            "days": days,
            "observed_days": len(trading_days),
            "from": in_window[0] if in_window else None,
            "to": in_window[-1] if in_window else None,
            "samples": len(in_window),
        },
        "boards": boards,
        "missing_days": missing_eod_days(trading_days, set(complete_days)),
        "leader_change_count": len(changes),
        "leader_changes": changes,
    }


def trailing_window_days(trading_days: list[date], days: int) -> list[date]:
    """取 `trading_days` 的**末尾** `days` 个（窗口必须恰好是请求的交易日数）。

    为什么必须截断（Important #2）：`trading_days` 来自 `daily_quotes`，右端由"该维度
    快照的最近日"决定；当快照落后于行情时（采集断了、行情照常），原实现会把
    `[snapshot_first, snapshot_last]` 里**所有**交易日都当成窗口 —— `days=60` 可能回
    80–100 个交易日，并把这几十天**全列进 `missing_days`**（正是"一串假缺日"）。
    截断到末尾 `days` 个后：窗口恰好 = "以快照最近日为右端、向前 `days` 个交易日"。

    `len(trading_days) <= days` 时原样返回（数据起点附近/该维度行集更短）——
    此时 `window.observed_days < window.days` 把差异如实暴露，而不是补零或补日。
    """
    return list(trading_days[-days:]) if days > 0 else []


def _empty_sector_trends(dimension: str, days: int, top_n: int) -> dict[str, Any]:
    """该维度**一行都没有**时的诚实空响应（早退，不查任何其它表）。

    这条路径必须存在且必须**早退**（Important #3）：原先它落到
    `list_trading_days(db, date.min, date.min)` —— `trade_date >= 0001-01-01 AND
    trade_date <= 0001-01-01` 在 `daily_quotes` 上是非 sargable 的全表扫，而兜底分支
    永远到不了。语义上它也不该问"行情有没有行"：没有快照行的维度就是没有可统计对象。
    """
    attribution = ATTRIBUTION_POINT_IN_TIME if dimension == "concept" else ATTRIBUTION_CURRENT_SW
    return {
        "dimension": dimension,
        "as_of": None,
        "available_from": None,
        "attribution": attribution,
        "attribution_note": _ATTRIBUTION_NOTES[attribution],
        "lu_count_source": (
            _LU_COUNT_POINT_IN_TIME if dimension == "concept" else _LU_COUNT_UNAVAILABLE
        ),
        "top_n": top_n,
        "window": {
            "days": days,
            "observed_days": 0,
            "from": None,
            "to": None,
            "samples": 0,
        },
        "boards": [],
        "missing_days": [],
        "leader_change_count": 0,
        "leader_changes": [],
    }


async def get_sector_trends(
    cache: CacheClient | None,
    dimension: str = "industry",
    days: int = 60,
    top_n: int = 20,
) -> dict[str, Any]:
    """`GET /market/sector-trends` 的编排（读时计算 + Redis 缓存，**不物化**）。

    窗口 = **以该维度快照的最近日为右端、向前恰好 `days` 个交易日**（`daily_quotes` 是
    交易日真源）；`missing_days` = 窗口内缺 `eod_full` 的交易日。三条边界：

    1. 该维度**一行都没有** → 立即回 :func:`_empty_sector_trends`（不查 `daily_quotes`）；
    2. 快照落后于行情 → `trading_days` 由 :func:`trailing_window_days` 截断到末尾 `days`
       个（否则会把行情独有的那几十天全报成缺日）；
    3. 交易日源缺失（`daily_quotes` 为空/迁移未跑）→ 退回用快照自己的行集日（有界）。

    缓存键含 `dimension`/`days`/`top_n`（三者都会改变结果）；空结果不缓存。
    """
    from app.core.database import async_session_factory  # noqa: PLC0415

    key = SECTOR_TRENDS_CACHE_KEY.format(dimension=dimension, days=days, top_n=top_n)
    if cache is not None:
        cached: dict[str, Any] | None = await cache.get(key)
        if cached:
            return cached

    empty_payload: dict[str, Any] | None = None
    async with async_session_factory() as db:
        # 右端锚点 = 该维度**有行的**最近日（`latest_sector_moneyflow_day`）。锚"有行"而不是
        # "有完整日"：若锚后者，`poll`-only 的最后几天会被整体丢出窗口、缺日反而看不见。
        anchor = await market_data_repo.latest_sector_moneyflow_day(db, dimension)
        if anchor is None:
            empty_payload = _empty_sector_trends(dimension, days, top_n)
        else:
            start = await market_data_repo.earliest_sector_moneyflow_day(db, dimension)
            trading_days = await market_data_repo.list_trading_days(
                db, start if start is not None else anchor, anchor
            )
            if not trading_days:
                # 交易日源缺失：退回快照自己的行集日（有界、不整表扫）
                trading_days = await market_data_repo.list_sector_moneyflow_trading_days(
                    db, dimension, limit=days
                )
            trading_days = trailing_window_days(trading_days, days)

            earliest = trading_days[0] if trading_days else None
            latest = trading_days[-1] if trading_days else None
            all_complete = await market_data_repo.earliest_sector_moneyflow_complete_day(
                db, dimension
            )
            # **只读完整日**（Ruling 21）：repo 函数自带 `capture_kind='eod_full'` 谓词，
            # 调用点不必再筛；段内的 `poll` 行**不进** `rows`（缺日由 `missing_days` 表达）。
            rows: list[Any] = []
            if earliest is not None and latest is not None:
                rows = await market_data_repo.list_sector_moneyflow_complete_days(
                    db, dimension, earliest, latest
                )
            lu_counts: dict[tuple[date, str], int] = {}
            if dimension == "concept" and earliest is not None and latest is not None:
                lu_counts = await _concept_lu_counts(db, earliest, latest, rows)

    if empty_payload is not None:
        # 空结果不缓存：该维度第一行落库后，下一次请求就要能看见它
        return empty_payload

    payload = sector_trends_payload(
        rows=rows,
        complete_days=sorted(eod_full_days_from_rows(rows)),
        all_complete_days=[all_complete] if all_complete is not None else [],
        trading_days=trading_days,
        dimension=dimension,
        days=days,
        top_n=top_n,
        lu_counts=lu_counts,
    )
    if cache is not None and payload["boards"]:
        await cache.set(key, payload, ttl=SECTOR_TRENDS_TTL)
    return payload


async def _concept_lu_counts(
    db: AsyncSession,
    start: date,
    end: date,
    rows: list[Any],
) -> dict[tuple[date, str], int]:
    """概念维度的"板块涨停家数"（**时点成分**归属，Ruling 22）。

    只有 concept 有"东财板块 → 个股"映射（`concept_members`）；industry/region 没有同源
    映射，故本函数只被 concept 调用（其余维度 `lu_count=null`，见 `lu_count_source`）。

    成分窗口取 `[请求窗口起点, 请求窗口终点]`：`concept_members` 的当前态按 `last_seen_on`
    过滤（本次请求要还原的时点区间），`concept_member_changes` 取同区间（它从
    2026-09-20 起才有，早于该日的时点成分无法还原 → 那些日子的 `lu_count=null`）。
    """
    changes = await market_data_repo.list_concept_membership_changes(db, start, end)
    current = await market_data_repo.list_concept_members_current(db, start, end)
    complete = [d for d in sorted(eod_full_days_from_rows(rows)) if start <= d <= end]
    if not complete or not current:
        return {}
    membership = concept_membership_snapshots(current, changes, on_days=complete)
    facts = await market_data_repo.list_limit_up_facts_in_range(db, start, end)
    return limit_up_counts_by_board(facts, membership)


async def ingest_market_moneyflow_daily(db: AsyncSession, days: int = 10) -> dict[str, int]:
    """大盘资金流日线（东财 fflow/daykline 沪深合成）幂等 upsert。"""
    client = _get_eastmoney()
    rows = await client.fetch_market_moneyflow_daily(days)
    upserted = await market_data_repo.upsert_market_moneyflow_daily(db, rows)
    logger.info("ingest_market_moneyflow_daily days=%s upserted=%s", days, upserted)
    return {"upserted": upserted}


async def get_market_moneyflow(cache: Any | None) -> dict[str, Any]:
    """大盘资金流：今日四档实时（ulist 合计，不落表）+ 近 30 日历史（表内）。

    ``history_as_of`` / ``history_stale_days``（自然日差）单独标注 30 日历史段的
    最近日：实时档位来自东财 ulist，与历史表是两条来源，卡片必须能分别说明历史是
    否陈旧（``market_moneyflow_daily`` 的 job 曾未注册，实测滞后 14 天）。
    ``history_stale_days`` 为负值意味着表内存在未来日期的脏行，前端适配层应视为异常
    而非"最新鲜"。历史表为空 → 两者均 ``None``。缓存键含 ``history_as_of``，翻日后
    不会回放旧 payload。

    与北向同一条 Task 8 口径：``history_as_of`` 由**返回的 history 末行**推导
    （而不是另取一次表级 max），两者按构造不可能不一致；缓存命中时
    ``history_stale_days`` 按当下重算（跨零点不冻结）。
    """
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.models.market_data import MarketMoneyflowDaily  # noqa: PLC0415

    async with async_session_factory() as db:
        history_as_of = await _latest_snapshot_day(db, MarketMoneyflowDaily.trade_date)
    key = MARKET_MONEYFLOW_CACHE_KEY.format(
        history_as_of=history_as_of.isoformat() if history_as_of is not None else "none"
    )
    if cache is not None:
        cached: dict[str, Any] | None = await cache.get(key)
        if cached:
            cached_history: list[dict[str, Any]] = cached["history"]
            return {
                **cached,
                **_history_meta(_iso_date(cached_history[-1]["date"]) if cached_history else None),
            }
    try:
        today = await _get_eastmoney().fetch_market_moneyflow_today()
    except Exception:
        logger.warning("market moneyflow today fetch failed", exc_info=True)
        today = None
    history: list[dict[str, Any]] = []
    if history_as_of is not None:  # 表空（history_as_of=None）时历史必然为空，不查
        async with async_session_factory() as db:
            for row in await market_data_repo.list_market_moneyflow_daily(db, 30):
                history.append(
                    {
                        "date": row.trade_date.isoformat(),
                        "main_net": row.main_net,
                        "super_large_net": row.super_large_net,
                        "large_net": row.large_net,
                        "mid_net": row.mid_net,
                        "small_net": row.small_net,
                        "main_ratio": row.main_ratio,
                        "close": row.close,
                        "pct_change": row.pct_change,
                        "amount": row.amount,
                    }
                )
    payload = {
        "today": today,
        "history": history,
        **_history_meta(_iso_date(history[-1]["date"]) if history else None),
    }
    if cache is not None and (history or today):
        await cache.set(key, payload, ttl=MARKET_MONEYFLOW_TTL)
    return payload


def _history_meta(history_as_of: date | None) -> dict[str, Any]:
    """历史段的元数据；两个字段成对出现，都只描述返回的 history。"""
    return {
        "history_as_of": history_as_of.isoformat() if history_as_of is not None else None,
        "history_stale_days": _stale_days(history_as_of),
    }


def _map_top_list_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    """top_list → dragon tiger rows（金额元；reason 列 String(160)，超长截断防 DB 报错）。"""
    rows: list[dict[str, Any]] = []
    for rec in df.to_dict("records"):
        rows.append(
            {
                "trade_date": _d(rec["trade_date"]),
                "ts_code": rec["ts_code"],
                "symbol": rec["ts_code"].split(".")[0],
                "name": rec.get("name"),
                "close": _f(rec.get("close")),
                "pct_change": _f(rec.get("pct_change")),
                "turnover_rate": _f(rec.get("turnover_rate")),
                "amount": _f(rec.get("amount")),
                "l_buy": _f(rec.get("l_buy")),
                "l_sell": _f(rec.get("l_sell")),
                "l_amount": _f(rec.get("l_amount")),
                "net_amount": _f(rec.get("net_amount")),
                "net_rate": _f(rec.get("net_rate")),
                "amount_rate": _f(rec.get("amount_rate")),
                "float_values": _f(rec.get("float_values")),
                "reason": str(rec.get("reason") or "")[:160],
            }
        )
    return rows


def _map_block_trade_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    """block_trade → block trade rows（price 元 / vol 万股 / amount 万元）。"""
    rows: list[dict[str, Any]] = []
    for rec in df.to_dict("records"):
        rows.append(
            {
                "trade_date": _d(rec["trade_date"]),
                "ts_code": rec["ts_code"],
                "symbol": rec["ts_code"].split(".")[0],
                "price": _f(rec.get("price")),
                "volume": _f(rec.get("vol")),
                "amount": _f(rec.get("amount")),
                "buyer": rec.get("buyer"),
                "seller": rec.get("seller"),
            }
        )
    return rows


def _dedupe_block_trade_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按去重键（date+code+buyer+seller+price+volume）同批去重，保留末次出现。

    ON CONFLICT 只处理与既有行的冲突、不处理同批 INSERT 内自冲突，须先在 Python 端去重。
    """
    deduped = {
        (r["trade_date"], r["ts_code"], r["buyer"], r["seller"], r["price"], r["volume"]): r
        for r in rows
    }
    return list(deduped.values())


def _map_hsgt_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    """moneyflow_hsgt 全列字符串 → northbound rows（north_money 万元，缺失/NaN 置 None）。"""
    rows: list[dict[str, Any]] = []
    for rec in df.to_dict("records"):
        raw = rec.get("north_money")
        net: float | None = None
        if raw is not None:
            try:
                f = float(raw)
            except (TypeError, ValueError):
                f = float("nan")
            net = None if math.isnan(f) else f
        rows.append({"trade_date": _d(rec["trade_date"]), "net_amount": net})
    return rows


async def ingest_northbound(db: AsyncSession, days: int = 30) -> dict[str, int]:
    """盘后采集：近 N 日沪深港通北向净流入（moneyflow_hsgt，幂等 upsert）。"""
    client = _get_tushare()
    start = (_today_sh() - timedelta(days=days)).strftime("%Y%m%d")
    end = _today_sh().strftime("%Y%m%d")
    df = await client.fetch_moneyflow_hsgt(start_date=start, end_date=end)
    upserted = await market_data_repo.upsert_northbound(db, _map_hsgt_rows(df))
    logger.info("ingest_northbound days=%s upserted=%s", days, upserted)
    return {"upserted": upserted}


NORTHBOUND_CACHE_KEY = "market:northbound:{days}:{as_of}"
NORTHBOUND_TTL = 300
NORTHBOUND_DISCONTINUED_AFTER_DAYS = 5


async def get_northbound_series(cache: CacheClient | None, days: int = 30) -> dict[str, Any]:
    """北向净流入日序列（升序）+ 数据源状态（Redis 300s 共享缓存，键含最近日）。

    ``source_status``：``stale_days <= 5`` → ``"live"``，否则 ``"discontinued"``
    （``stale_days`` 为自然日差 ``_today_sh() - as_of``）。负的 ``stale_days`` 意味着
    表内存在未来日期的脏行（``trade_date`` 无上界）：此时 ``<= 5`` 会把它算成
    ``"live"``，前端适配层必须把负值识别为异常而不是"新鲜"，人工核查脏行。上游 TuShare
    ``moneyflow_hsgt`` 已停更（实测 30 天窗口最新只到 2026-08-21，表内最近日
    2026-09-07），卡片必须能说"该源已停更"而不是画一条不带截止标注的线。

    **元数据只描述实际返回的 ``items``（Task 8 修正）**：``items`` 由
    ``trade_date >= 今天-days`` 的窗口过滤，而表内最大日可能早于该窗口——
    旧实现用"表级 max"当 ``as_of``，于是 ``days=5`` 可以返回"非空 as_of + 空
    items"。现在 ``as_of``/``stale_days``/``source_status`` 全部由 ``items`` 自己
    的末行日期推导，无行即 ``as_of=None`` / ``stale_days=None`` /
    ``source_status="discontinued"``（拿不到任何数据就谈不上 live）。缓存命中时
    ``stale_days`` 与 ``source_status`` 按当下重算（``as_of`` 是 items 的属性、
    不是 payload 的冻结字段）。
    """
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.models.market_data import NorthboundDaily  # noqa: PLC0415

    async with async_session_factory() as db:
        as_of = await _latest_snapshot_day(db, NorthboundDaily.trade_date)
        key = NORTHBOUND_CACHE_KEY.format(
            days=days, as_of=as_of.isoformat() if as_of is not None else "none"
        )
        if cache is not None:
            cached: Any = await cache.get(key)
            if cached:
                return _northbound_payload(
                    _iso_date(cached["items"][-1]["date"]) if cached.get("items") else None,
                    cached["items"],
                )

        items: list[dict[str, Any]] = []
        if as_of is not None:  # 表空（as_of=None）时明细必然为空，不查
            for n in await market_data_repo.list_northbound(db, days):
                items.append({"date": n.trade_date.isoformat(), "net_amount": n.net_amount})

    # ``as_of`` 由 items 的末行（升序）推导，而不是回看表级 max：窗口过滤后可能
    # 一行都不剩，此时元数据必须一起归零（见 docstring）。
    payload = _northbound_payload(_iso_date(items[-1]["date"]) if items else None, items)
    if cache is not None and items:
        await cache.set(key, payload, ttl=NORTHBOUND_TTL)
    return payload


def _northbound_payload(as_of: date | None, items: list[dict[str, Any]]) -> dict[str, Any]:
    """北向信封：``as_of``/``stale_days``/``source_status`` 全由返回的 items 推导。"""
    stale_days = _stale_days(as_of)
    return {
        "as_of": as_of.isoformat() if as_of is not None else None,
        "stale_days": stale_days,
        "source_status": (
            "live"
            if stale_days is not None and stale_days <= NORTHBOUND_DISCONTINUED_AFTER_DAYS
            else "discontinued"
        ),
        "items": items,
    }


def _map_share_float_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    """share_float → share float rows（float_share 万股 / float_ratio %；ann_date 可空）。"""
    rows: list[dict[str, Any]] = []
    for rec in df.to_dict("records"):
        rows.append(
            {
                "ann_date": _d_opt(rec.get("ann_date")),
                "float_date": _d(rec["float_date"]),
                "ts_code": rec["ts_code"],
                "symbol": rec["ts_code"].split(".")[0],
                "float_share": _f(rec.get("float_share")),
                "float_ratio": _f(rec.get("float_ratio")),
                "holder_name": rec.get("holder_name"),
                "share_type": rec.get("share_type"),
            }
        )
    return rows


def _map_repurchase_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    """repurchase → repurchase rows（vol 股 / amount 元；exp_date 常为 NaN → None）。"""
    rows: list[dict[str, Any]] = []
    for rec in df.to_dict("records"):
        rows.append(
            {
                "ann_date": _d(rec["ann_date"]),
                "ts_code": rec["ts_code"],
                "symbol": rec["ts_code"].split(".")[0],
                "end_date": _d_opt(rec.get("end_date")),
                "proc": str(rec.get("proc") or "")[:16],
                "exp_date": _d_opt(rec.get("exp_date")),
                "vol": _f(rec.get("vol")),
                "amount": _f(rec.get("amount")),
                "high_limit": _f(rec.get("high_limit")),
                "low_limit": _f(rec.get("low_limit")),
            }
        )
    return rows


def _dedupe_repurchase_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按去重键（ann_date+ts_code+proc）同批去重，保留末次出现。

    ON CONFLICT 只处理与既有行的冲突、不处理同批 INSERT 内自冲突，须先在 Python 端去重。
    """
    return list({(r["ann_date"], r["ts_code"], r["proc"]): r for r in rows}.values())


_CATCHUP_LOOKBACK_DAYS = 10


async def _open_trading_days_since(
    client: Any, last_collected: date | None, today: date
) -> list[date]:
    """(last_collected, today] 内的交易日，升序。

    last_collected 为 None（空表/首次）时从 today-回看窗口 起算，窗口整体不超过
    _CATCHUP_LOOKBACK_DAYS 个日历日——调度错过的日子逐日补拉，避免"只拉当天、
    错过即永久缺口 / 当日 18:00 数据未发布则整天丢失"。
    """
    start = (
        last_collected + timedelta(days=1)
        if last_collected
        else today - timedelta(days=_CATCHUP_LOOKBACK_DAYS)
    )
    if start > today:
        return []
    df = await client.fetch_trade_cal(
        start_date=start.strftime("%Y%m%d"), end_date=today.strftime("%Y%m%d"), is_open="1"
    )
    days = [_d(rec["cal_date"]) for rec in df.to_dict("records") if rec.get("cal_date")]
    return days[-_CATCHUP_LOOKBACK_DAYS:]


async def ingest_dragon_tiger(db: AsyncSession, trade_date: date | None = None) -> dict[str, int]:
    """盘后采集龙虎榜个股明细（top_list，幂等 upsert）。

    指定 trade_date → 只拉当日；None（调度/CLI 缺省）→ 补漏模式：自表内最新
    交易日起把缺失的交易日逐日拉齐（含当日；当日榜单未发布则下次运行自动重试）。
    """
    client = _get_tushare()
    if trade_date is not None:
        df = await client.fetch_top_list(trade_date.strftime("%Y%m%d"))
        upserted = await market_data_repo.upsert_dragon_tiger(db, _map_top_list_rows(df))
        logger.info("ingest_dragon_tiger trade_date=%s upserted=%s", trade_date, upserted)
        return {"upserted": upserted, "days": 1}
    last = await market_data_repo.max_dragon_tiger_date(db)
    days = await _open_trading_days_since(client, last, _today_sh())
    upserted = 0
    for day in days:
        try:
            df = await client.fetch_top_list(day.strftime("%Y%m%d"))
        except Exception:
            logger.warning("dragon_tiger fetch failed for %s", day, exc_info=True)
            continue
        upserted += await market_data_repo.upsert_dragon_tiger(db, _map_top_list_rows(df))
    logger.info(
        "ingest_dragon_tiger catchup days=%s upserted=%s", [d.isoformat() for d in days], upserted
    )
    return {"upserted": upserted, "days": len(days)}


async def ingest_block_trades(db: AsyncSession, trade_date: date | None = None) -> dict[str, int]:
    """盘后采集大宗交易明细（block_trade）。

    行无稳定业务主键 → DO NOTHING；同批重复行先 Python 端去重（见 _dedupe_block_trade_rows）。
    指定 trade_date → 只拉当日；None → 补漏模式（同 ingest_dragon_tiger）。
    """
    client = _get_tushare()
    if trade_date is not None:
        df = await client.fetch_block_trade(trade_date.strftime("%Y%m%d"))
        rows = _dedupe_block_trade_rows(_map_block_trade_rows(df))
        upserted = await market_data_repo.upsert_block_trades(db, rows)
        logger.info("ingest_block_trades trade_date=%s upserted=%s", trade_date, upserted)
        return {"upserted": upserted, "days": 1}
    last = await market_data_repo.max_block_trade_date(db)
    days = await _open_trading_days_since(client, last, _today_sh())
    upserted = 0
    for day in days:
        try:
            df = await client.fetch_block_trade(day.strftime("%Y%m%d"))
        except Exception:
            logger.warning("block_trade fetch failed for %s", day, exc_info=True)
            continue
        rows = _dedupe_block_trade_rows(_map_block_trade_rows(df))
        upserted += await market_data_repo.upsert_block_trades(db, rows)
    logger.info(
        "ingest_block_trades catchup days=%s upserted=%s", [d.isoformat() for d in days], upserted
    )
    return {"upserted": upserted, "days": len(days)}


DRAGON_TIGER_CACHE_KEY = "market:dragon-tiger:{date}"
DRAGON_TIGER_TTL = 300
DRAGON_TIGER_CACHE_LIMIT = 100  # 端点 limit 上限（le=100）：缓存全量再按请求切片


async def get_dragon_tiger(
    cache: CacheClient | None, date_iso: str | None = None, limit: int = 15
) -> list[dict[str, Any]]:
    """某交易日龙虎榜明细（date 缺省取表内最新 trade_date；Redis 300s 共享缓存）。"""
    day = datetime.fromisoformat(date_iso).date() if date_iso else None

    from app.core.database import async_session_factory  # noqa: PLC0415

    rows: list[dict[str, Any]] = []
    async with async_session_factory() as db:
        effective = day or await market_data_repo.max_dragon_tiger_date(db)
        if effective is None:
            return []
        key = DRAGON_TIGER_CACHE_KEY.format(date=effective.isoformat())
        if cache is not None:
            cached = await cache.get(key)
            if cached:
                rows_cached: list[dict[str, Any]] = cached
                return rows_cached[:limit]
        for t in await market_data_repo.list_dragon_tiger(db, effective, DRAGON_TIGER_CACHE_LIMIT):
            rows.append(
                {
                    "trade_date": t.trade_date.isoformat(),
                    "ts_code": t.ts_code,
                    "symbol": t.ts_code.split(".")[0],
                    "name": t.name,
                    "close": t.close,
                    "pct_change": t.pct_change,
                    "turnover_rate": t.turnover_rate,
                    "amount": t.amount,
                    "l_buy": t.l_buy,
                    "l_sell": t.l_sell,
                    "l_amount": t.l_amount,
                    "net_amount": t.net_amount,
                    "reason": t.reason,
                }
            )
    if cache is not None and rows:
        await cache.set(key, rows, ttl=DRAGON_TIGER_TTL)
    return rows[:limit]


BLOCK_TRADES_CACHE_KEY = "market:block-trades:{date}:{symbol}"
BLOCK_TRADES_TTL = 300
BLOCK_TRADES_CACHE_LIMIT = 100  # 端点 limit 上限（le=100）：缓存全量再按请求切片


async def get_block_trades(
    cache: CacheClient | None,
    date_iso: str | None = None,
    symbol: str | None = None,
    limit: int = 15,
) -> list[dict[str, Any]]:
    """某交易日大宗交易明细（date 缺省取表内最新；symbol 为 6 位代码；Redis 300s 共享缓存）。"""
    day = datetime.fromisoformat(date_iso).date() if date_iso else None

    from app.core.database import async_session_factory  # noqa: PLC0415

    rows: list[dict[str, Any]] = []
    async with async_session_factory() as db:
        effective = day or await market_data_repo.max_block_trade_date(db)
        if effective is None:
            return []
        key = BLOCK_TRADES_CACHE_KEY.format(date=effective.isoformat(), symbol=symbol or "all")
        if cache is not None:
            cached = await cache.get(key)
            if cached:
                rows_cached: list[dict[str, Any]] = cached
                return rows_cached[:limit]
        rows = await market_data_repo.list_block_trades(
            db, effective, symbol, BLOCK_TRADES_CACHE_LIMIT
        )
    if cache is not None and rows:
        await cache.set(key, rows, ttl=BLOCK_TRADES_TTL)
    return rows[:limit]


async def ingest_share_floats(db: AsyncSession, days: int = 7) -> dict[str, int]:
    """盘后采集：近 N 日公告的限售解禁计划（share_float，TuShare 按 ann_date 过滤，DO NOTHING）。"""
    client = _get_tushare()
    start = (_today_sh() - timedelta(days=days)).strftime("%Y%m%d")
    end = _today_sh().strftime("%Y%m%d")
    df = await client.fetch_share_float(start_date=start, end_date=end)
    upserted = await market_data_repo.upsert_share_floats(db, _map_share_float_rows(df))
    logger.info("ingest_share_floats days=%s upserted=%s", days, upserted)
    return {"upserted": upserted}


async def ingest_repurchases(db: AsyncSession, days: int = 7) -> dict[str, int]:
    """盘后采集：近 N 日股票回购进度（repurchase，进度/数量会修订 → DO UPDATE 幂等 upsert）。"""
    client = _get_tushare()
    start = (_today_sh() - timedelta(days=days)).strftime("%Y%m%d")
    end = _today_sh().strftime("%Y%m%d")
    df = await client.fetch_repurchase(start_date=start, end_date=end)
    rows = _dedupe_repurchase_rows(_map_repurchase_rows(df))
    upserted = await market_data_repo.upsert_repurchases(db, rows)
    logger.info("ingest_repurchases days=%s upserted=%s", days, upserted)
    return {"upserted": upserted}


SHARE_FLOATS_CACHE_KEY = "market:share-floats:{start}:{end}:{symbol}"
SHARE_FLOATS_TTL = 300
SHARE_FLOATS_CACHE_LIMIT = 100  # 端点 limit 上限（le=100）：缓存全量再按请求切片


async def get_share_floats(
    cache: CacheClient | None,
    start_iso: str | None = None,
    end_iso: str | None = None,
    symbol: str | None = None,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """解禁时间表（按 float_date 过滤，缺省近 30 天至未来 90 天——解禁是未来事件；Redis 300s）。"""
    end = datetime.fromisoformat(end_iso).date() if end_iso else _today_sh() + timedelta(days=90)
    start = (
        datetime.fromisoformat(start_iso).date() if start_iso else _today_sh() - timedelta(days=30)
    )
    key = SHARE_FLOATS_CACHE_KEY.format(
        start=start.isoformat(), end=end.isoformat(), symbol=symbol or "all"
    )
    if cache is not None:
        cached = await cache.get(key)
        if cached:
            rows_cached: list[dict[str, Any]] = cached
            return rows_cached[:limit]

    from app.core.database import async_session_factory  # noqa: PLC0415

    rows: list[dict[str, Any]] = []
    async with async_session_factory() as db:
        rows = await market_data_repo.list_share_floats(
            db, start, end, symbol, SHARE_FLOATS_CACHE_LIMIT
        )
    if cache is not None and rows:
        await cache.set(key, rows, ttl=SHARE_FLOATS_TTL)
    return rows[:limit]


REPURCHASES_CACHE_KEY = "market:repurchases:{start}:{end}:{symbol}"
REPURCHASES_TTL = 300
REPURCHASES_CACHE_LIMIT = 100  # 端点 limit 上限（le=100）：缓存全量再按请求切片


async def get_repurchases(
    cache: CacheClient | None,
    start_iso: str | None = None,
    end_iso: str | None = None,
    symbol: str | None = None,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """股票回购进度（按 ann_date 过滤，缺省近 30 天至今；Redis 300s 共享缓存）。"""
    end = datetime.fromisoformat(end_iso).date() if end_iso else _today_sh()
    start = (
        datetime.fromisoformat(start_iso).date() if start_iso else _today_sh() - timedelta(days=30)
    )
    key = REPURCHASES_CACHE_KEY.format(
        start=start.isoformat(), end=end.isoformat(), symbol=symbol or "all"
    )
    if cache is not None:
        cached = await cache.get(key)
        if cached:
            rows_cached2: list[dict[str, Any]] = cached
            return rows_cached2[:limit]

    from app.core.database import async_session_factory  # noqa: PLC0415

    rows: list[dict[str, Any]] = []
    async with async_session_factory() as db:
        rows = await market_data_repo.list_repurchases(
            db, start, end, symbol, REPURCHASES_CACHE_LIMIT
        )
    if cache is not None and rows:
        await cache.set(key, rows, ttl=REPURCHASES_TTL)
    return rows[:limit]


def _map_stk_limit_rows(
    df: pd.DataFrame, trade_date: date, stock_id_map: dict[str, int]
) -> list[dict[str, Any]]:
    """TuShare stk_limit 行 → 落库 dict（纯映射，同步函数：与模块内其他 `_map_*` 一致）。

    丢弃两类行：映射不到 A 股 stocks 的（基金/B 股，实测单日 5,637 行里 138 行）、
    限价为空的。**原值透传，不做任何比例重算。**
    """
    rows: list[dict[str, Any]] = []
    for row in df.to_dict("records"):
        ts_code = str(row.get("ts_code", "")).strip()
        stock_id = stock_id_map.get(ts_code)
        up_limit = row.get("up_limit")
        if stock_id is None or up_limit is None or pd.isna(up_limit):
            continue
        rows.append(
            {
                "trade_date": trade_date,
                "stock_id": stock_id,
                "ts_code": ts_code,
                "pre_close": None if pd.isna(row.get("pre_close")) else float(row["pre_close"]),
                "up_limit": float(up_limit),
                "down_limit": None if pd.isna(row.get("down_limit")) else float(row["down_limit"]),
            }
        )
    return rows


async def ingest_stock_price_limits(
    db: AsyncSession, trade_date: date | None = None, window_days: int = 20
) -> dict[str, Any]:
    """拉取缺失交易日的交易所口径涨跌停价（幂等 + 补漏）。

    「补漏」判据是 stock_price_limits 里没有该日任何行，而不是"最新日已存在就跳过"：
    窗口中间的日子缺一个，连板链就会断在那里，且不会报错——只能靠逐日对账发现。
    """
    from app.repositories import limit_up_repo, stock_repo  # noqa: PLC0415

    client = _get_tushare()
    as_of = trade_date or await limit_up_repo.latest_quote_date(db)
    if as_of is None:
        return {"status": "skipped", "reason": "daily_quotes empty"}
    dates = await limit_up_repo.list_recent_trade_dates(db, as_of, window_days)
    todo = await limit_up_repo.missing_price_limit_dates(db, dates)
    if trade_date is not None:
        todo = [trade_date]
    if not todo:
        return {"status": "ok", "as_of": as_of.isoformat(), "fetched": 0, "upserted": 0}

    stock_id_map = await stock_repo.build_ts_code_to_stock_id(db)
    fetched = upserted = 0
    for d in todo:
        df = await client.fetch_stk_limit(trade_date=d.strftime("%Y%m%d"))
        rows = _map_stk_limit_rows(df, d, stock_id_map)
        fetched += len(rows)
        upserted += await limit_up_repo.upsert_price_limits(db, rows)
    return {
        "status": "ok",
        "as_of": as_of.isoformat(),
        "dates": [d.isoformat() for d in todo],
        "fetched": fetched,
        "upserted": upserted,
    }


async def _main() -> None:
    from app.core.database import async_session_factory  # noqa: PLC0415

    args = sys.argv[1:]
    job = args[0] if args else ""
    async with async_session_factory() as db:
        if job == "global_index_daily":
            result = await ingest_global_index_daily(db)
        elif job == "backfill_global_index":
            result = await backfill_global_index_history(
                db, years=int(args[1]) if len(args) > 1 else 2
            )
        elif job == "sector_moneyflow":
            result = await ingest_sector_moneyflow(db)
        elif job == "northbound":
            result = await ingest_northbound(db)
        elif job == "market_moneyflow":
            result = await ingest_market_moneyflow_daily(
                db, days=int(args[1]) if len(args) > 1 else 10
            )
        elif job == "dragon_tiger":
            result = await ingest_dragon_tiger(db, _d(args[1]) if len(args) > 1 else None)
        elif job == "block_trades":
            result = await ingest_block_trades(db, _d(args[1]) if len(args) > 1 else None)
        elif job == "share_floats":
            result = await ingest_share_floats(db, days=int(args[1]) if len(args) > 1 else 7)
        elif job == "repurchases":
            result = await ingest_repurchases(db, days=int(args[1]) if len(args) > 1 else 7)
        elif job == "announcements":
            from app.services import announcement_service  # noqa: PLC0415

            result = await announcement_service.ingest_announcements(
                db, days=int(args[1]) if len(args) > 1 else 3
            )
        else:
            raise SystemExit(
                f"unknown job: {job}; available: global_index_daily, "
                "backfill_global_index, sector_moneyflow, northbound, dragon_tiger, "
                "block_trades, share_floats, repurchases, announcements"
            )
        await db.commit()
    print(job, "->", result)


if __name__ == "__main__":
    asyncio.run(_main())
