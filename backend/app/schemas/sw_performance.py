"""Shenwan L1 industry performance schemas (Task 3.1)."""

from datetime import date

from pydantic import BaseModel


class SwPerformanceItemOut(BaseModel):
    code: str
    name: str
    member_count: int
    avg_pct_chg: float
    # ``daily_quotes.amount`` is TuShare-native 千元; ``sum()`` inherits that unit and is
    # passed through raw to match ``StockEnrichedOut.amount`` / rankings. Consumers apply
    # the existing x1000 -> 元 conversion. Unit is 千元, not 元.
    #
    # Nullable: ``sum(amount)`` is NULL when none of a group's quoted members have an
    # amount, and this is an anonymous endpoint — a non-optional field would raise a
    # Pydantic ValidationError (500). Degrade to NULL and let the UI render ``--``.
    total_amount: float | None = None
    up_count: int
    down_count: int


class SwPerformanceResponseOut(BaseModel):
    as_of: date
    items: list[SwPerformanceItemOut]
