"""Import Shenwan industry classification into DB.

Data sources (checked in priority order):
1. ``backend/data/sw_seed.sql`` — pre-generated SQL dump (fastest, no XLS deps)
2. XLS/XLSX files in ``docs/references/sw/SwClass/`` (first-time bootstrap)

After a successful XLS import the service automatically exports a SQL seed
file so that subsequent deployments can skip the XLS parsing step entirely.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.database import async_session_factory
from app.models.sw_industry import StockCustomSwTag, SwIndustryClass, SwIndustryMember

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

_BACKEND_ROOT = Path(__file__).resolve().parents[2]  # /app in Docker, .../backend on host
SW_SQL_FILE = _BACKEND_ROOT / "data" / "sw_seed.sql"
CUSTOM_TAGS_SQL_FILE = _BACKEND_ROOT / "data" / "sw_custom_tags_seed.sql"


def _resolve_sw_data_dir() -> Path:
    if settings.sw_data_dir:
        return Path(settings.sw_data_dir)
    return Path(__file__).resolve().parents[3] / "docs" / "references" / "sw" / "SwClass"


_SW_DATA_DIR = _resolve_sw_data_dir()
SW_CLASS_FILE = _SW_DATA_DIR / "SwClassCode_2021.xls"
SW_MEMBER_FILE = _SW_DATA_DIR / "最新个股行业分类.xlsx"


def _sql_escape(value: str) -> str:
    return value.replace("'", "''")


# ---------------------------------------------------------------------------
# SQL seed export
# ---------------------------------------------------------------------------


async def export_sw_to_sql(dest: Path | None = None) -> Path:
    """Export current DB data to a self-contained SQL seed file."""
    dest = dest or SW_SQL_FILE

    async with async_session_factory() as db:
        classes = (
            (await db.execute(select(SwIndustryClass).order_by(SwIndustryClass.industry_code)))
            .scalars()
            .all()
        )
        members = (
            (
                await db.execute(
                    select(SwIndustryMember).order_by(
                        SwIndustryMember.industry_code, SwIndustryMember.stock_code
                    )
                )
            )
            .scalars()
            .all()
        )

    if not classes:
        raise RuntimeError("No SW classification data in DB to export")

    lines: list[str] = [
        "-- Shenwan industry classification seed data",
        f"-- {len(classes)} classification nodes, {len(members)} member mappings",
        "-- Auto-generated — do not edit manually",
        "",
        "DELETE FROM sw_industry_members;",
        "DELETE FROM sw_industry_classes;",
        "",
    ]

    # --- classes ---
    batch_size = 200
    for i in range(0, len(classes), batch_size):
        batch = classes[i : i + batch_size]
        lines.append(
            "INSERT INTO sw_industry_classes "
            "(industry_code, level, industry_name, parent_code) VALUES"
        )
        value_lines: list[str] = []
        for cls in batch:
            pc = f"'{cls.parent_code}'" if cls.parent_code else "NULL"
            value_lines.append(
                f"  ('{cls.industry_code}', {cls.level}, '{_sql_escape(cls.industry_name)}', {pc})"
            )
        lines.append(",\n".join(value_lines) + ";")
        lines.append("")

    # --- members ---
    for i in range(0, len(members), batch_size):
        member_batch = members[i : i + batch_size]
        lines.append(
            "INSERT INTO sw_industry_members (industry_code, stock_code, symbol, stock_name) VALUES"
        )
        value_lines = []
        for m in member_batch:
            name = _sql_escape(m.stock_name) if m.stock_name else ""
            value_lines.append(f"  ('{m.industry_code}', '{m.stock_code}', '{m.symbol}', '{name}')")
        lines.append(",\n".join(value_lines) + ";")
        lines.append("")

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Exported SW seed SQL to %s (%d bytes)", dest, dest.stat().st_size)
    return dest


# ---------------------------------------------------------------------------
# SQL seed import
# ---------------------------------------------------------------------------


def _split_sql_statements(sql: str) -> list[str]:
    """Split a simple SQL script into executable statements.

    先按 ``;`` 切块，再对每块逐行剔除 ``--`` 注释（既覆盖注释独占行，也覆盖
    分号之后的行尾注释，如 ``INSERT ...; -- 说明``）——不先剥离注释会连
    INSERT 一起丢掉（曾致 custom-tag overlay 导入恒为 0 行），行尾注释则会被
    当作下一条语句执行报语法错。

    限制：不支持字符串字面量内部的分号（如 ``('a;b')``）——本仓库 seed 均为
    代码/数字值；若未来出现含分号的字符串值，需改用真正的 SQL parser。
    """
    statements: list[str] = []
    for chunk in sql.split(";"):
        kept = [ln for ln in chunk.splitlines() if not ln.strip().startswith("--")]
        stmt = "\n".join(kept).strip()
        if stmt:
            statements.append(stmt)
    return statements


async def _execute_sql_script(db: AsyncSession, sql: str) -> None:
    for stmt in _split_sql_statements(sql):
        await db.execute(text(stmt))


async def import_sw_from_sql(src: Path | None = None) -> dict[str, int]:
    """Import SW data from a pre-generated SQL seed file."""
    src = src or SW_SQL_FILE
    if not src.exists():
        return {"classes": 0, "members": 0}

    sql = src.read_text(encoding="utf-8")
    if not sql.strip():
        return {"classes": 0, "members": 0}

    async with async_session_factory() as db:
        await _execute_sql_script(db, sql)
        await db.commit()

        class_count = (await db.execute(select(SwIndustryClass.id))).scalars().all()
        member_count = (await db.execute(select(SwIndustryMember.id))).scalars().all()

    result = {"classes": len(class_count), "members": len(member_count)}
    logger.info("Imported SW data from SQL seed: %s", result)
    return result


async def import_custom_tags_from_sql(src: Path | None = None) -> int:
    """Import the curated custom-tag overlay seed (additive — user tags preserved).

    The overlay carries the 2026-09-03 OTHER→SW merge (1439 rows). Statements are
    INSERT ... ON CONFLICT DO NOTHING, so re-runs and user-added tags are safe.
    """
    src = src or CUSTOM_TAGS_SQL_FILE
    if not src.exists():
        return 0
    sql = src.read_text(encoding="utf-8")
    if not sql.strip():
        return 0

    async with async_session_factory() as db:
        before = len((await db.execute(select(StockCustomSwTag.id))).scalars().all())
        await _execute_sql_script(db, sql)
        await db.commit()
        after = len((await db.execute(select(StockCustomSwTag.id))).scalars().all())

    # 打印"表内总数（本次新增）"：只报表计数会被误读成本次导入量（曾据此把
    # 全表 0 行误判为"导入无效果"而延长排障）
    logger.info(
        "custom-tag overlay applied: table now has %d rows (%+d this run)", after, after - before
    )
    return after


# ---------------------------------------------------------------------------
# XLS import (original path)
# ---------------------------------------------------------------------------


async def import_sw_classification(db: AsyncSession | None = None) -> int:
    """Parse SwClassCode_2021.xls and upsert into sw_industry_classes."""
    from app.services.sw_xls_parser import parse_sw_classes  # noqa: PLC0415

    if not SW_CLASS_FILE.exists():
        logger.warning("SW class file not found: %s", SW_CLASS_FILE)
        return 0

    rows = parse_sw_classes(SW_CLASS_FILE)
    if not rows:
        return 0

    async def _do(session: AsyncSession) -> int:
        for row in rows:
            stmt = (
                pg_insert(SwIndustryClass)
                .values(
                    industry_code=row["industry_code"],
                    level=row["level"],
                    industry_name=row["industry_name"],
                    parent_code=row["parent_code"],
                )
                .on_conflict_do_update(
                    constraint="uq_sw_class_code",
                    set_={
                        "level": row["level"],
                        "industry_name": row["industry_name"],
                        "parent_code": row["parent_code"],
                    },
                )
            )
            await session.execute(stmt)
        await session.commit()
        return len(rows)

    if db:
        count = await _do(db)
    else:
        async with async_session_factory() as session:
            count = await _do(session)

    logger.info("Imported %d SW industry classification nodes", count)
    return count


async def import_sw_members(db: AsyncSession | None = None) -> int:
    """Parse 最新个股行业分类.xlsx and upsert into sw_industry_members."""
    from app.services.sw_xls_parser import parse_sw_members  # noqa: PLC0415

    if not SW_MEMBER_FILE.exists():
        logger.warning("SW member file not found: %s", SW_MEMBER_FILE)
        return 0

    rows = parse_sw_members(SW_MEMBER_FILE)
    if not rows:
        return 0

    async def _do(session: AsyncSession) -> int:
        await session.execute(delete(SwIndustryMember))

        batch_size = 500
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            values = [
                {
                    "industry_code": row["industry_code"],
                    "stock_code": row["stock_code"],
                    "symbol": row["symbol"],
                    "stock_name": row["stock_name"],
                }
                for row in batch
            ]
            await session.execute(pg_insert(SwIndustryMember).values(values))

        await session.commit()
        return len(rows)

    if db:
        count = await _do(db)
    else:
        async with async_session_factory() as session:
            count = await _do(session)

    logger.info("Imported %d SW industry member mappings", count)
    return count


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def import_all() -> dict[str, int]:
    """Import SW data using the fastest available source.

    Priority: SQL seed file > XLS/XLSX files.
    After a successful XLS import, auto-exports a SQL seed for next time.
    """
    # 1) Try SQL seed first
    if SW_SQL_FILE.exists():
        logger.info("SW seed SQL found at %s — importing from SQL", SW_SQL_FILE)
        result = await import_sw_from_sql()
        try:
            await import_custom_tags_from_sql()
        except Exception:
            logger.warning("Failed to import custom-tag overlay seed", exc_info=True)
        return result

    # 2) Fall back to XLS parsing
    logger.info("No SW seed SQL — parsing XLS files")
    class_count = await import_sw_classification()
    member_count = await import_sw_members()
    result = {"classes": class_count, "members": member_count}

    # 3) Auto-export SQL for future deployments
    if class_count > 0:
        try:
            await export_sw_to_sql()
        except Exception:
            logger.warning("Failed to auto-export SW seed SQL", exc_info=True)

    try:
        await import_custom_tags_from_sql()
    except Exception:
        logger.warning("Failed to import custom-tag overlay seed", exc_info=True)

    return result


async def is_sw_data_loaded() -> bool:
    """Check if SW classification data already exists in DB."""
    async with async_session_factory() as db:
        count = (await db.execute(select(SwIndustryClass.id).limit(1))).scalar_one_or_none()
    return count is not None
