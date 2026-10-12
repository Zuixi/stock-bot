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


_default_client: AkShareClient | None = None


def get_akshare_client() -> AkShareClient:
    """Return the module-level singleton ``AkShareClient`` (lazy init)."""
    global _default_client
    if _default_client is None:
        _default_client = AkShareClient()
    return _default_client
