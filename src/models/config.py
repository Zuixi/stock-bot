"""Configuration models for fetchers."""

from typing import Any

from pydantic import BaseModel, Field


class PaginationConfig(BaseModel):
    """Pagination configuration."""

    page_size: int = Field(default=25, ge=1, le=200)
    cache_size: int = Field(default=1)


class RateLimitConfig(BaseModel):
    """Rate limiting configuration."""

    requests_per_second: float = Field(default=2.0, ge=0.1, le=10.0)
    page_delay: float = Field(default=0.5, ge=0.0)


class RetryConfig(BaseModel):
    """Retry configuration."""

    max_attempts: int = Field(default=3, ge=1, le=10)
    backoff_multiplier: float = Field(default=2.0, ge=1.0)
    initial_delay: float = Field(default=1.0, ge=0.1)


class JsonpConfig(BaseModel):
    """JSONP configuration."""

    param_name: str = Field(default="jsonCallBack")
    callback_prefix: str = Field(default="jsonpCallback")


class SseConfig(BaseModel):
    """SSE fetcher configuration."""

    endpoint: str = Field(default="https://query.sse.com.cn/sseQuery/commonQuery.do")
    
    query: dict[str, str] = Field(default_factory=lambda: {
        "sqlId": "COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L",
        "type": "inParams",
        "isPagination": "true",
    })
    
    filters: dict[str, str] = Field(default_factory=lambda: {
        "STOCK_TYPE": "1",
        "REG_PROVINCE": "",
        "CSRC_CODE": "",
        "STOCK_CODE": "",
        "COMPANY_STATUS": "2,4,5,7,8",
    })
    
    pagination: PaginationConfig = Field(default_factory=PaginationConfig)
    jsonp: JsonpConfig = Field(default_factory=JsonpConfig)
    headers: dict[str, str] = Field(default_factory=dict)
    cookies: dict[str, str] = Field(default_factory=dict)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    timeout: float = Field(default=30.0, ge=1.0)

    @classmethod
    def from_yaml(cls, data: dict[str, Any]) -> "SseConfig":
        """Create config from parsed YAML data."""
        # Flatten nested structures for Pydantic
        config_data = {
            "endpoint": data.get("endpoint"),
            "query": data.get("query", {}),
            "filters": data.get("filters", {}),
            "headers": data.get("headers", {}),
            "cookies": data.get("cookies", {}),
            "timeout": data.get("timeout", 30.0),
        }
        
        if "pagination" in data:
            config_data["pagination"] = PaginationConfig(**data["pagination"])
        if "jsonp" in data:
            config_data["jsonp"] = JsonpConfig(**data["jsonp"])
        if "rate_limit" in data:
            config_data["rate_limit"] = RateLimitConfig(**data["rate_limit"])
        if "retry" in data:
            config_data["retry"] = RetryConfig(**data["retry"])
        
        # Filter out None values
        config_data = {k: v for k, v in config_data.items() if v is not None}
        
        return cls(**config_data)

    def build_cookie_header(self) -> str:
        """Build Cookie header string from key-value pairs."""
        if not self.cookies:
            return ""
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    def get_safe_headers(self) -> dict[str, str]:
        """Get headers dict safe for logging/manifest (no cookies)."""
        return {k: v for k, v in self.headers.items() if k.lower() != "cookie"}
