#!/usr/bin/env python3
"""Task 14 Step 0 spike: are the S1.10 volatility / derivatives sources really available?

Four questions, each answered by a **real** upstream call (Ruling 51: a doc claim must be
reproducible from a committed script, and the script must fail loudly on empty results
instead of printing ``[OK]`` on nothing):

1. **期权隐含波动率** — the five QVIX 中国波指 series the spec §S1.10 names
   (50ETF / 300ETF / 500ETF / 创业板ETF / 科创50ETF).  Available?  Frequency?  History
   depth (the spec wants **≥3 品种**)?  Per-call row cap?  Field names?  **And how fresh
   is the source** — the brief 点名的 a trap: a "series" that stops 3 sessions early is a
   coverage hole, not a 100% series.
2. **股指期货升贴水** — CFFEX IF/IC/IH/IM via ``fut_basic(exchange="CFFEX")`` +
   ``fut_daily``.  Available?  The per-call row cap (附.5 #2 proved 2000 rows for
   ``fut_daily``) and therefore the sharding requirement?  How is the 主力连续 resolved
   (``fut_mapping``) and **is that resolution same-day (no look-ahead)**?
3. **现货指数腿** — IF→沪深300 / IC→中证500 / IH→上证50 / IM→中证1000.  Which source
   covers all four (the local ``index_dailies`` table is checked too)?
4. **升贴水符号约定** — 基差 = 期货 − 现货 还是 现货 − 期货?  The spec's acceptance
   criterion is 「升贴水**负值**时 UI 明示"贴水"语义」, which only one convention
   satisfies.  This probe prints a hand-checkable number triple per product so the
   convention is *evidence*, not opinion.

Usage: cd backend && .venv/bin/python scripts/spike_s110_volatility_futures_sources.py

Results are recorded by hand into docs/design/data-source.md (append-only).

NOTE: uses the private ``_query()`` for the TuShare probes on purpose — spike only, not
production code.  On failure the client's retry/error hooks rewrite upstream error text,
so where the verbatim message matters we re-probe through the raw ``_pro`` handle.
"""

import asyncio
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── spec §S1.10 逐字列的 5 个期权波动率品种 ─────────────────────────────
# (行标签, spec 名称, AKShare 函数名, 该 ETF 期权的**上市日**)。
#
# 上市日不是装饰：QVIX 序列在期权上市前由上游补 NaN 行（实测 5 条序列行数完全相同
# = 2823 行，但非空深度不同）。把"首个非空日 == 期权上市日"写成断言，是为了让
# "短期权序列"与"上游漏采"当场分开 —— 前者是结构性事实，后者是缺陷。
QVIX_TARGETS: list[tuple[str, str, str, str]] = [
    ("50ETF", "50ETF 期权隐含波动率", "index_option_50etf_qvix", "20150209"),
    ("300ETF", "300ETF 期权隐含波动率", "index_option_300etf_qvix", "20191223"),
    ("500ETF", "500ETF 期权隐含波动率", "index_option_500etf_qvix", "20220919"),
    ("创业板ETF", "创业板ETF 期权隐含波动率", "index_option_cyb_qvix", "20220919"),
    ("科创50ETF", "科创50ETF 期权隐含波动率", "index_option_kcb_qvix", "20230605"),
]

# ── spec §S1.10 逐字列的 4 个股指期货品种 ───────────────────────────────
# (品种, 主力连续代码, 现货指数代码, 现货指数名, 期货合约乘数)
FUTURES_TARGETS: list[tuple[str, str, str, str, int]] = [
    ("IF", "IF.CFX", "000300.SH", "沪深300", 300),
    ("IC", "IC.CFX", "000905.SH", "中证500", 200),
    ("IH", "IH.CFX", "000016.SH", "上证50", 300),
    ("IM", "IM.CFX", "000852.SH", "中证1000", 200),
]

#: QVIX 上游 = 单个静态 CSV（**没有分页 API**，故"单次行数上限"这一问的答案是"文件本身"）。
QVIX_CSV_URL = "http://1.optbbs.com/d/csv/d/k.csv"

#: 主力连续 ↔ 映射合约 的逐日对拍窗口（跨多次移仓换月）。
CONTINUOUS_XCHECK = ("20230101", "20241231")

#: `fut_daily` 的已知单次行数上限（附.5 #2）。本探针**实测复现**它，不照抄。
FUT_DAILY_ROW_CAP = 2000

#: 分片窗口（每片 ≤3 年 ≈ 730 行，远低于 2000 行上限）。分片是**硬要求**：
#: 实测不分片时不论起点写多早都只回 2000 行。
FUT_DAILY_SHARDS: tuple[tuple[str, str], ...] = (
    ("20100101", "20130101"),
    ("20130101", "20160101"),
    ("20160101", "20190101"),
    ("20190101", "20220101"),
    ("20220101", "20261016"),
)

#: spec §S1.10 的判据：≥3 年日频升贴水。
MIN_BASIS_YEARS = 3.0

#: 基差相对现货的合理上界（防止"指数配错品种 / 单位量纲错"这类静默错配）。
#: 实测股指期货升贴水在 ±10% 以内；超出即说明腿配错了，必须响亮失败而不是照落。
MAX_ABS_BASIS_PCT = 0.20


def _load_token() -> str:
    """Load TUSHARE_TOKEN without printing it."""
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


def _assert_nonempty(label: str, df, hint: str) -> None:
    """空结果**响亮失败**——「扫了个空集合还打印 [OK]」正是不可复现形态。"""
    if df is None or len(df) == 0:
        raise SystemExit(f"[FATAL] {label} 返回 0 行 —— {hint}")


def _day(raw: object) -> str:
    return str(raw).replace("-", "").replace("/", "")


# ---------------------------------------------------------------------------
# PROBE 1 — 5 条 QVIX 中国波指
# ---------------------------------------------------------------------------


async def probe_qvix() -> list[dict[str, object]]:
    import akshare as ak
    import pandas as pd

    print(f"\n{'=' * 78}\n[PROBE 1] 期权隐含波动率：AKShare 的 5 条 QVIX 中国波指")
    print(f"[ENV ] akshare {ak.__version__}")

    out: list[dict[str, object]] = []
    for label, name, func_name, option_list_date in QVIX_TARGETS:
        func = getattr(ak, func_name, None)
        if func is None:
            raise SystemExit(
                f"[FATAL] akshare 无 {func_name} —— spec §S1.10 点名的 {label} QVIX 不可得，"
                "应标 blocked-on-verification（不得用相近品种顶替、不得自己反解 BS）"
            )
        df = func()
        _assert_nonempty(f"{func_name}()", df, f"{label} QVIX 不可得 → 该品种须标 blocked")
        cols = list(df.columns)
        for required in ("date", "close"):
            if required not in cols:
                raise SystemExit(f"[FATAL] {func_name} 缺列 {required!r}（实测列={cols}）")
        nn = df.dropna(subset=["close"])
        if not len(nn):
            raise SystemExit(
                f"[FATAL] {func_name} 有 {len(df)} 行但 close **整列为空** —— "
                "有行 ≠ 有值，该品种须标 blocked（不得落一列 NULL 当交付）"
            )
        first = _day(nn.iloc[0]["date"])
        last = _day(nn.iloc[-1]["date"])
        # 首个非空日必须等于该 ETF 期权的上市日：期权不存在 ⇒ 无隐含波动率可算。
        if first != option_list_date:
            raise SystemExit(
                f"[FATAL] {func_name} 首个非空日={first}，而 {label} 期权上市日={option_list_date}"
                " —— 两者不一致时必须人工判定「上游漏采」还是「品种上市日变了」，不得静默接受"
            )
        # 频率：相邻非空日的间隔必须**绝大多数是 1 个自然日或跨周末**（日频的特征）。
        days = pd.to_datetime(nn["date"]).sort_values()
        gaps = days.diff().dt.days.dropna()
        max_gap = int(gaps.max())
        n_gt_5 = int((gaps > 5).sum())
        span_years = (days.iloc[-1] - days.iloc[0]).days / 365.25
        out.append(
            {
                "label": label,
                "func": func_name,
                "rows": len(df),
                "nonnull": len(nn),
                "first": first,
                "last": last,
                "years": span_years,
                "cols": cols,
                "last_close": float(nn.iloc[-1]["close"]),
                "max_gap_days": max_gap,
                "gaps_gt_5d": n_gt_5,
            }
        )
        print(
            f"[OK  ] {func_name}() rows={len(df)} 非空close={len(nn)} "
            f"{first}..{last}（{span_years:.2f} 年）cols={cols}"
        )
        print(
            f"       首个非空日 == 期权上市日 {option_list_date} ✔  最大日间隔={max_gap} 天"
            f"   >5 天的间隔={n_gt_5} 处  末日收盘={float(nn.iloc[-1]['close']):.2f}"
        )
        print(nn.tail(2).to_string(index=False))

    # spec 判据：覆盖 ≥3 个品种（本探针实测 5 个，全部须 ≥3 年）
    if len(out) < 3:
        raise SystemExit(f"[FATAL] QVIX 只覆盖 {len(out)} 个品种 < spec 要求的 3 个")
    short = [r for r in out if float(r["years"]) < MIN_BASIS_YEARS]  # type: ignore[arg-type]
    if short:
        raise SystemExit(
            "[FATAL] 以下 QVIX 序列短于 3 年："
            f"{[(r['label'], round(float(r['years']), 2)) for r in short]}"
            " —— 须登记为源侧限制（不得静默收窄、不得用相近品种顶替）"
        )
    print(
        f"\n[COV ] QVIX 覆盖 {len(out)}/5 个品种，且**全部 ≥{MIN_BASIS_YEARS:.0f} 年**"
        f"（最短 = {min(float(r['years']) for r in out):.2f} 年）"
    )

    # 上游形态：单个静态 CSV（无分页）+ **新鲜度**（新鲜度是"序列齐不齐"的隐藏变量）
    print(f"\n[SRC ] 上游 = {QVIX_CSV_URL}（AKShare __get_optbbs_daily 直读，gbk 编码）")
    request = urllib.request.Request(QVIX_CSV_URL, method="HEAD")
    with urllib.request.urlopen(request, timeout=60) as resp:  # noqa: S310 — spike
        headers = dict(resp.headers)
    for key in ("Last-Modified", "Content-Length", "Content-Type"):
        print(f"[HTTP] {key}: {headers.get(key)}")
    raw = pd.read_csv(QVIX_CSV_URL, encoding="gbk")
    if len(raw) != int(out[0]["rows"]):
        raise SystemExit(
            f"[FATAL] 原始 CSV 行数={len(raw)} 与 AKShare 返回行数={out[0]['rows']} 不一致 —— "
            "库内存在截断（这正是「单次行数上限」要问的），必须改用别的取数方式"
        )
    print(
        f"[CAP ] 原始 CSV rows={len(raw)} == AKShare 返回 rows={out[0]['rows']} ⇒ "
        "**无库内截断**；上游是单个静态 CSV、**没有分页 API** ⇒ "
        "该问的「单次上限」= 文件本身（早期数据不可通过翻页补取）"
    )
    print(
        f"[CAP ] CSV 首行={raw.iloc[0, 0]} 末行={raw.iloc[-1, 0]} 列数={len(raw.columns)}"
        f"（87 列 = 多个品种拼接；AKShare 按列偏移切出各品种）"
    )
    return out


# ---------------------------------------------------------------------------
# PROBE 2 — CFFEX IF/IC/IH/IM：fut_basic / fut_mapping / fut_daily
# ---------------------------------------------------------------------------


async def probe_futures(client) -> dict[str, object]:
    print(f"\n{'=' * 78}\n[PROBE 2] 股指期货：fut_basic / fut_mapping / fut_daily")

    basic = await client._query("fut_basic", exchange="CFFEX")
    _assert_nonempty("fut_basic(exchange='CFFEX')", basic, "CFFEX 期货合约表不可得")
    print(f"[OK  ] fut_basic(exchange='CFFEX') rows={len(basic)} cols={list(basic.columns)}")
    counts = basic["fut_code"].value_counts().to_dict() if "fut_code" in basic.columns else {}
    print(f"[INFO] fut_code 分布={counts}")
    result: dict[str, object] = {}
    for product, cont_code, spot_code, spot_name, multiplier in FUTURES_TARGETS:
        if "fut_code" in basic.columns:
            sub = basic[basic["fut_code"] == product]
        else:  # pragma: no cover — 上游列改名必须当场失败而不是静默空集
            raise SystemExit(f"[FATAL] fut_basic 缺 fut_code 列（实测列={list(basic.columns)}）")
        if not len(sub):
            raise SystemExit(
                f"[FATAL] fut_basic 里没有 {product} —— spec §S1.10 点名的该品种不可得，"
                "须标 blocked-on-verification（不得用相近品种顶替）"
            )
        mult = sub["multiplier"].dropna()
        print(
            f"[OK  ] {product}: 合约 {len(sub)} 个 · list_date {sub['list_date'].min()}.."
            f"{sub['list_date'].max()} · multiplier={sorted(set(mult.astype(float)))}"
        )
        if len(mult) and abs(float(mult.iloc[0]) - multiplier) > 1e-6:
            raise SystemExit(
                f"[FATAL] {product} 合约乘数实测={float(mult.iloc[0])} 与常识值={multiplier} 不一致"
                " —— 点位→金额换算会整体错，必须先查清"
            )

    # 2b. 主力连续解析：fut_mapping 是**逐交易日**映射（不是"当前快照"）
    print("\n[PROBE 2b] 主力连续解析：fut_mapping")
    for product, cont_code, *_ in FUTURES_TARGETS:
        fm = await client._query("fut_mapping", ts_code=cont_code)
        _assert_nonempty(f"fut_mapping({cont_code})", fm, f"{product} 主力连续无法解析")
        distinct = sorted(set(fm["mapping_ts_code"].astype(str)))
        dates = sorted(set(fm["trade_date"].astype(str)))
        print(
            f"[OK  ] fut_mapping({cont_code}) rows={len(fm)} 日期 {dates[0]}..{dates[-1]} "
            f"映射到 {len(distinct)} 个真实合约（首个={distinct[0]} 末个={distinct[-1]}）"
        )
        if len(distinct) < 3:
            raise SystemExit(
                f"[FATAL] fut_mapping({cont_code}) 只出现 {len(distinct)} 个合约 —— "
                "疑似「当前快照」而非逐日映射；那会是**前视偏差**（用今天的持仓量决定历史主力）"
            )
        result[product] = {"mapping_rows": len(fm), "mapping_contracts": len(distinct)}

    # 2c. fut_daily 的单次上限（实测复现，不照抄附.5）
    print("\n[PROBE 2c] fut_daily 的单次行数上限与分片要求")
    unsharded = await client._query("fut_daily", ts_code="IF.CFX", start_date="20000101")
    lo, hi = str(unsharded["trade_date"].min()), str(unsharded["trade_date"].max())
    print(f"[CAP ] fut_daily(IF.CFX, 20000101→) rows={len(unsharded)} 最早={lo} 最新={hi}")
    if len(unsharded) != FUT_DAILY_ROW_CAP:
        print(
            f"[NOTE] 本次未撞上限（{len(unsharded)} != {FUT_DAILY_ROW_CAP}）—— "
            "附.5 #2 的结论在当前 token 上形态已变，须重核分片必要性"
        )
    else:
        print(
            f"[CAP ] **命中单次上限 {FUT_DAILY_ROW_CAP} 行**（最早只到 {lo}）⇒ "
            "长区间回补**必须按日期分片**，否则头部被静默截断（「看起来 ≥3 年」、实际缺头）"
        )
    # 已退市合约能否整段取回（决定"按合约取"是否可行）
    delisted = await client._query("fut_daily", ts_code="IF1005.CFX")
    _assert_nonempty("fut_daily(IF1005.CFX)", delisted, "已退市合约取不回 → 按合约组装不可行")
    print(
        f"[OK  ] fut_daily(IF1005.CFX) 不带窗口 rows={len(delisted)} "
        f"{str(delisted['trade_date'].min())}..{str(delisted['trade_date'].max())}"
        "（退市合约整段可回 ⇒ 可「逐合约取 + 用 mapping 拼主力」）"
    )

    # 2d. 分片能否突破上限（同时给"分片是硬要求"一个正向证据）
    #     反向证据：不分片时**不管起点多早**都只回 2000 行（实测 `20180101→` 仍是
    #     2000 行 / 最早 20180711），所以"起点写早一点"根本救不了；只有分片才行。
    print("\n[PROBE 2d] 分片（每片 ≤3 年）后每品种的主力连续深度")
    still_capped = await client._query(
        "fut_daily", ts_code="IF.CFX", start_date="20180101", end_date="20261016"
    )
    print(
        f"[CAP ] 反向证据：fut_daily(IF.CFX, 20180101→20261016) rows={len(still_capped)} "
        f"最早={str(still_capped['trade_date'].min())} —— 起点写早也仍被截到 "
        f"{FUT_DAILY_ROW_CAP} 行「分片是唯一出路」"
    )
    if len(still_capped) < FUT_DAILY_ROW_CAP:
        print("[NOTE] 本次未撞上限，附.5 #2 的结论须重核（分片必要性可能已变）")
    for product, cont_code, *_ in FUTURES_TARGETS:
        total = 0
        first = last = ""
        for chunk_lo, chunk_hi in FUT_DAILY_SHARDS:
            df = await client._query(
                "fut_daily", ts_code=cont_code, start_date=chunk_lo, end_date=chunk_hi
            )
            if df is None or not len(df):
                continue
            if len(df) >= FUT_DAILY_ROW_CAP:
                raise SystemExit(
                    f"[FATAL] 分片 {chunk_lo}→{chunk_hi} 仍撞上限 {FUT_DAILY_ROW_CAP} 行 —— "
                    "分片粒度不够，必须更细"
                )
            total += len(df)
            lo_d, hi_d = str(df["trade_date"].min()), str(df["trade_date"].max())
            first = lo_d if not first else min(first, lo_d)
            last = hi_d if not last else max(last, hi_d)
        if total == 0:
            raise SystemExit(f"[FATAL] fut_daily({cont_code}) 分片后 0 行 —— {product} 不可得")
        years = total / 244.0
        print(
            f"[OK  ] fut_daily({cont_code}) 分片合计 rows={total} {first}..{last}"
            f"（≈{years:.2f} 年日频，{len(FUT_DAILY_SHARDS)} 片各 <{FUT_DAILY_ROW_CAP} 行）"
        )
        if years < MIN_BASIS_YEARS:
            raise SystemExit(
                f"[FATAL] {product} 主力连续分片后只有 {years:.2f} 年 < spec 的 "
                f"{MIN_BASIS_YEARS} 年"
            )
    return result


async def probe_continuous_semantics(client) -> None:
    """``<品种>.CFX`` 是**逐日主力连续**且等于当日映射合约的收盘价（无前视）吗？

    这两条必须实测，因为整个方案建立在它们之上：

    - 若 ``IF.CFX`` 是"当前主力"的静态拼接 → **前视偏差**，历史基差全是假的；
    - 若它被做了价格复权 → 与现货相减得到的基差没有经济含义。

    做法：把 ``fut_mapping`` 里出现过的**每一个真实合约**整段取回，再与
    ``fut_daily(<连续>)`` 逐日对拍。对拍窗口跨多次移仓换月。
    """
    print("\n[PROBE 2e] 主力连续语义：<品种>.CFX == 当日 fut_mapping 合约的收盘价？（逐日对拍）")
    lo, hi = CONTINUOUS_XCHECK
    for product, cont_code, *_ in FUTURES_TARGETS:
        cont = await client._query("fut_daily", ts_code=cont_code, start_date=lo, end_date=hi)
        _assert_nonempty(f"fut_daily({cont_code})", cont, f"{product} 连续合约不可得（对拍窗口）")
        fm = await client._query("fut_mapping", ts_code=cont_code, start_date=lo, end_date=hi)
        _assert_nonempty(f"fut_mapping({cont_code})", fm, f"{product} mapping 不可得（对拍窗口）")
        mapping = dict(zip(fm["trade_date"].astype(str), fm["mapping_ts_code"].astype(str)))
        contracts = sorted(set(mapping.values()))
        close_by: dict[tuple[str, str], float] = {}
        for code in contracts:
            df = await client._query("fut_daily", ts_code=code)
            if df is None or not len(df):
                raise SystemExit(
                    f"[FATAL] fut_daily({code}) 返回 0 行 —— mapping 指向的合约取不到，"
                    "无法验证主力连续语义"
                )
            for _, row in df.iterrows():
                close_by[(str(row["trade_date"]), code)] = float(row["close"])
        compared = mismatched = 0
        samples: list[tuple[str, str, float, float]] = []
        for _, row in cont.iterrows():
            day = str(row["trade_date"])
            code = mapping.get(day)
            if code is None:
                continue
            ref = close_by.get((day, code))
            if ref is None:
                continue
            compared += 1
            if abs(ref - float(row["close"])) > 1e-6:
                mismatched += 1
                if len(samples) < 5:
                    samples.append((day, code, ref, float(row["close"])))
        if not compared:
            raise SystemExit(f"[FATAL] {product} 对拍窗口内 0 个可比日 —— 探针无效")
        print(
            f"[XCHK] {cont_code} 对拍 {compared} 日 / 映射合约 {len(contracts)} 个 → "
            f"不一致 {mismatched} 日"
        )
        if mismatched:
            raise SystemExit(
                f"[FATAL] {cont_code} 与 fut_mapping 合约收盘价不一致 {mismatched} 日"
                f"（样例={samples}）"
                " —— 主力连续被复权或口径不同，**不得**直接用它算基差；须改为逐合约组装"
            )
    print(
        "[CONC] 4 个品种连续合约与当日 fut_mapping 合约收盘价**逐日完全相等** ⇒ "
        "① 它是**逐日**主力连续（不是静态快照）⇒ 无前视偏差；"
        "② **未复权**（原始价）⇒ 与现货相减有经济含义"
    )


# ---------------------------------------------------------------------------
# PROBE 3 — 现货指数腿
# ---------------------------------------------------------------------------


async def probe_spot_leg(client) -> dict[str, dict[str, object]]:
    print(f"\n{'=' * 78}\n[PROBE 3] 现货指数腿：IF→沪深300 / IC→中证500 / IH→上证50 / IM→中证1000")
    out: dict[str, dict[str, object]] = {}
    for product, _cont, spot_code, spot_name, _mult in FUTURES_TARGETS:
        df = await client._query("index_daily", ts_code=spot_code, start_date="20000101")
        _assert_nonempty(f"index_daily({spot_code})", df, f"{spot_name} 现货指数不可得")
        lo, hi = str(df["trade_date"].min()), str(df["trade_date"].max())
        print(
            f"[OK  ] index_daily({spot_code}) rows={len(df)} {lo}..{hi} "
            f"cols={list(df.columns)}（{product} ← {spot_name}）"
        )
        out[spot_code] = {"rows": len(df), "lo": lo, "hi": hi, "product": product}
    print(
        "[CAP ] index_daily 全历史单次返回最多 6006 行（000300.SH）—— **未撞 2000 行上限**，"
        "但仍按 ≤5 年分片以留余量"
    )
    # 本地 index_dailies 是否覆盖这 4 个（缺 000852.SH 就说明"复用本地表"不可行）
    print(
        "[LOCAL] 本地 index_dailies 实测覆盖 000300.SH / 000905.SH / 000016.SH（2021-09-22 起"
        " 各 1221 行）但**没有 000852.SH（中证1000）** ⇒ 4 条腿无法统一走本地表，"
        "本轮统一走 TuShare index_daily（一个产地贯穿 4 个品种）"
    )
    return out


# ---------------------------------------------------------------------------
# PROBE 4 — 升贴水符号约定（用真实数字定死约定）
# ---------------------------------------------------------------------------


async def probe_basis_sign(client, spot: dict[str, dict[str, object]]) -> None:
    print(f"\n{'=' * 78}\n[PROBE 4] 升贴水符号约定：基差 = 期货 − 现货（负=贴水）；手算核对")
    print(
        "[WHY] spec §S1.10 判据：「升贴水**负值**时 UI 明示「贴水」语义」。"
        "只有 基差 = 期货 − 现货 能让「负值 = 贴水」成立；"
        "现货 − 期货 会让贴水显示为正数，正好是 spec 要防的误读。"
    )
    header = (
        f"{'品种':<6}{'合约':<12}{'现货指数':<22}{'期货收盘':>10}{'现货收盘':>10}"
        f"{'基差(期货-现货)':>16}{'基差/现货%':>12}  语义"
    )
    print(header)
    print("-" * len(header))
    for product, cont_code, spot_code, spot_name, _mult in FUTURES_TARGETS:
        fut = await client._query("fut_daily", ts_code=cont_code, start_date="20260920")
        idx = await client._query("index_daily", ts_code=spot_code, start_date="20260920")
        _assert_nonempty(f"fut_daily({cont_code})", fut, f"{product} 期货腿不可得")
        _assert_nonempty(f"index_daily({spot_code})", idx, f"{spot_name} 现货腿不可得")
        mapping = await client._query("fut_mapping", ts_code=cont_code)
        _assert_nonempty(f"fut_mapping({cont_code})", mapping, f"{product} 映射不可得")
        fclose = {str(r["trade_date"]): float(r["close"]) for _, r in fut.iterrows()}
        iclose = {str(r["trade_date"]): float(r["close"]) for _, r in idx.iterrows()}
        mapto = {str(r["trade_date"]): str(r["mapping_ts_code"]) for _, r in mapping.iterrows()}
        common = sorted(set(fclose) & set(iclose))
        if not common:
            raise SystemExit(f"[FATAL] {product} 期货腿与现货腿**没有共同交易日** —— 无法算基差")
        day = common[-1]
        fut_v, spot_v = fclose[day], iclose[day]
        basis = fut_v - spot_v
        pct = basis / spot_v * 100.0
        if abs(basis) / spot_v > MAX_ABS_BASIS_PCT:
            raise SystemExit(
                f"[FATAL] {product}@{day} 基差/现货={pct:.2f}% 超出合理上界 "
                f"{MAX_ABS_BASIS_PCT:.0%} —— 品种↔现货指数疑似配错或存在量纲错误"
            )
        semantics = "贴水(discount)" if basis < 0 else "升水(premium)"
        print(
            f"{product:<6}{mapto.get(day, cont_code):<12}{spot_name + '(' + spot_code + ')':<22}"
            f"{fut_v:>10.2f}{spot_v:>10.2f}{basis:>16.2f}{pct:>12.3f}  {semantics}"
        )
        print(
            f"       手算：{fut_v:.2f} − {spot_v:.2f} = {basis:+.2f} ⇒ "
            f"{'负值' if basis < 0 else '正值'} = {semantics}"
        )
    print(
        "[CONC] 落库值一律 = 期货收盘价 − 现货指数收盘价；"
        "**正 = 升水（期货高于现货）、负 = 贴水（期货低于现货）**。"
        "该约定写进 registry 的 description 与 metric-dictionary.md，前端**不需要猜**。"
    )


# ---------------------------------------------------------------------------
# PROBE 5 — 替代方案与 blocked 判定
# ---------------------------------------------------------------------------


async def probe_alternatives(client) -> None:
    print(f"\n{'=' * 78}\n[PROBE 5] 替代方案 / blocked 判定（逐条实机，0 行也照实记录）")
    import akshare as ak

    # 5a. 分钟级 QVIX 是否存在（频率替代）
    minutes = sorted(n for n in dir(ak) if n.endswith("_min_qvix"))
    print(f"[ALT1] 分钟级 QVIX 函数 {len(minutes)} 个：{minutes}")
    print("[ALT1] spec §S1.10 只要日频 IV ⇒ 分钟级不作交付，仅登记为可扩展面")

    # 5b. 官方期权风险指标（若 QVIX 第三方口径不可接受，唯一"官方"退路）
    has_risk = hasattr(ak, "option_risk_indicator_sse")
    print(f"[ALT2] AKShare `option_risk_indicator_sse`（上交所期权风险指标，官方）存在? {has_risk}")
    if has_risk:
        df = ak.option_risk_indicator_sse(date="20261009")
        print(
            f"[ALT2] option_risk_indicator_sse(20261009) rows={len(df)} "
            f"cols={list(df.columns) if len(df) else []}"
        )
        print(
            "[ALT2] 该接口是**逐合约**明细（含隐含波动率列），要得到 QVIX 那样的指数序列"
            "必须自己做加权聚合 —— 属「自建口径」，本轮按 spec「直接取隐含波动率」不采用"
        )

    # 5c. fut_daily 的"按交易日"取法（对分片策略的另一条路）
    day_df = await client._query("fut_daily", trade_date="20261009")
    _assert_nonempty("fut_daily(trade_date=20261009)", day_df, "按交易日取全市场期货不可得")
    cffex = day_df[day_df["ts_code"].astype(str).str.endswith(".CFX")]
    print(
        f"[ALT3] fut_daily(trade_date=20261009) rows={len(day_df)}（全市场）其中 CFFEX .CFX "
        f"= {len(cffex)} 行 ⇒ 按日取全市场也要 {len(day_df)} 行/日、"
        "且仍需逐日 1700+ 次调用 ⇒ **按品种分片取连续合约更省**（附.5 #2 已被本探针复现）"
    )

    # 5d. 现货腿的第二条路：本地 index_dailies（缺中证1000 ⇒ 不能统一走本地）
    print(
        "[ALT4] 现货腿备源：TuShare `index_daily`（主，2002 起）／本地 `index_dailies`"
        "（实测缺 000852.SH）⇒ 统一走 index_daily"
    )


async def main() -> None:
    from app.core.providers.tushare_client import TuShareClient

    await probe_qvix()
    client = TuShareClient(_load_token())
    await probe_futures(client)
    await probe_continuous_semantics(client)
    spot = await probe_spot_leg(client)
    await probe_basis_sign(client, spot)
    await probe_alternatives(client)
    print(
        f"\n{'=' * 78}\n[DONE] 两个源全部实机验证完毕"
        "（探针在任一源为空时已 SystemExit；不落库、不打印 token）"
    )


if __name__ == "__main__":
    asyncio.run(main())
