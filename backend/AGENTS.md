# backend

backend service：为 frontend 提供 RESTful API，并负责三大交易所数据的采集与管理。

## Components
- app: FastAPI 服务主体（分层：`app/api/v1` 路由 → `app/services` → `app/repositories`；`app/models` + Alembic 迁移 `app/migrations`；`app/schemas` pydantic 模型；`app/core` 基础设施/数据源客户端；`app/scheduler` APScheduler 定时任务；`app/workers` RabbitMQ Worker 手动触发）
- crons: 独立爬虫脚本（如 `crons/SSE` 上交所指数爬取）
- data: 原始数据备份（JSONL）与静态种子文件
- docs: backend 文档
- scripts: 运维脚本
- tests: pytest 测试

## Tech Stack
- FastAPI + SQLAlchemy 2.0 (async) + Alembic 迁移
- PostgreSQL（基础镜像 quay.io/sclorg/postgresql-15-c9s）+ Redis 缓存 + RabbitMQ 任务队列
- uv 管理 Python 依赖；ruff（line-length 100）+ mypy（pydantic plugin）做静态检查

## 常用命令（在 backend/ 目录下）
- 跑测试：`uv run pytest`
- Lint / 类型检查：`uv run --extra dev ruff check .`、`uv run --extra dev mypy app`
- 注意：ruff/mypy 在 dev extra 中，直接 `uv run ruff` 会找不到命令

## Data Sources
- Primary: TuShare Pro API (stock_basic, stock_company, daily, trade_cal)
  - Client: `app/core/providers/tushare_client.py`
  - Ingest: `app/services/tushare_ingest.py`
  - Raw backup: `data/` directory (JSONL)
- Fallback: Exchange crawlers + AKShare + yfinance (`app/services/universe_ingest.py`)
- Index: CNINFO WebAPI (`app/core/providers/cninfo_client.py`)

Tushare API Reference: [Tushare API Reference](../docs/references/tushare/index.md)

IMPORTANT:
- UPDATE THIS FILE WHEN YOU MEET SOMETHING IMPORTANT AND USEFUL
- AFTER EACH TASK COMPLETION, SUMMARIZE USEFUL EXPERIENCES AND ADD TO [THIS DOCUMENT](../docs/references/best-practices.md)
