"""Application configuration loaded from environment variables."""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_env: str = "development"
    app_debug: bool = False
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # PostgreSQL
    database_url: str = "postgresql+asyncpg://stock_user:stock_pass@localhost:5432/stock_bot"
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    redis_default_ttl: int = 300  # seconds

    # RabbitMQ
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    rabbitmq_exchange: str = "stock_bot.topic"

    # TuShare
    tushare_token: str = ""

    # Industry research workbench data source: "mock" (default) | "akshare"
    # AKShare 接口名尚未实机验证（见 docs/design/data-source.md），默认 mock。
    industry_data_source: str = "mock"

    # CAAA 能繁母猪文章直连 URL（逃生通道：栏目列表页改版时手动指定最新文章；
    # 留空 = 自动从 pig.caaa.cn 行业动态栏目发现，见 app/core/providers/caaa_client.py）
    caaa_sow_article_url: str = ""

    # Shenwan industry classification XLS data directory
    sw_data_dir: str = ""

    # CORS — accepts comma-separated string or JSON array string
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    @field_validator("cors_origins", mode="after")
    @classmethod
    def parse_cors_origins(cls, v: str) -> list[str]:
        if not v:
            return []
        try:
            # Try JSON array first
            import json
            return json.loads(v)
        except (json.JSONDecodeError, TypeError):
            # Fall back to comma-separated
            return [origin.strip() for origin in v.split(",") if origin.strip()]

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"


settings = Settings()
