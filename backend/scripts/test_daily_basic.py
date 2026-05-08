#!/usr/bin/env python3
"""Test TuShare daily_basic API connectivity and data shape.

Run: cd backend && uv run python scripts/test_daily_basic.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure backend/app is on sys.path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

import tushare as ts  # noqa: E402

# ── Config ──────────────────────────────────────────────────────
TOKEN = os.getenv("TUSHARE_TOKEN", "").strip()
if not TOKEN:
    # Try loading from .env directly
    env_file = backend_dir / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("TUSHARE_TOKEN="):
                TOKEN = line.split("=", 1)[1].strip().strip('"').strip("'")
                break

if not TOKEN:
    print("❌ TUSHARE_TOKEN not set. Put it in backend/.env")
    sys.exit(1)

print(f"🔑 Token loaded: {TOKEN[:8]}...{TOKEN[-4:]}")
pro = ts.pro_api(TOKEN)

# ── Test 1: Single stock, one day ──────────────────────────────
print("\n── Test 1: 单只股票单日查询 (000001.SZ, 2025-05-07) ──")
try:
    df = pro.daily_basic(
        ts_code="000001.SZ",
        trade_date="20250507",
        fields="ts_code,trade_date,close,turnover_rate,volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,total_share,float_share,free_share,total_mv,circ_mv",
    )
    if df is not None and not df.empty:
        print(f"  ✅ rows={len(df)}, columns={list(df.columns)}")
        print(df.to_string(index=False))
    else:
        print("  ⚠️  Empty result (maybe non-trading day?)")
except Exception as e:
    print(f"  ❌ Error: {e}")

# ── Test 2: Single stock, date range (history) ─────────────────
print("\n── Test 2: 单只股票日期范围查询 (000001.SZ, 2025-04-01 ~ 2025-05-07) ──")
try:
    df = pro.daily_basic(
        ts_code="000001.SZ",
        start_date="20250401",
        end_date="20250507",
        fields="ts_code,trade_date,close,pe,pe_ttm,pb,total_mv,circ_mv,turnover_rate",
    )
    if df is not None and not df.empty:
        print(f"  ✅ rows={len(df)}, columns={list(df.columns)}")
        print(df.head(5).to_string(index=False))
        print(f"  ... (total {len(df)} rows)")
    else:
        print("  ⚠️  Empty result")
except Exception as e:
    print(f"  ❌ Error: {e}")

# ── Test 3: Full market, single day (最常用模式) ─────────────
print("\n── Test 3: 全市场单日查询 (trade_date=20250507) ──")
try:
    df = pro.daily_basic(
        trade_date="20250507",
        fields="ts_code,trade_date,close,turnover_rate,volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,total_share,float_share,free_share,total_mv,circ_mv",
    )
    if df is not None and not df.empty:
        print(f"  ✅ rows={len(df)}, columns={list(df.columns)}")
        print(f"  前5行:")
        print(df.head(5).to_string(index=False))
        print(f"  ... (total {len(df)} rows)")

        # Basic stats
        print(f"\n  📊 统计摘要:")
        print(f"     pe       : count={df['pe'].count()},   min={df['pe'].min():.2f}, max={df['pe'].max():.2f}, median={df['pe'].median():.2f}")
        print(f"     pb       : count={df['pb'].count()},   min={df['pb'].min():.2f}, max={df['pb'].max():.2f}, median={df['pb'].median():.2f}")
        print(f"     total_mv : count={df['total_mv'].count()}, min={df['total_mv'].min()/1e8:.0f}亿, max={df['total_mv'].max()/1e8:.0f}亿")
        print(f"     turnover : count={df['turnover_rate'].count()}, min={df['turnover_rate'].min():.4f}, max={df['turnover_rate'].max():.2f}")
    else:
        print("  ⚠️  Empty result (maybe non-trading day or weekend?)")
except Exception as e:
    print(f"  ❌ Error: {e}")

# ── Test 4: Verify ts_code format compatibility ────────────────
print("\n── Test 4: 检查 ts_code 格式与 stocks 表兼容性 ──")
try:
    df = pro.daily_basic(
        trade_date="20250507",
        fields="ts_code",
    )
    if df is not None and not df.empty:
        codes = df["ts_code"].head(10).tolist()
        print(f"  Sample ts_codes: {codes}")
        # Check suffixes
        suffixes = set(c.split(".")[-1] if "." in c else "NONE" for c in df["ts_code"])
        print(f"  Exchange suffixes: {suffixes}")
        if {"SH", "SZ", "BJ"}.issubset(suffixes):
            print("  ✅ 三交易所数据齐全")
        else:
            print(f"  ⚠️  缺失: {set(['SH','SZ','BJ']) - suffixes}")
except Exception as e:
    print(f"  ❌ Error: {e}")

# ── Test 5: Verify fields we care about are present ────────────
print("\n── Test 5: 验证所有目标字段存在 ──")
EXPECTED_FIELDS = {
    "ts_code", "trade_date", "close",
    "turnover_rate", "turnover_rate_f", "volume_ratio",
    "pe", "pe_ttm", "pb", "ps", "ps_ttm",
    "dv_ratio", "dv_ttm",
    "total_share", "float_share", "free_share",
    "total_mv", "circ_mv",
}
try:
    df = pro.daily_basic(trade_date="20250507")
    if df is not None and not df.empty:
        actual = set(df.columns)
        missing = EXPECTED_FIELDS - actual
        if missing:
            print(f"  ❌ 缺失字段: {missing}")
        else:
            print(f"  ✅ 所有 {len(EXPECTED_FIELDS)} 个字段齐全")
        print(f"  额外字段: {actual - EXPECTED_FIELDS}")
except Exception as e:
    print(f"  ❌ Error: {e}")

print("\n══════════════════════════════════════════════")
print("测试完成。如果前3个测试都 ✅，即可开始迁移。")
