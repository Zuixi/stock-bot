"""Task 8 Step 0 spike: is SW industry membership history available upstream?

Question: can ``index_member`` (TuShare Pro) return Shenwan industry members
**with** ``in_date`` / ``out_date`` — the two fields that make SCD2
back-fill possible?  Coverage: all 31 L1 industries?  L2 / L3 too?

Usage: cd backend && .venv/bin/python scripts/spike_sw_member_history.py

Results are recorded by hand into docs/design/data-source.md (append-only).

**Reproducibility contract（Fix round 1 · 评审 I3）**：本脚本是
``docs/design/data-source.md`` 附.6.2 #1–#3 的**唯一**证据来源，故它必须
① 真的做 ``src`` 参数枚举、② 真的跑 31 个 L1 的全量覆盖度扫描、
③ 在 L1 列表为空 / 覆盖不满时**响亮失败**（``SystemExit``），而不是打印 ``[OK]``
后静默退化成 ``sample=[]``（旧版本用 ``index_classify(src="SW")``，实测返回 0 行，
于是 PROBE 4 的 ``[COV ] 0/0`` 看起来像"跑过了"）。

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

# 对照表里三级的预期条数（申万 2021 版）；实测不符即失败，避免"静默扫了个空集合"
EXPECTED_BY_LEVEL = {1: 31, 2: 134, 3: 346}
EXPECTED_L1 = EXPECTED_BY_LEVEL[1]
# `index_classify` 的 src 候选（附.6.2 #2 逐字记录的就是这几个）
SRC_CANDIDATES = ["", "SW", "SW2021", "SW2014"]


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


async def _probe_src_enumeration(client) -> dict[str, int]:
    """PROBE 1：`index_classify` 的 `src` 参数枚举（附.6.2 #2 的来源）。

    返回 {src 字面量: 行数}。**不假设**哪个 src 是对的——那正是本探针要测的东西。
    """
    print(f"\n{'=' * 72}\n[PROBE 1] index_classify src 枚举：{SRC_CANDIDATES}")
    counts: dict[str, int] = {}
    for src in SRC_CANDIDATES:
        kwargs = {"src": src} if src else {}
        label = src or "(不传 src)"
        try:
            df = await client._query("index_classify", **kwargs)
        except Exception as exc:  # noqa: BLE001 — spike wants the raw error
            print(f"[ERR ] src={label}: {type(exc).__name__}: {exc}")
            continue
        counts[src] = len(df)
        dist = dict(df["level"].value_counts()) if len(df) and "level" in df.columns else {}
        print(f"[OK  ] src={label:<12} rows={len(df):>4} level 分布={dist}")
    return counts


async def _probe_l1_sweep(client, l1_codes: list[str]) -> tuple[int, str, int]:
    """PROBE 3：31 个 L1 逐条覆盖度扫描（附.6.2 #3 的来源）。

    返回 (成功条数, 最早 in_date, 区间行合计)。空列表 / 覆盖不满 / 历史太浅
    一律 ``SystemExit`` —— "扫了个空集合还打印 OK"正是评审 I3 指出的不可复现形态。
    """
    if not l1_codes:
        raise SystemExit(
            "[FATAL] L1 列表为空：index_classify 的 src 枚举没有任何一个返回 L1。"
            "本探针拒绝在空集合上打印 [OK]。"
        )
    print(f"\n{'=' * 72}\n[PROBE 3] {len(l1_codes)} 个 L1 逐条 index_member 覆盖度扫描")
    ok = 0
    earliest: str | None = None
    total_rows = 0
    for i, code in enumerate(sorted(l1_codes)):
        try:
            df = await client._query("index_member", index_code=code)
        except Exception as exc:  # noqa: BLE001
            print(f"[COV ] {i:02d} {code}: FAIL {type(exc).__name__}: {exc}")
            continue
        total_rows += len(df)
        has_in_date = "in_date" in df.columns
        non_null = int(df["in_date"].notna().sum()) if has_in_date and len(df) else 0
        if len(df) and has_in_date and non_null == len(df):
            ok += 1
        row_min = str(df["in_date"].min()) if non_null else "-"
        if non_null:
            earliest = row_min if earliest is None else min(earliest, row_min)
        print(
            f"[COV ] {i:02d} {code}: rows={len(df):>4} "
            f"in_date 非空={non_null}/{len(df)} min={row_min}"
        )
    print(
        f"[COV ] {ok}/{len(l1_codes)} 个 L1 返回非空且 in_date 全非空；"
        f"区间行合计={total_rows}；最早 in_date={earliest}"
    )
    if ok != len(l1_codes):
        raise SystemExit(
            f"[FATAL] L1 覆盖度 {ok}/{len(l1_codes)}，与附.6 声明的 31/31 不符"
            "——文档里的实机记录必须能被本脚本复现，故这里直接失败。"
        )
    if earliest is None or earliest > "19950101":
        raise SystemExit(
            f"[FATAL] 最早 in_date={earliest}，不像「可回溯到 1990 年代」的申万历史"
            "——上游形态可能已变，请重核附.6 的结论。"
        )
    return ok, str(earliest), total_rows


async def main() -> None:
    from app.core.providers.tushare_client import TuShareClient

    client = TuShareClient(_load_token())

    # ---- 1. index_classify 的 src 枚举（必须先做：L1 列表由它产出）----
    await _probe_src_enumeration(client)
    tree = await client._query("index_classify", src="SW2021")
    print(f"\n[OK  ] 采用 src='SW2021'：rows={len(tree)} cols={list(tree.columns)}")
    expected_total = sum(EXPECTED_BY_LEVEL.values())
    if len(tree) != expected_total:
        raise SystemExit(
            f"[FATAL] src='SW2021' 返回 {len(tree)} 行，与对照表 {expected_total} 行不符"
            "——附.6.1 ⑬ 的结论需要重核。"
        )
    dist = {int(k): int(v) for k, v in tree["level"].value_counts().items()}
    print(f"[OK  ] level 分布={dist}（期望 {EXPECTED_BY_LEVEL}）")
    if dist != EXPECTED_BY_LEVEL:
        raise SystemExit(f"[FATAL] level 分布 {dist} ≠ 期望 {EXPECTED_BY_LEVEL}")

    l1_codes = sorted(tree[tree["level"] == "L1"]["index_code"].tolist())
    if len(l1_codes) != EXPECTED_L1:
        raise SystemExit(f"[FATAL] L1 个数 {len(l1_codes)} ≠ 契约 {EXPECTED_L1}")
    print(f"[OK  ] L1 个数={len(l1_codes)}")
    print(f"[OK  ] L1 codes={l1_codes}")

    # ---- 2. index_member 是否带 in_date / out_date（单个 L1 深看）----
    probe_code = l1_codes[0]
    print(f"\n{'=' * 72}\n[PROBE 2] index_member 深看 index_code={probe_code}（不传日期参数）")
    df = await client._query("index_member", index_code=probe_code)
    print(f"[OK  ] rows={len(df)} cols={list(df.columns)}")
    print(df.head(5).to_string())
    for col in ("in_date", "out_date"):
        if col in df.columns:
            non_null = int(df[col].notna().sum())
            print(
                f"[HIST] {col}: non-null {non_null}/{len(df)} "
                f"min={df[col].min()!r} max={df[col].max()!r}"
            )
        else:
            raise SystemExit(f"[FATAL] index_member 返回缺列 {col} —— SCD2 历史不可回填")

    # ---- 2b. is_new 与 out_date IS NULL 是否等价（附.6.2 #7 的来源）----
    print(f"\n{'=' * 72}\n[PROBE 2b] is_new 分裂（附.6.2 #7）")
    split_total = 0
    for flag in ("Y", "N"):
        sub = await client._query("index_member", index_code=probe_code, is_new=flag)
        split_total += len(sub)
        nn = int(sub["out_date"].notna().sum()) if len(sub) else 0
        print(f"[SPLIT] is_new={flag}: rows={len(sub)} out_date 非空={nn}/{len(sub)}")
    print(f"[SPLIT] Y + N = {split_total}（不传参时 {len(df)}；相等 ⇒ 分裂是完备的）")

    # ---- 2c. 日期窗口是否被接受（附.6.2 #6 的来源）----
    print(f"\n{'=' * 72}\n[PROBE 2c] 同一 index_member 传日期窗口")
    win = await client._query(
        "index_member",
        index_code=probe_code,
        start_date="20240102",
        end_date="20240102",
    )
    print(
        f"[WINDOW] start_date=20240102&end_date=20240102 → rows={len(win)}"
        f"（不传参时 {len(df)}）与不传参相同={len(win) == len(df)}"
    )

    # ---- 3. 31 个 L1 全量覆盖度扫描（附.6.2 #3）----
    await _probe_l1_sweep(client, l1_codes)

    # ---- 4. L2 / L3 抽样 ----
    for level in ("L2", "L3"):
        subset = tree[tree["level"] == level]
        if subset.empty:
            raise SystemExit(f"[FATAL] index_classify 没有 {level} 行")
        code = sorted(subset["index_code"].tolist())[0]
        print(f"\n{'=' * 72}\n[PROBE 4] index_member {level} 抽样 code={code}")
        sub = await client._query("index_member", index_code=code)
        print(f"[OK  ] rows={len(sub)} cols={list(sub.columns)}")
        for col in ("in_date", "out_date"):
            nn = int(sub[col].notna().sum()) if len(sub) else 0
            print(f"[HIST] {col}: non-null {nn}/{len(sub)}")


if __name__ == "__main__":
    asyncio.run(main())
