"""Task 8 Step 0 spike: is SW industry membership history available upstream?

Question: can ``index_member`` (TuShare Pro) return Shenwan industry members
**with** ``in_date`` / ``out_date`` — the two fields that make SCD2
back-fill possible?  Coverage: all 31 L1 industries?  L2 / L3 too?

Usage: cd backend && .venv/bin/python scripts/spike_sw_member_history.py

Results are recorded by hand into docs/design/data-source.md (append-only).
NOTE: uses the private ``_query()`` on purpose — spike only, not production
code.  The client's retry/error hooks rewrite upstream error text, so on
failure we re-probe through the raw ``_pro`` handle to capture the verbatim
upstream message needed for the three-state verdict.
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _load_token() -> str:
    """Load TUSHARE_TOKEN without printing it.

    This worktree has no ``backend/.env`` (git-ignored); fall back to the
    main checkout's ``backend/.env`` used by every other spike in this repo.
    """
    from dotenv import dotenv_values

    candidates = [
        Path(__file__).resolve().parents[1] / ".env",
        Path("/Users/wqz/Developer/stock-bot/backend/.env"),
    ]
    for path in candidates:
        if path.exists():
            token = dotenv_values(path).get("TUSHARE_TOKEN") or ""
            if token:
                print(f"[ENV ] token loaded from {path} (len={len(token)}, value not printed)")
                return token
    raise SystemExit("[ENV ] no TUSHARE_TOKEN found in any candidate .env")


async def main() -> None:
    from app.core.providers.tushare_client import TuShareClient

    client = TuShareClient(_load_token())

    # ---- 1. index_classify: the SW tree, all three levels -------------
    print(f"\n{'=' * 72}\n[PROBE 1] index_classify src=SW (no level) — full tree")
    tree = await client._query("index_classify", src="SW")
    print(f"[OK  ] rows={len(tree)} cols={list(tree.columns)}")
    if "level" in tree.columns:
        print("[OK  ] level distribution: " + repr(tree["level"].value_counts().to_dict()))
    print(tree.head(3).to_string())

    l1 = tree[tree["level"] == "L1"] if "level" in tree.columns else tree
    l1_codes = sorted(l1["index_code"].tolist())
    print(f"[OK  ] L1 count={len(l1_codes)}")
    print(f"[OK  ] L1 codes={l1_codes}")

    # ---- 2. index_member: does it carry in_date / out_date? ----------
    print(f"\n{'=' * 72}\n[PROBE 2] index_member for one L1 (bare call, no date args)")
    probe_code = l1_codes[0] if l1_codes else "801010.SI"
    print(f"[     ] probe index_code={probe_code}")
    try:
        df = await client._query("index_member", index_code=probe_code)
        print(f"[OK  ] rows={len(df)} cols={list(df.columns)}")
        print(df.head(5).to_string())
        for col in ("in_date", "out_date"):
            if col in df.columns:
                non_null = df[col].notna().sum()
                print(
                    f"[HIST] {col}: non-null {non_null}/{len(df)} "
                    f"min={df[col].min()!r} max={df[col].max()!r}"
                )
            else:
                print(f"[HIST] {col}: COLUMN ABSENT")
    except Exception as exc:  # noqa: BLE001 — spike wants the raw error
        print(f"[ERR ] client={type(exc).__name__}: {exc}")
        try:
            raw = client._pro.query("index_member", index_code=probe_code)
            print(f"[RAW ] returned unexpectedly rows={len(raw)} cols={list(raw.columns)}")
        except Exception as raw_exc:  # noqa: BLE001
            print(f"[RAW ] {type(raw_exc).__name__}: {raw_exc}")

    # ---- 3. index_member for L2 / L3 ---------------------------------
    for level in ("L2", "L3"):
        subset = tree[tree["level"] == level] if "level" in tree.columns else tree
        if subset.empty:
            print(f"\n[     ] no {level} rows in index_classify; skip")
            continue
        code = sorted(subset["index_code"].tolist())[0]
        print(f"\n{'=' * 72}\n[PROBE 3] index_member for {level} code={code}")
        try:
            df = await client._query("index_member", index_code=code)
            print(f"[OK  ] rows={len(df)} cols={list(df.columns)}")
            for col in ("in_date", "out_date"):
                if col in df.columns:
                    print(
                        f"[HIST] {col}: non-null {df[col].notna().sum()}/{len(df)} "
                        f"min={df[col].min()!r} max={df[col].max()!r}"
                    )
                else:
                    print(f"[HIST] {col}: COLUMN ABSENT")
            print(df.head(3).to_string())
        except Exception as exc:  # noqa: BLE001
            print(f"[ERR ] client={type(exc).__name__}: {exc}")
            try:
                raw = client._pro.query("index_member", index_code=code)
                print(f"[RAW ] rows={len(raw)} cols={list(raw.columns)}")
            except Exception as raw_exc:  # noqa: BLE001
                print(f"[RAW ] {type(raw_exc).__name__}: {raw_exc}")

    # ---- 4. coverage sweep: member counts for a sample of L1 ----------
    print(f"\n{'=' * 72}\n[PROBE 4] coverage sweep over L1 codes (member counts)")
    sample = l1_codes[:5] + l1_codes[-2:]
    ok = 0
    for i, code in enumerate(sample):
        try:
            df = await client._query("index_member", index_code=code)
            print(f"[COV ] {i:02d} {code}: rows={len(df)} ok")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[COV ] {i:02d} {code}: FAIL {type(exc).__name__}: {exc}")
    print(f"[COV ] {ok}/{len(sample)} L1 sampled codes returned members")

    # ---- 5. do historical date args change the answer? ---------------
    print(f"\n{'=' * 72}\n[PROBE 5] same index_member with historical date args")
    for kwargs in (
        {"index_code": probe_code, "start_date": "20240102", "end_date": "20240102"},
        {"index_code": probe_code, "is_new": "Y"},
        {"index_code": probe_code, "is_new": "N"},
    ):
        sent = ", ".join(f"{k}={v!r}" for k, v in kwargs.items())
        print(f"\n[     ] index_member {sent}")
        try:
            df = await client._query("index_member", **kwargs)
            print(f"[OK  ] rows={len(df)} cols={list(df.columns)}")
            if len(df):
                print(df.head(3).to_string())
                if "out_date" in df.columns:
                    print(f"[HIST] out_date non-null {df['out_date'].notna().sum()}/{len(df)}")
        except Exception as exc:  # noqa: BLE001
            print(f"[ERR ] client={type(exc).__name__}: {exc}")
            try:
                raw = client._pro.query("index_member", **kwargs)
                print(f"[RAW ] rows={len(raw)} cols={list(raw.columns)}")
            except Exception as raw_exc:  # noqa: BLE001
                print(f"[RAW ] {type(raw_exc).__name__}: {raw_exc}")


if __name__ == "__main__":
    asyncio.run(main())
