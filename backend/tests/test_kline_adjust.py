"""纯单元测试：K线复权 — TuShare adj_factor 行映射 / qfq 计算 / 缓存 key 隔离。"""

from datetime import date

from app.schemas.quote import DailyQuoteOut
from app.services import quote_service
from app.services.quote_service import apply_qfq, kline_cache_key, map_adj_factor_rows


def _raw(**overrides) -> dict:
    row = {"ts_code": "600519.SH", "trade_date": "20260901", "adj_factor": 251.5}
    row.update(overrides)
    return row


# ── map_adj_factor_rows：TuShare 行 → (date, factor) ────────────────


def test_map_adj_factor_rows_parses_and_skips_dirty():
    rows = [
        _raw(trade_date="20260901", adj_factor=251.5),
        _raw(trade_date="20260902", adj_factor=251.6),
        _raw(trade_date="bad", adj_factor=1.0),  # 日期脏行 → 跳过
        _raw(trade_date="20260903", adj_factor=None),  # 因子缺失 → 跳过
        {"ts_code": "600519.SH", "trade_date": "20260904"},  # 因子键缺失 → 跳过
    ]
    assert map_adj_factor_rows(rows) == [(date(2026, 9, 1), 251.5), (date(2026, 9, 2), 251.6)]


# ── kline_cache_key：adjust 维度隔离缓存 ────────────────────────────


def test_kline_cache_key_includes_adjust():
    key_raw = kline_cache_key(
        "Shanghai_Stocks", "600519", date(2026, 8, 1), date(2026, 9, 1), "raw"
    )
    key_qfq = kline_cache_key(
        "Shanghai_Stocks", "600519", date(2026, 8, 1), date(2026, 9, 1), "qfq"
    )
    assert key_raw != key_qfq
    assert key_raw == "quote:kline:Shanghai_Stocks:600519:2026-08-01:2026-09-01:raw"


# ── 回补冷却 key：service 与 API 层共享的模板契约 ───────────────────


def test_adj_factor_backfill_cd_key_contract():
    from app.services.quote_service import (
        ADJ_FACTOR_BACKFILL_CD_KEY,
        ADJ_FACTOR_BACKFILL_CD_TTL,
    )

    key = ADJ_FACTOR_BACKFILL_CD_KEY.format(exchange="Shanghai_Stocks", symbol="600519")
    assert key == "quote:adj-factor:backfill-cd:Shanghai_Stocks:600519"
    assert ADJ_FACTOR_BACKFILL_CD_TTL == 300


# ── apply_qfq：前复权计算 ───────────────────────────────────────────


def _q(d: str, close: float, adj: float | None) -> DailyQuoteOut:
    return DailyQuoteOut(
        trade_date=date.fromisoformat(d),
        open=10.0,
        high=11.0,
        low=9.0,
        close=close,
        volume=100,
        amount=1000.0,
        adj_factor=adj,
    )


def test_apply_qfq_adjusts_ohlc_by_latest_factor():
    rows = [_q("2026-01-02", 100.0, 1.0), _q("2026-01-05", 200.0, 2.0)]  # 最新因子 2.0
    out = apply_qfq(rows)
    assert out is not None
    assert out[0].close == 50.0  # 100 * 1/2
    assert out[1].close == 200.0  # 基准日不动
    assert out[0].open == 5.0 and out[0].high == 5.5 and out[0].low == 4.5
    assert out[0].volume == 100 and out[0].amount == 1000.0  # 量额不动


def test_apply_qfq_returns_none_when_any_factor_missing():
    rows = [_q("2026-01-02", 100.0, None), _q("2026-01-05", 200.0, 2.0)]
    assert apply_qfq(rows) is None


def test_apply_qfq_empty_rows():
    assert apply_qfq([]) is None


# ── get_kline service 级：qfq 因子不完整不写缓存 / raw 正常缓存 ──────


class _FakeCache:
    """极简 cache 替身：get 恒 miss，只记录 set 调用。"""

    def __init__(self) -> None:
        self.set_calls: list[str] = []

    async def get(self, key: str):
        return None

    async def set(self, key: str, value, ttl: int | None = None) -> None:
        self.set_calls.append(key)


class _FakeStock:
    id = 1
    name = "贵州茅台"


def _patch_kline_source(monkeypatch, rows: list[DailyQuoteOut]) -> None:
    """monkeypatch stock/quote repo，绕开 DB 直接喂行情行。"""

    async def fake_get_stock(db, exchange, symbol):
        return _FakeStock()

    async def fake_get_kline(db, stock_id, start_date, end_date):
        return rows

    monkeypatch.setattr(quote_service.stock_repo, "get_stock_by_symbol", fake_get_stock)
    monkeypatch.setattr(quote_service.quote_repo, "get_kline", fake_get_kline)


async def test_get_kline_qfq_incomplete_factors_skips_cache(monkeypatch):
    rows = [_q("2026-01-02", 100.0, None), _q("2026-01-05", 200.0, 2.0)]
    _patch_kline_source(monkeypatch, rows)
    cache = _FakeCache()
    result = await quote_service.get_kline(
        None, cache, "Shanghai_Stocks", "600519",
        date(2026, 1, 1), date(2026, 1, 31), adjust="qfq",
    )
    assert result is not None
    assert result.adjust_available is False
    assert result.data[0].close == 100.0  # 因子缺失回退原始行情
    assert cache.set_calls == []  # qfq 不完整不缓存，回补后由 delete_pattern 兜底失效


async def test_get_kline_raw_caches_even_without_factors(monkeypatch):
    rows = [_q("2026-01-02", 100.0, None), _q("2026-01-05", 200.0, 2.0)]
    _patch_kline_source(monkeypatch, rows)
    cache = _FakeCache()
    result = await quote_service.get_kline(
        None, cache, "Shanghai_Stocks", "600519",
        date(2026, 1, 1), date(2026, 1, 31), adjust="raw",
    )
    assert result is not None
    assert result.adjust_available is False  # 因子缺失但 raw 不受影响
    assert len(cache.set_calls) == 1
    assert cache.set_calls[0].endswith(":raw")
