# backend

backend service used to provide unified restful api for the frontend service, and fetch and manage all data from the exchanges.

## Componenes
- app: fastAPI service to provide restful api for the frontend service.
- crons: cron jobs to fetch and manage data from the exchanges.
- docs: documentation for the backend service
- tests: tests for the backend service
- core: core fetching module to get datas.
- fetchers: unified fetcher client for 3 exchanges.
- schemas: pydantic models for the backend service.


## Tech Stack
- Using FastAPI to build the restful api for the frontend service.
- Using SQLModel to build the database models.
- Using Redis to cache the data.
- Using RabbitMQ to manage the data flow.
- Using TuShare Pro API as primary data source for stock universe and quotes.

## Data Sources
- Primary: TuShare Pro API (stock_basic, stock_company, daily, trade_cal)
  - Client: `app/core/providers/tushare_client.py`
  - Ingest: `app/services/tushare_ingest.py`
  - Raw backup: `data/` directory (JSONL)
- Fallback: Exchange crawlers + AKShare + yfinance (`app/services/universe_ingest.py`)
- Index: CNINFO WebAPI (`app/core/providers/cninfo_client.py`)

Tushare API Reference: [Tushare API Reference](../docs/references/tushare/index.md)
database use "quay.io/sclorg/postgresql-15-c9s:latest" as base image.

IMPORTANT:
- UPDATE THIS FILE WHEN YOU MEET SOMETHING IMPORTANT AND USEFUL
- AFTER EACH TASK COMPLETION, SUMMARIZE USEFUL EXPERIENCES AND ADD TO [THIS DOCUMENT](../docs/references/best-practice.md)