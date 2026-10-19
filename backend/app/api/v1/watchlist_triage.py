"""自选体检读面：``GET /api/v1/watchlists/triage`` + ``/triage/rules``（只读、零新表、零新 cron）。

鉴权 = **登录即可**（spec §S8.1）：数据是公开行情 + 用户自己的自选，不要求
``research:read``——要求它会让普通用户看不到自己自选的体检。

三件不可越界的事：
1. **不静默截断**：自选 > ``MAX_SYMBOLS`` 直接 422 并提示分组（spec §S8.3）；
2. **不扫全市场标的**：只算 ``symbols`` 里的代码（板块探测器需要全市场 L1 的**分位**，
   那是一条聚合查询，不是逐股探测器，见 ``triage_service._SECTOR_SQL``）；
3. **文案只陈述事实**：``headline`` 由探测器产出，本层只加固定的 ``DISCLAIMER``。

``/triage/rules`` 是**零 DB 查询**的：它只把 registry 与随仓库入库的静态统计产物
（``app/services/triage_rule_stats.json``）投影成契约，因此没有 ``DbDep``。
"""

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUserDep, DbDep
from app.schemas.triage import FactOut, TriageItemOut, TriageOut, TriageRulesOut
from app.services import triage_rules, triage_service
from app.services.triage_detectors import TriageItem

router = APIRouter()

#: 固定页脚（spec §S8.5 逐字）：与 ``backend/scripts/triage_compliance.py`` 的扫描面一致
DISCLAIMER = "本产品只展示已发生的数据事实，不构成投资建议；历史数据不代表未来表现。"


def _item_out(item: TriageItem) -> TriageItemOut:
    return TriageItemOut(
        symbol=item.symbol,
        name=item.name,
        detector=item.detector,
        severity=item.severity,
        headline=item.headline,
        facts=[FactOut(label=f.label, value=f.value, unit=f.unit) for f in item.facts],
        as_of=item.as_of,
        source=item.source,
        verify=item.verify,
        # 三字段来自 ``spec``（``triage_service`` 已用 ``with_provenance`` 补齐）
        rule_text=item.rule_text,
        rule_version=item.rule_version,
        source_table=item.source_table,
    )


@router.get("/triage/rules", response_model=TriageRulesOut)
async def get_triage_rules(_user: CurrentUserDep) -> TriageRulesOut:
    """8 条体检规则的**来源 + 标定统计**（登录即可；不查库、不扫全市场）。

    为什么单开一个端点而不是塞进 ``/triage``：卡片只需要三字段（``rule_text`` /
    ``rule_version`` / ``source_table``），而完整口径（``definition`` / ``failure_modes`` /
    ``stats``）是页面级的说明弹层数据，塞进每次体检的载荷会让 8 条规则的散文随每次请求下发。
    """
    return TriageRulesOut(rules=triage_rules.list_rules())


@router.get("/triage", response_model=TriageOut)
async def get_triage(
    db: DbDep,
    user: CurrentUserDep,
    symbols: str | None = Query(
        None,
        description="逗号分隔的自选代码（如 600000,000001）；缺省取当前用户的自选",
    ),
    watchlist_id: uuid.UUID | None = Query(
        None,
        description="只看某一张自选表（缺省=该用户全部自选，默认表优先）",
    ),
    limit: int = Query(
        triage_service.LIMIT,
        ge=1,
        le=triage_service.LIMIT,
        description=f"单页条数上限（≤{triage_service.LIMIT}，spec §S8.4）",
    ),
) -> TriageOut:
    """返回当前用户自选（或指定代码）今日的**事实清单**。"""
    assert user.user_id is not None
    codes = [code.strip() for code in (symbols or "").split(",") if code.strip()]
    # 先按**原始条数**判 422：去重后再判会让"重复贴 51 遍同一只"绕过上限检查
    if len(codes) > triage_service.MAX_SYMBOLS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"自选最多 {triage_service.MAX_SYMBOLS} 只，请分组后再试",
        )
    if not codes:
        codes = await triage_service.user_symbols(db, user.user_id, watchlist_id)
        if len(codes) > triage_service.MAX_SYMBOLS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"自选最多 {triage_service.MAX_SYMBOLS} 只，请分组后再试",
            )
    # 去重放在**边界**（重复代码会让 quiet_count / folded 被重复计数污染）
    codes = list(dict.fromkeys(codes))
    result = await triage_service.get_triage(db, codes, limit=limit)
    return TriageOut(
        as_of=result.as_of,
        state=result.state,
        quiet_count=result.quiet_count,
        folded=result.folded,
        # 上屏用 label；机器 key 只进 unavailable_keys（前端禁止渲染）
        unavailable=result.unavailable,
        unavailable_keys=result.unavailable_keys,
        disclaimer=DISCLAIMER,
        items=[_item_out(item) for item in result.items],
    )
