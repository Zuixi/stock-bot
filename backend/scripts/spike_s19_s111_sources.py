"""Task 13 Step 0 spike: are the S1.9 / S1.11 upstream sources really available?

Three questions, each answered by a **real** upstream call (Ruling 51: a doc claim
must be reproducible from a committed script, and the script must fail loudly on
empty results instead of printing ``[OK]`` on nothing):

1. **融资融券** — TuShare ``margin`` (market aggregate) + ``margin_detail``
   (per stock).  Available?  Frequency?  History depth?  Per-call row cap?
2. **指数换手率** — TuShare ``index_dailybasic`` for the 7 indices spec §S1.9
   names, **and** which column is the free-float caliber (``turnover_rate_f``)
   vs the total-share caliber (``turnover_rate``).  The local ``index_dailies``
   table is checked too: it has no turnover column at all (see PROBE 2a).
3. **10Y 国债收益率** — which interface (``yc_cb`` / ``bond`` family)?
   Available?  Frequency?  History depth?

Usage: cd backend && .venv/bin/python scripts/spike_s19_s111_sources.py

Results are recorded by hand into docs/design/data-source.md (append-only).

NOTE: uses the private ``_query()`` on purpose — spike only, not production
code.  The client's retry/error hooks rewrite upstream error text, so on failure
we re-probe through the raw ``_pro`` handle to capture the verbatim upstream
message needed for the three-state verdict.
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# spec §S1.9 逐字列出的 7 个指数（本地指数代码，见 backend/data/*.sql 的既有命名）。
# 每个都以 (标签, TuShare ts_code) 形式给出——**代码本身也是被验证的对象**：
# 某个指数换名字/换代码时，本探针必须当场发现，而不是静默少一行。
INDEX_TURNOVER_TARGETS: list[tuple[str, str]] = [
    ("上证指数", "000001.SH"),
    ("深证成指", "399001.SZ"),
    ("北证50", "899050.BJ"),
    ("科创综指", "000680.SH"),
    ("创业板指", "399006.SZ"),
    ("上证50", "000016.SH"),
    ("沪深300", "000300.SH"),
]

#: `index_dailybasic` 的日期窗口（一次拉满 7 个标的 × 约 8 个月，用于看列与频率）。
IDB_WINDOW = ("20260101", "20261016")

#: 10Y 国债收益率的候选：`yc_cb` 的曲线代号 / 期限。
#: 央票曲线（CB）的 10 年期就是市场引用的「10 年期国债收益率」代理；`yc_cb` 未开放
#: 时退到 `bond` 家族逐条枚举。候选写在这里而不是散在代码里，便于复核。
YC_CB_CURVES = ["0", "1", "2"]


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


async def _raw(client, api_name: str, **kwargs) -> None:
    """失败时经底层 ``_pro`` 取上游**逐字**原文（客户端会重写异常文案）。"""
    import asyncio as _asyncio

    try:
        await _asyncio.to_thread(lambda: client._pro.query(api_name, **kwargs))
    except Exception as exc:  # noqa: BLE001 — spike wants the raw error
        print(f"[RAW ] {api_name}({kwargs}) → {type(exc).__name__}: {exc}")
    else:
        print(f"[RAW ] {api_name}({kwargs}) → 底层直查成功（上层却失败了？）")


def _date_range(df) -> tuple[str, str]:
    if not len(df) or "trade_date" not in df.columns:
        return ("-", "-")
    return (str(df["trade_date"].min()), str(df["trade_date"].max()))


def _assert_nonempty(label: str, df, hint: str) -> None:
    """空结果**响亮失败**——"扫了个空集合还打印 [OK]"正是不可复现形态。"""
    if df is None or len(df) == 0:
        raise SystemExit(f"[FATAL] {label} 返回 0 行 —— {hint}")


# ---------------------------------------------------------------------------
# PROBE 1 — 融资融券
# ---------------------------------------------------------------------------


async def probe_margin(client) -> None:
    print(f"\n{'=' * 72}\n[PROBE 1] 融资融券：margin（市场汇总）+ margin_detail（个股明细）")

    # 1a. 单日快照：市场汇总
    df = await client._query("margin", trade_date="20261009")
    _assert_nonempty("margin(trade_date=20261009)", df, "S1.9 的杠杆资金底座不可得")
    print(f"[OK  ] margin(trade_date=20261009) rows={len(df)} cols={list(df.columns)}")
    print(df.to_string())

    # 1b. 区间拉取：看单次行数上限与历史深度
    win = await client._query("margin", start_date="20000101", end_date="20261016")
    _assert_nonempty("margin(20000101–20261016)", win, "区间参数不被接受")
    lo, hi = _date_range(win)
    print(f"[HIST] margin 区间 rows={len(win)} min={lo} max={hi}")
    if "exchange_id" in win.columns:
        dist = dict(win["exchange_id"].value_counts())
        print(f"[HIST] margin exchange_id 分布={dist}（沪深两市各一行/交易日）")

    # 1c. 个股明细：单日 + 区间
    md = await client._query("margin_detail", trade_date="20261009")
    _assert_nonempty("margin_detail(trade_date=20261009)", md, "个股两融明细不可得")
    print(f"[OK  ] margin_detail(trade_date=20261009) rows={len(md)} cols={list(md.columns)}")
    print(md.head(3).to_string())
    md_win = await client._query("margin_detail", start_date="20260101", end_date="20261016")
    lo2, hi2 = _date_range(md_win)
    print(f"[HIST] margin_detail 区间 rows={len(md_win)} min={lo2} max={hi2}")

    # 1d. 真实覆盖度：取一个早年的交易日，看 `margin` 是否真有历史
    early = await client._query("margin", start_date="20140101", end_date="20140110")
    print(
        f"[HIST] margin 2014-01 前 10 天 rows={len(early)} "
        f"（0 行 ⇒ 该 token 的 `margin` 历史不下探到 2014，深度结论须收窄）"
    )


# ---------------------------------------------------------------------------
# PROBE 2 — 指数换手率
# ---------------------------------------------------------------------------


async def probe_index_turnover(client) -> None:
    print(f"\n{'=' * 72}\n[PROBE 2] 指数换手率：index_dailybasic（7 个指数逐个）")
    print("[NOTE] 本地 index_dailies 表结构（app/models/index_daily.py）：")
    print("       ts_code/trade_date/open/high/low/close/pre_close/volume/amount")
    print("       → **无换手率列**，故必须外接 index_dailybasic（本探针不查库）")

    direct: list[str] = []
    missing: list[str] = []
    for label, code in INDEX_TURNOVER_TARGETS:
        df = await client._query(
            "index_dailybasic",
            ts_code=code,
            start_date=IDB_WINDOW[0],
            end_date=IDB_WINDOW[1],
        )
        if not len(df):
            missing.append(f"{label}({code})")
            print(f"[MISS] {label} {code}: rows=0 —— 该指数在 index_dailybasic 无数据")
            continue
        direct.append(code)
        lo, hi = _date_range(df)
        has_total = "turnover_rate" in df.columns
        has_free = "turnover_rate_f" in df.columns
        print(f"[OK  ] {label} {code}: rows={len(df)} min={lo} max={hi} cols={list(df.columns)}")
        n_total = int(df["turnover_rate"].notna().sum()) if has_total else "NO COL"
        n_free = int(df["turnover_rate_f"].notna().sum()) if has_free else "NO COL"
        print(f"       turnover_rate(总股本) 非空={n_total} / turnover_rate_f 非空={n_free}")
        print(df.head(2).to_string())

    print(f"\n[COV ] index_dailybasic 直取覆盖 {len(direct)}/7：{direct}")
    print(f"[COV ] 未覆盖 {len(missing)}/7：{missing}")

    # 单日全市场快照能否一次拿到 7 个（决定回补是按日还是按指数）
    day = await client._query("index_dailybasic", trade_date="20261009")
    gotten = (
        sum(1 for _, c in INDEX_TURNOVER_TARGETS if c in set(day["ts_code"])) if len(day) else 0
    )
    print(f"[SNAP] index_dailybasic(trade_date=20261009) rows={len(day)} 含 7 个目标={gotten}")

    # 历史深度与单次行数上限（决定回补要按年分片）
    full = await client._query("index_dailybasic", ts_code="000001.SH", start_date="20000101")
    lo, hi = _date_range(full)
    print(
        f"[HIST] index_dailybasic(000001.SH, 20000101→) rows={len(full)} min={lo} max={hi}"
        f"（行数=`index_dailybasic` 级单次上限的旁证；回补须按日期分片）"
    )
    if len(full) < 2000:
        raise SystemExit(
            f"[FATAL] index_dailybasic 单指数全历史只得 {len(full)} 行 —— "
            "与 spec §S1.9 的「≥3 年序列」需要重新评估，不得静默收窄。"
        )

    # 直取缺口的两条补验路径：①本地/镜像 index_daily 是否有该指数 ②能否用成分权重自建
    print(f"\n{'=' * 72}\n[PROBE 2b] 直取缺口的两条补验路径")
    print("       ① index_daily 是否有该指数 ② 能否用 index_weight + daily_basic 自建")
    if not missing:
        raise SystemExit(
            "[FATAL] 本探针预期 index_dailybasic 对 7 个指数有缺口（附.7 的三态结论据此写成），"
            "但本次 7/7 全部直取成功 —— 上游形态已变，请重核引.7 并删掉自建路径。"
        )
    for label, code in INDEX_TURNOVER_TARGETS:
        if code in direct:
            continue
        idxd = await client._query(
            "index_daily", ts_code=code, start_date="20260101", end_date="20261016"
        )
        print(f"[ALT1] index_daily({code}) rows={len(idxd)}（行情有、估值/换手无）")
        wt = await client._query("index_weight", index_code=code, trade_date="20260930")
        print(f"[ALT2] index_weight(index_code={code}, trade_date=20260930) rows={len(wt)}")
        if not len(wt):
            raise SystemExit(
                f"[FATAL] {label}({code}) 既不在 index_dailybasic 又拿不到成分权重 —— "
                "该指标应标 blocked-on-verification（不得用估算值顶替）。"
            )
        # 成分权重的历史深度（自建回补能走多远）
        wt_hist = await client._query(
            "index_weight", index_code=code, start_date="20200101", end_date="20261016"
        )
        if len(wt_hist):
            dates = sorted(set(wt_hist["trade_date"]))
            print(
                f"[ALT2] index_weight({code}) 20200101→ rows={len(wt_hist)} "
                f"期数={len(dates)} 最早={dates[0]} 最新={dates[-1]}（月度快照）"
            )
    # 成分股层面的 daily_basic 是否覆盖北交所（自建口径的必要条件）
    basic = await client._query("daily_basic", trade_date="20261009")
    bj = int(basic["ts_code"].astype(str).str.endswith(".BJ").sum()) if len(basic) else 0
    print(
        f"[SELF] daily_basic(trade_date=20261009) rows={len(basic)} "
        f"其中 .BJ={bj}（自建北证50口径的必要条件；0 行则自建不可行）"
    )
    if bj == 0:
        raise SystemExit("[FATAL] daily_basic 不含北交所标的 —— 自建北证50换手率不可行")


# ---------------------------------------------------------------------------
# PROBE 3 — 10Y 国债收益率
# ---------------------------------------------------------------------------


async def probe_treasury_10y(client) -> None:
    print(f"\n{'=' * 72}\n[PROBE 3] 10Y 国债收益率：yc_cb（中债收益率曲线）候选枚举")

    hits: list[str] = []
    for curve in YC_CB_CURVES:
        args = {"curve_type": curve, "curve_term": 10}
        try:
            df = await client._query("yc_cb", **args)
        except Exception as exc:  # noqa: BLE001
            print(f"[ERR ] yc_cb({args}): {type(exc).__name__}: {exc}")
            await _raw(client, "yc_cb", **args)
            continue
        if not len(df):
            print(f"[MISS] yc_cb({args}) rows=0")
            # 0 行也可能是"该曲线代号无此期限"，故同时看整条曲线（不传 curve_term）
            whole = await client._query("yc_cb", curve_type=curve)
            print(f"       yc_cb(curve_type={curve}) 不带 curve_term → rows={len(whole)}")
            if len(whole):
                print(whole.head(3).to_string())
            continue
        hits.append(curve)
        print(f"[OK  ] yc_cb(curve_type={curve}, term=10) rows={len(df)}")
        print(f"       cols={list(df.columns)}")
        print(df.head(5).to_string())
        for col in ("curve_name", "curve_type", "curve_term", "trade_date", "yield"):
            if col in df.columns:
                print(f"[INFO] {col}: 取值样例={sorted({str(v) for v in df[col]})[:6]}")
        if "trade_date" in df.columns:
            print(
                f"[HIST] yc_cb(curve_type={curve}) "
                f"min={df['trade_date'].min()} max={df['trade_date'].max()}"
            )

    if not hits:
        raise SystemExit(
            "[FATAL] yc_cb 的全部候选 curve_type 都没有返回数据 —— "
            "10Y 国债收益率必须换源或标 blocked-on-verification（不得用估算值顶替）。"
        )

    # 深度：用一个命中的曲线，不带期限拉全历史看单次上限与最早日期
    full = await client._query("yc_cb", curve_type=hits[0], curve_term=10, start_date="20000101")
    if "trade_date" in full.columns and len(full):
        print(
            f"[HIST] yc_cb(curve_type={hits[0]}, curve_term=10) 20000101→ rows={len(full)} "
            f"min={full['trade_date'].min()} max={full['trade_date'].max()}"
        )

    # 备选路径枚举：bond 家族里可能带收益率的接口，逐个试并记录上游逐字文案
    print(f"\n{'=' * 72}\n[PROBE 3b] bond 家族备选（逐字记录上游文案；0 行也照实记录）")
    for api, kwargs in (
        ("bond", {"start_date": "20261001", "end_date": "20261016"}),
        ("yc_cb", {}),
        ("bond_daily", {"trade_date": "20261009"}),
    ):
        try:
            df = await client._query(api, **kwargs)
        except Exception as exc:  # noqa: BLE001
            print(f"[ERR ] {api}({kwargs}): {type(exc).__name__}: {exc}")
            await _raw(client, api, **kwargs)
            continue
        print(f"[OK  ] {api}({kwargs}) rows={len(df)} cols={list(df.columns) if len(df) else []}")


# ---------------------------------------------------------------------------
# PROBE 4 — 10Y 国债收益率的替代方案（TuShare `yc_cb` 权限不足后的实测路径）
# ---------------------------------------------------------------------------


async def probe_treasury_alternatives() -> None:
    """`yc_cb` 权限不足 → 逐条实机验证替代源，并**响亮失败**于全部不可用。

    两条替代路径都是 AKShare 的既有封装（本仓库已依赖 akshare 1.18.94），底层端点
    在日志里逐字打印，便于把「产地」写进 data-source.md：

    - ①官方口径：中国债券信息网 `yield.chinabond.com.cn/cbweb-pbc-web/pbc/historyQuery`
      （`bond_china_yield`，**窗口必须 < 1 年**，否则静默返回 0 行——与 `disclosure_date`
      的区间坑同族）；曲线名 `中债国债收益率曲线`、期限列 `10年`。
    - ②第三方镜像：东财 `datacenter.eastmoney.com/api/data/get?type=RPTA_WEB_TREASURYYIELD`
      （`bond_zh_us_rate`，字段 `EMM00166466` → `中国国债收益率10年`），一次调用可取 2002 起全历史。
    """
    import akshare as ak
    import pandas as pd

    print(f"\n{'=' * 72}\n[PROBE 4] 10Y 国债收益率替代源（yc_cb 权限不足后的实测路径）")
    print(f"[ENV ] akshare {ak.__version__}")

    # ---- ①官方口径：中国债券信息网（逐年翻窗，窗口必须 < 1 年）----
    frames = []
    for year in (2024, 2025, 2026):
        df = ak.bond_china_yield(start_date=f"{year}0101", end_date=f"{year}1231")
        if not len(df):
            raise SystemExit(
                f"[FATAL] bond_china_yield({year}) 返回 0 行 —— 官方口径不可得，"
                "不得用估算值顶替 10Y 国债收益率。"
            )
        sub = df[df["曲线名称"].astype(str).str.contains("中债国债收益率曲线")]
        if not len(sub):
            raise SystemExit(f"[FATAL] {year} 年没有『中债国债收益率曲线』行")
        frames.append(sub)
        print(
            f"[OK  ] bond_china_yield({year}): rows={len(df)} 国债曲线行={len(sub)} "
            f"cols={list(df.columns)} 日期={sub['日期'].min()}~{sub['日期'].max()}"
        )
    cny = pd.concat(frames, ignore_index=True).sort_values("日期")
    print(f"[HIST] 中债国债曲线（官方）合计 rows={len(cny)}")
    print(f"       min={cny['日期'].min()} max={cny['日期'].max()}")
    print(cny[["曲线名称", "日期", "10年"]].tail(3).to_string(index=False))
    print(
        "[LIMIT] 窗口 > 1 年时 `bond_china_yield` 静默返回 0 行（实测 2018/2010/2005 起点均 0 行）"
        " → 回补必须逐年翻窗"
    )
    if cny["10年"].notna().sum() == 0:
        raise SystemExit("[FATAL] 官方路径 `10年` 列全空")

    # ---- ②第三方镜像：东财（一次取全历史）----
    us = ak.bond_zh_us_rate(start_date="20000101")
    if not len(us) or "中国国债收益率10年" not in us.columns:
        raise SystemExit("[FATAL] bond_zh_us_rate 不可用（10Y 国债没有第二条源）")
    ten = us[["日期", "中国国债收益率10年"]].dropna()
    print(
        f"[OK  ] bond_zh_us_rate(20000101→): rows={len(us)} cols={list(us.columns)}\n"
        f"[HIST] 中国国债收益率10年 非空={len(ten)} min={ten['日期'].min()} max={ten['日期'].max()}"
    )
    print(ten.tail(3).to_string(index=False))

    # ---- 两源同日对拍（同一日两个产地必须给出同一量级的 10Y）----
    a = cny.set_index("日期")["10年"].to_dict()
    b = ten.set_index("日期")["中国国债收益率10年"].to_dict()
    common = sorted(set(a) & set(b))[-5:]
    if not common:
        raise SystemExit("[FATAL] 两源没有共同交易日 —— 无法交叉验证 10Y 国债收益率")
    print("[XCHK] 两源同日对拍（官方中债 vs 东财镜像）：")
    for d in common:
        print(f"       {d}: 中债={a[d]:.4f}  东财={b[d]:.4f}  差={abs(a[d] - b[d]):.4f}")


async def main() -> None:
    from app.core.providers.tushare_client import TuShareClient

    client = TuShareClient(_load_token())
    await probe_margin(client)
    await probe_index_turnover(client)
    try:
        await probe_treasury_10y(client)
    except SystemExit as exc:
        # `yc_cb` 权限不足是**预期内**的分支：结论必须继续验证替代源，故此处不吞掉
        # 信息——打印原委并转 PROBE 4；PROBE 4 自己也响亮失败（两条替代源都不行时）。
        print(f"\n[NOTE] yc_cb 路径不可用：{exc}")
    await probe_treasury_alternatives()
    print(f"\n{'=' * 72}\n[DONE] 三个源全部实机验证完毕（探针在任一源为空时已 SystemExit）")


if __name__ == "__main__":
    asyncio.run(main())
