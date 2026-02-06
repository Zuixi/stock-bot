# stock bot

## Overview

stock bot is an automated tool for building a unified stock universe from China’s three exchanges (SSE, SZSE, BSE) and preparing data for downstream analysis. The current codebase focuses on exchange stock lists (M0) and provides a CLI to fetch, normalize, and persist results as snapshot JSONL files.

## Features

- Fetch full stock lists from SSE, SZSE, and BSE.
- Normalize raw exchange records into a unified `StockRecord` schema.
- Snapshot-based storage with manifest metadata for reproducibility.
- Modular fetcher/normalizer architecture for future data providers.
- CLI-based workflow for batch jobs.

## Project Structure

```
src/
	cli/          # CLI entry point
	fetchers/     # Exchange-specific clients and fetchers (sse, szse, bse)
	models/       # Pydantic models (config, stock, manifest)
	normalizers/  # Convert raw records to StockRecord
	storage/      # Snapshot JSONL storage + manifest
```

## Setup

Requirements: Python 3.11+.

Install the package in editable mode:

```
pip install -e .
```

## Configuration

Exchange configs live under `src/config/`:

- `sse.sample.yaml` → copy to `sse.yaml`
- `szse.sample.yaml` → copy to `szse.yaml`
- `bse.sample.yaml` → copy to `bse.yaml` (if applicable)

SSE and SZSE require cookies. Copy from browser DevTools and put them under the `cookies` section. Never commit real cookies.

## CLI Usage

```
# Fetch SSE stock universe (A-share by default)
stock-bot universe fetch --exchange sse --stock-type 1

# Fetch BSE stock universe
stock-bot universe fetch --exchange bse

# Fetch SZSE stock universe
stock-bot universe fetch --exchange sze

# List snapshots
stock-bot universe list

# Development (module execution)
python -m src.cli.universe fetch --exchange sse
python -m src.cli.universe fetch --exchange bse
python -m src.cli.universe fetch --exchange sze
```

## Data Output

Snapshots are written to `data/universe/`:

```
data/universe/
	snapshot=YYYY-MM-DDTHH-MM-SSZ/
		manifest.json
		Shanghai_Stocks/
			class=STOCK_TYPE_1_主板A股.jsonl
		Shenzen_Stocks/
			class=Shenzen_Stocks_主板.jsonl
		Beijing_Stocks/
			class=Beijing_Stocks_T.jsonl
```

Each JSONL line is a normalized `StockRecord`.

## Roadmap

See product requirements and milestones in `product.md`.
