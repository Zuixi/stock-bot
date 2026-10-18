# -*- coding: utf-8 -*-
"""Smoke-test for CNINFO client (index APIs) and AKShare market data.

Usage (from backend/):
    .venv\\Scripts\\python.exe scripts\\test_cninfo_and_market.py

Tests
-----
  1. mcode generation           -- pure-Python, no network
  2. CNINFO p_index2905         -- GET /api/index/p_index2905 (exchange indices)
  3. CNINFO p_swindex           -- GET /api/index/p_swindex   (Shenwan indices)
  4. CNINFO p_sysapi1015        -- POST /api/sysapi/p_sysapi1015 (stock quote, needs token)
  5. AKShare market indices     -- fallback: stock_zh_index_spot_sina
"""

from __future__ import annotations

import asyncio
import math
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

# Force UTF-8 output on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Allow running from repo root or backend/
sys.path.insert(0, str(Path(__file__).parents[1]))


# ---------------------------------------------------------------------------
# 1. mcode generation (no network)
# ---------------------------------------------------------------------------

def test_mcode() -> None:
    from app.core.providers.cninfo_client import _generate_mcode  # noqa: PLC0415

    mcode = _generate_mcode()
    print(f"[PASS] mcode = {mcode!r}  (len={len(mcode)})")

    KEY = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
    assert all(c in KEY for c in mcode), "mcode contains unexpected characters"
    # Unix epoch seconds is 10 digits -> ceil(10/3)*4 = 16 Base64 chars
    assert len(mcode) == 16, f"expected 16 chars, got {len(mcode)}"
    print("[PASS] mcode length correct (16)")


# ---------------------------------------------------------------------------
# 2. CNINFO p_index2905 — 交易所指数日行情
# ---------------------------------------------------------------------------

async def test_p_index2905() -> None:
    """GET /api/index/p_index2905 — no token required (free endpoint)."""
    from app.core.providers.cninfo_client import CnInfoClient  # noqa: PLC0415

    today = date.today()
    print(f"\n[TEST] CNINFO p_index2905  edate={today}")

    async with CnInfoClient() as client:
        records = await client.get_index_daily(edate=today)

    print(f"[INFO] Records returned: {len(records)}")
    if not records:
        # Try yesterday if today is a holiday / weekend
        yesterday = today - timedelta(days=1)
        while yesterday.weekday() >= 5:
            yesterday -= timedelta(days=1)
        print(f"[INFO] Retrying with last weekday: {yesterday}")
        async with CnInfoClient() as client:
            records = await client.get_index_daily(edate=yesterday)
        print(f"[INFO] Records returned: {len(records)}")

    if records:
        target = {"000001", "000300", "399001", "399006"}
        matched = [r for r in records if r.get("code") in target]
        print(f"[INFO] Target indices found: {[r['code'] for r in matched]}")
        for r in matched:
            print(f"       {r['code']} {r['name']:10s}  "
                  f"close={r.get('close')}  change={r.get('change')}  "
                  f"pct={r.get('changePercent')}%")
        if matched:
            print("[PASS] p_index2905 returned live index data")
        else:
            print(f"[WARN] p_index2905 returned {len(records)} records but none matched target codes")
            print(f"       Sample codes: {[r.get('code') for r in records[:5]]}")
    else:
        print("[WARN] p_index2905 returned no records")
        print("       Possible causes: auth required, server down, or not a trading day")


# ---------------------------------------------------------------------------
# 3. CNINFO p_swindex — 申万指数行情
# ---------------------------------------------------------------------------

async def test_p_swindex() -> None:
    """GET /api/index/p_swindex — no token required (free endpoint)."""
    from app.core.providers.cninfo_client import CnInfoClient  # noqa: PLC0415

    today = date.today()
    yesterday = today - timedelta(days=1)
    while yesterday.weekday() >= 5:
        yesterday -= timedelta(days=1)

    print(f"\n[TEST] CNINFO p_swindex  edate={yesterday}")

    async with CnInfoClient() as client:
        records = await client.get_sw_index(edate=yesterday)

    print(f"[INFO] Records returned: {len(records)}")
    if records:
        print(f"[INFO] Sample SW indices (first 5):")
        for r in records[:5]:
            print(f"       {r.get('code')} {r.get('name'):15s}  "
                  f"close={r.get('close')}  pct={r.get('changePercent')}%")
        print("[PASS] p_swindex returned SW industry index data")
    else:
        print("[WARN] p_swindex returned no records")


# ---------------------------------------------------------------------------
# 4. CNINFO p_sysapi1015 — 个股日行情（需要 token）
# ---------------------------------------------------------------------------

async def test_p_sysapi1015() -> None:
    from app.core.providers.cninfo_client import CnInfoClient  # noqa: PLC0415

    token = os.environ.get("CNINFO_TOKEN", "")
    if not token:
        print("[SKIP] CNINFO_TOKEN not set — p_sysapi1015 requires a registered paid token.")
        print("       Register at https://webapi.cninfo.com.cn and run:")
        print("         $env:CNINFO_TOKEN = '<your-token>'")
        return

    today = date.today()
    check = today - timedelta(days=1)
    while check.weekday() >= 5:
        check -= timedelta(days=1)

    symbol = "600519"
    print(f"\n[TEST] CNINFO p_sysapi1015  symbol={symbol}  date={check}")

    async with CnInfoClient(token=token) as client:
        result = await client.get_daily_quote(symbol, check)

    if result:
        print(f"[PASS] {symbol}  close={result['close']}  vol={result.get('volume')}")
    else:
        print(f"[WARN] No data returned for {symbol} on {check}")


# ---------------------------------------------------------------------------
# 5. AKShare fallback — market indices
# ---------------------------------------------------------------------------

def test_akshare_indices_fallback() -> None:
    """AKShare is the fallback when CNINFO index API is unavailable."""
    print("\n[TEST] AKShare fallback: stock_zh_index_spot_sina")
    import akshare as ak  # noqa: PLC0415

    df = ak.stock_zh_index_spot_sina()
    targets = {"sh000001", "sz399001", "sh000300"}
    codes = set(str(c).lower().strip() for c in df["代码"])
    matched = targets & codes
    print(f"[INFO] {len(df)} total rows.  Target codes found: {matched}")
    if matched:
        sub = df[df["代码"].str.lower().isin(targets)]
        print(sub[["代码", "名称", "最新价", "涨跌额", "涨跌幅"]].to_string(index=False))
        print("[PASS] AKShare fallback indices OK")
    else:
        print("[WARN] Target codes not found")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _section(label: str) -> None:
    print(f"\n{'='*60}\n  {label}\n{'='*60}")


async def main() -> None:
    _section("1. mcode generation")
    test_mcode()

    _section("2. CNINFO p_index2905 (exchange index daily)")
    try:
        await test_p_index2905()
    except Exception as e:
        print(f"[FAIL] {e}")

    _section("3. CNINFO p_swindex (Shenwan index)")
    try:
        await test_p_swindex()
    except Exception as e:
        print(f"[FAIL] {e}")

    _section("4. CNINFO p_sysapi1015 (stock quote, paid)")
    try:
        await test_p_sysapi1015()
    except Exception as e:
        print(f"[FAIL] {e}")

    _section("5. AKShare fallback (market indices)")
    try:
        test_akshare_indices_fallback()
    except Exception as e:
        print(f"[FAIL] {e}")

    print(f"\n{'='*60}\n  Done.\n{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
