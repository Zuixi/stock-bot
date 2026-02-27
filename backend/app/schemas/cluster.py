"""Clustering request/response schemas."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel


class ClusteringRunOut(BaseModel):
    id: uuid.UUID
    name: str | None
    algorithm: str
    params: dict
    asof_date: date
    window_days: int
    n_clusters: int | None
    silhouette: float | None
    metrics: dict | None
    status: str
    is_default: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ClusterDistributionItem(BaseModel):
    cluster_label: int
    count: int
    percentage: float


class ClusterDistributionOut(BaseModel):
    run_id: uuid.UUID
    total: int
    distribution: list[ClusterDistributionItem]


class ClusterMemberOut(BaseModel):
    stock_id: int
    symbol: str
    name: str
    exchange: str
    cluster_label: int
    distance: float | None


class ClusterExplanationOut(BaseModel):
    cluster_label: int
    summary: str
    input_summary: str | None
    model_version: str | None
    disclaimer: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
