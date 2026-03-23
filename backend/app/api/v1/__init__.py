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

from app.api.v1 import clusters, stocks, tasks

router = APIRouter(prefix="/api/v1")

# Exchange metadata: /api/v1/exchanges, /api/v1/exchanges/categories
router.include_router(stocks.router, prefix="/exchanges", tags=["exchanges"])

# Stock + quotes + features: /api/v1/exchanges/{exchange}/stocks/...
router.include_router(
    stocks.stocks_router,
    prefix="/exchanges/{exchange}/stocks",
    tags=["stocks"],
)

# Clustering
router.include_router(clusters.router, prefix="/clusters", tags=["clusters"])

# Background tasks
router.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
