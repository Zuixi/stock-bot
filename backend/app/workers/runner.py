"""Worker process entry point: starts all consumer workers concurrently."""

import asyncio
import logging

from app.workers.quotes_worker import QuotesWorker
from app.workers.universe_worker import UniverseWorker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    workers = [
        UniverseWorker(),
        QuotesWorker(),
    ]
    logger.info("Starting %d worker(s)", len(workers))
    await asyncio.gather(*[w.run() for w in workers])


if __name__ == "__main__":
    asyncio.run(main())
