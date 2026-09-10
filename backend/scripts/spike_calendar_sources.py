"""One-off spike: probe TuShare calendar/news interfaces with the real token.

Usage: cd backend && uv run python scripts/spike_calendar_sources.py

Results are recorded by hand into docs/design/data-source.md.

NOTE: uses the private _query() on purpose — spike only, not production code.
The client's retry/error hooks rewrite upstream error text (permission errors
become a canned RuntimeError, other errors become "failed after N retries"),
so on failure we re-probe through the raw ``_pro`` handle to capture the
verbatim upstream message needed for the availability verdict table.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.providers.tushare_client import get_tushare_client  # noqa: E402

# (api, kwargs, purpose) — keep exactly the 8 interfaces from the task brief.
PROBES: list[tuple[str, dict[str, str], str]] = [
    ("disclosure_date", {"start_date": "20260901", "end_date": "20260930"}, "财报披露计划"),
    ("dividend", {"ts_code": "600519.SH"}, "分红送股"),
    ("new_share", {"start_date": "20260801", "end_date": "20260911"}, "IPO 新股"),
    (
        "trade_cal",
        {"exchange": "SSE", "start_date": "20260101", "end_date": "20261231"},
        "交易日历",
    ),
    ("eco_cal", {"start_date": "20260901", "end_date": "20260930"}, "宏观日历"),
    ("news", {"start_date": "20260910", "end_date": "20260911"}, "新闻快讯"),
    (
        "major_news",
        {"start_date": "20260910 00:00:00", "end_date": "20260911 00:00:00"},
        "长篇通讯",
    ),
    ("cctv_news", {"date": "20260910"}, "新闻联播文字稿"),
]


async def main() -> None:
    client = get_tushare_client()
    for api, kwargs, purpose in PROBES:
        sent = ", ".join(f"{k}={v!r}" for k, v in kwargs.items())
        print(f"\n{'=' * 72}\n[PROBE] {api} ({purpose})  sent: {sent}")
        try:
            df = await client._query(api, **kwargs)
            print(f"[OK ] {api}: rows={len(df)} cols={list(df.columns)}")
            if len(df):
                print(df.head(2).to_string())
        except Exception as exc:  # noqa: BLE001 — spike wants the raw error
            print(f"[ERR] {api}: client={type(exc).__name__}: {exc}")
            try:
                raw = client._pro.query(api, **kwargs)
                print(f"[RAW] {api}: returned unexpectedly: rows={len(raw)}")
            except Exception as raw_exc:  # noqa: BLE001 — spike wants raw text
                print(f"[RAW] {api}: {type(raw_exc).__name__}: {raw_exc}")


if __name__ == "__main__":
    asyncio.run(main())
