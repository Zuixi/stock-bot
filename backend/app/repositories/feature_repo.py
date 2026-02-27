"""Feature repository: read and write stock features."""

from datetime import date

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.feature import StockFeature


async def get_latest_features(
    db: AsyncSession, stock_id: int, window_days: int
) -> StockFeature | None:
    stmt = (
        select(StockFeature)
        .where(
            StockFeature.stock_id == stock_id,
            StockFeature.window_days == window_days,
        )
        .order_by(desc(StockFeature.asof_date))
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_feature_history(
    db: AsyncSession,
    stock_id: int,
    window_days: int,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[StockFeature]:
    stmt = (
        select(StockFeature)
        .where(
            StockFeature.stock_id == stock_id,
            StockFeature.window_days == window_days,
        )
        .order_by(StockFeature.asof_date)
    )
    if start_date:
        stmt = stmt.where(StockFeature.asof_date >= start_date)
    if end_date:
        stmt = stmt.where(StockFeature.asof_date <= end_date)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def upsert_features(db: AsyncSession, features: list[StockFeature]) -> int:
    from sqlalchemy.dialects.postgresql import insert

    if not features:
        return 0

    values = [
        {
            "stock_id": f.stock_id,
            "asof_date": f.asof_date,
            "window_days": f.window_days,
            "total_return": f.total_return,
            "return_percentile": f.return_percentile,
            "annual_volatility": f.annual_volatility,
            "max_drawdown": f.max_drawdown,
            "downside_vol": f.downside_vol,
            "trend_slope": f.trend_slope,
            "trend_r2": f.trend_r2,
            "ma_bullish": f.ma_bullish,
            "trend_reversals": f.trend_reversals,
            "avg_volume": f.avg_volume,
            "volume_volatility": f.volume_volatility,
            "extra": f.extra,
        }
        for f in features
    ]

    stmt = (
        insert(StockFeature)
        .values(values)
        .on_conflict_do_update(
            constraint="uq_stock_features_key",
            set_={k: insert(StockFeature).excluded[k] for k in values[0] if k not in ("stock_id", "asof_date", "window_days")},
        )
    )
    result = await db.execute(stmt)
    await db.flush()
    return result.rowcount
