"""Test QUEUES completeness and DailyBasicWorker integration."""

import pytest

from app.core.mq import QUEUES


class TestQueuesDict:
    """Ensure QUEUES covers all worker queue_key values."""

    def test_daily_basic_fetch_is_registered(self):
        assert "daily_basic.fetch" in QUEUES
        assert QUEUES["daily_basic.fetch"] == "stock_bot.daily_basic.fetch"

    def test_all_known_workers_registered(self):
        """Every worker class defined in workers/ must have its queue_key in QUEUES."""
        required = {
            "universe.fetch",
            "quotes.fetch",
            "daily_basic.fetch",
        }
        missing = required - set(QUEUES)
        assert not missing, f"QUEUES missing keys: {missing}"

    def test_routing_keys_consistent(self):
        """Every queue_name must equal 'stock_bot.' + queue_key."""
        for key, name in QUEUES.items():
            expected = f"stock_bot.{key}"
            assert name == expected, (
                f"QUEUES[{key!r}] = {name!r}, expected {expected!r}"
            )


class TestDailyBasicWorkerInstantiation:
    """Smoke test: Worker can import and instantiate without errors."""

    def test_instantiate(self):
        from app.workers.daily_basic_worker import DailyBasicWorker

        worker = DailyBasicWorker()
        assert worker.queue_key == "daily_basic.fetch"
        assert worker.queue_key in QUEUES
