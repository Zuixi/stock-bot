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
                "akshare is not installed. Run `uv add akshare` or keep "
                "INDUSTRY_DATA_SOURCE=mock."
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


_default_client: AkShareClient | None = None


def get_akshare_client() -> AkShareClient:
    """Return the module-level singleton ``AkShareClient`` (lazy init)."""
    global _default_client
    if _default_client is None:
        _default_client = AkShareClient()
    return _default_client
