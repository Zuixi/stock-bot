"""S1.4 · SCD2 申万成分归属的 ingest（上游 TuShare ``index_member``）。

**为什么是 SCD2 而不是快照**：spec §S1.4 要求"任取一个历史交易日，按当时的分类归属
还原成分股"。上游 ``index_member`` 实测**带 ``in_date`` / ``out_date``**
（见 ``docs/design/data-source.md`` 附.6 实机记录），故历史可**回填**——
不需要"从今天开始记"。

**两个数据面，别混用**：

* ``sw_industry_member_periods``（本模块写入）= SCD2，**历史归属的唯一真相**；
* ``sw_industry_members``（``sw_industry_service`` 写入）= 当前态快照，整表替换式导入。

**幂等**：唯一键 ``(industry_code, stock_code, effective_from)`` 上
``ON CONFLICT DO UPDATE``，重跑同一份上游数据 → 新增行 0、仅 ``updated_at`` 变。

**诚实边界**（读面必须一并外露，见 ``market_service.get_sw_member_as_of``）：
上界 = 最近一次 ingest 的 ``as_of``；``effective_to IS NULL`` 的语义是
"截至该 ``as_of`` 仍在成分"，不是"截至今天"。
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sw_industry import SwIndustryClass, SwIndustryMemberPeriod, SwMemberIngestRun

logger = logging.getLogger(__name__)

# 上游 index_member 的返回列（实测 2026-10-18）：index_code/con_code/in_date/out_date/is_new
_MEMBER_COLUMNS = ("index_code", "con_code", "in_date", "out_date", "is_new")

# backend/ 目录（`app/services/x.py` 的 parents[2]）；与 sw_industry_service 的 _BACKEND_ROOT 同口径
_BACKEND_ROOT = Path(__file__).resolve().parents[2]

# 行业代码 → 上游指数代码 的对照表（随镜像发布的生成物）。
# 权威来源 = docs/references/sw/申万行业分类.md；生成脚本 = scripts/gen_sw_index_mapping.py。
# 为什么不运行时读 md：后端镜像 `COPY backend/` 后没有仓库根的 docs/（实测）。
_SW_MAPPING_FILE = _BACKEND_ROOT / "data" / "sw_index_mapping.csv"


@dataclass(frozen=True)
class SwIndexRef:
    """一行「本地行业代码 ↔ 上游指数代码」对照。"""

    industry_code: str
    index_code: str
    level: int
    industry_name: str


def parse_sw_index_mapping(path: Path | None = None) -> list[SwIndexRef]:
    """解析 ``data/sw_index_mapping.csv`` → ``SwIndexRef`` 列表。

    实测（2026-10-18，生成自 ``申万行业分类.md``）：511 行 = L1 31 + L2 134 + L3 346，
    ``industry_code`` 与 ``index_code`` 各自唯一。以 ``#`` 开头的注释行跳过。
    """
    src = path or _SW_MAPPING_FILE
    refs: list[SwIndexRef] = []
    with src.open(encoding="utf-8", newline="") as fh:
        lines = [ln for ln in fh if not ln.startswith("#")]
    for record in csv.DictReader(lines):
        industry_code = (record.get("industry_code") or "").strip()
        index_code = (record.get("index_code") or "").strip()
        raw_level = (record.get("level") or "").strip()
        if not industry_code or not index_code or not raw_level.isdigit():
            continue
        refs.append(
            SwIndexRef(
                industry_code=industry_code,
                index_code=index_code,
                level=int(raw_level),
                industry_name=(record.get("industry_name") or "").strip(),
            )
        )
    return refs


def _to_date(value: object) -> date | None:
    """上游日期一律 ``YYYYMMDD`` 字符串；空值 / NaN / 空串 → None。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "nat"}:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return datetime.strptime(text, "%Y%m%d").date()


def normalize_member_rows(df: pd.DataFrame, ref: SwIndexRef) -> list[dict[str, object]]:
    """纯函数：上游 DataFrame → 待 upsert 的 SCD2 行。

    ``in_date`` 缺失的行**丢弃**（``effective_from`` 必填，无起点就无法表达区间）；
    ``is_current`` 取上游 ``is_new == 'Y'``，与 ``out_date`` 是否为空**分别**保留
    （上游实测存在 ``is_new='Y'`` 但 ``out_date`` 非空的矛盾行，见报告：本函数不替
    上游"修数据"，只如实落库并在 ``detail`` 里计数）。
    """
    if df.empty:
        return []
    missing = [c for c in _MEMBER_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"index_member 返回缺列 {missing}（实测列 {list(df.columns)}）")
    rows: list[dict[str, object]] = []
    for record in df.to_dict("records"):
        effective_from = _to_date(record.get("in_date"))
        if effective_from is None:
            continue
        symbol = str(record["con_code"]).split(".")[0]
        rows.append(
            {
                "industry_code": ref.industry_code,
                "stock_code": str(record["con_code"]),
                "symbol": symbol,
                "effective_from": effective_from,
                "effective_to": _to_date(record.get("out_date")),
                "is_current": str(record.get("is_new", "")).strip().upper() == "Y",
                "source": "tushare:index_member",
            }
        )
    return rows


async def upsert_period_rows(db: AsyncSession, rows: list[dict[str, object]]) -> int:
    """唯一键 ``(industry_code, stock_code, effective_from)`` 上幂等 upsert。

    只更新 ``effective_to`` / ``is_current`` / ``updated_at``——``industry_code``、
    ``stock_code``、``effective_from`` 是键的一部分，不允许被"改到别的键上"。
    返回**本次真正写入的行数**（重跑同一份数据时该值仍为 len(rows) 但 ``xmax=0``，
    即没有新行；"新增行 0"的机械判据见 tests）。
    """
    if not rows:
        return 0
    stmt = pg_insert(SwIndustryMemberPeriod).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_sw_member_period_code_stock_from",
        set_={
            "effective_to": stmt.excluded.effective_to,
            "is_current": stmt.excluded.is_current,
            "source": stmt.excluded.source,
            "updated_at": func.now(),
        },
    )
    await db.execute(stmt)
    return len(rows)


async def _stock_names(db: AsyncSession) -> dict[str, str]:
    """symbol → 股票名（来自 stocks 表；上游 index_member 不返回名称）。"""
    from app.models.stock import Stock  # noqa: PLC0415  # 避免模型导入环

    rows = (await db.execute(select(Stock.symbol, Stock.name))).all()
    return {str(sym): str(name) for sym, name in rows if name}


async def _class_codes(db: AsyncSession) -> set[str]:
    rows = (await db.execute(select(SwIndustryClass.industry_code))).scalars().all()
    return {str(c) for c in rows}


@dataclass
class SwMemberIngestResult:
    """一次 ingest 的结果（也用于报告里的覆盖率证据）。"""

    as_of: date
    levels: tuple[int, ...]
    index_codes: int
    rows_upserted: int
    failed: dict[str, str]
    per_level_rows: dict[int, int]


def _as_of() -> date:
    """ingest 的"快照日"= 运行当日（上海时区）。

    上游 ``index_member`` 是**当前态**接口（不接受 ``trade_date``；实测传
    ``start_date``/``end_date`` 不改变返回），故"我们掌握到哪一天"只能由运行日表达。
    """
    from app.services.market_data_service import _today_sh  # noqa: PLC0415

    return _today_sh()


async def ingest_sw_member_periods(
    db: AsyncSession,
    *,
    levels: tuple[int, ...] = (3,),
    limit: int | None = None,
    refs: list[SwIndexRef] | None = None,
) -> SwMemberIngestResult:
    """从上游拉取申万成分并按 SCD2 落库。

    ``levels``：默认只灌 **L3**（346 个指数）——上游对祖级指数返回的是**同一批成分的
    并集**（实测 801010.SI 与 800111.SI 的并集 = 其下 L3 的并集），L1/L2 的成分
    可由 L3 沿 ``parent_code`` 上卷（既有 ``market_service._SW_PERF_SQL`` 就是这个口径）。
    需要各层独立成分时显式传 ``levels=(1, 2, 3)``（会多打 ~165 次上游调用）。

    上游调用失败**不静默跳过**：失败的 ``index_code`` 收进 ``failed`` 并计数，
    且整次运行以 ``status='partial'`` 落 ``sw_member_ingest_runs``。
    """
    from app.core.providers.tushare_client import get_tushare_client  # noqa: PLC0415

    all_refs = refs if refs is not None else parse_sw_index_mapping()
    wanted = [r for r in all_refs if r.level in levels]
    if limit is not None:
        wanted = wanted[:limit]

    client = get_tushare_client()
    names = await _stock_names(db)
    known_codes = await _class_codes(db)

    as_of = _as_of()
    failed: dict[str, str] = {}
    per_level: dict[int, int] = {}
    total = 0
    for ref in wanted:
        if known_codes and ref.industry_code not in known_codes:
            # 对照表里有、但分类树里没有 → 不写孤儿行（读面 join 不到名字）
            failed[ref.index_code] = f"industry_code {ref.industry_code} 不在 sw_industry_classes"
            continue
        try:
            df = await client.fetch_index_member(index_code=ref.index_code)
        except Exception as exc:  # noqa: BLE001 — 逐指数记录，不让单个失败毁掉整批
            failed[ref.index_code] = f"{type(exc).__name__}: {exc}"
            logger.warning("index_member fetch failed for %s: %s", ref.index_code, exc)
            continue
        rows = normalize_member_rows(df, ref)
        for row in rows:
            row["stock_name"] = names.get(str(row["symbol"]))
        total += await upsert_period_rows(db, rows)
        per_level[ref.level] = per_level.get(ref.level, 0) + len(rows)

    status = "ok" if not failed else "partial"
    detail = None
    if failed:
        preview = "; ".join(f"{k}: {v}" for k, v in list(failed.items())[:3])
        detail = f"{len(failed)} 个指数失败：{preview}"[:255]
    db.add(
        SwMemberIngestRun(
            as_of=as_of,
            index_codes=len(wanted) - len(failed),
            rows_upserted=total,
            status=status,
            detail=detail,
        )
    )
    await db.commit()
    return SwMemberIngestResult(
        as_of=as_of,
        levels=levels,
        index_codes=len(wanted) - len(failed),
        rows_upserted=total,
        failed=failed,
        per_level_rows=per_level,
    )


async def get_member_as_of(db: AsyncSession) -> date | None:
    """最近一次**成功** ingest 的 ``as_of``（SCD2 还原能力的时间上界）。

    没有任何成功记录 → ``None``，读面必须据此**明确声明"尚无历史"**，
    不得把空结果说成"当日无成分"。
    """
    row = (
        await db.execute(
            select(func.max(SwMemberIngestRun.as_of)).where(SwMemberIngestRun.status == "ok")
        )
    ).scalar_one_or_none()
    return row
