"""概念板块成分采集：东财全量板块 → 逐板成分 → 当前态 + 差分变更。

口径见 plans/2026-09-18-concept-boards-and-new-stocks.md §4；三条不可退让的约束：

1. **完整性门禁先于写入**：单板 `len(members) < board["member_total"]` 时判该板 partial，
   **不调 `upsert_members`** —— 差分只看"本轮 seen 集合 vs 库内当前态"，少一页会被解读成
   "这些成分退出了板块"，从而删成员 + 写入幻影 remove 变更行。T3 的空 `seen` 守卫只在整板
   0 行时兜底，抓 61/63 这种"半截页"它看不出来。`member_total is None` 无法判完整性：仍采集
   （客户端空页护栏已跑过），但计入 `partial_boards` 让"无法对账"可见而非静默。
2. **单板失败隔离**：任一客户端/仓库异常只计入 `failed_boards` 并跳过该板，绝不删除既有成分、
   绝不写变更行。板块列表本身抓取失败则向上抛（T5 的调度任务负责记录）——**不得**在那种情况下
   调用 `deactivate_missing_boards`（空集合等于"全部下架"，无回滚手段）。
3. **一次映射、不做交易所推断**：全 run 只用一条 `symbol_to_stock_ids` 建 `symbol → stocks.id`
   映射（`stocks` 名录会滞后），未命中的成员 `stock_id` 留 NULL 并计入 `unresolved`
   （见 docs/references/best-practices.md「源表被别的管道当映射表」条）。

本模块的 repo 函数一律以模块属性调用（`concept_repo.<fn>`），T4 测试据此 monkeypatch。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from app.repositories import concept_repo

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_SH = ZoneInfo("Asia/Shanghai")


def _today_sh() -> date:
    """与全系统一致的上海日期（容器 TZ 未设，直接 fromtimestamp 会落 UTC 语义）。"""
    return datetime.now(_SH).date()


def _get_eastmoney() -> Any:
    from app.core.providers.eastmoney_client import get_eastmoney_client  # noqa: PLC0415

    return get_eastmoney_client()


def _valid_members(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """成分行归一为 `{symbol, stock_name}`；缺 symbol/name 的脏行丢弃。

    非空列会让整板 INSERT 失败，故脏行一律不进 `upsert_members`。

    丢弃的行不进 `unresolved`：那是"名录滞后"的度量，不是"源数据脏"的度量。
    """
    return [
        {"symbol": str(m["symbol"]), "stock_name": str(m["name"])}
        for m in raw
        if m.get("symbol") and m.get("name")
    ]


async def ingest_concept_members(db: AsyncSession) -> dict[str, int]:
    """东财概念板块名录 + 全量成分 → `concept_boards` / `concept_members` / 变更表（不 commit）。

    返回 `{boards, members_upserted, added, removed, failed_boards, partial_boards, unresolved}`：
    `members_upserted = added + updated`（实际写入的行数，删除不算）；`unresolved` 只统计**本轮
    真正入库的板块**的成员，partial/failed 板未写入故不计入，避免门禁拦截把指标虚高。
    调用方（T5 的 job/手动入口）负责 `db.commit()`。
    """
    em = _get_eastmoney()
    today = _today_sh()

    # 列表抓不到 → 直接抛：空列表必须绝不被当成"全部下架"。
    boards = await em.fetch_concept_boards()
    await concept_repo.upsert_boards(db, boards, today)

    failed_boards = 0
    partial_boards = 0
    pending: list[tuple[str, list[dict[str, Any]]]] = []
    for board in boards:
        code = str(board["board_code"])
        try:
            members = await em.fetch_concept_members(code)
        except Exception:
            failed_boards += 1
            logger.warning("concept board %s members fetch failed, skipped", code, exc_info=True)
            continue
        total = board.get("member_total")
        if total is None:
            # 无法对账：仍入库（客户端空页护栏已跑过），但让"无 total"可见。
            partial_boards += 1
            logger.warning(
                "concept board %s member_total unknown, ingested without completeness check "
                "(fetched=%d)",
                code,
                len(members),
            )
        elif len(members) < total:
            # 半截页绝不能进差分：会删成员 + 写幻影 remove。
            partial_boards += 1
            logger.warning(
                "concept board %s incomplete: fetched=%d < member_total=%d, skipped",
                code,
                len(members),
                total,
            )
            continue
        pending.append((code, _valid_members(members)))

    # 全 run 一次映射（不做交易所推断；stocks 名录滞后由 unresolved 暴露）。
    # `unresolved` 按**行**计（同一未收录 symbol 出现在 N 个板块算 N 次），与
    # `select count(*) from concept_members where stock_id is null` 逐一对账。
    symbols = [row["symbol"] for _, rows in pending for row in rows]
    stock_ids = await concept_repo.symbol_to_stock_ids(db, symbols)
    unresolved = sum(1 for symbol in symbols if symbol not in stock_ids)

    added = updated = removed = 0
    for code, rows in pending:
        for row in rows:
            # 行内带上预解析 id（T4 契约）；T3 的 upsert_members 目前仍自行再映射一次，
            # 结果同源、幂等，多出的是 504 条走 idx_stocks_symbol 的点查（可接受）。
            row["stock_id"] = stock_ids.get(row["symbol"])
        try:
            board_added, board_updated, board_removed = await concept_repo.upsert_members(
                db, code, rows, today
            )
        except Exception:
            failed_boards += 1
            logger.warning("concept board %s members write failed, skipped", code, exc_info=True)
            continue
        added += board_added
        updated += board_updated
        removed += board_removed

    await concept_repo.deactivate_missing_boards(db, {str(b["board_code"]) for b in boards})

    members_upserted = added + updated
    result = {
        "boards": len(boards),
        "members_upserted": members_upserted,
        "added": added,
        "removed": removed,
        "failed_boards": failed_boards,
        "partial_boards": partial_boards,
        "unresolved": unresolved,
    }
    log_line = (
        "CONCEPT_INGEST boards=%d members=%d added=%d removed=%d failed=%d partial=%d unresolved=%d"
    )
    logger.info(
        log_line,
        result["boards"],
        members_upserted,
        added,
        removed,
        failed_boards,
        partial_boards,
        unresolved,
    )
    if failed_boards or partial_boards or unresolved:
        # "把 skipped 拆出来并升 warning"：只在有退化时再打一条，指向同一组数字。
        logger.warning(
            log_line,
            result["boards"],
            members_upserted,
            added,
            removed,
            failed_boards,
            partial_boards,
            unresolved,
        )
    return result
