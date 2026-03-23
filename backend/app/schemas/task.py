"""Task request/response schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel


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
