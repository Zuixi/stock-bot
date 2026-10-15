"""Task 15 Step 0 spike: S1.7 资金流四件套的源到底还在不在？

五个问题，每个都由**实机调用**回答（Ruling 51：结论必须能从一个入库脚本复现，
且脚本在空结果上**响亮失败**，绝不打印 ``[OK]``）：

1. **北向每日净买入**（本任务最重要的 Step-0 项）—— spec 的"背景硬事实"称它
   2024-08-19 被取消。但本仓库 ``northbound_daily`` 里躺着 42 行非空数据。到底是
   "能力已死" 还是 "``north_money`` 换了语义仍在供给"？本探针取断点前后的
   ``moneyflow_hsgt``，再用**独立产地**（东财 ``stock_hsgt_hist_em``）对拍。
2. **ETF 份额变化** —— TuShare ``fund_share`` 可得吗？频率？历史深度？单次行数上限？
   要取哪些宽基 ETF、依据是什么（``fund_basic.benchmark`` 文本能不能当依据）？
3. **南向** —— ``moneyflow_hsgt.south_money`` 是**日频净买入**还是**历史累计**？
   （这决定 ``southbound_net_buy`` 能不能直取该列，还是必须先做差分）
4. **产业资本增减持** —— 原料是公告（S3.2）。S3.2 完成了吗？上游有没有可替代的
   结构化接口（仅登记，本轮不采用）？
5. **陆股通持仓（季度）** —— ``hk_hold`` 的季度末可得性、行数上限与覆盖是否规则。

用法::

    cd backend && .venv/bin/python scripts/spike_s17_capital_flows_sources.py

结果按三态格式人工抄进 ``docs/design/data-source.md`` 附.9（append-only）。

NOTE: spike 里直接用客户端私有的 ``_query()``（不是生产路径）；与
``scripts/spike_s19_s111_sources.py`` / ``spike_s110_volatility_futures_sources.py``
同规矩。
"""

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

#: 2024-08-19 = 沪深港通交易信息披露机制调整的生效日（spec 的"背景硬事实"引的日期）。
BREAK_DATE = "20240819"
#: 断点前的窗口（须 ≤300 行，见 ``MONEYFLOW_HSGT_ROW_CAP``）。
PRE_LO, PRE_HI = "20240101", "20240816"
#: 断点后的分段窗口（分段是为了不撞单次行数上限）。
POST_WINDOWS = (
    ("20240819", "20250630"),
    ("20250701", "20251231"),
    ("20260101", "20261016"),
)

#: ``moneyflow_hsgt`` 的单次行数上限（实测：宽窗 ``20240101→20261016`` 只回 300 行）。
MONEYFLOW_HSGT_ROW_CAP = 300
#: ``fund_share`` 单代码的单次行数上限（实测：不带 ts_code 的宽窗只回最新 2000 行）。
FUND_SHARE_ROW_CAP = 2000
#: ``hk_hold`` 的单次行数上限（实测）。
HK_HOLD_ROW_CAP = 4200
#: ``stk_holdertrade`` 的单次行数上限（实测）。
HOLDER_TRADE_ROW_CAP = 3000

#: 宽基 ETF 篮子：**一族一只**。族名 → (ts_code, 交易所, 基金全名)。
#:
#: 依据（见 PROBE 2b 的实机排名输出）：每个宽基指数族里 **规模（份额 × 收盘价）最大**的一只。
#: ⚠️ **不能只比份额**：低净值的行业主题 ETF 份额更大而规模更小（实测
#: ``512010.SH`` 沪深300医药卫生ETF 有 387 万份却只有 147 亿元）。
#: ⚠️ ``fund_basic.benchmark`` 的文本**不能单独当依据**：``510050.SH`` 的 benchmark 是
#: 「上海证券交易所50成份指数×100%」、``510500.SH`` 是「中证小盘500指数×100%」——
#: 按指数名做文本匹配会把这两只**族内规模最大**的 ETF 漏掉（实测漏掉，见 [MISS] 行）。
BROAD_BASE_BASKET: list[tuple[str, str, str, str]] = [
    ("沪深300", "510300.SH", "SH", "华泰柏瑞沪深300ETF"),
    ("上证50", "510050.SH", "SH", "华夏上证50ETF"),
    ("中证500", "510500.SH", "SH", "南方中证500ETF"),
    ("中证1000", "512100.SH", "SH", "南方中证1000ETF"),
    ("科创50", "588000.SH", "SH", "华夏上证科创板50成份ETF"),
    ("创业板指", "159915.SZ", "SZ", "易方达创业板ETF"),
]

#: 篮子里每只 ETF 必须达到的最短序列（spec §S1.7 验收判据「≥1 年日频序列」）。
MIN_BASKET_TRADING_DAYS = 250

#: 篮子回补起点（探针验证"能回溯多远"，服务侧默认窗口取这里）。
BASKET_LO = "20230101"

#: ``fund_basic.benchmark`` 的宽基文本模式（**仅用于暴露匹配的坑**，不作为选篮依据）。
BENCHMARK_PATTERNS: dict[str, list[str]] = {
    "沪深300": ["沪深300指数"],
    "上证50": ["上证50指数"],
    "中证500": ["中证500指数"],
    "中证1000": ["中证1000指数"],
    "科创50": ["上证科创板50成份指数"],
    "创业板指": ["创业板指数"],
}

#: 南向口径断点的**待核窗口**（PROBE 3c 在这里面找断点，不预设断点日期）。
SOUTH_VINTAGE_WINDOWS = (("20230601", "20231231"), ("20240101", "20240229"))

#: 季度末（陆股通持仓按季度披露）—— 用于 PROBE 5 的可得性抽样。
QUARTER_ENDS = [
    "20231229",
    "20240329",
    "20240628",
    "20240930",
    "20241231",
    "20250331",
    "20250630",
    "20250930",
    "20251231",
    "20260331",
    "20260630",
    "20260930",
]


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


def _assert_nonempty(label: str, df: Any, hint: str) -> None:
    """空结果**响亮失败** —— "扫了个空集合还打印 [OK]" 正是不可复现形态。"""
    if df is None or len(df) == 0:
        raise SystemExit(f"[FATAL] {label} 返回 0 行 —— {hint}")


def _days(df: Any) -> list[str]:
    return sorted({str(d) for d in df["trade_date"].tolist()})


#: 两个产地逐值对拍时，允许的**不一致日占比**上限（0.02 = 2%）。
#: 为什么不是 0：实测 2024-07-08 两个产地的北向净买入不一致
#: （TuShare −3209.23 vs 东财 −2199.92，百万元），其余 147/148 日**逐值精确相等** ——
#: 这是产地修订/录入差异，属**必须登记**的事实，不是"我们的单位算错了"。
MAX_XCHK_MISMATCH_RATIO = 0.02


def _cross_check(
    label: str, ours: dict[str, float], theirs: dict[str, float], *, tol: float
) -> list[tuple[str, float, float, float]]:
    """两个产地逐值对拍：返回超差行 ``(日期, 我方, 对方, 差值)``，并**响亮失败**于多数不符。

    只对共同日比较；不一致日占比 > :data:`MAX_XCHK_MISMATCH_RATIO` 即 `SystemExit`
    （"符号/单位算错"与"个别产地修订"必须区分开，不能用一个 max 偏差把两者混为一谈）。
    """
    common = sorted(set(ours) & set(theirs))
    bad = [
        (d, ours[d], theirs[d], ours[d] - theirs[d])
        for d in common
        if abs(ours[d] - theirs[d]) > tol
    ]
    ratio = len(bad) / len(common) if common else 1.0
    print(
        f"[XCHK] {label}: 共同日 {len(common)}，超出容差 {tol} 的 {len(bad)} 日"
        f"（占比 {ratio:.4%}，上限 {MAX_XCHK_MISMATCH_RATIO:.2%}）"
    )
    for d, a, b, delta in bad:
        print(f"       [DIFF] {d}: 我方={a:.4f} 对方={b:.4f} 差={delta:.4f}")
    if not common or ratio > MAX_XCHK_MISMATCH_RATIO:
        raise SystemExit(
            f"[FATAL] {label} 逐值对拍不一致日占比 {ratio:.4%} > {MAX_XCHK_MISMATCH_RATIO:.2%} —— "
            "单位或语义结论错误，不得据此写附.9 / 不得据此落库。"
        )
    return bad


# ---------------------------------------------------------------------------
# PROBE 1 — 北向每日净买入：2024-08-19 断点前后的语义
# ---------------------------------------------------------------------------


async def probe_northbound_semantics(client: Any) -> None:
    import pandas as pd

    print(f"\n{'=' * 78}\n[PROBE 1] 北向：moneyflow_hsgt.north_money 在 {BREAK_DATE} 前后的语义")
    print("[Q    ] 上游还在给 north_money 吗？给的是**净买入**还是别的量？")

    pre = await client._query("moneyflow_hsgt", start_date=PRE_LO, end_date=PRE_HI)
    _assert_nonempty("moneyflow_hsgt(断点前)", pre, "北向断点前的对照样本取不到")
    pre["north_money"] = pd.to_numeric(pre["north_money"], errors="coerce")
    pre_neg = int((pre["north_money"] < 0).sum())
    print(
        f"[PRE ] {PRE_LO}..{PRE_HI} rows={len(pre)} "
        f"非空={int(pre['north_money'].notna().sum())} 负值={pre_neg} "
        f"区间=[{pre['north_money'].min():.2f}, {pre['north_money'].max():.2f}]"
    )
    if pre_neg == 0:
        raise SystemExit(
            "[FATAL] 断点前的 north_money 竟然没有一个负值 —— "
            "「断点前 = 净买入（可为负）」这一前提不成立，本节的语义结论必须重写。"
        )

    frames = []
    for lo, hi in POST_WINDOWS:
        df = await client._query("moneyflow_hsgt", start_date=lo, end_date=hi)
        _assert_nonempty(f"moneyflow_hsgt({lo}-{hi})", df, "断点后的 north_money 取不到")
        print(f"[POST] {lo}..{hi} rows={len(df)}")
        frames.append(df)
    post = pd.concat(frames, ignore_index=True)
    post["north_money"] = pd.to_numeric(post["north_money"], errors="coerce")
    post = post.drop_duplicates("trade_date")
    post_neg = int((post["north_money"] < 0).sum())
    post_zero = int((post["north_money"] == 0).sum())
    print(
        f"[POST] 合计 rows={len(post)} {_days(post)[0]}..{_days(post)[-1]} "
        f"非空={int(post['north_money'].notna().sum())} 负值={post_neg} 零值={post_zero}"
    )
    print(f"[POST] 区间=[{post['north_money'].min():.2f}, {post['north_money'].max():.2f}]")
    if post_neg or post_zero:
        raise SystemExit(
            f"[FATAL] 断点后 north_money 出现 负值={post_neg} / 零值={post_zero} —— "
            "「断点后不再是净买入」这一前提不成立，本节的语义结论必须重写。"
        )

    # 断点日逐行（语义切换必须看得到）
    around = await client._query("moneyflow_hsgt", start_date="20240814", end_date="20240823")
    _assert_nonempty("moneyflow_hsgt(20240814-20240823)", around, "断点日邻域取不到")
    print("[BREAK] 断点邻域逐行：")
    print(around.to_string(index=False))

    # north_money == hgt + sgt 恒等（说明它是两条腿的求和，不是独立量）
    both = pd.concat([pre, post], ignore_index=True)
    for col in ("hgt", "sgt"):
        both[col] = pd.to_numeric(both[col], errors="coerce")
    gap = (both["hgt"] + both["sgt"] - both["north_money"]).abs()
    n_gap = int((gap > 0.01).sum())
    print(f"[IDENT] north_money == hgt + sgt 不成立的行数 = {n_gap} / {len(both)}")

    # 单次行数上限：宽窗只回最近 300 行（长历史必须分段）
    wide = await client._query("moneyflow_hsgt", start_date="20240101", end_date="20261016")
    print(
        f"[CAP  ] 宽窗 20240101→20261016 只回 rows={len(wide)}"
        f"（上限 {MONEYFLOW_HSGT_ROW_CAP}），最早={_days(wide)[0]} ⇒ 长历史必须分段"
    )
    if len(wide) > MONEYFLOW_HSGT_ROW_CAP:
        print(f"[CAP  ] ⚠️ 实测上限已变（>{MONEYFLOW_HSGT_ROW_CAP}），附.9 的该条须重核")

    # ---- 独立产地对拍：东财（AKShare 封装）----
    print("\n[PROBE 1b] 独立对拍：东财 stock_hsgt_hist_em（北向资金）")
    east = _eastmoney_hsgt("北向资金")
    print()  # tqdm 的进度条不留换行，先起一行免得盖掉下一条
    # 东财日期是 ``YYYY-MM-DD``、TuShare 是 ``YYYYMMDD``：对拍前必须统一
    # （不统一会让"逐值对拍"静默变成"0 日对拍"，本探针为此响亮失败过一次）。
    east["日期"] = [str(d).replace("-", "") for d in east["日期"]]
    e_pre = east[east["日期"] <= "20240816"]
    e_post = east[east["日期"] >= "20240819"]
    for col in ("当日成交净买额", "买入成交额", "卖出成交额", "历史累计净买额"):
        print(f"[XCHK] 东财北向 断点前 {col} 非空={int(e_pre[col].notna().sum())}/{len(e_pre)}")
        print(f"[XCHK] 东财北向 断点后 {col} 非空={int(e_post[col].notna().sum())}/{len(e_post)}")
    if int(e_post["当日成交净买额"].notna().sum()) != 0:
        raise SystemExit(
            "[FATAL] 东财在断点后**仍有**北向当日成交净买额 —— "
            "「北向每日净买入已取消」这一 spec 前提不成立，必须重跑本节结论。"
        )
    if int(e_pre["当日成交净买额"].notna().sum()) == 0:
        raise SystemExit("[FATAL] 东财断点前也没有北向净买入 —— 对拍样本为空，结论不可下")

    # 断点前逐值对拍：north_money(百万元) == 东财当日成交净买额(亿元) × 100
    pre_idx = {str(r.trade_date): float(r.north_money) for r in pre.itertuples()}
    pairs = zip(e_pre["日期"], e_pre["当日成交净买额"], strict=False)
    east_pre = {d: float(v) * 100.0 for d, v in pairs if d in pre_idx and v == v}
    _cross_check("断点前 north_money vs 东财净买额×100（百万元）", pre_idx, east_pre, tol=0.01)
    print(
        f"{'=' * 78}\n[VERDICT 1] 断点后 north_money 非空 {len(post)}/{len(post)}"
        " 且零负值/零零值；东财同期的北向当日净买额 0 行非空 ⇒ **净买入口径确实已取消**，"
        "而 north_money 是**换语义后仍在日更的成交量口径**（非净买入）。"
    )


# ---------------------------------------------------------------------------
# PROBE 2 — ETF 份额（fund_share）
# ---------------------------------------------------------------------------


async def probe_fund_share(client: Any) -> None:
    import pandas as pd

    print(f"\n{'=' * 78}\n[PROBE 2] ETF 份额：TuShare fund_share")
    snap = await client._query("fund_share", trade_date="20261012")
    _assert_nonempty("fund_share(trade_date=20261012)", snap, "ETF 份额快照取不到")
    print(f"[OK  ] fund_share(20261012) rows={len(snap)} cols={list(snap.columns)}")
    print(f"       fd_share 非空={int(snap['fd_share'].notna().sum())}/{len(snap)}")
    print(f"       market 分布={dict(snap['market'].value_counts())}")
    print(f"       fund_type 分布={dict(snap['fund_type'].value_counts())}")

    # 单代码历史深度 + 单次上限（不带 ts_code 的宽窗只回最新 2000 行）
    hist = await client._query(
        "fund_share", ts_code="510300.SH", start_date="20100101", end_date="20261016"
    )
    _assert_nonempty("fund_share(510300.SH, 20100101→)", hist, "单只 ETF 的份额历史取不到")
    hd = _days(hist)
    print(f"[HIST] fund_share(510300.SH) rows={len(hist)} {hd[0]}..{hd[-1]}")
    wide = await client._query("fund_share", start_date="20000101", end_date="20261016")
    _assert_nonempty("fund_share(宽窗)", wide, "fund_share 宽窗取不到")
    wd = _days(wide)
    print(
        f"[CAP  ] 不带 ts_code 的宽窗 rows={len(wide)} 只覆盖 {wd[0]}..{wd[-1]}"
        f" ⇒ 单次上限 {FUND_SHARE_ROW_CAP} 且**截最近**，必须**逐 ts_code** 取"
    )

    # ---- 篮子选择的实机证据 ----
    print("\n[PROBE 2b] 宽基篮子选择证据：fund_basic.benchmark 文本匹配的坑")
    basic = await client._query("fund_basic", market="E")
    _assert_nonempty("fund_basic(market=E)", basic, "基金基础信息取不到，无法核篮子")
    live = basic[(basic["status"] == "L") & (basic["invest_type"] == "被动指数型")]
    print(f"[OK  ] fund_basic(market=E) rows={len(basic)}；被动指数型 & L = {len(live)}")
    # 「规模」= 份额 × 收盘价：**不能只比份额** —— 低净值的行业主题 ETF 份额更大但规模更小
    # （实测 512010.SH 沪深300医药卫生ETF 有 387 万份却只有 147 亿元）。
    px = await client._query("fund_daily", trade_date="20261012")
    _assert_nonempty("fund_daily(trade_date=20261012)", px, "拿不到收盘价，无法算规模")
    merged = live.merge(snap[["ts_code", "fd_share"]], on="ts_code", how="inner")
    merged = merged.merge(px[["ts_code", "close"]], on="ts_code", how="left")
    merged["scale_yi"] = merged["fd_share"] * merged["close"] / 1e4  # 万份 × 元 / 1e4 = 亿元
    for family, code, _exch, name in BROAD_BASE_BASKET:
        pats = BENCHMARK_PATTERNS[family]
        hit = merged[merged["benchmark"].astype(str).apply(lambda b: any(p in b for p in pats))]
        hit = hit.sort_values("scale_yi", ascending=False)
        lead = hit.iloc[0] if len(hit) else None
        own = merged[merged["ts_code"] == code]
        if not len(own):
            raise SystemExit(f"[FATAL] 选定的 {code}({name}) 不在 fund_basic(market=E) 的活 ETF 里")
        own_scale = float(own["scale_yi"].iloc[0])
        own_share = float(own["fd_share"].iloc[0])
        own_close = float(own["close"].iloc[0])
        own_bm = str(own["benchmark"].iloc[0])
        in_bm = code in set(hit["ts_code"])
        lead_scale = float(lead["scale_yi"]) if lead is not None else float("nan")
        lead_txt = f"{lead['ts_code']}({lead_scale:.1f}亿)" if lead is not None else "-"
        print(
            f"[RANK] {family}: 选定 {code} 规模={own_scale:.1f} 亿元"
            f"（fd_share={own_share:.1f} 万份 × close={own_close}）；"
            f"benchmark 文本命中 {len(hit)} 只、其中最大={lead_txt}、含选定者={in_bm}"
        )
        print(f"       选定者 benchmark 原文 = {own_bm!r}")
        if own_scale < lead_scale:
            raise SystemExit(
                f"[FATAL] 选定的 {code} 规模 {own_scale:.1f} 亿 < benchmark 文本命中集最大者 "
                f"{lead_txt} —— 选篮依据不成立（选了个更小的），附.9 必须重写"
            )
        if not in_bm:
            print(
                f"[MISS] ⚠️ benchmark 文本匹配**漏掉**了族内规模最大者 {code}"
                f"（规模 {own_scale:.1f} 亿 > 命中集最大者 {lead_txt}；原因是 benchmark 文案"
                "不写标准指数名）⇒ **benchmark 文本匹配不得单独作为选篮依据**"
            )

    # ---- 上游脏行：份额序列里是否有"非交易日"行（实测 159915.SZ 有周六行）----
    print("\n[PROBE 2c] 份额序列的脏行（非交易日的行）")
    weekend_hits: dict[str, list[str]] = {}
    for _family, code, _exch, name in BROAD_BASE_BASKET:
        sub = await client._query(
            "fund_share", ts_code=code, start_date="20260101", end_date="20261231"
        )
        days = sorted({str(d) for d in sub["trade_date"]})
        odd = [d for d in days if datetime.strptime(d, "%Y%m%d").weekday() >= 5]
        if odd:
            weekend_hits[code] = odd
            print(f"[DIRTY] {code}({name}) 非交易日行 = {odd}")
    if not weekend_hits:
        raise SystemExit(
            "[FATAL] 2026 年全年的 fund_share 里没有非交易日行 —— 附.9.2 #13 记的"
            "『159915.SZ 有 20260117 周六行』不再成立，该条结论必须重核（不得静默保留）"
        )
    else:
        print(
            f"[VERDICT 2d] 上游 `fund_share` **有非交易日行**（{list(weekend_hits)}）"
            " ⇒ 并集日历会因此多出一天，而那天只有部分成员可得 → 落 NULL + "
            "`partial_basket_coverage`（**不拿一只成员冒充全篮子**）"
        )

    # ---- 篮子逐只深度 ----
    print(f"\n[PROBE 2c] 篮子逐只：{BASKET_LO} 起的可得性（spec 要 ≥1 年日频）")
    ok = 0
    for family, code, _exch, name in BROAD_BASE_BASKET:
        df = await client._query(
            "fund_share", ts_code=code, start_date=BASKET_LO, end_date="20261231"
        )
        _assert_nonempty(f"fund_share({code})", df, f"{family} 篮成员的份额序列取不到")
        df["trade_date"] = df["trade_date"].astype(str)
        df["fd_share"] = pd.to_numeric(df["fd_share"], errors="coerce")
        dup = int(df["trade_date"].duplicated().sum())
        n_ok = int(df["fd_share"].notna().sum())
        zeros = int((df["fd_share"] == 0).sum())
        print(
            f"[OK  ] {code} {family:<7} {name:<22} rows={len(df)}"
            f" {df['trade_date'].min()}..{df['trade_date'].max()}"
            f" 非空={n_ok} 重复日={dup} 零值={zeros}"
        )
        if len(df) < MIN_BASKET_TRADING_DAYS or dup or n_ok != len(df):
            raise SystemExit(
                f"[FATAL] {code} 的份额序列不满足「≥{MIN_BASKET_TRADING_DAYS} 行、无重复日、"
                "全非空」—— spec §S1.7 的「≥1 年日频序列」不能成立。"
            )
        ok += 1
    if ok != len(BROAD_BASE_BASKET):
        raise SystemExit(f"[FATAL] 篮子只有 {ok}/{len(BROAD_BASE_BASKET)} 只可用")
    print(
        f"[VERDICT 2] fund_share 可得、日频、单代码上限 {FUND_SHARE_ROW_CAP} 行"
        f"（≥1 年绰绰有余）；篮子 {ok}/{len(BROAD_BASE_BASKET)} 只全部满足 ≥1 年。"
    )


# ---------------------------------------------------------------------------
# PROBE 3 — 南向：south_money 是日频净买入还是历史累计？
# ---------------------------------------------------------------------------


async def probe_southbound(client: Any) -> None:
    import pandas as pd

    print(f"\n{'=' * 78}\n[PROBE 3] 南向：moneyflow_hsgt.south_money 的口径")
    print("[Q    ] 这一列是**当日净买入**还是**历史累计净买额**？")
    print("        （决定 southbound_net_buy 能不能直取该列）")

    frames = []
    for lo, hi in ((PRE_LO, PRE_HI), *POST_WINDOWS):
        df = await client._query("moneyflow_hsgt", start_date=lo, end_date=hi)
        _assert_nonempty(f"moneyflow_hsgt({lo}-{hi})", df, "南向样本取不到")
        frames.append(df)
    allrows = pd.concat(frames, ignore_index=True).drop_duplicates("trade_date")
    for col in ("south_money", "ggt_ss", "ggt_sz"):
        allrows[col] = pd.to_numeric(allrows[col], errors="coerce")
    allrows["trade_date"] = allrows["trade_date"].astype(str)
    allrows = allrows.sort_values("trade_date")
    gap = (allrows["ggt_ss"] + allrows["ggt_sz"] - allrows["south_money"]).abs()
    n_bad = int((gap > 0.01).sum())
    print(
        f"[IDENT] south_money == ggt_ss + ggt_sz 不成立的行数 = {n_bad} / {len(allrows)}；"
        f"全区间 rows={len(allrows)} {allrows['trade_date'].min()}..{allrows['trade_date'].max()}"
    )
    diff = allrows["south_money"].diff()
    print(
        f"[SHAPE] south_money 负值={int((allrows['south_money'] < 0).sum())}"
        "（累计口径不会为负）；一阶差分下降天数="
        f"{int((diff < 0).sum())}/{int(diff.notna().sum())} ⇒ 差分才是**日频净买入**"
    )

    # ---- 独立对拍：东财南向 ----
    east = _eastmoney_hsgt("南向资金")
    print()
    east["日期"] = [str(d).replace("-", "") for d in east["日期"]]
    pairs = zip(east["日期"], east["历史累计净买额"], strict=False)
    cum = {d: float(v) for d, v in pairs if v == v}
    pairs2 = zip(east["日期"], east["当日成交净买额"], strict=False)
    day = {d: float(v) for d, v in pairs2 if v == v}
    print(f"[XCHK] 东财南向 rows={len(east)} 累计净买额非空={len(cum)} 当日净买额非空={len(day)}")
    if not cum or not day:
        raise SystemExit("[FATAL] 东财南向的累计/当日净买额为空 —— 南向对拍样本不可用")

    pairs3 = zip(allrows["trade_date"], allrows["south_money"], strict=False)
    ours = {d: float(v) for d, v in pairs3}
    if len(set(ours) & set(cum)) < 30:
        raise SystemExit(
            f"[FATAL] 南向对拍共同日只有 {len(set(ours) & set(cum))} 天 —— 不足以判定口径"
        )
    _cross_check(
        "south_money vs 东财历史累计净买额×1e4（亿元）",
        ours,
        {d: v * 1e4 for d, v in cum.items()},
        tol=0.01,
    )
    print("        ⇒ 口径 = 历史累计净买额、单位 = 亿元")

    prev = allrows["south_money"].shift(1)
    pairs4 = zip(allrows["trade_date"], allrows["south_money"] - prev, strict=False)
    ours_diff = {d: float(v) for d, v in pairs4 if v == v}
    _cross_check("Δsouth_money vs 东财当日成交净买额（亿元）", ours_diff, day, tol=0.01)
    tail = allrows.tail(1).iloc[0]
    print(
        f"[SAMPLE] {tail['trade_date']}: south_money={tail['south_money']:.2f}（亿元，累计）；"
        f"当日净买入 = {ours_diff[tail['trade_date']]:.4f} 亿元"
    )
    post = east[east["日期"] >= "20240819"]
    n_live = int(post["当日成交净买额"].notna().sum())
    print(
        f"[LIVE] 东财南向 断点后 rows={len(post)} 当日净买额非空={n_live}"
        " ⇒ **南向每日净买入未被取消**（与北向相反）"
    )
    if n_live == 0:
        raise SystemExit("[FATAL] 南向当日净买额在断点后全空 —— 南向日频口径也不可得")
    print(
        "[VERDICT 3] south_money = 历史累计净买额（亿元），**不是**日频净买入；"
        "日频值必须由一阶差分得出，且差分与东财逐值一致。"
    )


# ---------------------------------------------------------------------------
# PROBE 3c — 南向 `south_money` 在**同一列里换过语义**（口径断点）
# ---------------------------------------------------------------------------


async def probe_southbound_vintage_break(client: Any) -> None:
    """`south_money` 不是"一直一种口径"：它**中途换过语义**。

    判据不靠猜：把每一天的 `south_money` 同时与东财的
    ①「当日成交净买额 × 100」（日频/百万元 假设）与
    ②「历史累计净买额 × 1e4」（累计/亿元 假设）比较，谁逐值相等就归谁。
    **断点前的那一段如果当"累计"去差分，得到的是日频序列的二阶差分（纯噪声）。**
    """
    import pandas as pd

    print(f"\n{'=' * 78}\n[PROBE 3c] south_money 的口径断点（同一列换语义）")
    frames = []
    for lo, hi in SOUTH_VINTAGE_WINDOWS:
        df = await client._query("moneyflow_hsgt", start_date=lo, end_date=hi)
        _assert_nonempty(f"moneyflow_hsgt({lo}-{hi})", df, "南向断点窗口取不到")
        frames.append(df)
    rows = pd.concat(frames, ignore_index=True).drop_duplicates("trade_date")
    rows["trade_date"] = rows["trade_date"].astype(str)
    rows["south_money"] = pd.to_numeric(rows["south_money"], errors="coerce")
    rows = rows.sort_values("trade_date")

    east = _eastmoney_hsgt("南向资金")
    print()
    east["日期"] = [str(d).replace("-", "") for d in east["日期"]]
    pairs = zip(east["日期"], east["当日成交净买额"], strict=False)
    day = {d: float(v) for d, _, v in ((d, None, v) for d, v in pairs) if v == v}
    pairs2 = zip(east["日期"], east["历史累计净买额"], strict=False)
    cum = {d: float(v) for d, v in pairs2 if v == v}

    tags: list[tuple[str, str, float]] = []
    for r in rows.itertuples():
        d, v = str(r.trade_date), float(r.south_money)
        a, b = day.get(d), cum.get(d)
        if a is not None and abs(v - a * 100) < 0.01:
            tags.append((d, "DAILY", v))
        elif b is not None and abs(v - b * 1e4) < 0.01:
            tags.append((d, "CUM", v))
        else:
            tags.append((d, "??", v))
    first_cum = next((d for d, t, _v in tags if t == "CUM"), None)
    pre = [(d, v) for d, t, v in tags if first_cum is not None and d < first_cum]
    post = [(d, t, v) for d, t, v in tags if first_cum is not None and d >= first_cum]
    print(
        f"[TAGS ] 窗口内 {len(tags)} 日：DAILY={sum(1 for _d, t, _v in tags if t == 'DAILY')} "
        f"CUM={sum(1 for _d, t, _v in tags if t == 'CUM')} "
        f"??={sum(1 for _d, t, _v in tags if t == '??')}"
    )
    if first_cum is None:
        raise SystemExit("[FATAL] 窗口内没有任何一天符合「累计」假设 —— 断点结论必须重写")
    print(
        f"[BREAK ] 第一个「累计」日 = {first_cum}（此前最后一日 = "
        f"{max(d for d, _v in pre) if pre else '-'}）"
    )
    pre_neg = [d for d, v in pre if v < 0]
    print(
        f"[PRE   ] 断点前 {len(pre)} 日，其中负值 {len(pre_neg)} 日"
        f"（例：{pre_neg[:3]}）⇒ 断点前是**日频净买入**（百万元）"
    )
    post_bad = [d for d, t, v in post if t != "CUM" or v <= 0]
    print(f"[POST  ] 断点后 {len(post)} 日，不符「累计」假设或 ≤0 的 {len(post_bad)} 日")
    if not pre_neg:
        raise SystemExit(
            "[FATAL] 断点前的 south_money 没有负值 —— 「断点前 = 日频净买入」不成立，结论须重写"
        )
    if post_bad:
        raise SystemExit(
            f"[FATAL] 断点后有 {len(post_bad)} 日不符合「累计」假设（例：{post_bad[:3]}）—— "
            "南向口径结论必须重写，**不得**据此差分落库"
        )
    # 手算：**第一个"有累计前值"的日子**的 diff 必须等于东财当日净买额。
    # ⚠️ 断点日自身算不出 diff —— 它的前一日还是日频口径的值（混算是错的），
    # 故 `southbound_net_buy` 在断点日必须落 NULL + `prev_connect_day_missing`。
    cum_days = [d for d, t, _v in tags if t == "CUM"]
    idx = {d: v for d, _t, v in tags}
    print(
        f"[HAND  ] 断点日 {first_cum} 无累计前值 ⇒ 差分不可得"
        "（必须落 missing_reason='prev_connect_day_missing'）"
    )
    if len(cum_days) < 2:
        raise SystemExit("[FATAL] 累计日少于 2 天 —— 无法验证 diff 口径")
    d0, d1 = cum_days[0], cum_days[1]
    delta = idx[d1] - idx[d0]
    expect = day.get(d1)
    print(
        f"[HAND  ] {d1}: diff = {idx[d1]:.2f} − {idx[d0]:.2f} = {delta:.4f}；"
        f"东财当日净买额 = {expect}"
    )
    if expect is None or abs(delta - expect) > 0.01:
        raise SystemExit(f"[FATAL] 累计窗口首日 diff 与东财当日净买额对不上（{delta} vs {expect}）")
    print(
        f"[VERDICT 3c] `south_money` 在 **{first_cum}** 换过语义：此前 = 日频净买入（百万元，"
        f"含负值），此后 = 历史累计净买额（亿元，恒正）。"
        f"⇒ `southbound_net_buy` 只落**断点及之后**的差分窗口，且**必须**有"
        "「断点后出现 ≤0 值即响亮失败」的守卫（那是上游口径被改回去的信号）。"
    )


# ---------------------------------------------------------------------------
# PROBE 4 — 产业资本增减持：S3.2 依赖 + 未采用的上游替代
# ---------------------------------------------------------------------------


async def probe_industrial_capital(client: Any) -> None:
    print(f"\n{'=' * 78}\n[PROBE 4] 产业资本增减持：原料是公告（S3.2）")

    repo = Path(__file__).resolve().parents[1]
    ingest_dir = repo / "app/services/content_ingest"
    has_content_model = any(
        "content_items" in p.read_text(encoding="utf-8") for p in (repo / "app/models").glob("*.py")
    )
    print(f"[CODE ] S3.2 交付物 app/services/content_ingest/ 存在？{ingest_dir.exists()}")
    print(f"[CODE ] content_items / content_entities 模型存在？{has_content_model}")
    if ingest_dir.exists() or has_content_model:
        raise SystemExit(
            "[FATAL] S3.2 似乎已落地（content_ingest / content_items 存在）—— "
            "本项不应再标「待接」，结论必须重写。"
        )
    print("[CODE ] 结论：S3.2（公告采集：四类 + content_entities + raw_hash）**未落地**")
    print("        ⇒ 本项按 spec 原话标记**待接**，不落任何行（不得用假数据填充）")

    # 上游替代（仅登记，本轮不采用）—— 结构化增减持接口在不在？
    ht = await client._query("stk_holdertrade", trade_date="20261012")
    _assert_nonempty("stk_holdertrade(trade_date=20261012)", ht, "增减持结构化替代接口不可得")
    print(
        f"[ALT  ] stk_holdertrade(20261012) rows={len(ht)} cols={list(ht.columns)}"
        f"（上限 {HOLDER_TRADE_ROW_CAP}）"
    )
    in_de = sorted({str(v) for v in ht["in_de"].dropna()})
    htype = sorted({str(v) for v in ht["holder_type"].dropna()})
    print(f"       in_de 取值={in_de} holder_type 取值={htype}")
    if len(ht) >= HOLDER_TRADE_ROW_CAP:
        print(f"[ALT  ] ⚠️ 单日 {len(ht)} 行已撞上限 {HOLDER_TRADE_ROW_CAP} ⇒ 按日取也要分页")
    print(
        "[ALT  ] 该接口**本轮不采用**：spec §S1.7 明写原料来自公告（S3.2），"
        "公告面才提供 publisher_tier 与正文可回溯；换原料是控制器裁定权"
    )

    # 公告原始面在本 token 上的可得性（旁证）
    try:
        anns = await client._query("anns_d", start_date="20261001", end_date="20261016")
        print(f"[ANN  ] anns_d rows={len(anns)}")
    except Exception as exc:  # noqa: BLE001 — 权限不足是**预期内**的实测结论
        print(f"[ANN  ] anns_d 不可得：{type(exc).__name__}: {str(exc)[:120]}")


# ---------------------------------------------------------------------------
# PROBE 5 — 陆股通持仓（季度）
# ---------------------------------------------------------------------------


async def probe_northbound_holding(client: Any) -> None:
    print(f"\n{'=' * 78}\n[PROBE 5] 陆股通持仓（季度）：TuShare hk_hold 的季度末可得性")
    print(
        "[NOTE ] 方法论禁令（Ruling 19）：香港中央結算（HKSCC）是**名义持有人汇总账户**，"
        "持仓主体不可拆分 ⇒ 一切命名只说「陆股通持仓」，**不得称「外资」**"
    )

    found: list[str] = []
    empty: list[str] = []
    for d in QUARTER_ENDS:
        try:
            df = await client._query("hk_hold", trade_date=d)
        except Exception as exc:  # noqa: BLE001
            print(f"[ERR  ] hk_hold({d}) → {type(exc).__name__}: {str(exc)[:100]}")
            continue
        dist = dict(df["exchange"].value_counts()) if len(df) and "exchange" in df.columns else {}
        main = int(dist.get("SH", 0)) + int(dist.get("SZ", 0))
        print(f"[{d}] rows={len(df):<5} exchange={dist} 陆股通(SH+SZ)={main}")
        if main:
            found.append(d)
        else:
            empty.append(d)
    if not found:
        raise SystemExit("[FATAL] 全部季度末都拿不到陆股通（SH/SZ）持仓行 —— 该指标须整项待接")
    print(
        f"[VERDICT 5] hk_hold 在 {len(found)}/{len(QUARTER_ENDS)} 个季度末给出陆股通（SH/SZ）行"
        f"（0 行的季度末：{empty}），单次上限 {HK_HOLD_ROW_CAP} 行；覆盖**不规则**"
        " ⇒ 本轮只落「口径 + 滞后说明」，**不落任何数据**，ingest 与页面待后续批次"
    )


# ---------------------------------------------------------------------------
# 东财（独立产地）取数 —— 单点封装，便于重试与响亮失败
# ---------------------------------------------------------------------------


def _eastmoney_hsgt(symbol: str) -> Any:
    """东财沪深港通历史（AKShare 封装）；带重试，最终失败即响亮退出。"""
    import time

    import akshare as ak

    last: Exception | None = None
    for attempt in range(5):
        try:
            df = ak.stock_hsgt_hist_em(symbol=symbol)
        except Exception as exc:  # noqa: BLE001 — 东财偶发断连，重试即可
            last = exc
            print(f"[RETRY] stock_hsgt_hist_em({symbol}) 第 {attempt + 1} 次：{type(exc).__name__}")
            time.sleep(3)
            continue
        if df is not None and len(df):
            return df
        last = RuntimeError("empty frame")
    raise SystemExit(f"[FATAL] 东财 stock_hsgt_hist_em({symbol}) 取不到数据：{last}")


async def main() -> None:
    from app.core.providers.tushare_client import TuShareClient

    client = TuShareClient(_load_token())
    await probe_northbound_semantics(client)
    await probe_fund_share(client)
    await probe_southbound(client)
    await probe_southbound_vintage_break(client)
    await probe_industrial_capital(client)
    await probe_northbound_holding(client)
    print(
        f"\n{'=' * 78}\n[DONE] S1.7 五个源全部实机验证完毕"
        "（任一源为空 / 关键假设不成立时本脚本已 SystemExit）"
    )


if __name__ == "__main__":
    asyncio.run(main())
