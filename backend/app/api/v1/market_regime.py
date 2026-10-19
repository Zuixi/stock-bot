"""``GET /api/v1/market/regime``（spec §S10.3）：**登录即可**的只读状态机读面。

三件不做的事：

1. **不重算行情**：834 个交易日 × 全市场（445 万行）是分钟级扫描，HTTP 端点不能这么干。
   状态序列来自随仓库入库的离线产物（``app/services/market_regime_history.json``，
   由 ``scripts/build_market_regime.py`` 生成）；端点只做投影 + **一条**
   ``max(trade_date)`` 用来判断产物是否已落后——落后就写进 ``notes``，
   绝不静默给一个"看起来是今天"的旧状态；
2. **不 500**：产物缺失/损坏 → ``state=null`` + 原因 note。spec §S10.3 写的是
   "503 或 state=null + note"，这里选空态：一行文案的降级不该让前端走异常分支
   （与 ``/watchlists/triage/rules`` 的 ``stats=null + stats_note`` 同一套做法）；
3. **不预测**：``notes`` 只陈述判定口径与样本量（spec §S10.5）。

鉴权 = **登录即可**（``CurrentUserDep``）：状态是公开行情聚合，不要求 ``research:read``。
"""

from fastapi import APIRouter

from app.api.deps import CurrentUserDep, DbDep
from app.repositories import limit_up_repo
from app.schemas.market_regime import MarketRegimeOut, RegimeForwardOut
from app.services import market_regime_service as svc

router = APIRouter()


@router.get("/regime", response_model=MarketRegimeOut)
async def get_market_regime(db: DbDep, _user: CurrentUserDep) -> MarketRegimeOut:
    """当前市场状态 + 同态后续 5/20 日分布 + 转移概率（不预测，只陈述已发生的事实）。

    ``latest_trade_date`` 只用来说明"产物落后了没有"：它是**一个索引查询**，
    与状态机的重算无关（重算永远在离线脚本里）。
    """
    latest = await limit_up_repo.latest_quote_date(db)
    view = svc.read_regime(latest_trade_date=latest)
    return MarketRegimeOut(
        state=view.state,
        since=view.since,
        duration_days=view.duration_days,
        transition=view.transition,
        forward={
            key: (RegimeForwardOut(**bucket) if bucket is not None else None)
            for key, bucket in view.forward.items()
        },
        notes=view.notes,
        as_of=view.as_of,
        version=view.version,
    )
