"""Market-data face: ingest + read services.

数据源（字段/单位见 plans/2026-09-03-market-data-face.md「已验证数据源事实」）。
"""

from __future__ import annotations

import asyncio
import logging
import math
import sys
from datetime import date, datetime, timedelta
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

#: 交易时段 / 时区 / 币种 三要素的**市场级默认值**。
#: 依据是 S1.3 契约「名称 + 数据源代码 + 交易时段 + 时区 + 币种」五者必填。
#:
#: 做成"按市场取默认"而不是每条手抄：同一市场的时段/时区/币种天然相同，逐条复制只会
#: 在下一轮加标的时漏改一条。**A50 是唯一的例外**——它不在 A 股时段交易，故在条目上
#: **显式覆盖**（见下方 `session`），这正是导图点名要防的混淆。
_MARKET_DEFAULTS: dict[str, dict[str, str]] = {
    "CN": {"session": "09:30-11:30 / 13:00-15:00", "timezone": "Asia/Shanghai", "currency": "CNY"},
    "HK": {"session": "09:30-12:00 / 13:00-16:00", "timezone": "Asia/Hong_Kong", "currency": "HKD"},
    "JP": {"session": "09:00-11:30 / 12:30-15:00", "timezone": "Asia/Tokyo", "currency": "JPY"},
    "KR": {"session": "09:00-15:30", "timezone": "Asia/Seoul", "currency": "KRW"},
    "US": {"session": "09:30-16:00", "timezone": "America/New_York", "currency": "USD"},
    "SG": {
        "session": "T 09:00-16:30 / T+1 17:00-05:15",
        "timezone": "Asia/Singapore",
        "currency": "CNY",
    },
}

#: A50（富时中国A50 指数期货）的**显式**时段/时区/币种——不得落回 `_MARKET_DEFAULTS["SG"]` 之外
#: 的任何隐式默认，也不得与 A 股时段混淆（S1.3 逐字要求「UI 明示」）。币种取 **CNY**：
#: 该指数成分是 A 股、报价与保证金按人民币口径理解（导图亦标注「CNY 夜盘」）。
#: 时段依据：新交所 T+1 时段（夜盘）覆盖新加坡时间 17:00–次日 05:15（[新交所 T+1 开盘时间
#: 调整通知](https://henghua.hk/cmsbigfile/2025/04/f05d5388-013d-4b0f-9444-3e33f1810940/SGSHIJIAN.pdf)、
#: [SGX 指数商品交易时间异动通知](https://www.ibff.com.tw/Support/Bulletin/75ad66b9-7818-439d-9687-6b736fa4f4a1/2025SGX%E4%BA%A4%E6%98%93%E6%99%82%E9%96%93%E7%95%B0%E5%8B%95%E9%80%9A%E7%9F%A5.pdf)；
#: 本仓库侧佐证：东财延迟行情对 XIN9 的行情时间戳落在新加坡 17 时后，见
#: `docs/design/data-source.md` 附.5）。
_A50_SESSION = "T 09:00-16:30 / T+1 17:00-05:15（新加坡夜盘，非 A 股时段）"


def _defaults_for(market: str) -> dict[str, str]:
    """市场级默认三要素；未登记的市场 → **空串**（由测试断言非空，不许静默兜底成假值）。"""
    return _MARKET_DEFAULTS.get(market, {"session": "", "timezone": "", "currency": ""})


def _instrument(
    *,
    ts_code: str,
    name: str,
    market: str,
    region: str,
    source: str,
    em_secid: str | None = None,
    sina_code: str | None = None,
    session: str | None = None,
    timezone: str | None = None,
    currency: str | None = None,
    quote_provider: str = "eastmoney",
) -> dict[str, str]:
    """登记一条全球标的：五要素（名称 / 数据源代码 / 交易时段 / 时区 / 币种）一律非空。

    `session`/`timezone`/`currency` 缺省取 `_defaults_for(market)`；只有确实不落市场默认
    的标的（当前仅 A50）才显式覆盖——**A50 不得与 A 股时段混淆**是本条的用途。

    `em_secid` / `sina_code` 均可选：缺省表示该标的**没有**对应行情源覆盖，读路径
    不会把它拼进该源的请求串（避免"取到别人的行情"或静默失败）。
    """
    defaults = _defaults_for(market)
    row = {
        "ts_code": ts_code,
        "name": name,
        "market": market,
        "region": region,
        "session": session or defaults["session"],
        "timezone": timezone or defaults["timezone"],
        "currency": currency or defaults["currency"],
        "source": source,
        "quote_provider": quote_provider,
    }
    if em_secid:
        row["em_secid"] = em_secid
    if sina_code:
        row["sina_code"] = sina_code
    return row


#: 标的元数据登记表（S1.3 交付物「全球标的元数据表」）。
#:
#: 与改造前的差异（**既有 ts_code 一条未改**，只补字段 + 追加条目）：
#: - 每条补齐 `session` / `timezone` / `currency`（五要素非空，S1.3 验收判据）；
#: - 追加 NDX / SOX / HXC / HKTECH / XIN9；
#: - `em_secid` 改为**可选**：东财延迟行情只覆盖部分标的，无 secid 的标的
#:   不会进 `fetch_index_snapshot` 的 secids 列表（见 `get_global_index_cards`），
#:   因而不会静默取到别人的行情。
GLOBAL_INDICES: list[dict[str, str]] = [
    # ── A 股（8 条，`ts_code` 与改造前逐字一致；时段/时区/币种来自 CN 默认）──────────
    _instrument(
        ts_code="000001.SH",
        name="上证指数",
        market="CN",
        region="asia",
        em_secid="1.000001",
        source="index_daily",
    ),
    _instrument(
        ts_code="399001.SZ",
        name="深证成指",
        market="CN",
        region="asia",
        em_secid="0.399001",
        source="index_daily",
    ),
    _instrument(
        ts_code="399006.SZ",
        name="创业板指",
        market="CN",
        region="asia",
        em_secid="0.399006",
        source="index_daily",
    ),
    _instrument(
        ts_code="000300.SH",
        name="沪深300",
        market="CN",
        region="asia",
        em_secid="1.000300",
        source="index_daily",
    ),
    _instrument(
        ts_code="000905.SH",
        name="中证500",
        market="CN",
        region="asia",
        em_secid="1.000905",
        source="index_daily",
    ),
    _instrument(
        ts_code="000688.SH",
        name="科创50",
        market="CN",
        region="asia",
        em_secid="1.000688",
        source="index_daily",
    ),
    _instrument(
        ts_code="000016.SH",
        name="上证50",
        market="CN",
        region="asia",
        em_secid="1.000016",
        source="index_daily",
    ),
    _instrument(
        ts_code="899050.BJ",
        name="北证50",
        market="CN",
        region="asia",
        em_secid="0.899050",
        source="index_daily",
    ),
    # ── 香港（2 条）───────────────────────────────────────────────────────────
    _instrument(
        ts_code="HSI",
        name="恒生指数",
        market="HK",
        region="asia",
        em_secid="100.HSI",
        source="index_global",
    ),
    # 恒生科技指数：TuShare `index_global` 的代码是 **HKTECH**（不是 HSTECH）——
    # 2026-10-16 实机枚举该接口 22 个 ts_code 得到，HKTECH 2026 年内 190 个交易日可取；
    # 裸 HSTECH 实测 0 行（见 docs/design/data-source.md 附.5 实机记录）。
    _instrument(
        ts_code="HKTECH",
        name="恒生科技指数",
        market="HK",
        region="asia",
        source="index_global",
    ),
    # ── 日本 / 韩国（各 1 条）────────────────────────────────────────────────
    _instrument(
        ts_code="N225",
        name="日经225",
        market="JP",
        region="asia",
        em_secid="100.N225",
        source="index_global",
    ),
    _instrument(
        ts_code="KS11",
        name="韩国KOSPI",
        market="KR",
        region="asia",
        em_secid="100.KS11",
        source="index_global",
    ),
    # ── 美国（6 条：既有 3 + 新增 3）─────────────────────────────────────────
    _instrument(
        ts_code="DJI",
        name="道琼斯",
        market="US",
        region="americas",
        em_secid="100.DJIA",
        source="index_global",
    ),
    _instrument(
        ts_code="SPX",
        name="标普500",
        market="US",
        region="americas",
        em_secid="100.SPX",
        source="index_global",
    ),
    # ⚠️ **IXIC = 纳斯达克综合指数**（既有约定正确，本任务只补元数据、**不改 `ts_code`**）。
    # `em_secid` 沿用改造前的 `100.NDX`：它是**改造前就存在**的东财延迟行情映射，
    # 本任务不得改动既有条目（见任务硬性约束 2）；其与 `NDX` 条目的语义冲突记在
    # docs/design/data-source.md 附.5 的「已知遗留」里，交由接入 NDX 行情适配器的
    # 独立任务一并核对（本任务不擅自改既有 secid，避免把改造前的行为改坏）。
    _instrument(
        ts_code="IXIC",
        name="纳斯达克综合指数",
        market="US",
        region="americas",
        em_secid="100.NDX",
        source="index_global",
    ),
    # 纳斯达克100：TuShare `index_global` **无**此标的（裸 NDX / NDX.GI 实测 0 行），
    # 故走新浪美股指数行情（`gb_$ndx`，2026-10-16 实机取到真实值）；历史回补适配器
    # 见 docs/design/data-source.md 附.5，本任务只登记元数据、不编造数值。
    _instrument(
        ts_code="NDX",
        name="纳斯达克100指数",
        market="US",
        region="americas",
        source="sina_index",
        quote_provider="sina",
        sina_code="gb_$ndx",
    ),
    # 费城半导体指数：TuShare 同样无此标的，走新浪 `gb_$sox`（实机取到真实值）。
    _instrument(
        ts_code="SOX",
        name="费城半导体指数",
        market="US",
        region="americas",
        source="sina_index",
        quote_provider="sina",
        sina_code="gb_$sox",
    ),
    # 纳斯达克中国金龙指数：新浪 `gb_$hxc`（实机取到真实值）。
    # ⚠️ 注意 `gb_hxc`（无 `$`）是**另一只**标的（实测价 26.275，非指数点位），不得混用。
    _instrument(
        ts_code="HXC",
        name="纳斯达克中国金龙指数",
        market="US",
        region="americas",
        source="sina_index",
        quote_provider="sina",
        sina_code="gb_$hxc",
    ),
    # ── 新加坡（1 条）：富时中国A50 指数期货 ─────────────────────────────────
    # TuShare `index_global` 的 XIN9 实测可取（2026-10-09 close=13909.62，年内 4000 行上限）。
    # **时段显式覆盖**为新加坡 T+1 夜盘，绝不落回 A 股时段（S1.3 点名要求）。
    _instrument(
        ts_code="XIN9",
        name="富时中国A50指数期货",
        market="SG",
        region="asia",
        source="index_global",
        session=_A50_SESSION,
    ),
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


#: 日线回补源 → "怎么拉"。**显式登记**而不是 `if/else` 兜底：新增一个源却忘了登记时，
#: 行为是"该标的**没有**日线"，不是"拿 index_daily 去试一下"（后者会对非 A 股代码静默
#: 返回空表，看起来像"上游没数据"，排障时最贵）。
_DAILY_FETCHERS = ("index_global", "index_daily")


async def _collect_index_rows(client: Any, start: str, end: str) -> list[IndexDaily]:
    """按注册表逐指数拉取并映射为 IndexDaily 行。

    单指数失败只告警并跳过（部分成功仍入库），避免一次抖动作废整个调度批次。

    `source not in _DAILY_FETCHERS` 的标的（当前 = `sina_index` 的 NDX/SOX/HXC）
    **本轮不拉日线**：TuShare 无这三个标的，新浪日线回补适配器尚未写（见
    `docs/design/data-source.md` 附.5 的待办）。这里显式跳过并记一条 INFO，
    免得读者以为"注册了就该有日线"。
    """
    from app.models.index_daily import IndexDaily  # noqa: PLC0415

    rows: list[IndexDaily] = []
    for g in GLOBAL_INDICES:
        if g["source"] not in _DAILY_FETCHERS:
            logger.info(
                "global index %s source=%s has no daily backfill adapter yet; skipped",
                g["ts_code"],
                g["source"],
            )
            continue
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


GLOBAL_INDICES_CACHE_KEY = "market:global-indices:v2"
GLOBAL_INDICES_TTL = 60

#: 卡片契约版本：v2 起每条卡片带齐五要素（`session`/`timezone`/`currency`）与
#: `price_source`/`data_source`。缓存键同步升到 `:v2`——TTL 只有 60s，但**键不变会让
#: 正在过期的 v1 payload 被当成合法响应**，而 v1 没有这三个字段（S1.3 的验收判据
#: 会因此假绿）。旧键无人读取，自然过期。

#: `price_source` 取值（前端按它决定是否显示「数据源待接入」）：
#:   realtime        —— 东财延迟行情（实时快照）
#:   realtime_sina   —— 新浪美股指数行情（NDX/SOX/HXC）
#:   eod             —— 库内最近一根日线兜底
#:   none            —— 既无实时也无日线（**不得显示 0**，前端显示"待接入/暂无数据"）
_PRICE_REALTIME = "realtime"
_PRICE_REALTIME_SINA = "realtime_sina"
_PRICE_EOD = "eod"
_PRICE_NONE = "none"


def _get_sina_quotes() -> Any:
    from app.core.providers.sina_index_client import fetch_index_quotes  # noqa: PLC0415

    return fetch_index_quotes


async def _fetch_provider_quotes(tushare_codes: list[str], sina_codes: list[str]) -> dict[str, Any]:
    """两路行情各取一次（**单路失败不拖垮另一路**）；返回按"我们自己的查找键"索引的行情表。

    - 东财：按**完整 secid**（`100.DJIA`）索引——不可用短码，沪/深同号段会互相覆盖；
    - 新浪：按**新浪原始代码**（`gb_$ndx`）索引。
    """
    quotes: dict[str, Any] = {}
    if tushare_codes:
        try:
            em = _get_eastmoney()
            snap = await em.fetch_index_snapshot(tushare_codes)
            quotes.update({q["secid"]: q for q in snap if q.get("secid")})
        except Exception:
            logger.warning("global index snapshot fetch failed, falling back to EOD", exc_info=True)
    if sina_codes:
        try:
            quotes.update(await _get_sina_quotes()(sina_codes))
        except Exception:
            logger.warning("sina index quote fetch failed, falling back to EOD", exc_info=True)
    return quotes


async def get_global_index_cards(cache: CacheClient | None = None) -> list[dict[str, Any]]:
    """全球市场卡片：实时快照（东财 + 新浪，60s 共享缓存）+ 近 30 日 spark + EOD 兜底。

    **每条卡片都带齐五要素**（`name`/`ts_code` + `session`/`timezone`/`currency`，S1.3 硬要求）
    与 `data_source`（数据源代码/来源徽章用）。`em_secid` 缺省的标的不进东财 secids 请求串，
    `sina_code` 缺省的标的不进新浪请求串——宁可不取，也不取错。
    """
    if cache is not None:
        cached = await cache.get(GLOBAL_INDICES_CACHE_KEY)
        if cached:
            cards_cached: list[dict[str, Any]] = cached
            return cards_cached

    em_secids = [g["em_secid"] for g in GLOBAL_INDICES if g.get("em_secid")]
    sina_codes = [g["sina_code"] for g in GLOBAL_INDICES if g.get("sina_code")]
    quotes = await _fetch_provider_quotes(em_secids, sina_codes)

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

            lookup = g.get("em_secid") or g.get("sina_code") or ""
            q = quotes.get(lookup)
            now = datetime.now(_SH).isoformat(timespec="seconds")
            # 显式标 `dict[str, Any]`：不标的话 mypy 会按 `"spark": spark`（list[float]）把整个
            # dict 推成 `dict[str, Sequence[object]]`，下面 update 里的 float 就全是类型错误。
            base: dict[str, Any] = {
                "ts_code": g["ts_code"],
                "market": g["market"],
                "region": g["region"],
                "session": g["session"],
                "timezone": g["timezone"],
                "currency": g["currency"],
                "data_source": g["source"],
                "spark": spark,
                "updated_at": now,
            }
            if q and q.get("price") is not None:
                base.update(
                    {
                        "name": q.get("name") or g["name"],
                        "price": q["price"],
                        "change": q.get("change"),
                        "pct_change": q.get("pct_change"),
                        # 名称一律以**我们的登记表**为准会掩盖上游改名，故行情名非空时优先；
                        # `price_source` 标明这一格价来自哪一路，便于前端与排障区分。
                        "price_source": (
                            _PRICE_REALTIME_SINA if g.get("sina_code") else _PRICE_REALTIME
                        ),
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
                base.update(
                    {
                        "name": g["name"],
                        "price": last_close,
                        "change": change,
                        "pct_change": pct,
                        "price_source": _PRICE_EOD if last_close is not None else _PRICE_NONE,
                    }
                )
            cards.append(base)

    # 全部卡片都无价 → **不写缓存**：缓存一份全 `none` 的 payload 会让端点在整个 TTL 内
    # 无法通过下一次请求自愈（改造前的判据是 any(price is not None)，保持不变）。
    if cache is not None and any(c["price"] is not None for c in cards):
        await cache.set(GLOBAL_INDICES_CACHE_KEY, cards, ttl=GLOBAL_INDICES_TTL)
    return cards


SECTOR_MONEYFLOW_CACHE_KEY = "market:sector-moneyflow:{dimension}:{as_of}"
SECTOR_MONEYFLOW_TTL = 60
SECTOR_MONEYFLOW_CACHE_LIMIT = 100  # 端点 limit 上限（le=100）：缓存全量再按请求切片


async def ingest_sector_moneyflow(db: AsyncSession) -> dict[str, int]:
    """盘中轮询：industry/concept/region 三维当日快照 upsert。"""
    em = _get_eastmoney()
    today = _today_sh()
    result: dict[str, int] = {}
    for dimension in ("industry", "concept", "region"):
        rows = await em.fetch_sector_moneyflow(dimension)
        result[dimension] = await market_data_repo.upsert_sector_moneyflow(
            db, today, dimension, rows
        )
    logger.info("ingest_sector_moneyflow %s", result)
    return result


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
