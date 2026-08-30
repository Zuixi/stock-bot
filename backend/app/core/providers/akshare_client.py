"""AKShare client — best-effort adapter for industry spot-price data.

接口名来自公开文档调研（2026-08，见 docs/design/data-source.md），尚未实机验证，
全部标注 ``TODO(api-verify)``。按计划默认数据源为 mock
（``settings.industry_data_source = "mock"``）；设为 ``"akshare"`` 时才尝试真实拉取，
接口失效时由调用方捕获异常并跳过该指标（不影响其他源）。

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
    # Spot prices (生意社 100ppi.com，2011 年至今每个交易日)
    # ------------------------------------------------------------------

    async def fetch_spot_price_history(self, symbol_cn: str) -> pd.DataFrame:
        """生意社商品现货价格历史（生猪/玉米/豆粕等，参数为中文品种名）.

        TODO(api-verify): 文档显示为「生意社-商品与期货-现期图」接口，
        函数名与参数（symbol? plot?）需实机确认。
        """
        return await self.invoke_async(
            "spot_price_qh", lambda: self._ak.spot_price_qh(symbol=symbol_cn, plot=False)
        )

    # ------------------------------------------------------------------
    # Hog futures (DCE LH, 新浪主力连续)
    # ------------------------------------------------------------------

    async def fetch_lh_future_daily(self) -> pd.DataFrame:
        """生猪期货主力连续（LH0）日行情.

        TODO(api-verify): ``futures_zh_daily_sina(symbol="LH0")`` 需实机确认。
        """
        return await self.invoke_async(
            "futures_zh_daily_sina", lambda: self._ak.futures_zh_daily_sina(symbol="LH0")
        )

    # ------------------------------------------------------------------
    # Soozhu 搜猪网 生猪大数据
    # ------------------------------------------------------------------

    async def fetch_corn_price_soozhu(self) -> pd.DataFrame:
        """搜猪网-全国玉米价格走势.

        TODO(api-verify): ``spot_corn_price_soozhu`` 需实机确认。
        """
        return await self.invoke_async(
            "spot_corn_price_soozhu", lambda: self._ak.spot_corn_price_soozhu()
        )


_default_client: AkShareClient | None = None


def get_akshare_client() -> AkShareClient:
    """Return the module-level singleton ``AkShareClient`` (lazy init)."""
    global _default_client
    if _default_client is None:
        _default_client = AkShareClient()
    return _default_client
