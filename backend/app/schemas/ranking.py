"""Public market rankings schemas (Task 2.5)."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

# Ruling O: the five supported ranking types (amplitude excluded — expression only, no index).
RankingType = Literal["gainers", "losers", "amount", "turnover_rate", "volume"]


class RankingItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    name: str
    exchange: str | None = None
    close: float | None = None
    pct_chg: float | None = None
    # ``daily_quotes.amount`` is TuShare-native 千元 (see the model / get_sectors' x1000).
    # Passed through raw to match StockEnrichedOut.amount; consumers apply the existing
    # x1000 -> 元 conversion. Unit is 千元, not 元.
    amount: float | None = None
    volume: float | None = None  # 股
    turnover_rate: float | None = None
    total_mv: float | None = None  # 万元


class RankingResponseOut(BaseModel):
    as_of: date
    is_latest_trading_day: bool
    type: RankingType
    items: list[RankingItemOut]
