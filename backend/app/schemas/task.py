"""Task request/response schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class TaskOut(BaseModel):
    id: uuid.UUID
    type: str
    status: str
    progress: int
    payload: dict | None
    result: dict | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TaskListParams(BaseModel):
    type: str | None = None
    status: str | None = None


class FetchUniverseRequest(BaseModel):
    exchange: str
    stock_type: str | None = None
    source: str = Field(default="auto", pattern="^(auto|crawler|akshare)$")
    include_details: bool = True
    detail_source: str = Field(default="auto", pattern="^(auto|akshare|yfinance)$")
    detail_retry: int = Field(default=3, ge=1, le=8)
    detail_sleep_min: float = Field(default=0.08, ge=0)
    detail_sleep_max: float = Field(default=0.25, ge=0)

    @model_validator(mode="after")
    def validate_sleep_range(self) -> "FetchUniverseRequest":
        if self.detail_sleep_min > self.detail_sleep_max:
            raise ValueError("detail_sleep_min must be <= detail_sleep_max")
        return self


class FetchQuotesRequest(BaseModel):
    exchange: str | None = None
    symbols: list[str] | None = None
    start_date: str | None = None
    end_date: str | None = None


class RunClusteringRequest(BaseModel):
    algorithm: str = "kmeans"
    n_clusters: int = 10
    window_days: int = 60
    asof_date: str | None = None
    name: str | None = None
