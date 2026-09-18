"""盘中实时情绪快照——东财涨停池 → 与 ``limit_up_service.get_snapshot`` 同构的 payload。

设计约束（plan Task 10）：

- **纯函数** ``build_intraday_snapshot``——不读 DB、不打网络、不依赖时钟。
  IO 边界只发生在 ``fetch_intraday_pool``，它是 ``EastmoneyClient.fetch_limit_up_pool``
  的薄封装。其它逻辑单元/集成测试在 fixture 上跑即可。
- **同构输出**——shape 与 ``limit_up_service.get_snapshot`` 对齐：消费侧
  （api/v1/market_data.py、Task 11/12/13）能用 ``LimitUpLadderOut`` /
  ``SectorLimitUpOut`` / ``YesterdayLimitUpOut`` 同款解析路径同时吃下盘中
  与收盘两份快照，不引入第三套形状。
- **板块聚合按 ``board_name``**（东财 hybk）——SW L3 路径在收盘侧由
  ``limit_up_calculator.sector_ladder`` 完成；盘中无 SW 映射，先按东财板块名
  兜底，Task 14 会切到东财 BK 体系。
- **空池有 shape**——返回 dict 而非抛错；``degraded_reason="no_limit_up_rows"``。
  消费方按"as_of 仍为入参、echelons 为空"理解即可。
- **盘中无 '昨日→今日' 语义**——``yesterday=None``；``market_days=[as_of]``；
  ``breadth`` 用池行数 + 0/0（无 daily_quotes partial day 概念）。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.core.providers.eastmoney_client import get_eastmoney_client


def build_intraday_snapshot(
    pool_rows: list[dict[str, Any]],
    *,
    as_of: date,
    captured_at: datetime,
) -> dict[str, Any]:
    """东财涨停池 → 盘中 snapshot（同构于 ``limit_up_service.get_snapshot``）。

    Parameters
    ----------
    pool_rows
        ``EastmoneyClient.fetch_limit_up_pool`` 的输出（``_map_zt_pool_row`` 之后
        的归一字段：``symbol / name / streak / days / boards / seal_time /
        seal_fund / break_count / board_name / amount``）。空列表不抛错。
    as_of
        数据描述的日期（盘中恒为今日）。不取 ``captured_at.date()``——若
        ``captured_at`` 跨日（23:30 截到次日 0:05），as_of 仍应钉死为"今天"，
        避免半日数据被错误地标成次日。
    captured_at
        实际抓取时间戳；用于渲染 ``as_of_label``。
    """
    as_of_label = f"盘中 {captured_at:%H:%M}"
    market_days = [as_of]

    if not pool_rows:
        return _empty(as_of, as_of_label, market_days, reason="no_limit_up_rows")

    echelons, max_streak = _buckets_by_streak(pool_rows)
    sectors = _sectors_by_board_name(pool_rows)
    kpis = _kpis(pool_rows, max_streak)

    return {
        "as_of": as_of,
        "as_of_prev": None,  # 盘中无"昨日→今日"语义
        "as_of_quality": "partial",  # 盘中是 partial day（未收盘）
        "source": "eastmoney_intraday",
        "limits_present": True,
        "is_partial": False,  # 盘中 partial day 概念在收盘侧存在；盘中按完整源处理
        "sw_coverage": None,  # 盘中无 SW 映射
        "degraded_reason": None,
        "market_days": market_days,
        "breadth": {
            "zt_count": len(pool_rows),
            "dt_count": 0,
            "zb_count": 0,
            "quoted": len(pool_rows),
        },
        "kpis": kpis,
        "echelons": echelons,
        "sectors": sectors,
        "yesterday": None,  # 盘中无"昨日→今日"语义
        "as_of_label": as_of_label,
    }


async def fetch_intraday_pool(trade_date: str) -> list[dict[str, Any]]:
    """东财涨停池抓取（薄封装 ``EastmoneyClient.fetch_limit_up_pool``）。

    任何 IO 失败都向上抛（不为方便测试就吞掉）——Task 11 的端点会显式 try/except
    并在 ``as_of_label`` 标注回落。
    """
    return await get_eastmoney_client().fetch_limit_up_pool(trade_date)


# ----- 内部实现：纯函数（便于单测覆盖）---------------------------------------------


def _empty(
    as_of: date,
    as_of_label: str,
    market_days: list[date],
    *,
    reason: str,
) -> dict[str, Any]:
    return {
        "as_of": as_of,
        "as_of_prev": None,
        "as_of_quality": "partial",
        "source": "eastmoney_intraday",
        "limits_present": False,
        "is_partial": False,
        "sw_coverage": None,
        "degraded_reason": reason,
        "market_days": market_days,
        "breadth": {"zt_count": 0, "dt_count": 0, "zb_count": 0, "quoted": 0},
        "kpis": _empty_kpis(),
        "echelons": [],
        "sectors": {"items": [], "unclassified_count": 0},
        "yesterday": None,
        "as_of_label": as_of_label,
    }


def _empty_kpis() -> dict[str, Any]:
    """与 ``limit_up_calculator.sentiment_kpis`` 同键；空池时无任何统计量。"""
    return {
        "zt_count": 0,
        "dt_count": 0,
        "zb_count": 0,
        "broken_rate": None,
        "yzt_avg_pct": None,
        "yzt_avg_open_premium": None,
        "yzt_n": 0,
        "promo_1to2": None,
        "promo_1to2_n": 0,
        "promo_1to2_noisy": True,
        "promo_2to3": None,
        "promo_2to3_n": 0,
        "promo_2to3_noisy": True,
        "max_streak": 0,
    }


def _buckets_by_streak(pool_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """档位 = streak 分组，label 形如 ``"{streak}连板"``，按 streak 降序。

    每档内按 amount DESC、symbol ASC 排序（与收盘侧 ladder 裁决一致），
    保证"两次相同请求 → 完全相同 echelons"，可观测、可测。
    """
    buckets: dict[int, list[dict[str, Any]]] = {}
    for row in pool_rows:
        streak = int(row.get("streak") or 0)
        if streak <= 0:
            # 池里理论上不该出现 streak=0（涨停池），但防御性归 1 档
            streak = 1
        buckets.setdefault(streak, []).append(row)
    out: list[dict[str, Any]] = []
    for streak in sorted(buckets, reverse=True):
        stocks = sorted(
            buckets[streak],
            key=lambda r: (-float(r.get("amount") or 0.0), r["symbol"]),
        )
        out.append(
            {
                "streak": streak,
                "label": f"{streak}连板",
                "stocks": [_stock_out(r) for r in stocks],
            }
        )
    max_streak = max(int(r.get("streak") or 0) for r in pool_rows) if pool_rows else 0
    return out, max_streak


def _stock_out(row: dict[str, Any]) -> dict[str, Any]:
    """池行 → 档位 stock dict。

    收盘路径的 ladder stock 字段是"按 windows 内 11 列自算出来的"；
    盘中无窗口语义，无法给 days_span/boards_in_window/missing_days——置 None。
    sw_l1/sw_l3 同样无（东财 hybk 体系非 SW 体系），置 None。
    """
    return {
        "stock_id": None,  # 盘中无 stock_id（池是 symbol 维度，不映射内部 id）
        "symbol": row["symbol"],
        "name": row.get("name"),
        "streak": int(row.get("streak") or 0),
        "days_span": None,
        "boards_in_window": None,
        "missing_days": None,
        "sw_l1_name": None,
        "sw_l3_name": None,
        "seal_time": row.get("seal_time"),
        "seal_fund": row.get("seal_fund"),
        "break_count": row.get("break_count"),
    }


def _sectors_by_board_name(pool_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """按东财 ``board_name`` 聚合；Task 14 切换到东财 BK 体系时**仅**改此处。

    龙头裁决与收盘侧一致：``streak DESC, amount DESC, symbol ASC``。
    同一 board_name 缺位的行（防御性，不应出现）→ ``unclassified_count += 1``。
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    unclassified = 0
    for r in pool_rows:
        name = r.get("board_name")
        if not name:
            unclassified += 1
            continue
        groups.setdefault(name, []).append(r)

    items: list[dict[str, Any]] = []
    for name, members in groups.items():
        leader = min(
            members,
            key=lambda r: (
                -int(r.get("streak") or 0),
                -float(r.get("amount") or 0.0),
                r["symbol"],
            ),
        )
        items.append(
            {
                # 收盘侧的 SectorLimitUpItemOut 用 l3_code/l3_name/l1_code/l1_name
                # 四个 SW 字段；盘中无 SW 体系，置 None 并把 board_name 作为主键。
                "l3_code": None,
                "l3_name": None,
                "l1_code": None,
                "l1_name": None,
                "board_name": name,
                "max_streak": int(leader.get("streak") or 0),
                "leader_symbol": leader["symbol"],
                "leader_name": leader.get("name"),
                "leader_streak": int(leader.get("streak") or 0),
                "zt_count": len(members),
            }
        )
    items.sort(key=lambda i: (-i["max_streak"], -i["zt_count"], i["board_name"] or ""))
    return {"items": items, "unclassified_count": unclassified}


def _kpis(pool_rows: list[dict[str, Any]], max_streak: int) -> dict[str, Any]:
    """情绪温度计——盘中字段集与收盘对齐，但无 yzt/promo（无昨日基准）。"""
    zt = len(pool_rows)
    return {
        "zt_count": zt,
        "dt_count": 0,
        "zb_count": 0,
        "broken_rate": None,  # 盘中无炸板率（无 daily_quotes 的 zb 概念）
        "yzt_avg_pct": None,  # 盘中无"昨日涨停今日表现"语义
        "yzt_avg_open_premium": None,
        "yzt_n": 0,
        "promo_1to2": None,  # 同上
        "promo_1to2_n": 0,
        "promo_1to2_noisy": True,
        "promo_2to3": None,
        "promo_2to3_n": 0,
        "promo_2to3_noisy": True,
        "max_streak": max_streak,
    }
