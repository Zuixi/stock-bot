"""CacheClient 的序列化边界。

仓库里 ~20 个缓存替身（RecordingCache/_FakeCache/_SeededCache/InMemoryCache）直接存取
Python 对象，**绕过** ``CacheClient.get/set``，因此从不过 orjson。
``test_market_snapshot.py`` 里的 ``json.loads(json.dumps(payload))`` 模拟的是 stdlib，
不是真 CacheClient。这条路径此前近似零覆盖，本文件补上。

只用手写的 ~10 行 redis 替身，不引入 fakeredis（本地没装，也不值得为这加依赖）。
"""

import json
from typing import Any

import pytest

from app.core.redis import CacheClient


class _RawRedis:
    """最小 redis 替身：只存 UTF-8 字节，复刻 ``decode_responses=True`` 的读写语义。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: bytes | str, ex: int | None = None) -> None:
        self.store[key] = value.decode("utf-8") if isinstance(value, bytes) else value


def _client() -> tuple[CacheClient, _RawRedis]:
    raw: Any = _RawRedis()
    return CacheClient(raw), raw


@pytest.mark.asyncio
async def test_roundtrip_of_real_cache_shapes() -> None:
    """真实 payload 形状（快照行 + 中文）过真序列化后必须等值。"""
    cache, _ = _client()
    payload = [
        {
            "csrc_desc": "计算机、通信和其他电子设备制造业",
            "province": "广东",
            "pct_chg": 1.5,
            "amount": 100.0,
            "basic_date": "2026-09-11",
        }
    ]
    await cache.set("k", payload)
    assert await cache.get("k") == payload


@pytest.mark.asyncio
async def test_non_str_dict_keys_stay_cached() -> None:
    """stdlib json 把 int/bool/None 键字符串化；OPT_NON_STR_KEYS 必须保持同一奇偶性。

    回归护栏：去掉该 option 后 orjson 抛 TypeError，payload 会**静默地**不再落缓存。
    """
    cache, _ = _client()
    await cache.set("k", {1: "a", 2: "b", None: "c"})
    assert await cache.get("k") == {"1": "a", "2": "b", "null": "c"}

    # bool 键与 int 键在 Python 里哈希相同：dict[True] 会覆盖 dict[1]，只剩一个键。
    cache2, _ = _client()
    collided: dict[Any, Any] = {1: "a"}
    collided[True] = "b"
    await cache2.set("k", collided)
    assert await cache2.get("k") == {"1": "b"}


@pytest.mark.asyncio
async def test_unserializable_payload_never_raises() -> None:
    """编码失败必须是 skip，不能让已取到数据的请求 500。

    orjson 只支持 64-bit int（stdlib 任意精度），这是实测确认的分歧之一。
    """
    cache, raw = _client()
    await cache.set("k", {"n": 2**64})  # orjson: Integer exceeds 64-bit range
    assert await raw.get("k") is None  # 未写入，且未抛异常


@pytest.mark.asyncio
async def test_circular_reference_never_raises() -> None:
    """循环引用同样抛 TypeError（orjson.JSONEncodeError is TypeError），必须被吞掉。"""
    cache, raw = _client()
    circular: dict[str, Any] = {}
    circular["self"] = circular
    await cache.set("k", circular)
    assert await raw.get("k") is None


@pytest.mark.asyncio
async def test_legacy_stdlib_nan_value_degrades_to_miss() -> None:
    """旧 Redis 里 stdlib 写出的 NaN 文本（非法 JSON）：orjson 拒绝 -> 记 miss 自愈。"""
    cache, raw = _client()
    raw.store["k"] = json.dumps({"x": float("nan")})
    assert await cache.get("k") is None


@pytest.mark.asyncio
async def test_nan_value_is_pinned_not_silently_drifting() -> None:
    """钉死本改动唯一的静默语义变化：float nan/inf -> None。

    stdlib 会原样保留 nan（写出非法 JSON），orjson 拒绝并且转成 null。
    这不是期望行为，是"不许输出非法 JSON"的副产物——写在这里是为了它以后
    若再变化能被看见，而不是悄悄漂移。
    """
    cache, _ = _client()
    await cache.set("k", {"x": float("nan"), "y": float("inf")})
    assert await cache.get("k") == {"x": None, "y": None}
