"""S1.4 · SCD2 申万成分归属的 ingest（上游 TuShare ``index_member``）。

**为什么是 SCD2 而不是快照**：spec §S1.4 要求"任取一个历史交易日，按当时的分类归属
还原成分股"。上游 ``index_member`` 实测**带 ``in_date`` / ``out_date``**
（见 ``docs/design/data-source.md`` 附.6 实机记录），故历史可**回填**。

**两个数据面，别混用**：

* ``sw_industry_member_periods``（本模块写入）= SCD2，**历史归属的唯一真相**；
* ``sw_industry_members``（``sw_industry_service`` 写入）= 当前态快照，整表替换式导入。

**三级都要灌（Fix round 1 · 评审 C1 的关键修正）**：
原先默认只灌 L3，并假设"祖级指数返回其下全部成分的并集、L1/L2 可由 L3 上卷"。
**该假设实测为假**：31 个一级行业里有 **24 个** 的上游直取数 > 其下 L3 并集
（合计 upstream 5211 vs rollup 4950，−5.0%；农林牧渔 104 vs 80、钢铁 43 vs 28、
饲料 18 vs 11）。原因是上游存在**直接挂在 L1/L2 而来落在任何 L3 的成员**
（农林牧渔有 24 只这样的股票）。故 ``levels`` 默认 ``(1, 2, 3)``——读面按节点
**自身层级**取成员，只有该层级缺失时才退回上卷（见 ``sw_drilldown_service``）。

**幂等**：唯一键 ``(industry_code, stock_code, effective_from, effective_to)`` 上
``ON CONFLICT DO UPDATE``，重跑同一份上游数据 → 新增行 0、仅 ``updated_at`` 变。

**上界纪律（Fix round 1 · 评审 I1）**：
``limit`` 截断的运行、有任何指数拉取失败或下界断言未过的运行，一律
``fully_ingested=False`` / ``status`` ≠ ``ok``，**不得抬高读面的 ``scd2_as_of``**。
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sw_industry import SwIndustryClass, SwIndustryMemberPeriod, SwMemberIngestRun

logger = logging.getLogger(__name__)

# 上游 index_member 的返回列（实测 2026-10-12）：index_code/con_code/in_date/out_date/is_new
_MEMBER_COLUMNS = ("index_code", "con_code", "in_date", "out_date", "is_new")

# backend/ 目录（`app/services/x.py` 的 parents[2]）；与 sw_industry_service 的 _BACKEND_ROOT 同口径
_BACKEND_ROOT = Path(__file__).resolve().parents[2]

# 行业代码 → 上游指数代码 的对照表（随镜像发布的生成物）。
# 权威来源 = docs/references/sw/申万行业分类.md；生成脚本 = scripts/gen_sw_index_mapping.py。
# 为什么不运行时读 md：后端镜像 `COPY backend/` 后没有仓库根的 docs/（实测）。
_SW_MAPPING_FILE = _BACKEND_ROOT / "data" / "sw_index_mapping.csv"

# 默认灌全部三级（理由见模块 docstring：L1/L2 ≠ L3 并集）
DEFAULT_LEVELS: tuple[int, ...] = (1, 2, 3)

# 下界断言（Fix round 1 · 评审 I5）：本轮某层成员总数相对上一次 ok 运行的最大允许跌幅。
# 取 20% 是"明显塌方"的粗阈值，不是精度指标——它的用途是**让静默变少变成转红的记录**，
# 而不是做统计检验。跌破即 status='partial'，读面不再把该次运行当口径日。
_LEVEL_DROP_TOLERANCE = 0.20
# 单次运行允许的"上游返回 0 行"指数上限（对照表里有 71 个 L3 指数上游本来就无成员，
# 见 docs/design/data-source.md 附.6.2 #11；超过该值说明上游大面积异常）
_MAX_ZERO_ROW_RATIO = 0.30


@dataclass(frozen=True)
class SwIndexRef:
    """一行「本地行业代码 ↔ 上游指数代码」对照。"""

    industry_code: str
    index_code: str
    level: int
    industry_name: str


def parse_sw_index_mapping(path: Path | None = None) -> list[SwIndexRef]:
    """解析 ``data/sw_index_mapping.csv`` → ``SwIndexRef`` 列表。

    实测（2026-10-12，生成自 ``申万行业分类.md``）：511 行 = L1 31 + L2 134 + L3 346，
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
    text_value = str(value).strip()
    if not text_value or text_value.lower() in {"nan", "none", "nat"}:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return datetime.strptime(text_value, "%Y%m%d").date()


def normalize_member_rows(df: pd.DataFrame, ref: SwIndexRef) -> list[dict[str, object]]:
    """纯函数：上游 DataFrame → 待 upsert 的 SCD2 行。

    ``in_date`` 缺失的行**丢弃**（``effective_from`` 必填，无起点就无法表达区间）；
    ``is_current`` 取上游 ``is_new == 'Y'``，与 ``out_date`` 是否为空**分别**保留
    （上游实测存在 ``is_new='Y'`` 但 ``out_date`` 非空的矛盾行：本函数不替上游
    "修数据"，只如实落库）。
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


async def upsert_period_rows(db: AsyncSession, rows: list[dict[str, object]]) -> tuple[int, int]:
    """唯一键 ``(industry_code, stock_code, effective_from, effective_to)`` 上幂等 upsert。

    只更新 ``is_current`` / ``source`` / ``updated_at``——键的四个成分不允许被"改到别的键上"。

    **返回 ``(inserted, updated)``**（Fix round 1 · 评审 I2 后半）：用 Postgres 的
    ``xmax`` 判别——新插入的行 ``xmax = 0``，走 ``DO UPDATE`` 的既有行 ``xmax <> 0``。
    这样"重跑同一份数据（新增 0）"与"真有新行入库"在运行记录里可区分，
    而不是一律记成 ``rows_upserted = len(rows)``。
    """
    if not rows:
        return 0, 0
    insert_stmt = pg_insert(SwIndustryMemberPeriod).values(rows)
    # 幂等键是**表达式**唯一索引 → 用 `index_elements` 推断（表达式要与索引定义逐字一致），
    # 不能用 `constraint=`（PG 不允许用表达式唯一索引的名字做 ON CONFLICT 目标）。
    upsert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=[
            "industry_code",
            "stock_code",
            "effective_from",
            text("COALESCE(effective_to, DATE '9999-12-31')"),
        ],
        set_={
            "is_current": insert_stmt.excluded.is_current,
            "source": insert_stmt.excluded.source,
            "updated_at": func.now(),
        },
    ).returning(text("(xmax = 0) AS inserted"))
    result = await db.execute(upsert_stmt)
    flags = [bool(r[0]) for r in result.all()]
    inserted = sum(1 for f in flags if f)
    return inserted, len(flags) - inserted


async def _stock_names(db: AsyncSession) -> dict[str, str]:
    """symbol → 股票名（来自 stocks 表；上游 index_member 不返回名称）。"""
    from app.models.stock import Stock  # noqa: PLC0415  # 避免模型导入循环

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
    requested_indices: int
    index_codes: int
    rows_inserted: int
    rows_updated: int
    zero_row_indices: list[str]
    coverage_per_level: dict[int, tuple[int, int]]
    """level → (成功指数个数, 区间行总数)——**下界断言的记录面**。"""
    fully_ingested: bool
    status: str
    failed: dict[str, str] = field(default_factory=dict)

    @property
    def rows_upserted(self) -> int:
        return self.rows_inserted + self.rows_updated


def _as_of() -> date:
    """ingest 的"快照日"= 运行当日（上海时区）。

    上游 ``index_member`` 是**当前态**接口（不接受 ``trade_date``；实测传
    ``start_date``/``end_date`` 不改变返回），故"我们掌握到哪一天"只能由运行日表达。
    """
    from app.services.market_data_service import _today_sh  # noqa: PLC0415

    return _today_sh()


async def _previous_level_totals(db: AsyncSession) -> dict[int, int]:
    """上一次 ``fully_ingested`` 运行的逐层成员总数（从 ``detail`` 里解析）。

    为什么从 ``detail`` 解析而不是单开一张计数表：下界断言只是"别静默塌方"的粗护栏，
    为它建表属过度工程。``detail`` 的形状由本模块唯一写入，格式稳定且有测试锁定。
    """
    row = (
        await db.execute(
            select(SwMemberIngestRun.detail)
            .where(SwMemberIngestRun.fully_ingested.is_(True))
            .where(SwMemberIngestRun.status == "ok")
            .order_by(SwMemberIngestRun.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if not row:
        return {}
    totals: dict[int, int] = {}
    for part in str(row).split(";"):
        part = part.strip()
        if not part.startswith("L") or "=" not in part:
            continue
        head, _, tail = part.partition("=")
        try:
            level = int(head[1:].split(":")[0])
            # L2:指数 134/134,行 3105
            rows_part = [x for x in tail.split(",") if "行" in x]
            totals[level] = int(rows_part[0].replace("行", "").strip())
        except (ValueError, IndexError):
            continue
    return totals


def _check_level_floors(
    coverage: dict[int, tuple[int, int]], previous: dict[int, int]
) -> list[str]:
    """下界断言（Fix round 1 · 评审 I5）：返回"没过"的原因列表，空列表 = 全过。

    三条机械断言：

    1. **每个成功指数都要有记录**：``成功指数个数 > 0``（整层 0 个指数 = 上游/对照表异常）；
    2. **单层不得塌方**：该层区间行总数相对上一次 ok 运行跌幅 ≤ ``_LEVEL_DROP_TOLERANCE``；
    3. **0 行响应比例不得过大**：``zero_row/requested > _MAX_ZERO_ROW_RATIO`` → 上游大面积异常。

    "没过的运行不抬高口径日"由调用方据此把 ``status`` 置 ``partial`` 落实。
    """
    reasons: list[str] = []
    for level, (indices, rows) in sorted(coverage.items()):
        if indices <= 0:
            reasons.append(f"L{level} 成功指数为 0")
        if rows <= 0:
            reasons.append(f"L{level} 区间行总数为 0")
        prev = previous.get(level)
        if prev and prev > 0 and rows < prev * (1 - _LEVEL_DROP_TOLERANCE):
            drop = (prev - rows) / prev * 100
            reasons.append(f"L{level} 行数 {rows} 较上次 {prev} 跌 {drop:.0f}%（阈值 20%）")
    return reasons


async def ingest_sw_member_periods(
    db: AsyncSession,
    *,
    levels: tuple[int, ...] = DEFAULT_LEVELS,
    limit: int | None = None,
    refs: list[SwIndexRef] | None = None,
) -> SwMemberIngestResult:
    """从上游拉取申万成分并按 SCD2 落库。

    ``levels`` 默认 **``(1, 2, 3)``**（全部 511 个指数，约 5 分钟）——见模块 docstring：
    L1/L2 的成分**不能**由 L3 上卷得到（24/31 个一级行业实测偏少）。

    ``limit`` 只用于冒烟：被截断的运行**一定** ``fully_ingested=False``，
    因此**不会**抬高读面的 ``scd2_as_of``（Fix round 1 · 评审 I1）。

    上游调用失败**不静默跳过**：失败的 ``index_code`` 收进 ``failed`` 并计数，
    整次运行以 ``status='partial'`` / ``fully_ingested=False`` 落
    ``sw_member_ingest_runs``。
    """
    from app.core.providers.tushare_client import get_tushare_client  # noqa: PLC0415

    all_refs = refs if refs is not None else parse_sw_index_mapping()
    wanted = [r for r in all_refs if r.level in levels]
    truncated = False
    if limit is not None and limit < len(wanted):
        wanted = wanted[:limit]
        truncated = True

    client = get_tushare_client()
    names = await _stock_names(db)
    known_codes = await _class_codes(db)
    previous_totals = await _previous_level_totals(db)

    as_of = _as_of()
    failed: dict[str, str] = {}
    zero_row: list[str] = []
    per_level: dict[int, int] = {}
    per_level_indices: dict[int, int] = {}
    inserted = 0
    updated = 0
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
        if df.empty:
            # 0 行**不是失败**（对照表里有 71 个 L3 指数上游本来就无成员），
            # 但必须**记数**：它是下界信号之一，也是"静默变少"最早的征兆。
            zero_row.append(ref.index_code)
            per_level_indices[ref.level] = per_level_indices.get(ref.level, 0) + 1
            continue
        rows = normalize_member_rows(df, ref)
        for row in rows:
            row["stock_name"] = names.get(str(row["symbol"]))
        ins, upd = await upsert_period_rows(db, rows)
        inserted += ins
        updated += upd
        per_level[ref.level] = per_level.get(ref.level, 0) + len(rows)
        per_level_indices[ref.level] = per_level_indices.get(ref.level, 0) + 1

    coverage = {
        level: (per_level_indices.get(level, 0), per_level.get(level, 0)) for level in levels
    }
    floor_reasons = _check_level_floors(coverage, previous_totals)
    zero_ratio = len(zero_row) / len(wanted) if wanted else 0.0
    if zero_ratio > _MAX_ZERO_ROW_RATIO:
        floor_reasons.append(
            f"上游 0 行响应 {len(zero_row)}/{len(wanted)}（{zero_ratio:.0%}）超过阈值 "
            f"{_MAX_ZERO_ROW_RATIO:.0%}"
        )

    fully_ingested = (not truncated) and (not failed) and not floor_reasons
    status = "ok" if fully_ingested else "partial"

    detail_parts = [
        # 逐层计数：既是给人看的证据，也是下一次下界断言的输入（见 _previous_level_totals）
        f"L{lv}:指数 {idx}/{len([r for r in all_refs if r.level == lv])},行 {rows}"
        for lv, (idx, rows) in sorted(coverage.items())
    ]
    detail_parts.append(f"0行指数 {len(zero_row)}")
    detail_parts.append(f"新增 {inserted}/更新 {updated}")
    if truncated:
        in_scope = len([r for r in all_refs if r.level in levels])
        detail_parts.append(f"limit 截断（{len(wanted)}/{in_scope}）")
    if floor_reasons:
        detail_parts.append("下界未过：" + "；".join(floor_reasons))
    if failed:
        preview = "; ".join(f"{k}: {v}" for k, v in list(failed.items())[:3])
        detail_parts.append(f"{len(failed)} 个指数失败：{preview}")
    detail = " | ".join(detail_parts)[:512]

    db.add(
        SwMemberIngestRun(
            as_of=as_of,
            index_codes=len(wanted) - len(failed),
            rows_upserted=inserted + updated,
            rows_inserted=inserted,
            rows_updated=updated,
            fully_ingested=fully_ingested,
            zero_row_indices=len(zero_row),
            status=status,
            detail=detail,
        )
    )
    await db.commit()
    return SwMemberIngestResult(
        as_of=as_of,
        levels=levels,
        requested_indices=len(wanted),
        index_codes=len(wanted) - len(failed),
        rows_inserted=inserted,
        rows_updated=updated,
        zero_row_indices=zero_row,
        coverage_per_level=coverage,
        fully_ingested=fully_ingested,
        status=status,
        failed=failed,
    )


async def get_member_as_of(db: AsyncSession) -> date | None:
    """最近一次**完整成功** ingest 的 ``as_of``（SCD2 还原能力的时间上界）。

    过滤条件是 ``fully_ingested=True AND status='ok'``（Fix round 1 · 评审 I1）：
    ``--limit`` 截断的冒烟运行、有失败或下界未过的运行**都不抬高**这个上界。
    没有合格记录 → ``None``，读面必须据此**明确声明"尚无历史"**，
    不得把空结果说成"当日无成分"。
    """
    return (
        await db.execute(
            select(func.max(SwMemberIngestRun.as_of))
            .where(SwMemberIngestRun.fully_ingested.is_(True))
            .where(SwMemberIngestRun.status == "ok")
        )
    ).scalar_one_or_none()


async def get_last_run(db: AsyncSession) -> dict[str, object] | None:
    """最近一次运行的覆盖摘要（读面用来把"缺数"外露成可核查的记录）。

    返回任意 ``status`` 的最近一行——**读面必须同时看 ``status``/``fully_ingested``**
    才能判断"这个口径日算不算数"。
    """
    row = (
        await db.execute(select(SwMemberIngestRun).order_by(SwMemberIngestRun.id.desc()).limit(1))
    ).scalar_one_or_none()
    if row is None:
        return None
    return {
        "as_of": row.as_of,
        "status": row.status,
        "fully_ingested": bool(row.fully_ingested),
        "index_codes": int(row.index_codes),
        "rows_inserted": int(row.rows_inserted),
        "rows_updated": int(row.rows_updated),
        "zero_row_indices": int(row.zero_row_indices),
        "detail": row.detail,
    }
