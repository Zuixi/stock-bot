"""Step-0 spike：Wave 1 缺口能否改走**官方一手出口**？（沪/深交易所 / 巨潮 / 中证指数 / 港交所）

owner 2026-10-16 问「Wave 1 还缺的数据打算从哪来，能否从官网取」。本脚本用**实机调用**回答，
并且每个 probe 都带**反证断言**：一旦结论不再成立就**响亮失败**（Ruling 51：结论必须能从一个
入库脚本复现；空结果绝不打印 ``[OK]``）。

七个待答问题（对应 ``docs/design/official-data-sources-verification.md`` §2）：

1. **董监高持股变动（沪）**：``query.sse.com.cn`` 的时间窗是否真的生效（日更取数可行）？
   样例里有没有**披露日**字段（PIT 的前提）？无窗口时总量多少（比较"全量翻页"有多贵）？
2. **董监高持股变动（深）**：``txtStart/txtEnd`` 窗口是否生效？行内**有没有披露日**？
3. **ETF 份额（沪/深）**：能否按**历史日期**取到（决定"能否回补"）？两市各有多少只？
4. **两融（沪/深）**：沪汇总接口的 ``total`` 是不是 2010 年至今的全历史？深市 JSON
   是否真的**行内无日期字段**（这是最危险的坑：靠顺序对齐，错位即静默错标）？
5. **巨潮类目有效性**：传一个**不存在**的 ``category`` 会不会报错？——
   若静默退化为全量，则"按类目采集"必须**机械断言过滤生效**。
6. **中证指数官方估值**：官方一手 PE 序列有多长（能不能当历史序列用）？
7. **港交所季度持股**：官方出口可程序化吗？**历史季度还能不能取到**（决定是"序列"还是"快照"）？

用法（**不入任何门禁**，手动运行；需要外网）::

    cd backend && uv run python scripts/spike_official_sources.py
    cd backend && uv run python scripts/spike_official_sources.py --probe holder-change
    cd backend && uv run python scripts/spike_official_sources.py --probe hkex-holdings

结果按三态格式人工抄进 ``docs/design/official-data-sources-verification.md``（结论裁定后再回写
``docs/design/data-source.md`` 附.11）。退出码：任一 probe 失败即 1。

NOTE: 与 ``scripts/spike_s17_capital_flows_sources.py`` /
``spike_s112_investor_behavior_sources.py`` 同规矩：**只读、不写库**；AKShare 只在
"官方出口已有官方封装"处使用（沪/深 ETF 份额、深两融、中证指数估值），
需要**时间窗**参数的一律直连官方接口
（AKShare 的增减持实现是全历史翻页，不可用于日更）。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Callable
from datetime import date

import httpx

# ── 官方出口 ──────────────────────────────────────────────────────────────
SSE_COMMON_QUERY = "https://query.sse.com.cn/commonQuery.do"
SSE_HOLDER_CHANGE_SQL_ID = "COMMON_SSE_XXPL_CXJL_SSGSGFBDQK_S"
SSE_MARGIN_TOTAL_SQL_ID = "RZRQ_HZ_INFO"
SSE_COMMON_SOA_QUERY = "https://query.sse.com.cn/commonSoaQuery.do"
SSE_REFERER = "https://www.sse.com.cn/"
SZSE_REPORT_DATA = "https://www.szse.cn/api/report/ShowReport/data"
SZSE_HOLDER_CHANGE_CATALOG_ID = "1801_cxda"
SZSE_REFERER = "https://www.szse.cn/disclosure/supervision/change/index.html"
CNINFO_ANNOUNCEMENT_QUERY = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_REFERER = "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice"
CNINFO_STOCK_CHANGE_CATEGORY = "category_gqbd_szsh"  # 股本变动（大股东权益变动/减持计划）
HKEX_NORTHBOUND_URL = "https://www3.hkexnews.hk/sdw/search/mutualmarket.aspx?t={market}"

#: 增减持的**时间窗**（2026-10-12~16 是 5 个交易日；实测沪 69 行 / 深 104 行）。
HOLDER_WINDOW_START = "2026-10-12"
HOLDER_WINDOW_END = "2026-10-16"
#: 沪市单日（用于证明"窗口能窄到一天"，日更即可）。
HOLDER_SINGLE_DAY = "2026-10-16"

#: ETF 份额的历史探针日：若这两天取不到，"可回补"的结论即不成立（probe 会失败）。
ETF_HISTORY_DATES = ("2024-01-15", "2020-01-15")
ETF_LATEST_DATE = "2026-10-14"

#: 巨潮"股本变动"类目的实测量级（2026-10-08~16 共 1102 条）；用于断言"过滤真的生效"。
CNINFO_WINDOW = "2026-10-08~2026-10-16"
CNINFO_MAX_FILTERED_TOTAL = 5000

#: ETF 份额**官方 vs 主源对拍**：8 只宽基篮子成员 × 2 个交易日。
#: 结论若为"逐只逐日差值 0"，则 `etf_share_change` 保持 L2（TuShare `fund_share` 是忠实转售），
#: 不必为它新开一条官方通道（ADR 0019 判据 ②：沪市官方只有**按单日**查询，回补 = N 次请求）。
ETF_COMPARE_BASKET = (
    "510300.SH",
    "510310.SH",
    "159919.SZ",
    "510050.SH",
    "510500.SH",
    "512100.SH",
    "588000.SH",
    "159915.SZ",
)
ETF_COMPARE_DAYS = ("2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15")

#: 官方 ETF 份额出口的单位是**份**，TuShare `fd_share` 是**万份** ⇒ 对拍前 ÷1e4。
WAN = 10_000.0

#: **主源 vs 官方** 对拍（B 两融 / C PE）：取两个最近交易日。
PRIMARY_COMPARE_DAYS = ("20261014", "20261015")

#: 港交所季度持股：当期季末（实测可取）与更早季末（实测 0 行 ⇒ 历史不可回补）。
HKEX_CURRENT_QUARTER_END = "2026/09/30"
HKEX_STALE_QUARTER_ENDS = ("2025/12/31", "2025/06/30")

#: 沪市两融全历史起点（两融试点开通日）；实测 total=4016。
SSE_MARGIN_MIN_TOTAL = 3000
SSE_MARGIN_SINGLE_DAY = "2026-10-15"

#: 中证指数官方估值只有近 20 行（实测）——超过此数即"它其实是长序列"，结论要改。
CSINDEX_MAX_ROWS = 60

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
)


class ProbeFailed(RuntimeError):
    """probe 的**反证断言**失败：结论不再成立，必须以非零退出码暴露。"""


def _client() -> httpx.Client:
    return httpx.Client(timeout=40.0, follow_redirects=True, headers={"User-Agent": _UA})


def _ak() -> object:
    import akshare  # noqa: PLC0415  # 官方出口的官方封装（延迟导入：probe 可只跑不依赖 akshare 的那些）

    return akshare


def _expect(cond: bool, msg: str) -> None:
    if not cond:
        raise ProbeFailed(msg)


# ── 1/2. 董监高持股变动（沪 / 深）────────────────────────────────────────
def probe_holder_change(client: httpx.Client) -> None:
    """沪/深官网的增减持表：窗口是否生效、有没有披露日、全量翻页有多贵。"""
    base = {
        "isPagination": "true",
        "pageHelp.pageSize": "100",
        "pageHelp.pageNo": "1",
        "pageHelp.beginPage": "1",
        "pageHelp.cacheSize": "1",
        "pageHelp.endPage": "1",
        "sqlId": SSE_HOLDER_CHANGE_SQL_ID,
        "COMPANY_CODE": "",
        "NAME": "",
        "BOARDTYPE": "",
    }

    def sse(begin: str, end: str) -> dict[str, object]:
        params = {**base, "BEGIN_DATE": begin, "END_DATE": end}
        resp = client.get(SSE_COMMON_QUERY, params=params, headers={"Referer": SSE_REFERER})
        resp.raise_for_status()
        return resp.json()["pageHelp"]

    win = sse(HOLDER_WINDOW_START, HOLDER_WINDOW_END)
    day = sse(HOLDER_SINGLE_DAY, HOLDER_SINGLE_DAY)
    whole = sse("1990-01-01", "2050-01-01")
    print(
        f"  [沪] 窗口 {HOLDER_WINDOW_START}~{HOLDER_WINDOW_END}: "
        f"total={win['total']} 页数={win['pageCount']}"
    )
    print(f"  [沪] 单日 {HOLDER_SINGLE_DAY}: total={day['total']}")
    print(
        f"  [沪] 无窗口（1990~2050）: total={whole['total']} "
        f"页数={whole['pageCount']}  ← 全量翻页的代价"
    )
    _expect(int(whole["total"]) > int(win["total"]), "沪市时间窗似乎没生效（窗口总量不小于全量）")
    _expect(
        int(day["total"]) > 0, f"沪市单日 {HOLDER_SINGLE_DAY} 返回 0 行 —— 窗口/日期格式可能已变"
    )

    resp = client.get(
        SSE_COMMON_QUERY,
        params={**base, "BEGIN_DATE": HOLDER_WINDOW_START, "END_DATE": HOLDER_WINDOW_END},
        headers={"Referer": SSE_REFERER},
    )
    rows = resp.json().get("result") or []
    _expect(bool(rows), "沪市窗口查询没有返回任何 result 行")
    has_form_date = "FORM_DATE" in rows[0]
    print(f"  [沪] 样例字段: {sorted(rows[0])}")
    _expect(has_form_date, "沪市样例缺 FORM_DATE（披露日）—— PIT 前提不成立")

    def szse(start: str | None, end: str | None) -> tuple[int, int]:
        params = {
            "SHOWTYPE": "JSON",
            "CATALOGID": SZSE_HOLDER_CHANGE_CATALOG_ID,
            "TABKEY": "tab1",
            "PAGENO": "1",
            "random": "0.5",
        }
        if start and end:
            params["txtStart"] = start
            params["txtEnd"] = end
        resp = client.get(SZSE_REPORT_DATA, params=params, headers={"Referer": SZSE_REFERER})
        resp.raise_for_status()
        meta = resp.json()[0]["metadata"]
        return int(meta.get("pagecount") or 0), int(
            meta.get("recordcount") or meta.get("recordCount") or 0
        )

    win_pages, win_rows = szse(HOLDER_WINDOW_START, HOLDER_WINDOW_END)
    all_pages, all_rows = szse(None, None)
    print(
        f"  [深] 窗口 {HOLDER_WINDOW_START}~{HOLDER_WINDOW_END}: "
        f"recordcount={win_rows} 页数={win_pages}"
    )
    print(
        f"  [深] 无窗口: recordcount={all_rows} 页数={all_pages}  "
        "← AKShare 的实现就是这条路（不可用于日更）"
    )
    _expect(win_pages > 0 and win_rows > 0, "深市 txtStart/txtEnd 窗口似乎没生效")
    _expect(all_pages > win_pages, "深市无窗口的页数不大于窗口页数 —— 窗口可能没生效")

    resp = client.get(
        SZSE_REPORT_DATA,
        params={
            "SHOWTYPE": "JSON",
            "CATALOGID": SZSE_HOLDER_CHANGE_CATALOG_ID,
            "TABKEY": "tab1",
            "PAGENO": "1",
            "random": "0.5",
            "txtStart": HOLDER_WINDOW_START,
            "txtEnd": HOLDER_WINDOW_END,
        },
        headers={"Referer": SZSE_REFERER},
    )
    sz_rows = resp.json()[0].get("data") or []
    _expect(bool(sz_rows), "深市窗口查询没有返回 data 行")
    print(f"  [深] 样例字段: {sorted(sz_rows[0])}")
    _expect("jyrq" in sz_rows[0], "深市样例缺 jyrq（变动日期）—— 字段名可能已变")
    print("  [深] ⚠️ 样例中**没有**披露日字段（PIT 缺口，见报告 §2.D）")
    print("  ⚠️ 单位不同：沪 CHANGE_NUM（股）vs 深 bdgs（实测 -6.50 量级 ⇒ 万股），归一化是必做项")


# ── 3. ETF 份额（沪 / 深）────────────────────────────────────────────────
def probe_etf_share(client: httpx.Client) -> None:
    """按历史日期取 ETF 份额 —— 决定"能否回补"，以及两市各覆盖多少只。"""
    ak = _ak()
    counts: dict[str, int] = {}
    for probe_date in (ETF_LATEST_DATE, *ETF_HISTORY_DATES):
        compact = probe_date.replace("-", "")
        sse = ak.fund_etf_scale_sse(date=compact)  # type: ignore[attr-defined]
        szse = ak.fund_scale_daily_szse(  # type: ignore[attr-defined]
            start_date=compact, end_date=compact, symbol="ETF"
        )
        counts[probe_date] = int(len(sse))
        print(f"  {probe_date}: 沪={len(sse)} 只  深={len(szse)} 只")
        _expect(len(sse) > 0, f"沪市 {probe_date} 无 ETF 份额行 —— 历史回补不成立")
        _expect(len(szse) > 0, f"深市 {probe_date} 无 ETF 份额行 —— 历史回补不成立")
    _expect(
        counts[ETF_HISTORY_DATES[0]] < counts[ETF_LATEST_DATE],
        "历史日期的 ETF 只数不小于最新日 —— 可能取的是「当前值回填」而非历史时点值",
    )
    print("  ⚠️ 深市官网页脚自述：T 日夜间值仅供参考，**以 T+1 日早间为准**（须 T+1 采或回改）")


# ── 4. 两融（沪 / 深）───────────────────────────────────────────────────
def probe_margin(client: httpx.Client) -> None:
    """沪汇总的全历史 total；深市 JSON 是否真的行内无日期字段。"""
    resp = client.get(
        SSE_COMMON_SOA_QUERY,
        params={
            "isPagination": "true",
            "sqlId": SSE_MARGIN_TOTAL_SQL_ID,
            "pageHelp.pageSize": "25",
            "pageHelp.pageNo": "1",
            "pageHelp.beginPage": "1",
            "pageHelp.cacheSize": "1",
            "pageHelp.endPage": "1",
        },
        headers={"Referer": SSE_REFERER},
    )
    resp.raise_for_status()
    payload = resp.json()
    total = int(payload["pageHelp"]["total"])
    rows = payload.get("result") or []
    print(f"  [沪] RZRQ_HZ_INFO: total={total}（2010-03-31 起，两融试点开通日）")
    print(f"  [沪] 样例: {rows[0] if rows else None}")
    _expect(
        total >= SSE_MARGIN_MIN_TOTAL, f"沪市两融 total={total} 低于预期（{SSE_MARGIN_MIN_TOTAL}）"
    )
    _expect(bool(rows) and "opDate" in rows[0], "沪市两融样例缺 opDate —— 日期字段可能已改")

    ak = _ak()
    sz = ak.stock_margin_szse(date=SSE_MARGIN_SINGLE_DAY.replace("-", ""))  # type: ignore[attr-defined]
    print(
        f"  [深] stock_margin_szse({SSE_MARGIN_SINGLE_DAY}): "
        f"rows={len(sz)} cols={sorted(sz.columns)}"
    )
    _expect(len(sz) > 0, "深市两融单日返回 0 行")
    date_like = {name for name in sz.columns if "date" in str(name).lower() or "日期" in str(name)}
    print(
        f"  [深] 含日期语义的列: {sorted(date_like) or '（无）'}  "
        "← 实测：行内无日期字段，须靠顺序+日历对齐"
    )
    _expect(not date_like, "深市两融 JSON 出现了日期列 —— 该坑的结论要更新（可能是好事）")


# ── 5. 巨潮类目有效性 ────────────────────────────────────────────────────
def probe_cninfo_category(client: httpx.Client) -> None:
    """无效 category 是否静默退化为全量？——若是，"按类目采集"必须机械断言过滤生效。"""
    headers = {"Referer": CNINFO_REFERER}
    common = {
        "pageNum": "1",
        "pageSize": "5",
        "column": "szse",
        "tabName": "fulltext",
        "seDate": CNINFO_WINDOW,
        "isHLtitle": "true",
    }

    def total(category: str) -> int:
        resp = client.post(
            CNINFO_ANNOUNCEMENT_QUERY, data={**common, "category": category}, headers=headers
        )
        resp.raise_for_status()
        return int(resp.json().get("totalAnnouncement") or 0)

    unfiltered = total("")
    bogus = total("category_gddj_szsh")  # 实测：不存在的 code
    print(f"  无类目: total={unfiltered}")
    print(f"  不存在的 code（category_gddj_szsh）: total={bogus}")
    _expect(
        bogus == unfiltered,
        "不存在的 category 竟然不等于全量 —— 需要重新刻画这个坑（可能是好事）",
    )

    # 管道**实际配置**的每个类目都要断言过滤生效（ADR 0019 硬纪律；
    # 少断言一个类目，就等于允许它在生产里静默采全量）。
    from app.core.providers.cninfo_client import (  # noqa: PLC0415
        ANNOUNCEMENT_CATEGORIES,
        ANNOUNCEMENT_CATEGORY_QUERY,
    )

    for category in ANNOUNCEMENT_CATEGORIES:
        code = ANNOUNCEMENT_CATEGORY_QUERY[category]
        filtered = total(code)
        print(f"  类目 {category:<14} {code:<22} total={filtered}")
        _expect(filtered > 0, f"类目 {category}（{code}）返回 0 条 —— code 可能已失效")
        _expect(
            filtered < unfiltered,
            f"类目 {category}（{code}）与不带类目的总量相同 —— 过滤没生效，不能靠它采集",
        )
        if category == "equity_change":
            _expect(
                filtered <= CNINFO_MAX_FILTERED_TOTAL,
                f"股本变动类目量级 {filtered} 超出预期上限 {CNINFO_MAX_FILTERED_TOTAL}",
            )
        _expect(code.startswith("category_"), f"类目 {category} 的 code 形态不对：{code!r}")
    print("  ⚠️ 实测：**无效 category 静默退化为全量**（不报错）→ 写路径必须断言过滤生效")


# ── 6. 中证指数官方估值 ──────────────────────────────────────────────────
def probe_csindex(client: httpx.Client) -> None:
    """官方一手 PE/股息率序列有多长（决定它是"序列"还是"只能对拍的快照"）。"""
    del client
    ak = _ak()
    for code in ("000300", "000905"):
        df = ak.stock_zh_index_value_csindex(symbol=code)  # type: ignore[attr-defined]
        first = df.iloc[-1] if len(df) else None
        last = df.iloc[0] if len(df) else None
        print(
            f"  {code}: rows={len(df)} 区间 {first['日期'] if first is not None else '?'}"
            f" → {last['日期'] if last is not None else '?'}"
        )
        _expect(len(df) > 0, f"中证指数 {code} 返回 0 行")
        _expect(
            len(df) <= CSINDEX_MAX_ROWS,
            f"中证指数 {code} 返回 {len(df)} 行（>{CSINDEX_MAX_ROWS}）"
            "—— 它其实能当历史序列用，结论要更新",
        )
    print("  ⚠️ 实测只有近 20 行 ⇒ 只够做 `pe_csi300` 的口径对拍（附.7.4 #5），不能当历史序列")


# ── 7. 港交所季度持股 ────────────────────────────────────────────────────
def _hkex_rows(client: httpx.Client, market: str, shareholding_date: str) -> int:
    """ASP.NET WebForms：GET 取 __VIEWSTATE，再 POST（__EVENTTARGET=btnSearch）查指定日期。

    行数用 ``"Stock Code:"`` 的出现次数计（实测：当期季末 2043 次 / 失效日期 0 次，
    比数 ``<tr`` 稳——后者会把表头算进来）。
    """
    url = HKEX_NORTHBOUND_URL.format(market=market)
    page = client.get(url)
    page.raise_for_status()
    import re  # noqa: PLC0415

    def hidden(name: str) -> str:
        match = re.search(rf'name="{name}"[^>]*value="([^"]*)"', page.text)
        return match.group(1) if match else ""

    resp = client.post(
        url,
        data={
            "__EVENTTARGET": "btnSearch",
            "__EVENTARGUMENT": "",
            "__VIEWSTATE": hidden("__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": hidden("__VIEWSTATEGENERATOR"),
            "today": hidden("today"),
            "sortBy": "stockcode",
            "sortDirection": "asc",
            "alertMsg": "",
            "txtShareholdingDate": shareholding_date,
        },
    )
    resp.raise_for_status()
    return resp.text.count("Stock Code:")


def probe_hkex_holdings(client: httpx.Client) -> None:
    """官方出口能否程序化取数？历史季度还在不在（决定"序列"vs"当期快照"）？"""
    current = _hkex_rows(client, "sh", HKEX_CURRENT_QUARTER_END)
    deep = _hkex_rows(client, "sz", HKEX_CURRENT_QUARTER_END)
    print(f"  沪股通 {HKEX_CURRENT_QUARTER_END}: 表格行 ≈ {current}")
    print(f"  深股通 {HKEX_CURRENT_QUARTER_END}: 表格行 ≈ {deep}")
    _expect(current > 100 and deep > 100, "当期季末取不到持股行 —— 官方出口或查询姿势已变")
    stale = {d: _hkex_rows(client, "sh", d) for d in HKEX_STALE_QUARTER_ENDS}
    print(f"  更早季末（沪）: {stale}")
    _expect(
        all(rows == 0 for rows in stale.values()),
        "更早的季末仍能取到数据 —— “历史不可回补”的结论要更新（可回补是好事，但要改设计）",
    )
    print("  ⚠️ 结论：官方只是**当期快照**（2024-08-19 起季度化，季末后第 5 个北向交易日发布）；")
    print("     历史序列只能走主源（hk_hold），且必须落「季度快照纪律」（错过即永久缺）")


def probe_etf_share_compare(client: httpx.Client) -> None:
    """ETF 份额：**官方一手（沪深交易所）** vs **主源（TuShare `fund_share`）** 对拍。

    ADR 0019 判据 ④ 的证据：只有主源与官方**不一致**时才值得另开官方通道；一致则保持 L2。

    实测（2026-10-17，本 probe）两条坑：
    1. **单位**：官方两个出口都返回**份**，TuShare `fd_share` 是**万份** ⇒ 对拍前 ÷1e4。
    2. **深市的日期标签差 1 个交易日**：同一份额值出现在官方 D 日与 TuShare 的**下一个交易日**
       （沪市**没有**偏移，同日逐点一致）。官方页脚自述「T 日夜间值仅供参考、以 T+1 早间为准」，
       故偏移语义（xlsx 的日期列是统计日还是发布日）**尚未定论**，已登记为未验证余项。
       ⇒ 深市按**值 → 日期**匹配，断言"每个主源值都能在官方值里找到"，并打印日期偏移。
    """
    del client
    if not os.environ.get("TUSHARE_TOKEN"):
        print("  （跳过：未设 TUSHARE_TOKEN —— 对拍需要主源）")
        return

    async def _run() -> None:
        from app.core.providers.akshare_client import AkShareClient  # noqa: PLC0415
        from app.core.providers.tushare_client import TuShareClient  # noqa: PLC0415

        ak = AkShareClient()
        ts = TuShareClient(os.environ["TUSHARE_TOKEN"])
        days = [d for d in ETF_COMPARE_DAYS]
        day_lo, day_hi = days[0].replace("-", ""), days[-1].replace("-", "")

        sse: dict[tuple[str, str], float] = {}
        for day in days:
            for _, row in (await ak.fetch_etf_share_sse(day.replace("-", ""))).iterrows():
                code = str(row.get("基金代码") or "").zfill(6)
                if code and row.get("基金份额") is not None:
                    sse[(code, day)] = float(row["基金份额"]) / WAN
        szse: dict[str, dict[str, float]] = {}
        for _, row in (await ak.fetch_etf_share_szse(day_lo, day_hi)).iterrows():
            code = str(row.get("基金代码") or "").zfill(6)
            if code and row.get("基金份额") is not None:
                szse.setdefault(code, {})[str(row["日期"])] = float(row["基金份额"]) / WAN

        primary: dict[str, dict[str, float]] = {}
        for ts_code in ETF_COMPARE_BASKET:
            df = await ts.fetch_fund_share(ts_code=ts_code, start_date=day_lo, end_date=day_hi)
            primary[ts_code] = {
                f"{str(r.trade_date)[:4]}-{str(r.trade_date)[4:6]}-{str(r.trade_date)[6:8]}": float(
                    r.fd_share
                )
                for r in df.itertuples()
                if r.fd_share is not None
            }

        # ── 沪市：**同日逐点**必须一致 ────────────────────────────────
        checked = bad = 0
        for ts_code in (c for c in ETF_COMPARE_BASKET if c.endswith(".SH")):
            code = ts_code.split(".")[0]
            for day, value in primary[ts_code].items():
                off = sse.get((code, day))
                if off is None:
                    continue
                checked += 1
                if abs(value - off) > 1e-4:
                    bad += 1
                    print(f"    ✘ {ts_code} {day}: 主源={value} 官方={off}")
        print(f"  [沪] 同日对拍 {checked} 点；不一致 {bad} 点")
        _expect(checked >= 4, f"沪市对拍点太少（{checked}）—— 至少一侧没取到数")
        _expect(not bad, "沪市官方与主源份额不一致 —— 需按 ADR 0019 判据 ④ 重评是否切换")

        # ── 深市：值 → 日期匹配，披露日期偏移 ─────────────────────────
        matched = unmatched = 0
        offsets: set[int] = set()
        for ts_code in (c for c in ETF_COMPARE_BASKET if c.endswith(".SZ")):
            code = ts_code.split(".")[0]
            off_by_value = {round(v, 4): d for d, v in szse.get(code, {}).items()}
            # 窗口最早的**主源日**在官方侧没有"前一日"可对（差的正是那 1 个交易日）⇒ 跳过，
            # 否则会把窗口边缘效应误报成"值不一致"。
            primary_days = sorted(primary[ts_code])
            for day in primary_days[1:]:
                value = primary[ts_code][day]
                off_day = off_by_value.get(round(value, 4))
                if off_day is None:
                    unmatched += 1
                    continue
                matched += 1
                offsets.add(int(day.replace("-", "")) - int(off_day.replace("-", "")))
        print(
            f"  [深] 值匹配 {matched} 点；未匹配 {unmatched} 点；"
            f"日期差（主源−官方）= {sorted(offsets)}"
        )
        _expect(matched >= 2, f"深市值匹配点太少（{matched}）—— 至少一侧没取到数")
        _expect(
            unmatched == 0,
            "深市存在**值本身**对不上的点（不只是日期标签差异）—— 需按 ADR 0019 重评",
        )
        print("  ✔ 沪市同日逐点一致；深市逐值一致、但日期标签有偏移（已登记为未验证余项）")
        print("  ⇒ 结论：ETF 份额保持 L2（TuShare 是忠实转售），不为它另开官方通道")

    asyncio.run(_run())


def probe_primary_compare(client: httpx.Client) -> None:
    """**主源 vs 官方** 对拍（ADR 0019 判据 ④ 的证据）：B 两融 + C 沪深300 PE。

    - **B 两融**：沪市官方 `RZRQ_HZ_INFO` 的 `rzye`（元） vs TuShare `margin` 的 SSE `rzye`。
      结论若为 0 差异 ⇒ 两融保持 L2、只作对拍（不必换源）。
    - **C PE**：中证指数官网 `市盈率1` vs TuShare `index_dailybasic.pe`（000300.SH）。
      官网只有近 20 行 ⇒ 只能做近期对拍，这也正是本 probe 的用途。
    """
    del client
    if not os.environ.get("TUSHARE_TOKEN"):
        print("  （跳过：未设 TUSHARE_TOKEN —— 对拍需要主源）")
        return

    async def _run() -> None:
        from app.core.providers.akshare_client import AkShareClient  # noqa: PLC0415
        from app.core.providers.tushare_client import TuShareClient  # noqa: PLC0415

        ak = AkShareClient()
        ts = TuShareClient(os.environ["TUSHARE_TOKEN"])

        # ── B 两融（沪市）────────────────────────────────────────────
        sse = await ak.fetch_sse_margin_summary(PRIMARY_COMPARE_DAYS[0], PRIMARY_COMPARE_DAYS[-1])
        official_margin = {str(r["信用交易日期"]): float(r["融资余额"]) for _, r in sse.iterrows()}
        tushare = await ts.fetch_margin(
            start_date=PRIMARY_COMPARE_DAYS[0], end_date=PRIMARY_COMPARE_DAYS[-1]
        )
        tushare = tushare[tushare["exchange_id"] == "SSE"]
        checked = mismatch = 0
        for row in tushare.itertuples():
            day = str(row.trade_date)
            off = official_margin.get(day)
            if off is None:
                continue
            checked += 1
            if abs(float(row.rzye) - off) > 1.0:  # 元，允许 1 元浮点噪声
                mismatch += 1
                print(f"    ✘ 两融 {day}: 主源={row.rzye} 官方={off}")
        print(f"  [B] 沪市两融 同日对拍 {checked} 天；不一致 {mismatch} 天")
        _expect(checked > 0, "两融对拍没有交集（日期或字段名变了）")
        _expect(not mismatch, "两融主源与官方不一致 —— 需按 ADR 0019 判据 ④ 重评")

        # ── C PE（沪深300）───────────────────────────────────────────
        csindex = await ak.fetch_index_valuation_csindex("000300")
        official_pe = {str(r["日期"]): float(r["市盈率1"]) for _, r in csindex.iterrows()}
        tushare_pe = await ts.fetch_index_dailybasic(ts_code="000300.SH")
        rel_diffs: list[float] = []
        for row in tushare_pe.itertuples():
            day = f"{str(row.trade_date)[:4]}-{str(row.trade_date)[4:6]}-{str(row.trade_date)[6:8]}"
            off = official_pe.get(day)
            if off is None or not off:
                continue
            rel_diffs.append((float(row.pe) - off) / off)
        print(f"  [C] 沪深300 PE 对拍 {len(rel_diffs)} 天")
        _expect(len(rel_diffs) > 0, "PE 对拍没有交集（官网只保留近 20 行，窗口已滑走？）")
        worst = max(rel_diffs, key=abs)
        print(
            f"      相对差：中位 {sorted(rel_diffs)[len(rel_diffs) // 2]:+.2%} · 最大 {worst:+.2%}"
        )
        # **已知口径差，不是 bug**：官方"市盈率1"与 TuShare `index_dailybasic.pe` 都是整体法，
        # 但股本口径/更新时间不同 ⇒ 实测系统性差 ~1%。断言只卡"差异有界且稳定"（超过 3% 才说明
        # 某一侧口径变了，需要重新裁决）。
        _expect(
            abs(worst) < 0.03,
            f"PE 相对差最大 {worst:+.2%} 超过 3% —— 某一侧口径可能变了，需按 ADR 0019 重评",
        )
        print("  ✔ 两融：主源与官方逐日一致 ⇒ 保持 L2（只作对拍）")
        print("  ✔ PE：存在**已知的系统性口径差（~1%）**，两者都不足以为对方背书 ⇒")
        print("     保持 L2（官网只有近 20 行，不满足判据 ②），差异记录在 metric-dictionary")

    asyncio.run(_run())


PROBES: dict[str, Callable[[httpx.Client], None]] = {
    "holder-change": probe_holder_change,
    "etf-share": probe_etf_share,
    "margin": probe_margin,
    "cninfo-category": probe_cninfo_category,
    "csindex": probe_csindex,
    "hkex-holdings": probe_hkex_holdings,
    "etf-share-compare": probe_etf_share_compare,
    "primary-compare": probe_primary_compare,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wave 1 官方一手出口 Step-0 探针（只读）")
    parser.add_argument(
        "--probe",
        action="append",
        choices=sorted(PROBES),
        help="只跑指定 probe（可重复）；默认全部",
    )
    args = parser.parse_args(argv)
    selected = args.probe or sorted(PROBES)
    print(f"官方一手出口探针 @ {date.today().isoformat()} —— probes: {', '.join(selected)}")

    failures: list[str] = []
    with _client() as client:
        for name in selected:
            print(f"\n[{name}]")
            try:
                PROBES[name](client)
            except ProbeFailed as exc:
                failures.append(f"{name}: 反证断言失败 —— {exc}")
                print(f"  ✘ {exc}")
            except Exception as exc:  # noqa: BLE001  # 网络/解析异常一律计入失败，不吞
                failures.append(f"{name}: {type(exc).__name__}: {exc}")
                print(f"  ✘ {type(exc).__name__}: {exc}")

    print("\n== 汇总 ==")
    for name in selected:
        print(f"  {'✘' if any(f.startswith(name + ':') for f in failures) else '✔'} {name}")
    if failures:
        print("\n失败明细：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("全部 probe 通过（结论与 docs/design/official-data-sources-verification.md 一致）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
