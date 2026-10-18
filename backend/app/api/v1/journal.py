"""决策日志读/写面（spec §S9.1/§S9.4）：**只有 POST/GET，没有 PATCH/PUT/DELETE**。

不可篡改是产品契约（"创建后 ``thesis/invalidation/horizon_days/symbol`` 不可修改"）：
本文件里出现不了 ``@router.patch`` / ``@router.put`` / ``@router.delete``，
``tests/test_journal_api.py`` 用 grep + 真实路由表两条断言钉死。

鉴权 = **登录即可**（``CurrentUserDep``）：数据是用户自己的判断，不是研究内容，
要求 ``research:read`` 会让普通用户读不到自己的日志。所有查询都带 ``user_id``，
非本人条目统一 **404**（不泄露"有没有这条"）。

五个端点（与计划 Task 3/5 的契约一一对应）：:

    POST /api/v1/journal                    → 201 创建（快照落库）
    GET  /api/v1/journal?status=            → 列表 + 三态计数
    GET  /api/v1/journal/stats              → 统计（占比无样本为 null）
    GET  /api/v1/journal/{id}               → 单条（含到期自动结果）
    POST /api/v1/journal/{id}/review        → 201 归因（二次复盘 409）
"""

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUserDep, DbDep
from app.schemas.journal import (
    JournalEntryCreate,
    JournalEntryOut,
    JournalEvaluationOut,
    JournalListOut,
    JournalReviewCreate,
    JournalReviewOut,
    JournalStatsOut,
)
from app.services import journal_service as svc

router = APIRouter()

#: 固定页脚（spec §S9.5 逐字）：记录的是**用户自己的判断**，不是产品建议。
DISCLAIMER = "决策日志记录的是你自己的判断，不构成投资建议；结果统计不代表未来表现。"


def _entry_out(row: svc.JournalRow, evaluation: dict | None = None) -> JournalEntryOut:
    entry = row.entry
    return JournalEntryOut(
        id=entry.id,
        symbol=entry.symbol,
        kind=entry.kind,
        thesis=entry.thesis,
        invalidation=entry.invalidation or {},
        horizon_days=entry.horizon_days,
        snapshot=entry.snapshot or {},
        created_at=entry.created_at,
        due_on=entry.due_on,
        status=row.status,
        snapshot_summary=svc.snapshot_line(entry.snapshot),
        review=(None if row.review is None else JournalReviewOut.model_validate(row.review)),
        evaluation=None if evaluation is None else JournalEvaluationOut(**evaluation),
    )


# 静态路径必须声明在 `/{entry_id}` 之前：否则 "stats" 会先被路径参数吃掉（422 而不是路由）
@router.get("/stats", response_model=JournalStatsOut)
async def get_journal_stats(db: DbDep, user: CurrentUserDep) -> JournalStatsOut:
    """长期看自己最常犯的错（``reviewed_n < 5`` 时占比为 ``null``）。"""
    assert user.user_id is not None
    result = await svc.stats(db, user.user_id)
    return JournalStatsOut(**result)


@router.get("", response_model=JournalListOut)
async def list_journal_entries(
    db: DbDep,
    user: CurrentUserDep,
    status_filter: str | None = Query(
        None,
        alias="status",
        description="open（未到期）/ due（到期待复盘）/ reviewed（已复盘）；缺省=全部",
    ),
) -> JournalListOut:
    """列表页三块（记一笔 / 待复盘 / 已复盘）的唯一读面。"""
    assert user.user_id is not None
    if status_filter is not None and status_filter not in svc.STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"status 只接受 {list(svc.STATUSES)}",
        )
    rows = await svc.list_entries(db, user.user_id, status=status_filter)
    summary = await svc.status_counts(db, user.user_id)
    # 截断按**本次筛选**该有的条数比（用 total 比会在 status 过滤时误报）
    expected = summary["total"] if status_filter is None else summary[status_filter]
    # 到期条目的自动结果**批量**算（列表页不逐条查库：evaluate_entries 的查询数与条数无关）
    due_entries = [row.entry for row in rows if row.status == "due"]
    evaluations = await svc.evaluate_entries(db, due_entries) if due_entries else {}
    return JournalListOut(
        items=[_entry_out(row, evaluations.get(row.entry.id)) for row in rows],
        snapshot_summary=summary,
        truncated=len(rows) < expected,
        status=status_filter,
        disclaimer=DISCLAIMER,
    )


@router.post("", response_model=JournalEntryOut, status_code=status.HTTP_201_CREATED)
async def create_journal_entry(
    payload: JournalEntryCreate, db: DbDep, user: CurrentUserDep
) -> JournalEntryOut:
    """记一笔：3 行必填 + 自动快照（快照在**创建时**落库，之后不随行情变化）。"""
    assert user.user_id is not None
    entry = await svc.create_entry(db, user.user_id, payload)
    return _entry_out(svc.JournalRow(entry=entry, review=None, status="open"))


@router.get("/{entry_id}", response_model=JournalEntryOut)
async def get_journal_entry(entry_id: int, db: DbDep, user: CurrentUserDep) -> JournalEntryOut:
    """单条详情：**含到期自动结果**（复盘弹窗用它展示 outcome/bench/excess）。"""
    assert user.user_id is not None
    try:
        row = await svc.get_entry(db, user.user_id, entry_id)
    except svc.EntryNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    # 未到期的条目不展示"结果"（spec §S9.4：到期后、复盘前才展示）——也不白跑查询
    evaluation = None if row.status == "open" else await svc.evaluate_entry(db, row.entry)
    return _entry_out(row, evaluation)


@router.post(
    "/{entry_id}/review", response_model=JournalReviewOut, status_code=status.HTTP_201_CREATED
)
async def review_journal_entry(
    entry_id: int,
    payload: JournalReviewCreate,
    db: DbDep,
    user: CurrentUserDep,
) -> JournalReviewOut:
    """一次复盘（不可覆盖）：未到期 422 · 已复盘 409 · 非本人 404 · 结果未就绪 422。"""
    assert user.user_id is not None
    try:
        review = await svc.review_entry(
            db, user.user_id, entry_id, verdict=payload.verdict, note=payload.note
        )
    except svc.EntryNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except svc.AlreadyReviewed as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (svc.NotDueYet, svc.EvaluationNotReady) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return JournalReviewOut.model_validate(review)
