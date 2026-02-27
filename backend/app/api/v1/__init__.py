"""API v1 router assembly."""

from fastapi import APIRouter

from app.api.v1 import clusters, features, quotes, stocks, tasks

router = APIRouter(prefix="/api/v1")
router.include_router(stocks.router, prefix="/stocks", tags=["stocks"])
router.include_router(quotes.router, prefix="/quotes", tags=["quotes"])
router.include_router(features.router, prefix="/features", tags=["features"])
router.include_router(clusters.router, prefix="/clusters", tags=["clusters"])
router.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
