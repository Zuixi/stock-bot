# Copilot instructions (stock_bot)

## Project state
- This repo is currently a *product skeleton*: there is no application code yet.
- The canonical product intent is in [product.md](../product.md). Keep it aligned.

## Product requirements (from product.md)
- Stock universe: traverse all listed stocks from the three exchanges/pages:
  - Shanghai (SSE): https://www.sse.com.cn/assortment/stock/list/share/
  - Shenzhen (SZSE): https://www.szse.cn/market/stock/deal/index.html
  - Beijing (BSE): https://www.bse.cn/nq/listedcompany.html
- Classification model: first group by exchange, then within each exchange group by “stock class/category”, e.g.
  - `Shanghai_Stocks` → `class A/B/C/...`
  - `Beijing_Stocks` → `class A/B/C/...`
  - `Shenzen_Stocks` → `class A/B/C/...`

## Product direction (from product.md)
- After building the stock universe, the product also targets: fetching trading data (at least daily OHLCV) and using LLM-assisted clustering/trend analysis to help investors interpret price behavior.
- Treat “trading data source” as a pluggable provider; avoid hard-coding assumptions tied to a single website/API.

## How to contribute (given current repo)
- Prefer small, incremental PR-sized steps that add a thin vertical slice (e.g., one exchange universe fetch + normalize + persist; then one trading-data provider; then features → clustering → export).
- When adding code, also add/update documentation under [product.md](../product.md) (and optionally `docs/`) to reflect any concrete decisions (data schema, file layout, CLI usage).

## Naming & terminology
- Keep exchange naming consistent with [product.md](../product.md) when creating identifiers, output keys, folders, etc.
- If you discover the official “stock category/class” taxonomy differs per exchange, document the mapping decision in [product.md](../product.md) before refactoring widely.

## Repo conventions
- Editor settings live in `.vscode/settings.json`; avoid committing changes there unless they’re project-wide necessities.
