"""概念板块成分采集：东财全量板块 → 逐板成分 → 当前态 + 差分变更。

口径见 plans/2026-09-18-concept-boards-and-new-stocks.md §4；三条不可退让的约束：

1. **完整性门禁先于写入**：单板先剔脏行（缺 symbol/name）再用**清洗后**的行数与
   `board["member_total"]` 对账；`len(valid) < member_total` 或出现任何脏行时判该板 partial，
   **不调 `upsert_members`** —— 差分只看"本轮 seen 集合 vs 库内当前态"，少一页/少一行会被解读成
   "这些成分退出了板块"，从而删成员 + 写入幻影 remove 变更行（append-only，无法回滚）。T3 的空
   `seen` 守卫只在整板 0 行时兜底，抓 61/63 或 62 valid + 1 脏行这种"半截/缩水集合"它看不出来。
   `member_total is None` 无法判完整性：仍采集（客户端空页护栏已跑过），但计入 `partial_boards`
   让"无法对账"可见而非静默。
2. **单板失败隔离**：任一客户端/仓库异常只计入 `failed_boards` 并跳过该板，绝不删除既有成分、
   绝不写变更行。板块列表本身抓取失败则向上抛（T5 的调度任务负责记录）；列表为空同样不会造成
   "全部下架"——T3 的 `deactivate_missing_boards` 对空 set 是文档化 no-op（见其 docstring），
   但空列表意味着整轮静默落空，故单独打 WARNING 让它可见。
3. **一次映射、不做交易所推断**：全 run 只用一条 `symbol_to_stock_ids` 建 `symbol → stocks.id`
   映射（`stocks` 名录会滞后），未命中的成员 `stock_id` 留 NULL 并计入 `unresolved`
   （见 docs/references/best-practices.md「源表被别的管道当映射表」条）。
4. **本 run 行不带 `stock_id`**：`concept_repo.upsert_members` 内部自行 `symbol_to_stock_ids`
   再解析一次（T3 权威），故服务层预解析并塞回行内是死代码，已删除；这里保留的这一次映射
   只服务 `unresolved` 指标。重复的点查走 `idx_stocks_symbol`，5 分钟节流下可接受
   （T3 变更，不在 T4 范围内）。

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

    丢弃的行不进 `unresolved`：那是"名录滞后"的度量，不是"源数据脏"的度量。它们单独计入
    返回值的 `dropped_members`，并且**整板**会被判 partial 跳过（见 `ingest_concept_members`）。
    """
    return [
        {"symbol": str(m["symbol"]), "stock_name": str(m["name"])}
        for m in raw
        if m.get("symbol") and m.get("name")
    ]


async def ingest_concept_members(db: AsyncSession) -> dict[str, int]:
    """东财概念板块名录 + 全量成分 → `concept_boards` / `concept_members` / 变更表（不 commit）。

    返回 `{boards, members_upserted, added, removed, failed_boards, partial_boards, unresolved,
    dropped_members}`：`members_upserted = added + updated`（实际写入的行数，删除不算）；
    `unresolved` 只统计**本轮真正入库的板块**的成员，partial/failed 板未写入故不计入，避免门禁
    拦截把指标虚高；`dropped_members` 是本轮源数据里缺 symbol/name 的脏行数（见 `_valid_members`）。
    调用方（T5 的 job/手动入口）负责 `db.commit()`。
    """
    em = _get_eastmoney()
    today = _today_sh()

    # 列表抓不到 → 直接抛：空列表必须绝不被当成"全部下架"。
    boards = await em.fetch_concept_boards()
    if not boards:
        # 东财可以 rc=0 + 空 data：这不是失败，但等于整轮空转，必须让它可见。
        logger.warning(
            "CONCEPT_INGEST board list empty; nothing ingested "
            "(deactivate_missing_boards is a no-op on empty set)"
        )
    await concept_repo.upsert_boards(db, boards, today)

    failed_boards = 0
    partial_boards = 0
    dropped_members = 0
    pending: list[tuple[str, list[dict[str, Any]]]] = []
    for board in boards:
        code = str(board["board_code"])
        try:
            members = await em.fetch_concept_members(code)
        except Exception:
            failed_boards += 1
            logger.warning("concept board %s members fetch failed, skipped", code, exc_info=True)
            continue
        # 脏行先剔除再对账：缺 symbol/name 的行无法 INSERT，且会让 `seen` 少一个仍在该板的成员
        # → 差分把它写成"退出板块"（幻影 remove）。故见脏行即整板 partial 跳过，用清洗后的
        # 行数对账（62 valid + 1 脏 = 63 total 这种"刚好凑数"必须判 partial）。
        valid = _valid_members(members)
        dropped = len(members) - len(valid)
        if dropped:
            dropped_members += dropped
            partial_boards += 1
            logger.warning(
                "concept board %s has %d dirty member row(s) (fetched=%d valid=%d), skipped",
                code,
                dropped,
                len(members),
                len(valid),
            )
            continue
        total = board.get("member_total")
        if total is None:
            # 无法对账：仍入库（客户端空页护栏已跑过），但让"无 total"可见。
            partial_boards += 1
            logger.warning(
                "concept board %s member_total unknown, ingested without completeness check "
                "(fetched=%d)",
                code,
                len(valid),
            )
        elif len(valid) < total:
            # 半截页绝不能进差分：会删成员 + 写幻影 remove。
            partial_boards += 1
            logger.warning(
                "concept board %s incomplete: fetched=%d < member_total=%d, skipped",
                code,
                len(valid),
                total,
            )
            continue
        pending.append((code, valid))

    # 全 run 一次映射（不做交易所推断；stocks 名录滞后由 unresolved 暴露）。
    # `unresolved` 按**行**计（同一未收录 symbol 出现在 N 个板块算 N 次），与
    # `select count(*) from concept_members where stock_id is null` 逐一对账。
    # 不把结果塞回行内：`upsert_members` 会自行再解析（T3 权威），塞了也是死字段。
    symbols = [row["symbol"] for _, rows in pending for row in rows]
    stock_ids = await concept_repo.symbol_to_stock_ids(db, symbols)
    unresolved = sum(1 for symbol in symbols if symbol not in stock_ids)

    added = updated = removed = 0
    for code, rows in pending:
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
        "dropped_members": dropped_members,
    }
    log_line = (
        "CONCEPT_INGEST boards=%d members=%d added=%d removed=%d failed=%d partial=%d "
        "unresolved=%d dropped=%d"
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
        dropped_members,
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
            dropped_members,
        )
    return result
