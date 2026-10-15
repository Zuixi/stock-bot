"""新浪财经・美股指数行情客户端（`hq.sinajs.cn`，S1.3 全球标的补齐用）。

**为什么不走 TuShare**：`index_global` 是唯一已接入的全球指数源，但它**没有**
纳斯达克100（NDX）/ 费城半导体（SOX）/ 纳斯达克中国金龙（HXC）——2026-10-16 实机
枚举该接口全部 22 个 `ts_code` 得到（见 `docs/design/data-source.md` 附.5 实机记录），
裸 `NDX` / `NDX.GI` 均返回 0 行。按 [ADR 0001] 的单一主源纪律，**能用 TuShare 的
一律用 TuShare**（本仓库只在 `market_data_service.GLOBAL_INDICES` 里给这三个标的
标 `source="sina_index"`），此处是**已实机验证**的补位源。

**实机验证（2026-10-16）**：`GET https://hq.sinajs.cn/rn=<n>&list=gb_$ndx,gb_$sox,gb_$hxc`
（需 `Referer: https://finance.sina.com.cn`）返回 GBK 文本，逐行形如::

    var hq_str_gb_$ndx="纳斯达克100,30883.1478,0.51,2026-10-10 05:30:00,157.3384,30900.0649,...";

字段位（下标 0 起）：0=名称 1=最新价 2=涨跌幅(%) 3=行情时间 4=涨跌额 5=开盘 6=最高
7=最低 8=52周最高 9=52周最低 10=成交量 11=成交额 …。**同一批次可混多个标的**，
一次调用取回全部（本模块的 `fetch_quotes` 就是这么用的）。

⚠️ **`gb_hxc`（无 `$`）是另一只标的**（实测价 26.275，非指数点位），必须带 `$`。
⚠️ 上游对**未知代码**返回**空串**（`var hq_str_gb_xxx="";`）而不是报错——
消费方必须把"空串"当**缺失**处理，不得当成 0。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_SINA_BASE = "https://hq.sinajs.cn/"
# 新浪要求带 Referer，否则返回 403（2026-10-16 实测：不带 Referer 被拒）
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn/",
}
_TIMEOUT_SECONDS = 8.0
# 同一进程内串行化 + 最小间隔，避免刷上游（与 eastmoney_client 的 _MIN_INTERVAL 同思路）
_MIN_INTERVAL = 0.3

_last_request_ts = 0.0
_lock = asyncio.Lock()


def _f(value: str) -> float | None:
    """新浪无效值可能是空串 / ``--``；一律 → ``None``（不得当 0）。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_sina_index_quote(text: str) -> dict[str, dict[str, Any]]:
    """新浪返回文本 → ``{代码: 行情}``（纯函数，便于单测）。

    代码取 `hq_str_` 之后、`=` 之前的部分（如 ``gb_$ndx``）。**空 payload**（未知代码）
    → 该代码不入结果字典（"没有" 与 "为 0" 是两件事）。
    """
    quotes: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("var hq_str_"):
            continue
        head, _, payload = line.partition("=")
        code = head[len("var hq_str_") :].strip()
        payload = payload.strip().rstrip(";").strip('"')
        if not payload:
            continue  # 未知代码 → 空串，按缺失处理
        parts = payload.split(",")
        if len(parts) < 5:
            continue
        quotes[code] = {
            "code": code,
            "name": parts[0],
            "price": _f(parts[1]),
            "pct_change": _f(parts[2]),
            "quote_time": parts[3] or None,
            "change": _f(parts[4]),
        }
    return quotes


async def fetch_index_quotes(codes: list[str]) -> dict[str, dict[str, Any]]:
    """批量取新浪美股指数行情；**任何失败都返回空字典**（调用方回落 EOD）。

    ``codes`` 为新浪原始代码（如 ``gb_$ndx``）。空列表 → 不发起请求。
    """
    if not codes:
        return {}

    global _last_request_ts
    async with _lock:
        elapsed = time.monotonic() - _last_request_ts
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        _last_request_ts = time.monotonic()

    url = f"{_SINA_BASE}rn={int(time.time() * 1000)}&list={','.join(codes)}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            resp = await client.get(url, headers=_HEADERS)
            resp.raise_for_status()
            # 响应是 GBK 编码的中文名称；httpx 有时猜不到 → 显式按 GBK 解，失败退 utf-8
            try:
                text = resp.content.decode("gbk")
            except UnicodeDecodeError:
                text = resp.text
    except Exception:
        logger.warning("sina index quote fetch failed for %s", codes, exc_info=True)
        return {}
    return parse_sina_index_quote(text)
