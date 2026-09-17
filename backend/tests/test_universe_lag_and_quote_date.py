"""名录冻结的两处后果：行情被静默丢弃 + "数据截至"口径错绑。

现场证据（2026-09-17 活库）：`stocks.asof` 全表停在 2026-05-08（universe ingest
只在空库首启/手动触发时跑过），而 `daily_quotes` 已到 09-16。日线采集用 stocks 表
把 ts_code → stock_id，映射不到就 `skipped += 1; continue` → 对账当日原始 JSONL
得 65 只次新股的行「从未落库」，且每日 fetched 5550 / upserted 5485。

本文件钉住四件事（都是单测，不依赖活栈）：
1. 名录缺失的 ts_code 必须计数 + warning，不能只留一个 skipped 数字；
2. universe_refresh_job 覆盖三所、单所失败不牵连其余、失败隔离后仍失效缓存；
3. 该任务在调度器上按「周六 09:00 + 永不静默丢弃」注册；
4. enriched 的 latest_quote_date 来自 daily_quotes.trade_date（!= stocks.asof）。
"""

import logging
from datetime import date
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from apscheduler.triggers.cron import CronTrigger

from app.scheduler.jobs import universe_refresh_job
from app.scheduler.runner import create_scheduler
from app.schemas.stock import StockEnrichedOut
from app.services.market_service import _GET_ENRICHED_SQL
from app.services.tushare_ingest import TuShareIngestService

pytestmark = pytest.mark.asyncio


def _daily_row(ts_code: str = "600000.SH", trade_date: str = "20260916") -> dict:
    return {
        "ts_code": ts_code,
        "trade_date": trade_date,
        "open": 10.0,
        "high": 11.0,
        "low": 9.9,
        "close": 10.5,
        "pre_close": 10.0,
        "change": 0.5,
        "pct_chg": 5.0,
        "vol": 1000.0,
        "amount": 10500.0,
    }


async def test_unknown_ts_code_is_counted_and_warned(monkeypatch, caplog) -> None:
    """行情里有、名录里没有的行会被丢弃 —— 必须留痕，否则"少 65 只"不可见。"""
    upserted: list = []

    async def _upsert(_db, quotes):
        upserted.extend(quotes)
        return len(quotes)

    async def _stock_id_map(_self, _db):
        return {"600000.SH": 1}

    monkeypatch.setattr("app.repositories.quote_repo.upsert_quotes", _upsert)
    monkeypatch.setattr(TuShareIngestService, "_build_stock_id_map", _stock_id_map, raising=True)

    df = pd.DataFrame([_daily_row("600000.SH"), _daily_row("001232.SZ")])
    client = AsyncMock()
    client.fetch_daily = AsyncMock(return_value=df)
    service = TuShareIngestService(client=client, data_saver=AsyncMock())

    with caplog.at_level(logging.WARNING, logger="app.services.tushare_ingest"):
        result = await service.ingest_daily_quotes(AsyncMock(), trade_date="20260916")

    assert result["unknown_ts_codes"] == 1
    assert result["skipped"] == 1
    # 已知行照常落库，未知行不得进 upsert
    assert [q.stock_id for q in upserted] == [1]
    records = [r for r in caplog.records if "不在 stocks 名录中" in r.getMessage()]
    assert len(records) == 1
    assert "001232.SZ" in records[0].getMessage()


async def test_universe_refresh_job_covers_all_exchanges_and_isolates_failure(monkeypatch) -> None:
    """三所逐个跑；一所抛异常不得中断其余，且缓存仍要失效。"""
    ingested: list[str] = []

    class _Service:
        async def ingest_stock_universe(self, _db, exchange, **_kw):
            ingested.append(exchange)
            if exchange == "Shenzen_Stocks":
                raise RuntimeError("tushare boom")
            return {"inserted": 1, "skipped": 0}

    deleted: list[str] = []

    class _Redis:
        async def keys(self, pattern):
            return [f"{pattern}stub"]

        async def delete(self, *keys):
            deleted.extend(keys)
            return len(keys)

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def commit(self):
            return None

    monkeypatch.setattr("app.services.tushare_ingest.TuShareIngestService", _Service)
    monkeypatch.setattr("app.core.database.async_session_factory", lambda: _Session())
    monkeypatch.setattr("app.core.redis.get_redis_pool", AsyncMock(return_value=_Redis()))

    await universe_refresh_job()

    assert ingested == ["Shanghai_Stocks", "Shenzen_Stocks", "Beijing_Stocks"]
    assert sorted(deleted) == ["stock:categories:*stub", "stock:list:*stub"]


async def test_universe_refresh_registered_saturday_and_never_dropped() -> None:
    """周六 09:00 Asia/Shanghai；盘后类任务必须继承 job_defaults 的"永不丢弃"。"""
    scheduler = create_scheduler()
    scheduler.start()
    try:
        job = scheduler.get_job("universe_refresh")
        assert job is not None, "universe_refresh job not registered"
        trigger = job.trigger
        assert isinstance(trigger, CronTrigger)
        fields = {f.name: str(f) for f in trigger.fields}
        assert fields["day_of_week"] == "sat"
        assert fields["hour"] == "9"
        assert fields["minute"] == "0"
        assert str(trigger.timezone) == "Asia/Shanghai"
        assert job.misfire_grace_time is None
        assert job.coalesce is True
    finally:
        scheduler.shutdown(wait=False)


async def test_enriched_quote_date_comes_from_daily_quotes_not_stocks_asof() -> None:
    """latest_quote_date 必须取 daily_quotes.trade_date；与名录 asof 是两条管道。"""
    assert "q.trade_date AS latest_quote_date" in _GET_ENRICHED_SQL
    # LATERAL 必须把 trade_date 暴露出来，否则上面的别名取不到值（静默 NULL）
    lateral = _GET_ENRICHED_SQL.split("LEFT JOIN LATERAL", 1)[1]
    assert "trade_date" in lateral.split(") q ON true", 1)[0]

    field = StockEnrichedOut.model_fields["latest_quote_date"]
    assert field.default is None
    assert field.annotation == date | None
    # asof 仍是名录 ingest 时间戳，不能被复用为行情口径
    assert StockEnrichedOut.model_fields["asof"].annotation is not date
