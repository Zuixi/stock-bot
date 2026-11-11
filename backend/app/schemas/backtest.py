"""Backtest request/response contracts (spec §S2.3)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SplitRange(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: date = Field(alias="from")
    to: date


class SplitSpec(BaseModel):
    in_sample: SplitRange
    out_of_sample: SplitRange


class BacktestIn(BaseModel):
    scope_key: str = Field(min_length=1, max_length=32)
    rule_key: str = Field(min_length=1, max_length=32)
    rule_version: str = Field(default="v1", min_length=1, max_length=32)
    window_from: date
    window_to: date
    horizons: list[int] = Field(default_factory=lambda: [1, 5, 20])
    benchmark: str = Field(default="000300.SH", min_length=1, max_length=16)
    split: SplitSpec


class BacktestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rule_key: str
    rule_version: str
    scope_type: str
    scope_keys: list[str]
    window_from: date
    window_to: date
    horizons: list[int]
    benchmark: str
    split: dict[str, Any]
    pit_enforced: bool
    stats: dict[str, Any]
    created_at: datetime
