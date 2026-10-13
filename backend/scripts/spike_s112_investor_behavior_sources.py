"""Task 17 Step 0 spike: S1.12 情绪 · 投资者行为的三个候选源到底可不可得？

三个问题，每个都由**实机调用**回答（Ruling 51：结论必须能从一个入库脚本复现，
且脚本在空结果 / 关键假设不成立时**响亮失败**，绝不打印 ``[OK]``）：

1. **新增散户开户数**（spec：「待 S0.3 实机验证；不可得则删除，不用估算值填充」）
   —— 候选 AKShare ``stock_account_statistics_em``（东财「股票账户统计」）实机可调吗？
   频率（月/周）？历史深度？**还有没有权威第二源**？若最新月停在多年前 ⇒ 判"不可得"，
   **按 spec 删除该项**（不是占位空图、不是估算值）。
2. **新成立基金份额**（spec：采用）—— 候选 AKShare ``fund_new_found_em``
   （东财「新成立基金」）。频率？历史深度？「新成立基金份额」该取哪一列
   （``募集份额`` vs ``成立份额``）？单位？A/C 份额双计怎么处理？
   有没有**行数上限导致的静默截断**？
3. **公募基金仓位**（spec：「季报、滞后 ~15 工作日 → 仅作背景展示，不进信号链路」）——
   候选 AKShare ``fund_stock_position_lg``（乐咕乐股「基金仓位·股票型基金」）。
   实机可调吗？**频率真的是季报吗**？滞后多少？**"仓位"的原始口径是什么**？
   上游 JSON 里有没有 ``报告期`` / ``发布日期`` 之类的字段（PIT 全靠它）？

用法（**不入任何门禁**，手动运行）::

    cd backend && .venv/bin/python scripts/spike_s112_investor_behavior_sources.py
    # 可选：把规范化后的月度序列落盘，供回补脚本**离线**复跑（默认不写文件）
    cd backend && .venv/bin/python scripts/spike_s112_investor_behavior_sources.py \\
        --json-out /tmp/s112_fund_new_found.json

结果按三态格式人工抄进 ``docs/design/data-source.md`` 附.10（append-only）。

NOTE: 与 ``scripts/spike_s17_capital_flows_sources.py`` /
``spike_s110_volatility_futures_sources.py`` 同规矩：直连 AKShare（生产路径的
客户端方法在服务侧另加守卫），**不在空结果上打印 ``[OK]``**。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

#: 开户数候选源的"新鲜度"下界：spec 要的是**当期**增量资金行为。
#: 东财来源若最新月早于此日 ⇒ 判"不可得"（数据停在 2023-08 即为实测结论）。
ACCOUNT_MIN_FRESH = date(2026, 1, 1)

#: 开户数候选源若"仅 100 行上下"即为该接口的分页上限（实测 101 行 = 单页）。
ACCOUNT_MAX_ROWS_FOR_CONSISTENCY = 1000

#: 新成立基金份额的合理单只区间（亿元；实测 min 0.10 / max 80.00）。
FUND_SHARE_MIN = 0.01
FUND_SHARE_MAX = 500.0

#: 公募基金仓位的合理区间（%，实测 min 77.81 / max 97.13）。
POSITION_MIN, POSITION_MAX = 50.0, 100.0

#: 公募基金仓位实测是**周度**观测（间隔 6–8 天占 410/455）。spec 写的是"季报"，
#: 本探针必须把两者摆在一起（结论差异写进附.10，不得悄悄按 spec 记 frequency）。
POSITION_WEEKLY_GAP_SHARE_MIN = 0.75


def _ak() -> Any:
    import akshare as ak  # noqa: PLC0415

    return ak


def _retry(fn: Any, label: str, attempts: int = 4) -> Any:
    """AKShare 调用统一重试；最终失败即 ``SystemExit``（不静默当"没数据"）。"""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — 公共数据站偶发断连，重试即可
            last = exc
            print(f"[RETRY] {label} 第 {i + 1} 次：{type(exc).__name__}: {str(exc)[:100]}")
            time.sleep(2)
    raise SystemExit(f"[FATAL] {label} 取不到数据：{type(last).__name__}: {last}")


def _require_frame(df: Any, label: str) -> Any:
    """空表 = 响亮失败（Ruling 51：不许在空结果上打印 ``[OK]``）。"""
    if df is None or not len(df):
        raise SystemExit(f"[FATAL] {label} 返回 0 行 —— 该源不可得，须按附.10 的结论处置")
    return df


# ---------------------------------------------------------------------------
# PROBE 1 — 新增散户开户数（spec 明写"不可得则删除"）
# ---------------------------------------------------------------------------


def probe_account_statistics() -> dict[str, Any] | None:
    print(f"\n{'=' * 78}")
    print("[PROBE 1] 新增散户开户数：东财 stock_account_statistics_em")
    ak = _ak()
    df = _require_frame(
        _retry(ak.stock_account_statistics_em, "stock_account_statistics_em"),
        "stock_account_statistics_em",
    )
    cols = list(df.columns)
    print(f"[ROWS ] rows={len(df)} cols={cols}")
    if "数据日期" not in cols or "新增投资者-数量" not in cols:
        raise SystemExit(f"[FATAL] 缺关键列（实测列={cols}）—— 上游改版，须重新裁决")

    periods = [str(v).strip() for v in df["数据日期"].tolist()]
    parsed = sorted({p for p in periods if len(p) == 7 and p[4] == "-"})
    if not parsed:
        raise SystemExit(f"[FATAL] 数据日期 解析不出 YYYY-MM（样例={periods[:5]}）")
    print(f"[RANGE] 月数={len(parsed)} 最早={parsed[0]} 最新={parsed[-1]}")
    print(f"[SAMPLE] 首行={df.iloc[0].to_dict()}")
    print(f"[SAMPLE] 末行={df.iloc[-1].to_dict()}")

    # 频率：月频？（两条相邻月的间隔应恒为 1 个月）
    months = [(int(p[:4]), int(p[5:])) for p in parsed]
    gaps = Counter(
        (months[i + 1][0] - months[i][0]) * 12 + (months[i + 1][1] - months[i][1])
        for i in range(len(months) - 1)
    )
    print(f"[FREQ ] 相邻月间隔分布={dict(sorted(gaps.items()))}（恒为 1 ⇒ 月频、无缺月）")

    # 值：新增投资者-数量（万户）非空/零值
    values = [float(v) for v in df["新增投资者-数量"].tolist() if v == v]  # NaN != NaN
    print(
        f"[VALUE] 新增投资者-数量 非空={len(values)}/{len(df)} "
        f"min={min(values):.2f} max={max(values):.2f} 零值={sum(1 for v in values if v == 0)}"
    )

    # 权威第二源排查：AKShare 里还有别的"新增投资者"接口吗？（同一产地不算第二源）
    ak_names = sorted(
        n
        for n in dir(ak)
        if ("investor" in n.lower() or "account" in n.lower()) and not n.startswith("_")
    )
    print(f"[2ND  ] AKShare 里含 investor/account 的函数名={ak_names}")
    print(
        "[2ND  ] 中国结算（chinaclear.cn）月报是**唯一权威产地**，AKShare 未封装"
        "（无 fetch 方法）⇒ 本仓现有通道**无权威第二源**可用于对拍/回填"
    )

    fresh = parsed[-1] >= ACCOUNT_MIN_FRESH.strftime("%Y-%m")
    rows_consistent = len(df) < ACCOUNT_MAX_ROWS_FOR_CONSISTENCY
    print(
        f"[FRESH] 最新月={parsed[-1]} ≥ {ACCOUNT_MIN_FRESH.strftime('%Y-%m')}? {fresh}"
        f"；rows={len(df)} < {ACCOUNT_MAX_ROWS_FOR_CONSISTENCY}(单页上限) ? {rows_consistent}"
    )
    if fresh:
        # 反证型断言：若某天该源真的复活了，本探针必须**失败**，逼人重新裁决
        # （而不是让旧结论"不可得"继续被抄下去）。
        raise SystemExit(
            f"[FATAL] 东财股票账户统计最新月={parsed[-1]} —— 该源似乎**已复活**；"
            "本任务的「不可得」结论不再成立，必须重新裁决（不得照抄旧结论）"
        )
    print(
        f"[VERDICT 1] 不可得（作当期指标）：最新月 {parsed[-1]}，距观测日 "
        f"{date.today().isoformat()} 已停滞 ~3 年；仅 {len(df)} 行 = 该接口单页上限；"
        "无权威第二产地。按 spec「不可得则删除，不用估算值填充」⇒ "
        "**不注册 metric_key、UI 不存在该项**"
    )
    return {"latest": parsed[-1], "rows": len(df), "first": parsed[0]}


# ---------------------------------------------------------------------------
# PROBE 2 — 新成立基金份额
# ---------------------------------------------------------------------------


def _fetch_new_found_pages() -> Any:
    """逐页取东财「新成立基金」原始 JSON（``page=N,M`` 是页窗口）。"""
    import pandas as pd  # noqa: PLC0415
    import requests  # noqa: PLC0415
    from akshare.utils import demjson  # noqa: PLC0415

    url = "https://fund.eastmoney.com/data/FundNewIssue.aspx"
    frames = []
    for page_no in (1, 2, 3):
        params = {
            "t": "xcln",
            "sort": "jzrgq,desc",
            "y": "",
            "page": f"{page_no},50000",
            "isbuy": "1",
            "v": "0.4069919776543214",
        }
        r = requests.get(url, params=params, timeout=60)
        payload = demjson.decode(r.text.strip("var newfunddata="))
        datas = payload.get("datas") or []
        print(f"[PAGE ] page={page_no},50000 rows={len(datas)}")
        if not datas:
            break
        frames.append(pd.DataFrame(datas))
    if not frames:
        raise SystemExit("[FATAL] FundNewIssue 三个页窗口全部 0 行")
    return pd.concat(frames, ignore_index=True)


def probe_fund_new_found(json_out: str | None) -> dict[str, Any]:
    print(f"\n{'=' * 78}")
    print("[PROBE 2] 新成立基金份额：东财 FundNewIssue.aspx（AKShare fund_new_found_em）")
    ak = _ak()
    df = _require_frame(_retry(ak.fund_new_found_em, "fund_new_found_em"), "fund_new_found_em")
    cols = list(df.columns)
    print(f"[ROWS ] rows={len(df)} cols={cols}")

    # ① 分页/截断实况（附.9 的 hk_hold / fut_daily 同族陷阱：撞上限即**截最近**）
    raw = _fetch_new_found_pages()
    print(f"[CAP  ] 三页原始合计 rows={len(raw)}（AKShare 单次 rows={len(df)}）")
    if len(raw) == len(df):
        print(
            "[CAP  ] 两个通道行数相同 ⇒ 没有**分页丢行**；该产地自 2023-01 起完整"
            "（2023 前只剩零星历史行，见下）"
        )

    # ② 列与单位：只有「募集份额」，**没有**「成立份额」列 —— spec 说的
    #    「新成立基金份额」在唯一可得列上就是 募集份额（= 成立时的募集份额）。
    want = ["募集份额", "成立日期", "基金代码", "基金类型"]
    missing = [c for c in want if c not in cols]
    if missing:
        raise SystemExit(f"[FATAL] 缺关键列 {missing}（实测列={cols}）—— 上游改版")
    print(
        "[COLS ] 「募集份额」= 成立时的募集（成立份额，口径注释见附.10）；"
        "本表**没有**独立的「成立份额」/「发行份额」列 ⇒ 只能取它"
    )

    values = [float(v) for v in df["募集份额"].tolist() if v == v]
    nulls = len(df) - len(values)
    out_of_range = [v for v in values if not FUND_SHARE_MIN <= v <= FUND_SHARE_MAX]
    print(
        f"[VALUE] 募集份额 非空={len(values)}/{len(df)} null={nulls} "
        f"零值={sum(1 for v in values if v == 0)} min={min(values):.2f} "
        f"max={max(values):.2f} 越界(∉[{FUND_SHARE_MIN},{FUND_SHARE_MAX}])={len(out_of_range)}"
    )
    print(
        f"[UNIT ] 单位推断=**亿元**：全表合计 {sum(values):.1f}（若为「亿份」或「万元」"
        "则量级荒谬；东财页面亦标「亿元」）"
    )

    # ③ 双计陷阱：A/C 份额是**两行**（同一次募集）—— 必须显式登记，不悄悄除以 2。
    codes = df["基金代码"].astype(str)
    dup_codes = int(codes.duplicated().sum())
    print(f"[DUP  ] 基金代码 重复={dup_codes}（=0 ⇒ 每行是不同代码）")
    ac_pairs = sum(1 for c in codes if c.endswith(("A", "C")))
    print(
        f"[DUP  ] 代码以 A/C 结尾的行={ac_pairs}（A/C 是**同一基金的两种份额**，"
        "各占一行且募集份额常相同 ⇒ 直接 sum 会双计；选择见附.10 实现决定）"
    )

    # ④ 成立日期范围 / 逐年行数（暴露 2013/2018 的孤行与 2019–2022 的空洞）
    days = [str(v)[:10] for v in df["成立日期"].tolist()]
    years = Counter(d[:4] for d in days if len(d) == 10)
    print(f"[RANGE] 成立日期 {min(days)} … {max(days)}；逐年行数={dict(sorted(years.items()))}")
    if max(days)[:4] != str(date.today().year):
        print(f"[WARN ] 最新成立日期={max(days)} 不在今年（源侧更新滞后）")

    # ⑤ 月度聚合（落库形状）：周期 = 成立日的月首（period 语义见附.10）
    per_month: dict[str, dict[str, Any]] = {}
    for _, row in df.iterrows():
        d = str(row["成立日期"])[:10]
        if len(d) != 10:
            continue
        month = d[:7] + "-01"
        slot = per_month.setdefault(
            month, {"period": month, "sum": 0.0, "n": 0, "n_null": 0, "last_day": d}
        )
        value = row["募集份额"]
        if value == value:
            slot["sum"] += float(value)
            slot["n"] += 1
        else:
            slot["n_null"] += 1
        if d > slot["last_day"]:
            slot["last_day"] = d
    months = sorted(per_month)
    non_zero = [m for m in months if per_month[m]["sum"] > 0]
    print(
        f"[AGG  ] 月度行数={len(months)} 最早={months[0]} 最新={months[-1]} "
        f"有值月={len(non_zero)} 有 null 成分的月="
        f"{sum(1 for m in months if per_month[m]['n_null'])}"
    )
    print("[AGG  ] 最近 6 个月：")
    for m in months[-6:]:
        s = per_month[m]
        print(
            f"        {m} sum={s['sum']:.2f} 亿元 n={s['n']} n_null={s['n_null']} "
            f"last_establish={s['last_day']}"
        )
    print(f"[AGG  ] 最早 3 个月：{[(m, round(per_month[m]['sum'], 2)) for m in months[:3]]}")

    if json_out:
        Path(json_out).write_text(
            json.dumps([per_month[m] for m in months], ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        print(f"[DUMP ] 月度序列已写 {json_out}（供回补脚本离线复跑）")

    print(
        f"[VERDICT 2] 可得：月频（按成立日聚合），完整深度 {months[0]} … {months[-1]}"
        f"（实测 {len(months)} 个月，其中 2019–2022 该产地无数据）；"
        "取值列 = 募集份额（亿元）"
    )
    return {"months": len(months), "first": months[0], "last": months[-1]}


# ---------------------------------------------------------------------------
# PROBE 3 — 公募基金仓位（spec 写"季报"，实测未必）
# ---------------------------------------------------------------------------


def probe_fund_position() -> dict[str, Any]:
    print(f"\n{'=' * 78}")
    print("[PROBE 3] 公募基金仓位：乐咕乐股 fund_stock_position_lg（股票型基金·总仓位）")
    ak = _ak()
    df = _require_frame(
        _retry(ak.fund_stock_position_lg, "fund_stock_position_lg"), "fund_stock_position_lg"
    )
    cols = list(df.columns)
    print(f"[ROWS ] rows={len(df)} cols={cols}")
    if cols != ["date", "close", "position"]:
        raise SystemExit(f"[FATAL] 列集合变了（实测={cols}）—— PIT/口径结论须重新裁决")

    days = [date.fromisoformat(str(v)[:10]) for v in df["date"].tolist()]
    print(f"[RANGE] date {min(days)} … {max(days)} 天；重复日={len(days) - len(set(days))}")

    gaps = Counter((days[i + 1] - days[i]).days for i in range(len(days) - 1))
    weekly = sum(v for k, v in gaps.items() if 6 <= k <= 8)
    print(f"[FREQ ] 相邻观测间隔分布={dict(sorted(gaps.items()))}")
    print(
        f"[FREQ ] 6–8 天间隔占 {weekly}/{len(days) - 1} = {weekly / (len(days) - 1):.1%}"
        " ⇒ 实测是**周度**发布（不是 spec 写的季度）"
    )
    if weekly / (len(days) - 1) < POSITION_WEEKLY_GAP_SHARE_MIN:
        raise SystemExit("[FATAL] 观测间隔不像周度 —— 频率结论（附.10）须按新证据重写，不得照抄")

    pos = [float(v) for v in df["position"].tolist()]
    bad = [v for v in pos if not POSITION_MIN <= v <= POSITION_MAX]
    print(
        f"[VALUE] position 非空={len(pos)}/{len(df)} min={min(pos):.2f} max={max(pos):.2f} "
        f"越界(∉[{POSITION_MIN},{POSITION_MAX}])={len(bad)} 零值={sum(1 for v in pos if v == 0)}"
    )
    print(f"[SAMPLE] 首行={df.iloc[0].to_dict()}")
    print(f"[SAMPLE] 末行={df.iloc[-1].to_dict()}")

    # 原始 JSON 的字段集合 = PIT 的唯一可能来源。实测只有 date/close/position。
    import akshare.stock_feature.stock_a_indicator as si  # noqa: PLC0415
    import requests  # noqa: PLC0415

    url = "https://legulegu.com/api/stockdata/fund-position"
    r = requests.get(
        url,
        params={
            "token": si.get_token_lg(),
            "type": "pos_stock",
            "category": "总仓位",
            "marketId": "5",
        },
        **si.get_cookie_csrf(url="https://legulegu.com/stockdata/fund-position/pos-stock"),
        timeout=30,
    )
    payload = r.json()
    keys = sorted(payload[0].keys()) if payload else []
    print(f"[RAW  ] 上游 JSON 字段={keys}（第一行={payload[0] if payload else None}）")
    pit_fields = [k for k in keys if any(t in k.lower() for t in ("publish", "report", "period"))]
    if pit_fields:
        # 反证：若上游补了 PIT 字段，本探针必须失败（结论要重写）。
        raise SystemExit(
            f"[FATAL] 上游 JSON 出现了 PIT 字段 {pit_fields} —— 附.10 的"
            "「published_at 只能按 spec 的 ~15 工作日推算」结论须重新裁决"
        )
    print(
        "[PIT  ] 上游**没有** report_period / publish_date / 口径方法说明 ⇒ "
        "published_at 无产地字段可依；只能按 spec 的「季报、滞后 ~15 工作日」推算"
        "（附.10 未验证余项）"
    )
    print(
        "[CALIB] 口径不可复算：上游仅给 position 数值，无成分/权重/方法说明 ⇒ "
        "「股票投资占基金总资产比」只是页面标题的转述，**未经证实**"
    )
    print(
        f"[VERDICT 3] 可得但**不是季报披露值**：实测周度观测 {len(df)} 行 / "
        f"{min(days).isoformat()}…{max(days).isoformat()}；口径与发布时刻不可从产地"
        "复算 ⇒ 按 spec 处置为**仅作背景展示**，registry 的 source_tier 与描述须体现"
        "「低频 / 滞后」，且不得进任何信号链路"
    )
    return {"rows": len(df), "first": min(days).isoformat(), "last": max(days).isoformat()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--json-out",
        default=None,
        help="把新成立基金份额的月度序列写到此路径（供回补脚本离线复跑）",
    )
    args = parser.parse_args()

    account = probe_account_statistics()
    fund_new = probe_fund_new_found(args.json_out)
    position = probe_fund_position()
    print(f"\n{'=' * 78}")
    print("[DONE] S1.12 三个候选源实机验证完毕（任一源为空 / 关键假设不成立时本脚本已 SystemExit）")
    print(f"[DONE] 开户数={account} 新成立基金份额={fund_new} 公募基金仓位={position}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
