"""API v1 router assembly — RESTful resource hierarchy.

/api/v1/exchanges
/api/v1/exchanges/categories

/api/v1/exchanges/{exchange}/stocks
    GET  /
    GET  /{symbol}
    GET  /{symbol}/quotes/daily
    GET  /{symbol}/quotes/latest
    GET  /{symbol}/features
    GET  /{symbol}/features/radar

/api/v1/clusters/...
/api/v1/tasks/...
"""

from fastapi import APIRouter

from app.api.v1 import (
    backtests,
    clusters,
    concepts,
    content,
    financials,
    industries,
    journal,
    market,
    market_data,
    market_regime,
    metrics,
    new_stocks,
    ops,
    provenance,
    regulatory,
    stocks,
    tags,
    tasks,
    watchlist_triage,
    watchlists,
)

router = APIRouter(prefix="/api/v1")

# Exchange metadata: /api/v1/exchanges, /api/v1/exchanges/categories
router.include_router(stocks.router, prefix="/exchanges", tags=["exchanges"])

# Stock + quotes + features: /api/v1/exchanges/{exchange}/stocks/...
router.include_router(
    stocks.stocks_router,
    prefix="/exchanges/{exchange}/stocks",
    tags=["stocks"],
)

# Financial statements + valuation: /api/v1/exchanges/{exchange}/stocks/{symbol}/...
router.include_router(
    financials.router,
    prefix="/exchanges/{exchange}/stocks",
    tags=["stocks", "financials"],
)

# Clustering
router.include_router(clusters.router, prefix="/clusters", tags=["clusters"])

# Market dashboard
router.include_router(market.router, prefix="/market", tags=["market"])

# Market-data face: /api/v1/market/global-indices（后续 moneyflow/龙虎榜等面片追加至此）
router.include_router(market_data.router, prefix="/market", tags=["market-data"])

# 市场状态机（spec §S10）：/api/v1/market/regime —— 离线产物投影，登录即可
router.include_router(market_regime.router, prefix="/market", tags=["market"])

# 监管异动雷达：/api/v1/market/regulatory-anomalies —— 读时计算（零新表）、公开
router.include_router(regulatory.router, prefix="/market", tags=["market"])

# Background tasks
router.include_router(tasks.router, prefix="/tasks", tags=["tasks"])

# User-defined tags
router.include_router(tags.router, prefix="/tags", tags=["tags"])

# User watchlists
router.include_router(watchlists.router, prefix="/watchlists", tags=["watchlists"])

# 自选体检（spec §S8.3）：/api/v1/watchlists/triage —— 只读、读时算、零新表
router.include_router(watchlist_triage.router, prefix="/watchlists", tags=["watchlists"])

# Industry research workbench: /api/v1/industries/...
router.include_router(industries.router, prefix="/industries", tags=["industry-research"])

# Concept boards: /api/v1/concepts/...（T6 列表 / T8 详情 / T9 成分·反查）
router.include_router(concepts.router, prefix="/concepts", tags=["concepts"])

# Unified metric read surface: /api/v1/metrics/...（spec §S1.2，复用 industry_metrics 单表）
router.include_router(metrics.router, prefix="/metrics", tags=["metrics"])

# New-stock board: /api/v1/new-stocks（T10，东财 BK0501 成分 × 本地行情）
router.include_router(new_stocks.router, prefix="/new-stocks", tags=["new-stocks"])

# 决策日志（spec §S9）：/api/v1/journal —— 只有 POST/GET（不可篡改），登录即可
router.include_router(journal.router, prefix="/journal", tags=["journal"])

# Ops read-only endpoints (data quality / db health / consistency)
router.include_router(ops.router, prefix="/ops", tags=["ops"])

# 内容读面（S3.x）：/api/v1/content —— 列表/详情公开，用户状态需登录
router.include_router(content.router, prefix="/content", tags=["content"])

# Metric provenance (display value -> caliber -> source rows -> batch -> raw JSONL)
router.include_router(provenance.router, prefix="/provenance", tags=["provenance"])

# 单规则事件研究回测（S2.3）：参数/切分/PIT 标记/统计全部落库，结论可复现。
router.include_router(backtests.router, prefix="/backtests", tags=["backtests"])
