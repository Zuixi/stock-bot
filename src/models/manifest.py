"""Manifest models for universe snapshots."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class FetchStats(BaseModel):
    """Statistics from a fetch operation."""

    total_pages: int = Field(description="Total pages fetched")
    total_records: int = Field(description="Total records before dedup")
    unique_records: int = Field(description="Unique records after dedup")
    failed_pages: int = Field(default=0, description="Number of failed page requests")
    retry_count: int = Field(default=0, description="Total retry attempts")
    duration_seconds: float = Field(description="Total fetch duration")
    
    categories: dict[str, int] = Field(
        default_factory=dict,
        description="Record count per category"
    )


class UniverseManifest(BaseModel):
    """Manifest for a universe snapshot.

    Records all parameters and metadata for reproducibility.
    """

    # Snapshot identification
    exchange: str = Field(description="Exchange identifier")
    asof: datetime = Field(description="Snapshot timestamp (ISO 8601)")
    version: str = Field(default="1.0", description="Manifest schema version")
    
    # Source information
    endpoint: str = Field(description="API endpoint URL")
    query_params: dict[str, str] = Field(description="Fixed query parameters (sqlId, type, etc.)")
    filters: dict[str, str] = Field(description="Filter parameters (STOCK_TYPE, etc.)")
    
    # Pagination config (actually used)
    pagination: dict[str, Any] = Field(description="Pagination settings")
    
    # Request config (sanitized - no cookies!)
    headers: dict[str, str] = Field(description="Request headers (excluding sensitive)")
    
    # Rate limiting & retry config
    rate_limit: dict[str, float] = Field(description="Rate limiting settings")
    retry: dict[str, Any] = Field(description="Retry settings")
    timeout: float = Field(description="Request timeout in seconds")
    
    # Fetch statistics
    stats: FetchStats = Field(description="Fetch statistics")
    
    # Error information (if any)
    errors: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Error samples (first N errors)"
    )
    
    # Output files
    output_files: list[str] = Field(
        default_factory=list,
        description="List of output file paths relative to snapshot dir"
    )

    def to_safe_dict(self) -> dict[str, Any]:
        """Convert to dict, ensuring no sensitive data is included."""
        data = self.model_dump(mode="json")
        # Explicitly remove any accidentally included sensitive fields
        if "cookies" in data:
            del data["cookies"]
        return data
