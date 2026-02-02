# AGENT.md

This file provides additional guidance for Claude Code when working with this project.

## Quick Start

### Fetch Stock Universe
```bash
# SSE (Shanghai Stock Exchange)
python -m src.cli.universe fetch --exchange sse --stock-type 1

# BSE (Beijing Stock Exchange)
python -m src.cli.universe fetch --exchange bse
```

### Data Storage
- **Location**: `data/universe/snapshot=YYYY-MM-DDTHH-MM-SSZ/`
- **Format**: JSONL (one JSON record per line)
- **Exchange directories**: `Shanghai_Stocks/`, `Beijing_Stocks/`

### Configuration
- Copy `src/config/{exchange}.sample.yaml` to `src/config/{exchange}.yaml`
- Fill in cookie values from browser DevTools
- Never commit config files with secrets

## Key Files
- `src/fetchers/bse/client.py` - BSE JSONP API client
- `src/fetchers/bse/fetcher.py` - BSE pagination and record iteration
- `src/normalizers/bse.py` - BSE to unified StockRecord normalization
- `src/models/stock.py` - RawBseRecord and StockRecord models
