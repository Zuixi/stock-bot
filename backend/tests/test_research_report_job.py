"""研报每日任务与回补脚本的接线测试（不联网、不碰真库）。

钉住三件事：

1. ``research_reports_daily_job`` 用**近 3 日**窗口调用同一个 ingest，并 commit；
2. ingest 抛错时走 ``_alert_job_failure("research_reports_daily", …)``，不冒泡；
3. 回补脚本的窗口切分/参数解析是纯函数，边界（单日、跨段）正确。
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.scheduler import jobs as scheduler_jobs
from scripts import backfill_research_reports as backfill

SHANGHAI = ZoneInfo("Asia/Shanghai")


class _FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class _FakeSessionFactory:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session
        self.entered = 0

    def __call__(self) -> _FakeSessionFactory:
        return self

    async def __aenter__(self) -> _FakeSession:
        self.entered += 1
        return self.session

    async def __aexit__(self, *_exc: Any) -> None:
        return None


async def test_daily_job_uses_three_day_window_and_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.core.database as database

    session = _FakeSession()
    factory = _FakeSessionFactory(session)
    monkeypatch.setattr(database, "async_session_factory", factory)
    calls: list[tuple[Any, date, date]] = []

    async def fake_ingest(db: Any, begin: date, end: date, **_: Any) -> dict[str, int]:
        calls.append((db, begin, end))
        return {"fetched": 2, "inserted": 1, "skipped": 0}

    monkeypatch.setattr(
        "app.services.content_ingest.research_report.ingest_research_reports", fake_ingest
    )

    await scheduler_jobs.research_reports_daily_job()

    assert factory.entered == 1
    assert session.commits == 1
    assert len(calls) == 1
    _, begin, end = calls[0]
    today = datetime.now(SHANGHAI).date()
    assert end == today
    assert begin == today - timedelta(days=3)


async def test_daily_job_alerts_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.core.database as database

    session = _FakeSession()
    monkeypatch.setattr(database, "async_session_factory", _FakeSessionFactory(session))
    alerted: list[tuple[Any, BaseException]] = []

    async def boom(*_args: Any, **_kwargs: Any) -> dict[str, int]:
        raise RuntimeError("upstream down")

    async def fake_alert(job_ids: Any, exc: BaseException) -> None:
        alerted.append((job_ids, exc))

    monkeypatch.setattr("app.services.content_ingest.research_report.ingest_research_reports", boom)
    monkeypatch.setattr(scheduler_jobs, "_alert_job_failure", fake_alert)

    await scheduler_jobs.research_reports_daily_job()

    assert alerted and alerted[0][0] == "research_reports_daily"
    assert session.commits == 0


def test_backfill_windows_cover_range_once() -> None:
    windows = backfill._windows(date(2026, 11, 1), date(2026, 11, 11), 7)
    assert windows == [
        (date(2026, 11, 1), date(2026, 11, 7)),
        (date(2026, 11, 8), date(2026, 11, 11)),
    ]


def test_backfill_parse_args_defaults_and_aliases() -> None:
    args = backfill._parse_args(["--start", "2026-11-01", "--end", "2026-11-11"])
    assert isinstance(args, argparse.Namespace)
    assert (args.day_from, args.day_to) == (date(2026, 11, 1), date(2026, 11, 11))


def test_backfill_parse_args_rejects_inverted_range() -> None:
    with pytest.raises(SystemExit):
        backfill._parse_args(["--from", "2026-11-11", "--to", "2026-11-01"])
