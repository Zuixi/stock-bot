# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Linting and Formatting
```bash
# Run linter (configured in pyproject.toml: line-length=100)
ruff check src/

# Auto-fix lint issues
ruff check --fix src/

# Type checking
mypy src/
```

### Testing
```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/path/to/test_file.py
```

### CLI Usage
```bash
# Fetch stock universe (SSE only in M0)
stock-bot universe fetch --exchange sse --stock-type 1

# List available snapshots
stock-bot universe list

# For development with local module
python -m src.cli.universe fetch --exchange sse
```

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

Current state: **M0** - Stock universe fetching for SSE only.

Planned milestones:
- M0: Single exchange universe fetch + normalize + persist (current)
- M1: Daily trading data fetch + incremental updates
- M2: Feature engineering + clustering
- M3: LLM cluster interpretation
- M4: All three exchanges + scheduled tasks

See [product.md](product.md) for full requirements.

## Adding a New Exchange

1. Create `src/fetchers/{exchange}/` with `client.py` and `fetcher.py`
2. Create `Raw{Exchange}Record` model in `models/stock.py`
3. Create `normalize_{exchange}_record()` in `normalizers/{exchange}.py`
4. Add YAML config in `src/config/{exchange}.sample.yaml`
5. Update CLI in `cli/universe.py` to support new exchange

Follow the SSE implementation as reference. The storage layer expects the same `iter_raw_records()` generator pattern.
