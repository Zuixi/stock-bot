"""TuShare Pro API client.

Wraps the synchronous ``tushare`` library with async helpers, retry logic,
and rate-limiting suitable for the 2000-credit tier (50 req/min).

API reference: docs/references/tushare/index.md

Usage::

    from app.core.providers.tushare_client import get_tushare_client

    client = get_tushare_client()
    df = await client.fetch_stock_basic("Shanghai_Stocks")
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pandas as pd

from app.core.providers.rate_limited import RateLimitedSyncProvider

logger = logging.getLogger(__name__)

EXCHANGE_TO_TUSHARE: dict[str, str] = {
    "Shanghai_Stocks": "SSE",
    "Shenzen_Stocks": "SZSE",
    "Beijing_Stocks": "BSE",
}

TUSHARE_TO_EXCHANGE: dict[str, str] = {v: k for k, v in EXCHANGE_TO_TUSHARE.items()}

_REQUEST_INTERVAL = 0.5  # seconds between requests (120 req/min, under 200/min limit)
_MAX_RETRIES = 3


class TuShareClient(RateLimitedSyncProvider):
    """Async-friendly wrapper around ``tushare.pro_api``.

    Throttling / retry / thread-offload live in :class:`RateLimitedSyncProvider`.
    """

    request_interval = _REQUEST_INTERVAL
    max_retries = _MAX_RETRIES

    def __init__(self, token: str, max_retries: int = _MAX_RETRIES) -> None:
        super().__init__()
        import tushare as ts

        if not token:
            logger.error("[TOKEN ERROR]: NEED VALID TOKEN.")
            raise ValueError("TuShare token is required. Set TUSHARE_TOKEN in .env")
        self._pro = ts.pro_api(token)
        self.max_retries = max(1, max_retries)

    # ------------------------------------------------------------------
    # Base-class hooks
    # ------------------------------------------------------------------

    def handle_error(self, api_name: str, exc: Exception) -> None:
        msg = str(exc)
        if "权限" in msg or "40203" in msg:
            raise RuntimeError(
                f"TuShare API '{api_name}' permission denied. "
                "Check your token credits at https://tushare.pro/document/1?doc_id=108"
            ) from exc

    def normalize_result(self, result: Any) -> Any:
        return pd.DataFrame() if result is None else result

    # ------------------------------------------------------------------
    # Low-level query with retry + rate-limiting
    # ------------------------------------------------------------------

    def _query_sync(self, api_name: str, fields: str = "", **kwargs: Any) -> pd.DataFrame:
        """Call a TuShare Pro API with retry and inter-request throttling."""
        if fields:
            return self.invoke_sync(
                api_name, lambda: self._pro.query(api_name, fields=fields, **kwargs)
            )
        return self.invoke_sync(api_name, lambda: self._pro.query(api_name, **kwargs))

    async def _query(self, api_name: str, fields: str = "", **kwargs: Any) -> pd.DataFrame:
        return await asyncio.to_thread(self._query_sync, api_name, fields=fields, **kwargs)

    # ------------------------------------------------------------------
    # Stock universe APIs
    # ------------------------------------------------------------------

    async def fetch_stock_basic(
        self,
        exchange: str,
        list_status: str = "L",
        fields: str = (
            "ts_code,symbol,name,area,industry,fullname,enname,cnspell,"
            "market,exchange,list_status,list_date,delist_date,is_hs,"
            "act_name,act_ent_type"
        ),
    ) -> pd.DataFrame:
        """Fetch basic stock info for an exchange.

        See: docs/references/tushare/stock_list.md
        """
        ts_exchange = EXCHANGE_TO_TUSHARE.get(exchange, exchange)
        return await self._query(
            "stock_basic",
            fields=fields,
            exchange=ts_exchange,
            list_status=list_status,
        )

    async def fetch_stock_company(
        self,
        exchange: str,
        fields: str = (
            "ts_code,com_name,com_id,exchange,chairman,manager,secretary,"
            "reg_capital,setup_date,province,city,introduction,website,"
            "email,employees,main_business,business_scope"
        ),
    ) -> pd.DataFrame:
        """Fetch listed-company details for an exchange.

        See: docs/references/tushare/company_basic_info.md
        """
        ts_exchange = EXCHANGE_TO_TUSHARE.get(exchange, exchange)
        return await self._query(
            "stock_company",
            fields=fields,
            exchange=ts_exchange,
        )

    async def fetch_new_share(
        self,
        start_date: str = "",
        end_date: str = "",
    ) -> pd.DataFrame:
        """Fetch IPO listing data.

        See: docs/references/tushare/IPO_stocks.md
        """
        kwargs: dict[str, str] = {}
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return await self._query("new_share", **kwargs)

    async def fetch_stk_managers(self, ts_code: str) -> pd.DataFrame:
        """Fetch company management info.

        See: docs/references/tushare/company_sharers.md
        """
        return await self._query("stk_managers", ts_code=ts_code)

    # ------------------------------------------------------------------
    # Trading data APIs
    # ------------------------------------------------------------------

    async def fetch_daily(
        self,
        trade_date: str = "",
        ts_code: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> pd.DataFrame:
        """Fetch daily OHLCV quotes.

        Prefer ``trade_date`` for batch-fetching an entire market's daily data
        (more efficient than looping over ts_code).

        See: docs/references/tushare/reference.md
        """
        kwargs: dict[str, str] = {}
        if trade_date:
            kwargs["trade_date"] = trade_date
        if ts_code:
            kwargs["ts_code"] = ts_code
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return await self._query("daily", **kwargs)

    async def fetch_adj_factor(
        self,
        ts_code: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> pd.DataFrame:
        """Fetch daily adjust factors (复权因子) for one stock's history."""
        kwargs: dict[str, str] = {"ts_code": ts_code}
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return await self._query("adj_factor", **kwargs)

    async def fetch_trade_cal(
        self,
        exchange: str = "SSE",
        start_date: str = "",
        end_date: str = "",
        is_open: str = "1",
    ) -> pd.DataFrame:
        """Fetch trading calendar.

        See: docs/references/tushare/reference.md
        """
        kwargs: dict[str, str] = {"exchange": exchange, "is_open": is_open}
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return await self._query("trade_cal", fields="cal_date", **kwargs)

    # ------------------------------------------------------------------
    # Index data APIs
    # ------------------------------------------------------------------

    async def fetch_index_daily(
        self,
        ts_code: str = "",
        trade_date: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> pd.DataFrame:
        """Fetch index daily OHLCV (e.g. 000001.SH for 上证指数).

        Use ``trade_date`` for a snapshot of all indices on one day, or
        ``ts_code`` + date range for one index's history.
        """
        kwargs: dict[str, str] = {}
        if ts_code:
            kwargs["ts_code"] = ts_code
        if trade_date:
            kwargs["trade_date"] = trade_date
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return await self._query("index_daily", **kwargs)

    # ------------------------------------------------------------------
    # Daily metrics API
    # ------------------------------------------------------------------

    async def fetch_daily_basic(
        self,
        ts_code: str = "",
        trade_date: str = "",
        start_date: str = "",
        end_date: str = "",
        fields: str = (
            "ts_code,trade_date,close,turnover_rate,volume_ratio,"
            "pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,"
            "total_share,float_share,free_share,total_mv,circ_mv"
        ),
    ) -> pd.DataFrame:
        """Fetch daily fundamental indicators (PE/PB/market cap etc.).

        See: docs/references/tushare/每日指标.md
        """
        kwargs: dict[str, str] = {}
        if ts_code:
            kwargs["ts_code"] = ts_code
        if trade_date:
            kwargs["trade_date"] = trade_date
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return await self._query("daily_basic", fields=fields, **kwargs)

    # ------------------------------------------------------------------
    # Fund / convertible-bond daily APIs (P5 行情面)
    # ------------------------------------------------------------------

    async def fetch_fund_daily(
        self,
        ts_code: str = "",
        trade_date: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> pd.DataFrame:
        """Fetch ETF/LOF daily OHLCV (TuShare fund_daily，vol=手、amount=千元).

        See: docs/references/tushare/reference.md
        """
        kwargs: dict[str, str] = {}
        if ts_code:
            kwargs["ts_code"] = ts_code
        if trade_date:
            kwargs["trade_date"] = trade_date
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return await self._query("fund_daily", **kwargs)

    async def fetch_fund_share(
        self,
        ts_code: str = "",
        trade_date: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> pd.DataFrame:
        """Fetch ETF/LOF 份额（TuShare fund_share，``fd_share`` 单位**万份**）.

        两处实测约束（spec §S1.7 Step-0，见
        ``backend/scripts/spike_s17_capital_flows_sources.py``）：

        - **单次 2000 行上限，且超限时截"最近"**：不带 ``ts_code`` 的宽窗（如
          ``20000101→20261016``）只回最近 2000 行（实测只覆盖 ``20261009..20261016``）——
          按区间拉全市场会**静默缺头**，取一只基金的长历史必须**逐 ts_code**。
        - ``fd_share`` 实测全列非空、无 0 值、每只基金每交易日至多一行（无重复日）。
        """
        kwargs: dict[str, str] = {}
        if ts_code:
            kwargs["ts_code"] = ts_code
        if trade_date:
            kwargs["trade_date"] = trade_date
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return await self._query("fund_share", **kwargs)

    async def fetch_cb_daily(
        self,
        ts_code: str = "",
        trade_date: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> pd.DataFrame:
        """Fetch convertible-bond daily OHLCV (TuShare cb_daily，vol=张、amount=千元).

        See: docs/references/tushare/reference.md
        """
        kwargs: dict[str, str] = {}
        if ts_code:
            kwargs["ts_code"] = ts_code
        if trade_date:
            kwargs["trade_date"] = trade_date
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return await self._query("cb_daily", **kwargs)

    async def fetch_cb_basic(self) -> pd.DataFrame:
        """Fetch convertible-bond reference list (cb_basic).

        该积分档返回列不含 issuer/list_status：正股名称见 stk_short_name，
        在市判定用 delist_date 为空。
        """
        return await self._query("cb_basic")

    # ------------------------------------------------------------------
    # 两融 / 指数日度指标 / 指数成分权重（spec §S1.9 / §S1.11 底座）
    # ------------------------------------------------------------------

    async def fetch_margin(
        self,
        trade_date: str = "",
        start_date: str = "",
        end_date: str = "",
        fields: str = "trade_date,exchange_id,rzye,rzmre,rzche,rqye,rqmcl,rzrqye,rqyl",
    ) -> pd.DataFrame:
        """融资融券**市场汇总**（TuShare ``margin``，交易所口径，2026-10-12 实机验证）.

        一行 = 一个交易日 × 一个交易所（`exchange_id` ∈ {SSE, SZSE, BSE}）；
        金额单位**元**（实测 20261009 SSE `rzye`=1.291437e12 元），`rqyl` 单位**股**。
        单次返回上限 **4000 行**（约 8 年 × 3 交易所）→ 长区间回补必须分片。

        字段：`rzye` 融资余额 / `rzmre` 融资买入额 / `rzche` 融资偿还额 /
        `rqye` 融券余额 / `rqmcl` 融券卖出量 / `rzrqye` 融资融券余额 / `rqyl` 融券余量。
        """
        kwargs: dict[str, str] = {}
        if trade_date:
            kwargs["trade_date"] = trade_date
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return await self._query("margin", fields=fields, **kwargs)

    async def fetch_margin_detail(
        self,
        trade_date: str = "",
        ts_code: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> pd.DataFrame:
        """融资融券**个股明细**（TuShare ``margin_detail``，2026-10-12 实机验证）.

        实测 20261009 = 4,455 行（沪深北全市场，单日）；单次返回上限 **6000 行**
        → 按日拉取即可，区间拉取会被上限截断（实测两天窗口就撞 6000）。
        **本任务的 spec §S1.9 只需要市场汇总**（`margin`），个股明细留作后续面。
        """
        kwargs: dict[str, str] = {}
        if trade_date:
            kwargs["trade_date"] = trade_date
        if ts_code:
            kwargs["ts_code"] = ts_code
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return await self._query("margin_detail", **kwargs)

    async def fetch_index_dailybasic(
        self,
        ts_code: str = "",
        trade_date: str = "",
        start_date: str = "",
        end_date: str = "",
        fields: str = (
            "ts_code,trade_date,total_mv,float_mv,total_share,float_share,"
            "free_share,turnover_rate,turnover_rate_f,pe,pe_ttm,pb"
        ),
    ) -> pd.DataFrame:
        """指数日度指标（TuShare ``index_dailybasic``，2026-10-12 实机验证）.

        **两个换手率口径并存，必须显式选一个**（spec §S1.9 验收判据）：

        - ``turnover_rate``  = 成交量 / **总股本**
        - ``turnover_rate_f`` = 成交量 / **自由流通股本**（free_share）

        本任务的 registry 采用 **``turnover_rate_f``（自由流通股本口径）**，源即
        TuShare 该列自身定义，不做二次换算。

        ⚠️ **并非所有指数都有**：实测 `index_dailybasic` 只覆盖 15 个指数代码，
        spec §S1.9 的 7 个里 **北证50（899050.BJ）与科创综指（000680.SH）返回 0 行**
        （不报错），须由 `index_weight` + `daily_basic` 自建（见
        `scripts/spike_s19_s111_sources.py` PROBE 2b 与 `docs/design/data-source.md` 附.7）。
        单次返回上限实测 **3000 行**（`ts_code=000300.SH` 无窗口 = 3000 行，
        最早只到 20140609）→ 长历史回补必须按日期分片。
        """
        kwargs: dict[str, str] = {}
        if ts_code:
            kwargs["ts_code"] = ts_code
        if trade_date:
            kwargs["trade_date"] = trade_date
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return await self._query("index_dailybasic", fields=fields, **kwargs)

    async def fetch_index_weight(
        self,
        index_code: str,
        trade_date: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> pd.DataFrame:
        """指数成分权重（TuShare ``index_weight``，2026-10-12 实机验证）.

        `index_code` 如 `899050.BJ`（北证50）/ `000680.SH`（科创综指）。返回
        `index_code, con_code, trade_date, weight`，**月度快照**（实测北证50 自
        20221130 起 47 个月度期、科创综指自 20250930 起 13 期）。

        ⚠️ **必须传日期窗口或 trade_date**：不传窗口一次只返回最新一期（实测
        北证50 不带窗口 = 上一期 50 行）。本任务是自建换手率的成分底座。
        """
        kwargs: dict[str, str] = {"index_code": index_code}
        if trade_date:
            kwargs["trade_date"] = trade_date
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return await self._query("index_weight", **kwargs)

    async def fetch_daily_basic_by_trade_date(
        self,
        trade_date: str,
        *,
        fields: str = "ts_code,trade_date,turnover_rate,turnover_rate_f,total_share,free_share",
        page_size: int = 6000,
        max_pages: int = 4,
    ) -> pd.DataFrame:
        """**按交易日**拉全市场 `daily_basic`（含 ``turnover_rate_f``），自动翻页.

        为什么不是逐股票拉：`daily_basic` 单次上限实测 **6000 行**（全市场约 5559 行/日），
        故"按日一次调用"即可覆盖全市场；逐 `ts_code` 拉 500 只票要多 500 倍调用。

        **为什么必须显式翻页**：`ts_code` + 日期窗口的调用会撞 6000 行上限，
        而北证50 有 349 只成分股 × 3 年 ≈ 26 万行——靠"逐日"而不是"逐股"才能不被截断。

        `max_pages` 是防御：市场若涨到 > 24000 只票，本方法会**抛错**而不是静默丢行
        （丢行会让自建换手率悄悄少算成分）。
        """
        frames: list[pd.DataFrame] = []
        for page in range(max_pages):
            df = await self._query(
                "daily_basic",
                fields=fields,
                trade_date=trade_date,
                offset=page * page_size,
                limit=page_size,
            )
            if df is None or not len(df):
                break
            frames.append(df)
            if len(df) < page_size:
                break
        else:  # pragma: no cover — 只有在页数用尽仍满页时才可能到
            raise RuntimeError(
                f"daily_basic({trade_date}) 超过 {max_pages} 页仍未取完 —— "
                "请调大 max_pages，不要静默只用前几页（会少算成分）"
            )
        if not frames:
            # **响亮失败**（Ruling 51 同类纪律）：调用方只在真实交易日调用本方法
            # （交易日来自 `daily_quotes` 实际持有的日期），一个真交易日返回 0 行
            # 只可能是上游异常/被限频/超时。返回空表会让调用方把它当成"这天没数据"
            # 而静默落 NULL —— 实测 2026-09-01…10-09 有 23 天因此被记成
            # `source_empty`（其实是 `Read timed out` 重试耗尽后的假缺口）。
            raise RuntimeError(
                f"daily_basic({trade_date}) 返回 0 行 —— 该日是真实交易日（来自 daily_quotes），"
                "0 行意味着上游异常/限频/超时。**不得**把它当成缺数静默落 NULL，请重跑回补"
                "（自建指标有增量缓存，重跑只补缺口）。"
            )
        return pd.concat(frames, ignore_index=True)

    # ------------------------------------------------------------------
    # 股指期货（spec §S1.10 升贴水的两条腿）
    # ------------------------------------------------------------------

    async def fetch_fut_basic(
        self,
        exchange: str = "",
        fut_type: str = "",
        fields: str = (
            "ts_code,symbol,exchange,name,fut_code,multiplier,trade_unit,per_unit,"
            "quote_unit,quote_unit_desc,d_mode_desc,list_date,delist_date,d_month,last_ddate"
        ),
    ) -> pd.DataFrame:
        """期货合约表（TuShare ``fut_basic``，2026-10-13 实机验证）.

        ⚠️ **必须传 ``exchange``**：不带参数的一次调用返回 **10000 行（命中该接口上限）**，
        各交易所被静默截断（实测 CFFEX 只落 642 行）；带 ``exchange="CFFEX"`` 时返回
        728 行 = 完整表（CFFEX 的 IF/IC/IH/IM/T/TF/TS/TL 全在）。

        本任务用 ``exchange="CFFEX"`` 取 IF/IC/IH/IM 的 ``multiplier``（合约乘数，
        点位→金额）与 ``delist_date``（最后交易日，年化贴水率的分母）。实测
        ``fut_mapping`` 引用过的 183/126/124/35 个真实合约**全部**在表内、``delist_date``
        全非空。
        """
        kwargs: dict[str, str] = {}
        if exchange:
            kwargs["exchange"] = exchange
        if fut_type:
            kwargs["fut_type"] = fut_type
        return await self._query("fut_basic", fields=fields, **kwargs)

    async def fetch_fut_daily(
        self,
        ts_code: str = "",
        trade_date: str = "",
        start_date: str = "",
        end_date: str = "",
        fields: str = (
            "ts_code,trade_date,pre_close,pre_settle,open,high,low,close,settle,"
            "change1,change2,vol,amount,oi,oi_chg"
        ),
    ) -> pd.DataFrame:
        """期货日线行情（TuShare ``fut_daily``，2026-10-13 实机验证）.

        ``ts_code`` 可传**连续合约**（`IF.CFX` 主力连续 / `IFL.CFX` 当月连续 /
        `IFL1/2/3.CFX` 下月·下季·下下季连续）或真实合约（`IF2612.CFX`，退市合约也整段可回）。

        ⚠️ **单次 2000 行上限，回补必须按日期分片**（实测复现附.5 #2 的结论，且更狠：
        带 ``start_date="20180101"`` 仍只回 2000 行、最早停在 20180711 —— **起点写早没用**，
        分片是唯一出路）。按 ≤3 年分片（每片 ≈730 行）后实测 IF 4002 日（2010-04-16 起）、
        IC/IH 2791 日（2015-04-16 起）、IM 1021 日（2022-07-22 起）。

        字段：``close`` 收盘价 / ``settle`` **结算价**（中金所收盘后计算发布的官方标价，
        保证金与对冲成本的结算依据）/ ``oi`` 持仓量 / ``vol`` 成交量。
        实测连续合约的 ``close``/``settle``/``oi`` **全列非空、无 0 值、无重复日期**。
        """
        kwargs: dict[str, str] = {}
        if ts_code:
            kwargs["ts_code"] = ts_code
        if trade_date:
            kwargs["trade_date"] = trade_date
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return await self._query("fut_daily", fields=fields, **kwargs)

    async def fetch_fut_mapping(
        self,
        ts_code: str,
        start_date: str = "",
        end_date: str = "",
    ) -> pd.DataFrame:
        """主力合约映射（TuShare ``fut_mapping``，2026-10-13 实机验证）.

        ``ts_code`` = 连续合约代码（如 `IF.CFX`）；返回 ``ts_code, trade_date,
        mapping_ts_code``，**一行 = 一个交易日** → 逐日映射，**不是"当前主力"快照**。

        实测不带窗口返回全部历史且**未撞 2000 行上限**（`IF.CFX` 4003 行
        2010-04-16→2026-10-13、映射到 183 个真实合约）。**无前视偏差的一手证据**：
        把窗口内出现过的每一个真实合约整段取回，与 `.CFX` 连续序列**逐日**比对 ``close``，
        四品种各 484 个交易日 / 23–24 个合约**不一致 0 日** ⇒ `.CFX` 是逐日主力连续
        且**未复权**（= 当日映射合约的原始收盘价）。

        本任务用它做两件事：① 在 ``extra.contract`` 里**标注**当日的真实合约代码（可复核）；
        ② 作为测试/探针的**独立复核**来源（不把它的结果当 `value` 的输入）。
        """
        kwargs: dict[str, str] = {"ts_code": ts_code}
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return await self._query("fut_mapping", **kwargs)

    # ------------------------------------------------------------------
    # Shenwan industry classification APIs
    # ------------------------------------------------------------------

    async def fetch_index_classify(
        self,
        level: str = "",
        src: str = "SW",
    ) -> pd.DataFrame:
        """Fetch Shenwan industry classification tree.

        ``level``: L1 / L2 / L3.  ``src``: SW (申万).
        Returns: index_code, industry_name, level, etc.
        """
        kwargs: dict[str, str] = {"src": src}
        if level:
            kwargs["level"] = level
        return await self._query("index_classify", **kwargs)

    async def fetch_index_member(
        self,
        index_code: str = "",
        ts_code: str = "",
    ) -> pd.DataFrame:
        """Fetch constituent stocks of an index (Shenwan or exchange index).

        Either ``index_code`` (e.g. '850531.SI') or ``ts_code`` can be used.
        """
        kwargs: dict[str, str] = {}
        if index_code:
            kwargs["index_code"] = index_code
        if ts_code:
            kwargs["ts_code"] = ts_code
        return await self._query("index_member", **kwargs)

    async def fetch_stk_limit(
        self,
        trade_date: str = "",
        ts_code: str = "",
        fields: str = "trade_date,ts_code,pre_close,up_limit,down_limit",
    ) -> pd.DataFrame:
        """Fetch daily price limit info (涨跌停价格).

        ``pre_close`` 在 TuShare 文档里是「默认显示 = N」字段：不显式传 fields，
        API 只返回 [trade_date, ts_code, up_limit, down_limit]（2026-09-14 实测）。
        漏传会让 stock_price_limits.pre_close 整列为 NULL，而昨日涨停溢价/赚钱效应
        KPI 全依赖它——静默产出 null 而不是报错。
        """
        kwargs: dict[str, str] = {}
        if trade_date:
            kwargs["trade_date"] = trade_date
        if ts_code:
            kwargs["ts_code"] = ts_code
        return await self._query("stk_limit", fields=fields, **kwargs)

    # ------------------------------------------------------------------
    # Market-data face APIs (全球指数 + 资金面/事件面)
    # ------------------------------------------------------------------

    async def fetch_index_global(
        self, ts_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """全球指数日线（裸代码如 N225/KS11/HSI/DJI/SPX/IXIC）。"""
        return await self._query(
            "index_global",
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
        )

    async def fetch_hk_hold(
        self,
        trade_date: str,
        *,
        exchange: str | None = None,
        ts_code: str | None = None,
    ) -> pd.DataFrame:
        """沪深港通持股明细（TuShare ``hk_hold``，季度末快照；**必须按交易所分批**）.

        已验证 2026-10-17（`scripts/backfill_northbound_holding.py` 的实跑）：
        返回列 = ``code, trade_date, ts_code, name, vol(股), ratio(%), exchange``。

        ⚠️ **单次 4200 行上限**（实测：不带 ``exchange`` 时 2026-09-30 返回
        ``SH 2089 + SZ 1097 + HK 1014 = 4200``，即 **SZ 被截断**；按 ``exchange`` 分两次取
        得到 ``SH 2089 + SZ 2320 = 4409`` 才完整）⇒ 调用方**必须**逐交易所取并断言未撞上限。
        """
        # `exchange` **必须**作为上游参数传下去：上游单次 4200 行上限是**过滤前**生效的，
        # 先取全量再本地过滤会把 SZ 截掉一半（实测 1097 vs 2320）。
        kwargs: dict[str, Any] = {"trade_date": trade_date}
        if exchange is not None:
            kwargs["exchange"] = exchange
        df = await self._query("hk_hold", **kwargs)
        if exchange is not None:
            df = df[df["exchange"] == exchange]
        if ts_code is not None:
            df = df[df["ts_code"] == ts_code]
        return df

    async def fetch_moneyflow_hsgt(self, start_date: str, end_date: str) -> pd.DataFrame:
        """沪深港通资金（north_money 为字符串、单位万元，映射层处理）。"""
        return await self._query("moneyflow_hsgt", start_date=start_date, end_date=end_date)

    async def fetch_top_list(self, trade_date: str) -> pd.DataFrame:
        """龙虎榜（每日个股上榜明细）。"""
        return await self._query("top_list", trade_date=trade_date)

    async def fetch_block_trade(self, trade_date: str) -> pd.DataFrame:
        """大宗交易（每日成交明细）。"""
        return await self._query("block_trade", trade_date=trade_date)

    async def fetch_share_float(self, start_date: str, end_date: str) -> pd.DataFrame:
        """限售股解禁（计划解禁明细）。"""
        return await self._query("share_float", start_date=start_date, end_date=end_date)

    async def fetch_repurchase(self, start_date: str, end_date: str) -> pd.DataFrame:
        """股票回购（回购明细）。"""
        return await self._query("repurchase", start_date=start_date, end_date=end_date)

    # Financial statements APIs
    # ------------------------------------------------------------------

    async def fetch_income(
        self,
        *,
        ts_code: str = "",
        period: str = "",
        start_date: str = "",
        end_date: str = "",
        report_type: str = "",
        fields: str = (
            "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,"
            "total_revenue,revenue,operate_cost,operate_profit,total_profit,"
            "n_income,n_income_attr_p,deduct_n_income,basic_eps,diluted_eps,"
            "sell_exp,admin_exp,fin_exp,rd_exp"
        ),
    ) -> pd.DataFrame:
        """Fetch income statement rows.

        See: docs/references/tushare/利润表.md
        """
        kwargs: dict[str, str] = {}
        if ts_code:
            kwargs["ts_code"] = ts_code
        if period:
            kwargs["period"] = period
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        if report_type:
            kwargs["report_type"] = report_type
        return await self._query("income", fields=fields, **kwargs)

    async def fetch_balance_sheet(
        self,
        *,
        ts_code: str = "",
        period: str = "",
        start_date: str = "",
        end_date: str = "",
        report_type: str = "",
        fields: str = (
            "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,"
            "total_assets,total_liab,total_hldr_eqy_exc_min_int,"
            "total_hldr_eqy_inc_min_int,money_cap,accounts_receiv,inventories,"
            "fix_assets,intan_assets,st_borrow,lt_borrow,st_note_payable,bond_payable"
        ),
    ) -> pd.DataFrame:
        """Fetch balance sheet rows.

        See: docs/references/tushare/资产负债表.md
        """
        kwargs: dict[str, str] = {}
        if ts_code:
            kwargs["ts_code"] = ts_code
        if period:
            kwargs["period"] = period
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        if report_type:
            kwargs["report_type"] = report_type
        return await self._query("balancesheet", fields=fields, **kwargs)

    async def fetch_cash_flow(
        self,
        *,
        ts_code: str = "",
        period: str = "",
        start_date: str = "",
        end_date: str = "",
        report_type: str = "",
        fields: str = (
            "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,"
            "n_cashflow_act,n_cashflow_inv_act,n_cashflow_fin_act,"
            "c_cash_equ_end_period,c_paid_goods_s,c_paid_to_for_empl,"
            "c_paid_for_taxes,c_recp_from_release_sale_sg"
        ),
    ) -> pd.DataFrame:
        """Fetch cash flow statement rows.

        See: docs/references/tushare/现金流量表.md
        """
        kwargs: dict[str, str] = {}
        if ts_code:
            kwargs["ts_code"] = ts_code
        if period:
            kwargs["period"] = period
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        if report_type:
            kwargs["report_type"] = report_type
        return await self._query("cashflow", fields=fields, **kwargs)

    async def fetch_financial_indicator(
        self,
        *,
        ts_code: str = "",
        period: str = "",
        start_date: str = "",
        end_date: str = "",
        fields: str = (
            "ts_code,ann_date,end_date,roe,roe_dt,grossprofit_margin,"
            "netprofit_margin,debt_to_assets,current_ratio,quick_ratio,"
            "bps,ocfps,eps,or_yoy,netprofit_yoy,dt_netprofit_yoy,"
            "ar_turn,ca_turn,fa_turn,assets_turn,equity_multi"
        ),
    ) -> pd.DataFrame:
        """Fetch financial indicator ratios (ROE, margins, growth, turnover).

        See: docs/references/tushare/财务指标.md
        """
        kwargs: dict[str, str] = {}
        if ts_code:
            kwargs["ts_code"] = ts_code
        if period:
            kwargs["period"] = period
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return await self._query("fina_indicator", fields=fields, **kwargs)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_default_client: TuShareClient | None = None


def get_tushare_client() -> TuShareClient:
    """Return the module-level singleton ``TuShareClient`` (lazy init)."""
    global _default_client
    if _default_client is None:
        from app.config import settings

        _default_client = TuShareClient(settings.tushare_token)
    return _default_client
