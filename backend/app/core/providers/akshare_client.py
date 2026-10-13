"""AKShare client — 行业投研指标真实数据源适配器（搜猪网/新浪期货）.

四个接口均于 2026-09-03 实机验证（akshare 1.18.94，绑定 plans/2026-09-03-akshare-integration.md
的数据源测绘表），返回 pandas DataFrame；上游改版失效时由调用方捕获异常并跳过该指标
（不影响其他源）。按计划默认数据源为 mock（``settings.industry_data_source = "mock"``）；
设为 ``"akshare"`` 时才真实拉取。

Usage::

    from app.core.providers.akshare_client import get_akshare_client
    df = await get_akshare_client().fetch_lh_future_daily()
"""

from __future__ import annotations

import logging

import pandas as pd

from app.core.providers.rate_limited import RateLimitedSyncProvider

logger = logging.getLogger(__name__)

_REQUEST_INTERVAL = 1.0  # be gentle with public data sites
_MAX_RETRIES = 2

#: QVIX 中国波指各品种 → AKShare 函数名（spec §S1.10 逐字列的 5 个 ETF 期权）。
#: **键名是稳定标识**（不是 akshare 函数名的截断）：上游若改函数名，只在这里改一处。
#: 全部 5 个已于 2026-10-13 实机验证存在（akshare 1.18.94）。
QVIX_SERIES: dict[str, str] = {
    "50etf": "index_option_50etf_qvix",  # 上证50ETF 期权
    "300etf": "index_option_300etf_qvix",  # 沪深300ETF 期权
    "500etf": "index_option_500etf_qvix",  # 中证500ETF 期权
    "cyb": "index_option_cyb_qvix",  # 创业板ETF 期权
    "kcb": "index_option_kcb_qvix",  # 科创50ETF 期权
}


class AkShareClient(RateLimitedSyncProvider):
    """Async-friendly wrapper around the optional ``akshare`` package."""

    request_interval = _REQUEST_INTERVAL
    max_retries = _MAX_RETRIES

    def __init__(self) -> None:
        super().__init__()
        try:
            import akshare as ak  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - depends on env
            raise RuntimeError(
                "akshare is not installed. Run `uv add akshare` or keep INDUSTRY_DATA_SOURCE=mock."
            ) from exc
        self._ak = ak

    # ------------------------------------------------------------------
    # 搜猪网 soozhu 现货价格（元/kg，日度，列：日期/价格）
    # ------------------------------------------------------------------

    async def fetch_hog_price_trend(self) -> pd.DataFrame:
        """当年全国生猪出栏均价走势（~200 行，元/kg，日度）.

        已验证 2026-09-03 · akshare 1.18.94：窗口为"当年 1 月至今"（逐年累积），
        跨年长历史需靠滚动 ingest 累积落库。
        """
        return await self.invoke_async(
            "spot_hog_year_trend_soozhu", lambda: self._ak.spot_hog_year_trend_soozhu()
        )

    async def fetch_corn_price(self) -> pd.DataFrame:
        """全国玉米价格走势（每次返回最近 15 行，元/kg，日度）.

        已验证 2026-09-03 · akshare 1.18.94：单次窗口仅 15 天，长历史靠逐日滚动累积。
        """
        return await self.invoke_async(
            "spot_corn_price_soozhu", lambda: self._ak.spot_corn_price_soozhu()
        )

    async def fetch_soybean_meal_price(self) -> pd.DataFrame:
        """全国豆粕价格走势（每次返回最近 15 行，元/kg，日度）.

        已验证 2026-09-03 · akshare 1.18.94：窗口同玉米（15 天），逐日滚动累积。
        """
        return await self.invoke_async(
            "spot_soybean_price_soozhu", lambda: self._ak.spot_soybean_price_soozhu()
        )

    # ------------------------------------------------------------------
    # Hog futures (DCE LH 主力连续，新浪)
    # ------------------------------------------------------------------

    async def fetch_lh_future_daily(self) -> pd.DataFrame:
        """生猪期货主力连续（LH0）日行情（全历史 ~1370 行，2021-01-08 起）.

        已验证 2026-09-03 · akshare 1.18.94：列含 ``date``/``close`` 等（date 为 ISO
        字符串），单位元/吨；全历史返回，由调用方按 months 窗口截尾。
        """
        return await self.invoke_async(
            "futures_zh_daily_sina", lambda: self._ak.futures_zh_daily_sina(symbol="LH0")
        )

    # ------------------------------------------------------------------
    # 中债国债收益率曲线（spec §S1.11 的 10Y 无风险利率底座）
    # ------------------------------------------------------------------

    async def fetch_china_bond_yield(self, start_date: str, end_date: str) -> pd.DataFrame:
        """**中债国债收益率曲线**（中国债券信息网，官方口径）日度全曲线.

        已验证 2026-10-12 · akshare 1.18.94（`scripts/spike_s19_s111_sources.py`
        PROBE 4）：AKShare `bond_china_yield` 直连
        `yield.chinabond.com.cn/cbweb-pbc-web/pbc/historyQuery`（`qxId=ycqx`，HTML 表），
        返回列 = `曲线名称, 日期, 3月, 6月, 1年, 3年, 5年, 7年, 10年, 30年`。
        本任务只取 `曲线名称 == '中债国债收益率曲线'` 的 `10年` 列。

        `start_date` / `end_date` 形如 `YYYYMMDD`。

        ⚠️ **窗口必须 < 1 年**：上游对更长窗口**静默返回 0 行**（不报错）——实测
        `20180101→20261016` / `20100101→…` / `20050101→…` 全部 0 行，而
        `20250101→20251231` 返回 248 个国债曲线交易日。故回补必须**逐年翻窗**（调用方负责）。

        ⚠️ 备源（本方法之外）：东财 `bond_zh_us_rate`（`中国国债收益率10年` 列，
        2002-01-04 起全历史、一次可取）。两源同日实测**逐值相同**（附.7 的 XCHK）。
        """
        return await self.invoke_async(
            "bond_china_yield",
            lambda: self._ak.bond_china_yield(start_date=start_date, end_date=end_date),
        )

    # ------------------------------------------------------------------
    # 期权隐含波动率：QVIX 中国波指 5 条日频序列（spec §S1.10）
    # ------------------------------------------------------------------

    async def fetch_qvix(self, series: str) -> pd.DataFrame:
        """**QVIX 中国波指**日频序列（第三方 optbbs.com 编制，AKShare 内置封装）.

        已验证 2026-10-13 · akshare 1.18.94
        （`scripts/spike_s110_volatility_futures_sources.py` PROBE 1）：
        返回列 = ``date, open, high, low, close``，**落库取 ``close``**（日频；单位 =
        波动率百分数，如 13.84 表示 13.84% 的年化隐含波动率）。

        ``series`` 取值见 :data:`QVIX_SERIES`（50etf / 300etf / 500etf / cyb / kcb）。

        ⚠️ **三条实测限制**（详见 `docs/design/data-source.md` 附.8）：

        1. **上游是单个静态 CSV**（`http://1.optbbs.com/d/csv/d/k.csv`，gbk 编码，
           87 列多品种拼接，AKShare 按列偏移切分）：**没有分页 API**，早期数据不可翻页
           补取；原始 CSV 行数 == AKShare 返回行数（**无库内截断**，实测 2823 行）。
        2. **期权上市前上游补 NaN 行**：5 条序列行数完全相同（2823 行）但非空深度不同
           —— 实测「首个非空日 == 该 ETF 期权上市日」（50ETF 2015-02-09 / 300ETF
           2019-12-23 / 500ETF·创业板ETF 2022-09-19 / 科创50ETF 2023-06-05）。
        3. **有发布滞后**：实测 2026-10-13 时 CSV 仍停在 2026-09-30
           （`Last-Modified: Wed, 30 Sep 2026 07:13:54 GMT`），比期货腿晚 3 个交易日
           → 缺的交易日由调用方落 NULL + `missing_reason`，**不得**落 0、**不得**取最近值。

        本方法**不做**任何插值/填充/反解 —— spec §S1.10 要的是"直接取隐含波动率序列"。
        """
        func_name = QVIX_SERIES.get(series)
        if func_name is None:
            raise ValueError(f"未知的 QVIX series={series!r}；可用={sorted(QVIX_SERIES)}")
        func = getattr(self._ak, func_name, None)
        if func is None:
            # 上游改版/降级时必须**响亮失败**（Ruling 51 同类纪律）：静默返回空表会把
            # "接口没了"伪装成"这天没数据"，整条指标序列会被静默写成 NULL。
            raise RuntimeError(
                f"akshare 无 {func_name}() —— QVIX 中国波指序列 {series} 不可得，"
                "须按 docs/design/data-source.md 附.8 的结论标 blocked-on-verification"
            )
        return await self.invoke_async(func_name, func)

    # ------------------------------------------------------------------
    # §S1.12 投资者行为：新成立基金募集份额（月频）+ 公募基金仓位（周度观测）
    # ------------------------------------------------------------------

    async def fetch_fund_new_found(self) -> pd.DataFrame:
        """**东财「新成立基金」**（`fund.eastmoney.com/data/xinfound.html`）全表.

        已验证 2026-10-13 · akshare 1.18.94
        （`scripts/spike_s112_investor_behavior_sources.py` PROBE 2）：
        返回列 = ``基金代码, 基金简称, 发行公司, 基金类型, 集中认购期, 募集份额,
        成立日期, 成立来涨幅, 基金经理, 申购状态, 优惠费率``。

        ⚠️ **三条实测陷阱**（详见 `docs/design/data-source.md` 附.10）：

        1. **只有「募集份额」一列有份额语义**（无独立的「成立份额」/「发行份额」列）；
           本表全是**新成立**基金 ⇒ 发行份额 == 成立份额 == 募集份额。单位**亿元**。
        2. **A/C 份额双计**：同一基金的 A、C 代码各有**独立行**（代码不同、`募集份额`
           常相同）⇒ 按行求和会把一次募集计两次。服务侧按行业惯例保留双计并登记口径。
        3. **不是全历史**：实测 7012 行、成立日期 2023-01-01…2026-09-30 完整，更早只剩
           2013/2018 两行孤例，**2019–2022 该产地没有数据**。逐页取（`page=1..3,50000`）
           合计同为 7012 行 ⇒ **无分页丢行**（与 `hk_hold`/`fut_daily` 的"截最近"不同族）。
           ``募集份额`` 实测 340/7012 为空 ⇒ 服务侧落 NULL（**不填 0**）。
        """
        return await self.invoke_async("fund_new_found_em", lambda: self._ak.fund_new_found_em())

    async def fetch_fund_stock_position(self) -> pd.DataFrame:
        """**乐咕乐股「基金仓位 · 股票型基金 · 总仓位」**（第三方编制，周度观测）.

        已验证 2026-10-13 · akshare 1.18.94
        （`scripts/spike_s112_investor_behavior_sources.py` PROBE 3）：
        返回列 = ``date, close, position``（455 行 / 2017-12-04…2026-09-30）。

        ⚠️ **spec §S1.12 最初写的是「季报、滞后 ~15 工作日」，但实测与此不符**（附.10；
        该表述已由控制器按 Ruling 64 修正）：

        1. **实测是周度观测**（相邻间隔 6–8 天占 410/454 = 90.3%），不是季度披露值
           ⇒ registry 的 `freq` 按**实测**登记 `weekly`（机读字段不得与实测不符）。
        2. **上游 JSON 只有 `date/close/position` 三列** —— **没有**报告期
           （`report_period`）/ 发布日期（`publish_date`）/ 编制方法说明。故
           「股票投资占基金总资产比」这一口径**无法从产地复算**，`published_at` 只能按
           「滞后 ~15 个工作日」推算（服务侧落该保守值并登记为未验证余项；**该推算未被
           Ruling 64 改变**）。
        3. ``close`` 是乐咕乐股随行的**指数收盘**（实测 2026-09-30 = 4357.62，与同期
           上证指数 3800–4900 同量级）；本指标**不使用**它（口径不可复算），服务侧只取
           ``position``。

        ⚠️ 本方法**不做**任何插值/填充/反解，也**不**把它标成官方口径
        （`source_tier='calc'`）；spec 明令它**仅作背景展示、不进信号链路**。
        """
        return await self.invoke_async(
            "fund_stock_position_lg", lambda: self._ak.fund_stock_position_lg()
        )


_default_client: AkShareClient | None = None


def get_akshare_client() -> AkShareClient:
    """Return the module-level singleton ``AkShareClient`` (lazy init)."""
    global _default_client
    if _default_client is None:
        _default_client = AkShareClient()
    return _default_client
