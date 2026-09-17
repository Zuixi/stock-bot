"""单日市场快照 —— 市场平面所有「按日聚合」端点共用的唯一取行入口。

背景（Phase 0 实测）：市场平面有 4~6 个端点各自执行同形的「取最新一个交易日 +
JOIN stocks + GROUP BY」，且每个都用一个 ``LEFT JOIN LATERAL (… ORDER BY
trade_date DESC LIMIT 1)`` 逐股回看前收来**重算** pct_chg（每股一次历史查找）。
``daily_quotes.pct_chg`` 现在是权威列（Phase 0 已把历史 NULL 回填、ingest 直取
TuShare 原生值），重算纯属浪费。

本模块把「一天的全市场一行一票」收敛成一次查询 + 一份缓存：

- :func:`load_day_rows` —— 取行（可选 Redis 缓存，TTL 300s）；
- :func:`group_by` / :func:`summarize_group` —— 纯函数，Python 侧分组与汇总，
  替代每个端点各写一遍 SQL 聚合（口径只此一份）；
- ``SNAPSHOT_CACHE_KEY`` / ``SNAPSHOT_TTL`` —— 缓存身份与寿命。

SQL 只读 ``daily_quotes.pct_chg``，**不再**对 ``daily_quotes`` 做前收 LATERAL；
市值/换手率仍取每股最近一条 ``daily_basic_indicators``（``trade_date <= :day``，
不看到未来）——那一个 LATERAL 保留，理由见 :func:`_snapshot_stmt`。

性能口径（实测，不作「单查询提速」宣称）：

- 本 loader 单次取行 **warm ~70ms / 冷会话 ~200ms**（含连接建立与首次执行开销），
  **比旧的单端点 SQL（~46ms）慢** —— 旧形态只回 10 行聚合结果，本语句回 5485 行 × 11 列
  （含旧形态根本没有的市值/换手率），其中约 46ms 还是 ``daily_basic`` 的逐股 LATERAL。
- 收益在架构侧：(a) 市场平面 4~6 个端点从「各自一次 ~46ms SQL + 各自的 daily_basic 查询」
  收敛为**共用这 1 次取行 + 1 份缓存**；(b) 缓存命中约 **15ms**（1.2MB payload 的 JSON
  解析占大头）。故 Task 7 必须让**所有**市场平面端点都改走本 loader —— 只改部分端点会
  让未改的端点从 ~46ms 退到 ~70ms。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, Select, bindparam, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_basic import DailyBasicIndicator
from app.models.quote import DailyQuote
from app.models.stock import Stock

logger = logging.getLogger(__name__)

SNAPSHOT_CACHE_KEY = "market:day:rows:{day}"
SNAPSHOT_TTL = 300  # 5 minutes — 与 market_service 的 _MARKET_CACHE_TTL 对齐

#: 单行契约（键序即 SQL 选择顺序）。下游按这些键消费，缓存 payload 也按它们校验。
ROW_KEYS: tuple[str, ...] = (
    "stock_id",
    "symbol",
    "name",
    "csrc_desc",
    "province",
    "close",
    "pct_chg",
    "amount",
    "total_mv",
    "circ_mv",
    "turnover_rate",
)

#: 数值列的规范化目标类型（Decimal/None → float/None），保证冷热两条路径同型。
_FLOAT_KEYS: tuple[str, ...] = (
    "close",
    "pct_chg",
    "amount",
    "total_mv",
    "circ_mv",
    "turnover_rate",
)

#: 字符串列必须真是 ``str | None``：一个 dict/int 漏进来会在下游变成分组键。
_STRING_KEYS: tuple[str, ...] = ("symbol", "name", "csrc_desc", "province")


def _snapshot_stmt(day: date) -> Select[Any]:
    """一天的全市场取行语句：一票一行，按 ``stock_id`` 升序（下游分组可复现）。

    ``pct_chg`` 直接读 ``daily_quotes`` 存储列 —— 不再有 ``daily_quotes`` 上的
    前收 LATERAL（436 万行表上每股一次历史查找）。成本诚实说明：整个取行 warm 约
    70ms、冷会话约 200ms（含连接与首次执行），**比旧单端点 SQL 的约 46ms 慢** ——
    旧形态只回 10 行聚合，本语句回 5485 行 × 11 列；收益来自 4~6 个端点共用这
    一次取行 + 一份缓存（Redis 命中约 15ms），不是单条查询提速。

    市值/换手率用一个 **correlated LATERAL** 取每股 ``trade_date <= :day`` 的最近
    一条 ``daily_basic_indicators``，而不是 ``DISTINCT ON`` 整表子查询：后者要在
    190 万行（且每日 +5.5k）上物化并排序，实测 1.4s；LATERAL 走
    ``idx_daily_basic_stock_date`` 只做 5485 次索引查找（约 46ms，是本查询当前的
    主要成本）。``<= :day``（而非无界最新）避免把未来某天的市值提前用在这一天。
    """
    day_param = bindparam("day", value=day, type_=Date)
    latest_basic = (
        select(
            DailyBasicIndicator.total_mv.label("total_mv"),
            DailyBasicIndicator.circ_mv.label("circ_mv"),
            DailyBasicIndicator.turnover_rate.label("turnover_rate"),
        )
        .where(
            DailyBasicIndicator.stock_id == DailyQuote.stock_id,
            DailyBasicIndicator.trade_date <= day_param,
        )
        .order_by(DailyBasicIndicator.trade_date.desc())
        .limit(1)
        .correlate(DailyQuote)
        .lateral("latest_basic")
    )
    return (
        select(
            DailyQuote.stock_id,
            Stock.symbol,
            Stock.name,
            Stock.csrc_desc,
            Stock.province,
            DailyQuote.close,
            DailyQuote.pct_chg,
            DailyQuote.amount,
            latest_basic.c.total_mv,
            latest_basic.c.circ_mv,
            latest_basic.c.turnover_rate,
        )
        .select_from(DailyQuote)
        .join(Stock, Stock.id == DailyQuote.stock_id)
        # ``ON true``：相关性由子查询内部的 ``stock_id = daily_quotes.stock_id`` 承担。
        # 不在 LATERAL 选择列表里多取 stock_id（5485 次循环各多读一列，实测 +10ms）。
        .outerjoin(latest_basic, true())
        .where(DailyQuote.trade_date == day_param)
        .order_by(DailyQuote.stock_id)
    )


def _as_float(value: Any) -> float | None:
    """``None`` → ``None``；``Decimal``/``int``/``float`` → ``float``。

    数值列在这里一次性规范化（Decimal 就是「JSON 不安全」的那个类型），于是冷
    （打库）/热（缓存）两条路径返回同型值、写缓存也无需再转换。
    其他类型（含数字字符串）按 payload 损坏处理，由 :func:`_from_payload` 兜底成 miss。
    """
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    raise TypeError(f"snapshot field is not numeric: {type(value).__name__}")


def _normalize_row(row: Mapping[Any, Any]) -> dict[str, Any]:
    """把一行（RowMapping 或缓存 dict）规范成 :data:`ROW_KEYS` 契约。

    幂等：规范化过的 dict 再进一次结果不变。缺键抛 KeyError、类型不对抛
    TypeError/ValueError/OverflowError —— 由 :func:`_from_payload` 转成「缓存 miss」。

    数值列走 :func:`_as_float`；字符串列要求 ``str | None``，否则同样按损坏处理
    （一个 dict 型 ``csrc_desc`` 漏过去会在下游变成分组键）。

    直接按契约键索引 ``row``（``row[key]``）而不先 ``dict(row)``：RowMapping →
    dict 的整表物化在 5485 行上值 6ms（实测 12.5ms → 6.5ms），不值得。
    （``Mapping[Any, Any]`` 而非 ``Mapping[str, Any]``：SQLAlchemy 的 ``RowMapping``
    不是前者的子类型，键都是字符串但类型层面得放宽。）
    这是全模块**唯一**的规范化点：:func:`_fetch_rows` 返回原始 DB 映射，
    :func:`load_day_rows` 对它调用一次 :func:`_to_payload` 即同时得到返回值和缓存 payload
    （旧写法 fetch 里规范化一遍、写缓存再规范化一遍，5485 行多花约 6.5ms）。
    """
    normalized: dict[str, Any] = {"stock_id": int(row["stock_id"])}
    for key in ROW_KEYS[1:]:
        value = row[key]
        if key in _FLOAT_KEYS:
            normalized[key] = _as_float(value)
        elif key in _STRING_KEYS:
            if value is not None and not isinstance(value, str):
                raise TypeError(f"snapshot field {key!r} is not a string: {type(value).__name__}")
            normalized[key] = value
        else:  # pragma: no cover — ROW_KEYS 全覆盖，防止新增键时静默漏掉类型校验
            raise TypeError(f"snapshot field {key!r} has no declared type")
    return normalized


def _to_payload(rows: Iterable[Mapping[Any, Any]]) -> list[dict[str, Any]]:
    """规范化成 JSON 安全 payload（键白名单 + Decimal → float），全模块唯一转换点。"""
    return [_normalize_row(row) for row in rows]


def _from_payload(payload: Any) -> list[dict[str, Any]] | None:
    """从缓存 payload 重建行列表；不可用（形状/类型损坏）时返回 ``None`` = miss。

    ``CacheClient`` 走 JSON，日期/Decimal 回来都是别的类型；这里不做猜测，
    只接受本模块自己写出的形状（**非空** ``list[dict]`` + ROW_KEYS 齐全 +
    数值可转 float + 字符串列为 ``str | None``）。损坏 payload 记 warning 并按
    miss 处理，绝不把半截数据喂给下游。

    ``[]`` 也算 miss：本模块从不写空列表（只有非空的一天才写缓存），而一个游离的
    ``[]`` 会把「无数据」状态冻满 300s —— 正是「空日不写缓存」想避免的状态。
    ``OverflowError`` 一并兜住：JSON 整数可以有 >308 位，``float()`` 会当场溢出。
    """
    if isinstance(payload, list) and payload:
        try:
            return [_normalize_row(item) for item in payload]
        except (KeyError, TypeError, ValueError, OverflowError):
            pass
    # 只记前 200 字符：合法 payload 是 1.2MB 级，坏 payload 也可能是（别把日志打爆）。
    logger.warning("load_day_rows: unusable cache payload %s", repr(payload)[:200])
    return None


async def _fetch_rows(db: AsyncSession, day: date) -> list[Mapping[Any, Any]]:
    """取行 SQL seam（单测 monkeypatch 本函数即不碰库）。

    返回**原始** DB 映射（数值是 ``Decimal``）：规范化只由 :func:`load_day_rows`
    统一做一次，seam 语义即「把库里的行原样交出来」。
    """
    result = await db.execute(_snapshot_stmt(day))
    return list(result.mappings())


async def load_day_rows(
    db: AsyncSession,
    day: date,
    *,
    cache: Any = None,
) -> list[dict[str, Any]]:
    """一天的全市场快照行，按 ``stock_id`` 升序（缓存命中时亦然）。

    ``cache`` 是 duck-typed 客户端（``async get`` / ``async set(key, value, ttl=…)``），
    可为 ``None``（不打缓存）。只有取到非空行才写缓存：空日不写，免得把一次瞬时
    空结果冻 5 分钟（日级判据本身要求行数与 pct_chg 覆盖率达标，空日不该被消费）。

    冷路径只规范化一次：``_fetch_rows`` 交回原始映射，这里一个 :func:`_to_payload`
    同时产出返回值与缓存 payload。
    """
    cache_key = SNAPSHOT_CACHE_KEY.format(day=day.isoformat())
    if cache is not None:
        cached = await cache.get(cache_key)
        if cached is not None:
            rows = _from_payload(cached)
            if rows is not None:
                return rows
    payload = _to_payload(await _fetch_rows(db, day))
    if cache is not None and payload:
        await cache.set(cache_key, payload, SNAPSHOT_TTL)
    return payload


def group_by(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    """按 ``key`` 分桶；``None``/空串 的行跳过（不产生空桶）。

    纯函数。组内保持输入行序（本模块的行序 = ``stock_id`` 升序，故分组结果可复现），
    dict 的插入序 = 该组首次出现的顺序。
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        value = row.get(key)
        if value is None or value == "":
            continue
        groups.setdefault(str(value), []).append(row)
    return groups


def summarize_group(items: list[dict[str, Any]]) -> dict[str, Any]:
    """组内涨跌统计：``total`` / ``up_count`` / ``flat_count`` / ``down_count`` / ``avg_chg``。

    口径（**有意与旧 SQL ``_hot_board_rows`` 不同**，控制器已批准）：
    ``up_count`` = ``pct_chg > 0``，``down_count`` = ``pct_chg < 0``，
    ``flat_count`` 含 ``pct_chg == 0`` **与 ``pct_chg is None``**。

    旧形态的 ``CASE WHEN prev.close > 0 THEN … ELSE 0 END`` 把「前收缺失」就地伪造成
    ``0.0``、又把这个 0% 喂进 ``AVG``；而真正算出 ``NULL``（当日 close 缺失）的行会同时
    落空 ``> 0`` / ``= 0`` / ``< 0`` 三个 CASE、一个桶都不进。于是旧口径下
    ``up+flat+down == total`` 并不成立，均值里也混着伪造出来的 0%。本模块改为
    「``NULL`` 计平盘、但不进均值分母」：``up+flat+down == total`` **按构造恒成立**，
    也不再凭空造出 0%。这是刻意的语义变更本身（``flat_count`` / ``avg_chg`` 都可能与旧
    数字不同），不是等价改写。

    ``avg_chg`` 是非空 ``pct_chg`` 的算术平均，没有非空值时 ``0.0``；分母取自**存储列**
    （``Numeric(8,4)`` 已四舍五入），不再由 full-precision 的 ``close/prev`` 现算，
    与旧口径可能存在 <0.01 的差异（展示层仍按现状 ``round(…, 2)``）。
    """
    up_count = 0
    flat_count = 0
    down_count = 0
    total_chg = 0.0
    counted = 0
    for item in items:
        chg = item.get("pct_chg")
        if chg is None:
            flat_count += 1
            continue
        if chg > 0:
            up_count += 1
        elif chg < 0:
            down_count += 1
        else:
            flat_count += 1
        total_chg += float(chg)
        counted += 1
    return {
        "total": len(items),
        "up_count": up_count,
        "flat_count": flat_count,
        "down_count": down_count,
        "avg_chg": total_chg / counted if counted else 0.0,
    }
