"""P5 原始 JSONL 回放：写入 → 读取 → 参数/日期不匹配时不回放。"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from app.services.data_saver import DataSaver


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


async def test_roundtrip_returns_same_rows(tmp_path) -> None:
    saver = DataSaver(base_dir=tmp_path)
    df = pd.DataFrame(
        [
            {"ts_code": "000001.SZ", "close": 10.5, "trade_date": "20261110"},
            {"ts_code": "600000.SH", "close": 8.0, "trade_date": "20261110"},
        ]
    )
    await saver.save_dataframe("daily", df, {"trade_date": "20261110"}, exchange="all_20261110")

    loaded = await saver.load_dataframe("daily", {"trade_date": "20261110"})
    assert loaded is not None
    assert len(loaded) == 2
    assert set(loaded["ts_code"]) == {"000001.SZ", "600000.SH"}


async def test_params_mismatch_returns_none(tmp_path) -> None:
    saver = DataSaver(base_dir=tmp_path)
    df = pd.DataFrame([{"ts_code": "000001.SZ", "close": 10.5}])
    await saver.save_dataframe("daily", df, {"trade_date": "20261110"})

    assert await saver.load_dataframe("daily", {"trade_date": "20261111"}) is None


async def test_other_day_is_not_replayed(tmp_path) -> None:
    """跨天不回放：防止「用旧快照冒充新日」。"""
    saver = DataSaver(base_dir=tmp_path)
    df = pd.DataFrame([{"ts_code": "000001.SZ", "close": 10.5}])
    await saver.save_dataframe("daily", df, {"trade_date": "20261110"})

    assert await saver.load_dataframe("daily", {"trade_date": "20261110"}, day="1999-01-01") is None


async def test_corrupt_jsonl_is_a_cache_miss(tmp_path) -> None:
    saver = DataSaver(base_dir=tmp_path)
    directory = tmp_path / "daily" / _today()
    directory.mkdir(parents=True)
    (directory / "broken.jsonl").write_text("{not json}\n", encoding="utf-8")

    assert await saver.load_dataframe("daily", {}) is None


async def test_latest_raw_path_points_at_newest(tmp_path) -> None:
    saver = DataSaver(base_dir=tmp_path)
    await saver.save_dataframe("daily", pd.DataFrame([{"a": 1}]), {})
    path = await saver.latest_raw_path("daily")
    assert path is not None and path.exists()
    assert path.parent.name == _today()
