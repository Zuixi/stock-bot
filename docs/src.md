# SRC 组件说明

## Architecture Overview

### Module Structure
```
src/
├── cli/          # CLI entry point (typer-based commands)
├── config/       # YAML configuration loader
├── fetchers/     # Exchange-specific API clients (sse, sze, bse)
├── models/       # Pydantic models (config, stock, manifest)
├── normalizers/  # Convert raw records to unified StockRecord schema
└── storage/      # Snapshot-based JSONL storage with manifests
```

### Data Flow
1. **CLI** ([cli/universe.py](src/cli/universe.py)) - User-facing commands using typer
2. **Fetcher** ([fetchers/sse/](src/fetchers/sse/)) - Exchange API client with pagination, rate limiting, retries
3. **Normalizer** ([normalizers/sse.py](src/normalizers/sse.py)) - Converts raw exchange records to unified `StockRecord`
4. **Storage** ([storage/universe.py](src/storage/universe.py)) - Writes JSONL files with manifest metadata

### Key Abstractions

**Fetcher Pattern**: Each exchange implements a fetcher with:
- `client` - HTTP client with JSONP parsing (SSE uses JSONP)
- `iter_raw_records(asof)` - Generator yielding `(raw_record, source_url, asof_timestamp)`
- Config-driven rate limiting, retries, pagination

**Normalizer Pattern**: Converts exchange-specific raw records to unified `StockRecord` schema. The normalizer must map:
- `exchange` - Must be exactly "Shanghai_Stocks", "Shenzen_Stocks", or "Beijing_Stocks"
- `symbol` - Stock code (e.g., "600105")
- `category` - Exchange's official classification (preserve original, don't normalize cross-exchange yet)

**Storage Structure**:
```
data/universe/
  snapshot=2026-01-30T12-00-00Z/
    manifest.json                    # Fetch metadata, stats, config (sanitized)
    Shanghai_Stocks/
      class=STOCK_TYPE_1_主板A股.jsonl
```

Each JSONL file contains one `StockRecord` per line. Files are grouped by exchange/category for efficient partitioning.

### Configuration System

Exchange-specific YAML configs in [src/config/](src/config/):
- `sse.yaml` - SSE fetcher config (requires cookies, never commit)
- `sse.sample.yaml` - Template with documented fields

Config loading: `load_config("sse")` returns dict, then `SseConfig.from_yaml(data)` creates Pydantic model.

**Security**: Configs may contain cookies/secrets. Never log or commit `sse.yaml`. Use `config.get_safe_headers()` for manifests.

### Exchange Naming Convention

**Critical**: Use these exact strings for `exchange` field (defined in [models/stock.py](src/models/stock.py)):
- `Shanghai_Stocks` (SSE)
- `Shenzen_Stocks` (SZSE) - note: "Shenzen" not "Shenzhen"
- `Beijing_Stocks` (BSE)

This is used for directory names and filtering. Do not change without updating storage layer.

## Product Roadmap Context

Current state: **M0** - Stock universe fetching for SSE and BSE.

Planned milestones:
- M0: Multi-exchange universe fetch + normalize + persist (SSE + BSE complete)
- M1: Daily trading data fetch + incremental updates
- M2: Feature engineering + clustering
- M3: LLM cluster interpretation
- M4: SZSE support + scheduled tasks
