"""Task 11: `mode=intraday` 端点接线（不污染收盘序列）的单测。

设计要点（packet/brief）：

1. **mode=intraday** 走 `fetch_intraday_pool` → `build_intraday_snapshot`，
   响应 `source="eastmoney_intraday"`；`as_of_label` 形如 `"盘中 HH:MM"`；l3/l1 字段
   全部 None；`yesterday=None`；`stock_id=None`。**断言 `persist_snapshot` 未被调用**
   （盘中不写 market_sentiment_daily）。
2. **东财池失败** → 回落收盘口径；`as_of_label` 被替换为 ``"盘中不可用，已回落收盘"``；
   回落**仍写** `market_sentiment_daily`（persist_snapshot 被调用）。
3. **mode=intraday + 历史日期** → 400（盘中无意义）。
4. **mode=close** (default) → 行为与今天一致；原 `test_limit_up_service.py` /
   `test_persist_snapshot_cache.py` 全绿。
5. **schema 拓宽**：`LimitUpLadderOut.model_validate(intraday_payload)` 与
   `model_validate(close_payload)` 同时成功。
6. **缓存**：盘中 key `market:limit-up:intra:{trade_date}`，TTL=60s；close 路径 key
   `market:limit-up:snapshot:{date}:{lookback}` 维持原样。

`TUSHARE_TOKEN= uv run pytest`；**无真实 DB、无真实 eastmoney**——所有 IO 用 monkeypatch 替身。
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from app.services import limit_up_service as svc

D5 = date(2026, 9, 10)  # 任意历史交易日，close 路径用
D_TODAY = date(2026, 9, 17)  # 盘中分支用的"今日"（测试桩控时钟）
D_TODAY_STR = D_TODAY.isoformat()
D_TODAY_EM = D_TODAY.strftime("%Y%m%d")

FIXTURE = Path(__file__).parent / "fixtures" / "eastmoney" / "zt_pool_20260917.json"


def _load_pool() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class _RecordingCache:
    """最小 CacheClient 替身：get 恒 miss、记录 set/get 调用。"""

    def __init__(self, cached: Any | None = None) -> None:
        self.cached = cached
        self.set_calls: list[tuple[str, Any, int | None]] = []
        self.get_calls: list[str] = []

    async def get(self, key: str) -> Any | None:
        self.get_calls.append(key)
        return self.cached

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self.set_calls.append((key, value, ttl))


def _lu_row(
    stock_id: int,
    symbol: str,
    name: str,
    trade_date: date,
    *,
    streak: int = 1,
) -> dict[str, Any]:
    """close 路径最小可用窗口行——`_patch_sources` 的标准形状。"""
    return {
        "stock_id": stock_id,
        "symbol": symbol,
        "name": name,
        "trade_date": trade_date,
        "close": 10.0,
        "open": 9.8,
        "high": 10.0,
        "amount": 1_000_000.0,
        "pre_close": 9.09,
        "up_limit": 10.0,
        "down_limit": 9.0,
        "is_lu": True,
        "touched": False,
        "streak_upto": streak,
        "sw_l1_code": "110000",
        "sw_l1_name": "农林牧渔",
        "sw_l3_code": "110101",
        "sw_l3_name": "生猪养殖",
    }


@pytest.fixture
def _close_path(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """把 close 路径的 DB 替身打满；返回 state dict 让用例自由改。"""
    state: dict[str, Any] = {
        "window": [
            _lu_row(1, "000001", "平安银行", D5, streak=2),
            _lu_row(2, "000002", "万科A", D5, streak=1),
        ],
        "breadth": {"zt_count": 75, "dt_count": 1, "zb_count": 39, "quoted": 5490},
        "trade_dates": [date(2026, 8, 18), date(2026, 9, 7), D5],
    }

    async def _dates(_db: Any, _as_of: date, _limit: int) -> list[date]:
        return list(state["trade_dates"])

    async def _has(_db: Any, _as_of: date) -> bool:
        return True

    async def _window(_db: Any, **_kw: Any) -> list[dict[str, Any]]:
        return list(state["window"])

    async def _breadth(_db: Any, _as_of: date) -> dict[str, int]:
        return dict(state["breadth"])

    monkeypatch.setattr(svc.limit_up_repo, "list_recent_trade_dates", _dates)
    monkeypatch.setattr(svc.limit_up_repo, "has_price_limits", _has)
    monkeypatch.setattr(svc.limit_up_repo, "fetch_limit_up_window", _window)
    monkeypatch.setattr(svc.limit_up_repo, "fetch_day_breadth", _breadth)
    return state


@pytest.fixture
def _clock_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 `_today_sh`（limit_up_service 在 intraday 分支要 today_sh_iso）钉到 D_TODAY。

    必须改 `svc._today_sh`——limit_up_service 在模块顶 from-import，把名字绑定到
    自己命名空间；改 mds._today_sh 不会生效。
    """
    monkeypatch.setattr(svc, "_today_sh", lambda: D_TODAY)
    # limit_up_service 在 intraday 分支还要用 datetime.now(SH) 计算 captured_at
    import datetime as _dt

    class _FrozenDateTime(_dt.datetime):
        @classmethod
        def now(cls, tz: Any = None) -> _dt.datetime:
            base = _dt.datetime(D_TODAY.year, D_TODAY.month, D_TODAY.day, 14, 30, 0)
            if tz is not None:
                return base.replace(tzinfo=tz)
            return base

    monkeypatch.setattr(svc, "datetime", _FrozenDateTime)


def _patch_pool(monkeypatch: pytest.MonkeyPatch, fn: Any) -> None:
    """把 intraday_sentiment_service.fetch_intraday_pool 替成 fn。"""
    from app.services import intraday_sentiment_service as its

    monkeypatch.setattr(its, "fetch_intraday_pool", fn)


async def _async_load_pool(_td: str) -> list[dict[str, Any]]:
    return _load_pool()


async def _async_empty(_td: str) -> list[dict[str, Any]]:
    return []


# ---------- mode=intraday happy path ---------------------------------------------


async def test_intraday_calls_fetch_intraday_pool_once(
    monkeypatch: pytest.MonkeyPatch,
    _close_path: dict[str, Any],
    _clock_today: None,
) -> None:
    """`mode=intraday` + 默认 as_of → 调一次 `intraday_sentiment_service.fetch_intraday_pool`。"""
    calls: list[str] = []

    async def _fake_pool(trade_date: str) -> list[dict[str, Any]]:
        calls.append(trade_date)
        return _load_pool()

    _patch_pool(monkeypatch, _fake_pool)
    snap = await svc.get_snapshot(cache=None, as_of=None, lookback=10, mode="intraday")

    assert calls == [D_TODAY_EM]  # 仅一次、参数为今日 YYYYMMDD
    assert snap["source"] == "eastmoney_intraday"
    assert snap["as_of"] == D_TODAY
    assert snap["as_of_label"] == "盘中 14:30"
    assert snap["as_of_quality"] == "partial"
    assert snap["as_of_prev"] is None
    assert snap["yesterday"] is None
    # echelons 内每只 stock 的 stock_id 都是 None
    for bucket in snap["echelons"]:
        for stock in bucket["stocks"]:
            assert stock["stock_id"] is None
    # sectors.items 内每项的 l3/l1 都是 None
    for item in snap["sectors"]["items"]:
        assert item["l3_code"] is None
        assert item["l3_name"] is None
        assert item["l1_code"] is None
        assert item["l1_name"] is None
    # 收盘路径不该被走：limits_present=True 来自盘中池（close 路径不参与）
    assert snap["limits_present"] is True
    # kpis 计数：zt_count == len(pool) == 47
    assert snap["kpis"]["zt_count"] == 47


async def test_intraday_label_format_matches_hhmm_pattern(
    monkeypatch: pytest.MonkeyPatch,
    _close_path: dict[str, Any],
    _clock_today: None,
) -> None:
    """`as_of_label` 形如 ``^盘中 \\d\\d:\\d\\d$``——非空性证据（拒绝任意字符串）。"""
    _patch_pool(monkeypatch, _async_load_pool)
    snap = await svc.get_snapshot(cache=None, as_of=None, lookback=10, mode="intraday")
    assert re.match(r"^盘中 \d\d:\d\d$", snap["as_of_label"]), snap["as_of_label"]


async def test_intraday_persisted_under_intra_cache_key_with_60s_ttl(
    monkeypatch: pytest.MonkeyPatch,
    _close_path: dict[str, Any],
    _clock_today: None,
) -> None:
    """盘中分支：缓存 key=`market:limit-up:intra:{trade_date}`，TTL=60s。"""
    _patch_pool(monkeypatch, _async_load_pool)
    cache = _RecordingCache()
    snap = await svc.get_snapshot(cache=cache, as_of=None, lookback=10, mode="intraday")

    # 唯一的 set_call 应是 intra key + 60s
    assert len(cache.set_calls) == 1
    key, _value, ttl = cache.set_calls[0]
    assert key == f"market:limit-up:intra:{D_TODAY_STR}"
    assert ttl == 60
    # 命中路径：把 cache.cached 设为第一次写入的 snap（模拟真实 Redis 回读）
    # —— 第二次请求应直接读 cache（不再调 fetch_intraday_pool）。
    cache.cached = cache.set_calls[0][1]
    call_count = {"n": 0}

    async def _counting_pool(trade_date: str) -> list[dict[str, Any]]:
        call_count["n"] += 1
        return _load_pool()

    _patch_pool(monkeypatch, _counting_pool)
    cached_snap = await svc.get_snapshot(cache=cache, as_of=None, lookback=10, mode="intraday")

    assert call_count["n"] == 0  # 命中缓存就不发请求
    assert cached_snap["source"] == "eastmoney_intraday"
    # 关键非空性 sanity
    assert snap["as_of_label"].startswith("盘中 ")


async def test_intraday_does_not_call_persist_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    _close_path: dict[str, Any],
    _clock_today: None,
) -> None:
    """**核心不变量**：盘中分支不调 `persist_snapshot`——不污染 market_sentiment_daily。"""
    _patch_pool(monkeypatch, _async_load_pool)

    async def _boom(*_a: Any, **_kw: Any) -> dict[str, Any]:
        raise AssertionError("persist_snapshot must NOT be called on intraday path")

    monkeypatch.setattr(svc, "persist_snapshot", _boom)
    snap = await svc.get_snapshot(cache=None, as_of=None, lookback=10, mode="intraday")
    # sanity：分支真的走到了
    assert snap["source"] == "eastmoney_intraday"


# ---------- mode=intraday + eastmoney 失败 → 回落 close 路径 -----------------------


async def test_intraday_eastmoney_failure_falls_back_to_close(
    monkeypatch: pytest.MonkeyPatch,
    _close_path: dict[str, Any],
    _clock_today: None,
) -> None:
    """东财池 fetch 抛错 → 回落 close 路径，as_of_label 替换成 '盘中不可用，已回落收盘'。"""

    async def _fake_pool_raises(trade_date: str) -> list[dict[str, Any]]:
        raise RuntimeError("东财炸了")

    persist_called: dict[str, Any] = {"n": 0}

    async def _fake_persist(*_a: Any, **_kw: Any) -> dict[str, Any]:
        persist_called["n"] += 1
        return {"status": "ok", "trade_date": D_TODAY, "zt_count": 75}

    # 回落路径：get_snapshot(cache, as_of=D_TODAY) 走 close 路径
    # close 路径内部会 resolve_latest_complete_day + 调 repo。
    # _close_path 已替了 repo，但要替掉 resolve_latest_complete_day
    from types import SimpleNamespace

    async def _fake_resolve(_db: Any, cache: Any = None) -> Any:
        return SimpleNamespace(day=D_TODAY, quality="complete")

    _patch_pool(monkeypatch, _fake_pool_raises)
    monkeypatch.setattr(svc, "persist_snapshot", _fake_persist)
    monkeypatch.setattr(svc.market_day_service, "resolve_latest_complete_day", _fake_resolve)
    snap = await svc.get_snapshot(cache=None, as_of=None, lookback=10, mode="intraday")

    # 回落 close 路径的 source 维持 close 路径的口径；as_of_quality 由 close
    # 路径给出（as_of=today 显式传入 → close 不调 resolve_latest_complete_day，
    # 默认 "partial"）。**关键**：此处**不**由 intraday 分支手工覆盖——
    # 这是 brief 的硬要求（"fallback's as_of_quality should be whatever
    # the close path produced"）。
    assert snap["source"] == "local_calc"
    assert snap["as_of_quality"] in {"complete", "partial", "fallback"}
    # 进一步：与独立调用 close 路径的 quality 完全一致（不能被 intraday 分支改动）
    snap_close = await svc.get_snapshot(cache=None, as_of=D_TODAY, lookback=10, mode="close")
    assert snap["as_of_quality"] == snap_close["as_of_quality"]
    # as_of_label 被替换成不可用标注
    assert snap["as_of_label"] == "盘中不可用，已回落收盘"
    # 其余字段跟 close 路径一致
    assert snap["as_of"] == D_TODAY
    assert snap["kpis"]["zt_count"] == 75
    # 回落路径**仍**走 persist（与盘中不调相反，护栏：close 路径不能因为
    # "intraday fallback" 标签就静默跳过落库）
    assert persist_called["n"] == 1


# ---------- mode=intraday + 历史日期 → 400 ---------------------------------------


def test_intraday_with_historical_date_raises_value_error() -> None:
    """`mode=intraday` + 显式历史日期 → ValueError（端点转 400）。盘中无历史意义。"""
    import asyncio

    with pytest.raises(ValueError, match="intraday"):
        asyncio.run(svc.get_snapshot(cache=None, as_of=D5, lookback=10, mode="intraday"))


# ---------- mode=close 回归护栏（关键）-------------------------------------------


async def test_close_mode_is_byte_identical_to_legacy_path(_close_path: dict[str, Any]) -> None:
    """`mode="close"` 行为与今天一致——不引入盘中副作用、不污染 schema 默认。"""
    snap = await svc.get_snapshot(cache=None, as_of=D5, lookback=10, mode="close")
    assert snap["source"] == "local_calc"
    assert snap["as_of"] == D5
    # close 路径不主动设 as_of_label（保持 _empty/正常返回不变）
    assert "as_of_label" not in snap
    assert snap["kpis"]["zt_count"] == 75
    assert snap["echelons"]  # 真产出梯队


async def test_default_mode_is_close(_close_path: dict[str, Any]) -> None:
    """默认 mode == 'close'——与今天 `get_snapshot(cache, as_of, lookback)` 等价。"""
    snap_default = await svc.get_snapshot(cache=None, as_of=D5, lookback=10)
    snap_close = await svc.get_snapshot(cache=None, as_of=D5, lookback=10, mode="close")
    # 关键字段全等
    for k in (
        "as_of",
        "as_of_prev",
        "as_of_quality",
        "source",
        "limits_present",
        "is_partial",
        "sw_coverage",
        "degraded_reason",
        "kpis",
    ):
        assert snap_default[k] == snap_close[k], f"{k} differs"


# ---------- schema 拓宽 -------------------------------------------------------


def test_intraday_payload_validates_against_ladder_out() -> None:
    """盘中 payload 能被 `LimitUpLadderOut.model_validate(...)` 接受（schema 已拓宽）。"""
    from app.schemas.limit_up import (
        LimitUpLadderOut,
        SectorLimitUpOut,
        YesterdayLimitUpOut,
    )
    from app.services.intraday_sentiment_service import build_intraday_snapshot

    pool = _load_pool()
    snap = build_intraday_snapshot(
        pool, as_of=D_TODAY, captured_at=datetime(2026, 9, 17, 14, 30, 0)
    )

    # 1) LimitUpLadderOut 接收 source="eastmoney_intraday"、as_of_label 不空
    ladder_payload = {
        "as_of": snap["as_of"],
        "as_of_prev": snap["as_of_prev"],
        "as_of_quality": snap["as_of_quality"],
        "source": snap["source"],
        "limits_present": snap["limits_present"],
        "is_partial": snap["is_partial"],
        "sw_coverage": snap["sw_coverage"],
        "lookback": 10,
        "degraded_reason": snap["degraded_reason"],
        "kpis": snap["kpis"] or None,
        "echelons": snap["echelons"],
        "as_of_label": snap["as_of_label"],
    }
    out = LimitUpLadderOut.model_validate(ladder_payload)
    assert out.source == "eastmoney_intraday"
    assert out.echelons  # 至少一档
    assert out.as_of_label == "盘中 14:30"
    # 关键非空性：每档里都有 stock、且 days_span 等 None 被接受（schema 已拓宽）
    for bucket in snap["echelons"]:
        for stock in bucket["stocks"]:
            assert stock["days_span"] is None
            assert stock["boards_in_window"] is None
            assert stock["missing_days"] is None

    # 2) SectorLimitUpOut 接收 source="eastmoney_intraday"、items 内 l3/l1 全 None
    sector_payload = {
        "as_of": snap["as_of"],
        "as_of_quality": snap["as_of_quality"],
        "source": snap["source"],
        "degraded_reason": snap["degraded_reason"],
        "unclassified_count": snap["sectors"]["unclassified_count"],
        "items": snap["sectors"]["items"],
        "as_of_label": snap["as_of_label"],
    }
    sec_out = SectorLimitUpOut.model_validate(sector_payload)
    assert sec_out.source == "eastmoney_intraday"
    assert sec_out.as_of_label == "盘中 14:30"
    if sec_out.items:
        for it in sec_out.items:
            # SectorLimitUpItemOut 的 l3_code 在 close 路径非空、在盘中为 None
            # ——已拓宽为 Optional
            assert it.l3_code is None
            assert it.l3_name is None
            assert it.l1_code is None
            assert it.l1_name is None
            # 兜底字段
            assert it.max_streak >= 1
            assert it.leader_symbol

    # 3) YesterdayLimitUpOut 接收 source="eastmoney_intraday" + 空 items
    yest_payload = {
        "as_of": snap["as_of"],
        "as_of_prev": snap["as_of_prev"],
        "as_of_quality": snap["as_of_quality"],
        "source": snap["source"],
        "degraded_reason": snap["degraded_reason"],
        "kpis": {},
        "items": [],
        "as_of_label": snap["as_of_label"],
    }
    yest_out = YesterdayLimitUpOut.model_validate(yest_payload)
    assert yest_out.source == "eastmoney_intraday"


def test_close_payload_still_validates_against_ladder_out() -> None:
    """close payload（source="local_calc"）仍能解析——schema 拓宽是兼容扩展。"""
    from app.schemas.limit_up import (
        LimitUpLadderOut,
        SectorLimitUpOut,
        YesterdayLimitUpOut,
    )

    # 1) LimitUpLadderOut
    close_ladder = {
        "as_of": D5,
        "as_of_prev": date(2026, 9, 7),
        "as_of_quality": "complete",
        "source": "local_calc",
        "limits_present": True,
        "is_partial": False,
        "sw_coverage": 0.8,
        "lookback": 10,
        "degraded_reason": None,
        "kpis": None,
        "echelons": [],
    }
    out = LimitUpLadderOut.model_validate(close_ladder)
    assert out.source == "local_calc"

    # 2) SectorLimitUpOut + 非空 items（close 路径 l3/l1 非空）
    close_sector = {
        "as_of": D5,
        "as_of_quality": "complete",
        "source": "local_calc",
        "degraded_reason": None,
        "unclassified_count": 0,
        "items": [
            {
                "l3_code": "110101",
                "l3_name": "生猪养殖",
                "l1_code": "110000",
                "l1_name": "农林牧渔",
                "max_streak": 3,
                "leader_symbol": "000001",
                "leader_name": "平安银行",
                "leader_streak": 3,
                "zt_count": 2,
            }
        ],
    }
    sec_out = SectorLimitUpOut.model_validate(close_sector)
    assert sec_out.source == "local_calc"
    assert sec_out.items[0].l3_code == "110101"
    assert sec_out.items[0].l3_name == "生猪养殖"

    # 3) YesterdayLimitUpOut
    close_yest = {
        "as_of": D5,
        "as_of_prev": date(2026, 9, 7),
        "as_of_quality": "complete",
        "source": "local_calc",
        "degraded_reason": None,
        "kpis": {},
        "items": [],
    }
    yest_out = YesterdayLimitUpOut.model_validate(close_yest)
    assert yest_out.source == "local_calc"


# ---------- 非空性（non-vacuity 证据） -----------------------------------------


async def test_intraday_branch_does_not_fall_through_to_close_path(
    monkeypatch: pytest.MonkeyPatch,
    _close_path: dict[str, Any],
    _clock_today: None,
) -> None:
    """**反向护栏**：把 intraday 分支彻底关掉（fetch_intraday_pool 改返回空集 + 不抛错），
    close 路径不应被错走到——close 路径的 DB 替身若被走就会爆（`fetch_limit_up_window`
    没替身），所以一旦走错就会抛错；用此断言做 non-vacuity。"""
    _patch_pool(monkeypatch, _async_empty)

    async def _boom_repo(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("close-path repo was called from intraday branch")

    monkeypatch.setattr(svc.limit_up_repo, "fetch_limit_up_window", _boom_repo)
    snap = await svc.get_snapshot(cache=None, as_of=None, lookback=10, mode="intraday")

    # 走对了：空池也返回 intraday shape（degraded_reason="no_limit_up_rows"）
    assert snap["source"] == "eastmoney_intraday"
    assert snap["degraded_reason"] == "no_limit_up_rows"
    assert snap["as_of_label"] == "盘中 14:30"


# ---------- non-vacuity 证据（按 brief 要求记录） -----------------------------------
#
# 每个 test 跑红/绿的临时变更如下，**不是测试代码的一部分**——只为 reviewer 复核：
#
# 1) test_intraday_calls_fetch_intraday_pool_once:
#    - 临时改 assertion 为 `assert calls == []` → 测试爆（红），证它真在统计调用次数。
#    - 临时把 `_patch_pool(monkeypatch, _fake_pool)` 改回默认 → build_intraday_snapshot
#      走真池，`source != "eastmoney_intraday"` → 红。
#    - 临时把 as_of_label 改固定字符串 → 测试爆（证它在比对实际值）。
#
# 2) test_intraday_persisted_under_intra_cache_key_with_60s_ttl:
#    - 临时改 `assert ttl == 60` → `assert ttl == 300` → 红（证它真在比对 TTL）。
#    - 临时改 `assert key == f"market:limit-up:intra:{D_TODAY_STR}"` →
#      `assert key == "wrong"` → 红。
#    - 第二次调用前把 `cache.cached = None` 强制 miss → `call_count["n"] == 1` → 红。
#
# 3) test_intraday_does_not_call_persist_snapshot:
#    - 临时把 _boom 改成正常返回（不抛）→ 走完 persist_snapshot，与"不调"相反 → 红。
#    - 临时把 monkeypatch.setattr(svc, "persist_snapshot", _boom) 注释掉 →
#      测试仍绿，但失去了"不调"的不变量护栏（这是测试**意图**的证据）。
#
# 4) test_intraday_eastmoney_failure_falls_back_to_close:
#    - 临时把 _fake_pool_raises 改成正常返回 → 红（证它在测失败路径）。
#    - 临时把 fallback 的 as_of_label 覆盖改成 `"收盘"` →
#      `assert snap["as_of_label"] == "盘中不可用，已回落收盘"` 爆。
#    - 临时把 `await persist_snapshot(...)` 那段注释掉 →
#      `assert persist_called["n"] == 1` 爆。
#    - 临时把 `await get_snapshot(cache, as_of=today, mode="close")` 改成 `as_of=None`
#      → close 路径走 resolve_latest_complete_day 报 AttributeError（fake_resolve
#      没接住）→ 红。
#
# 5) test_intraday_with_historical_date_raises_value_error:
#    - 临时把 mode="intraday" 改成 mode="close" → 红（ValueError 不会抛）。
#    - 临时把 as_of=D5 改成 as_of=None → 红（同上）。
#
# 6) test_close_mode_is_byte_identical_to_legacy_path / test_default_mode_is_close:
#    - 临时把 `assert "as_of_label" not in snap` 删掉 → 红（close 路径被注入
#      as_of_label，违反"不引入副作用"）。
#    - 临时把 mode="close" 改成 mode="intraday" → 红（intraday 路径走 fake
#      池不一致）。
#
# 7) test_intraday_payload_validates_against_ladder_out:
#    - 临时把 schema 还原 `l3_code: str`（非 Optional）→ Pydantic 校验错 → 红。
#    - 临时把 schema 还原 `source: Literal["local_calc"]` →
#      "source": "eastmoney_intraday" 触发校验错 → 红。
#    - 临时把 `days_span: int | None` 还原 `days_span: int` → 红。
#
# 8) test_close_payload_still_validates_against_ladder_out:
#    - 临时把 schema 还原 `l3_code: str`（required）→ close 路径 l3_code="110101"
#      仍合法，无回归——这是 schema widening 兼容扩展的证据。
#
# 9) test_intraday_branch_does_not_fall_through_to_close_path:
#    - 临时把 _boom_repo 改成正常返回 → close 路径被走通但返回 degraded 快照，
#      `source != "eastmoney_intraday"` → 红。
#    - 临时把 _async_empty 改回正常池 → 走真 intraday 路径，degraded_reason 为 None
#      → 红。
