"""Abstract base class for RabbitMQ consumer workers."""

import asyncio
import json
import logging
import uuid
from abc import ABC, abstractmethod

import aio_pika
from aio_pika.abc import AbstractIncomingMessage

from app.core.database import async_session_factory
from app.core.mq import QUEUES, get_mq_channel
from app.repositories import task_repo

logger = logging.getLogger(__name__)


class BaseWorker(ABC):
    """Consumes messages from a named queue and updates task state."""

    queue_key: str  # Must match a key in QUEUES dict

    @abstractmethod
    async def process(self, task_id: uuid.UUID, payload: dict) -> dict:
        """Execute the work; return a result dict on success."""

    async def handle_message(self, message: AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            try:
                body = json.loads(message.body)
                task_id = uuid.UUID(body["task_id"])
                payload = body.get("payload", {})
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                logger.error("Malformed message: %s — %s", message.body, e)
                return

            async with async_session_factory() as db:
                try:
                    await task_repo.update_task_status(db, task_id, "running")
                    await db.commit()

                    result = await self.process(task_id, payload)

                    await task_repo.update_task_status(
                        db, task_id, "completed", progress=100, result=result
                    )
                    await db.commit()
                    logger.info("Task %s completed", task_id)
                except Exception as e:
                    logger.exception("Task %s failed: %s", task_id, e)
                    await db.rollback()
                    async with async_session_factory() as err_db:
                        await task_repo.update_task_status(
                            err_db, task_id, "failed", error=str(e)
                        )
                        await err_db.commit()

    async def run(self) -> None:
        channel = await get_mq_channel()
        queue_name = QUEUES[self.queue_key]
        queue = await channel.get_queue(queue_name)
        logger.info("%s listening on %s", self.__class__.__name__, queue_name)
        await queue.consume(self.handle_message)
        await asyncio.get_event_loop().create_future()  # run forever
