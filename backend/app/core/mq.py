"""RabbitMQ connection and message publishing via aio-pika."""

import json
import logging
from datetime import UTC, datetime

import aio_pika
from aio_pika import ExchangeType, Message
from aio_pika.abc import AbstractRobustChannel, AbstractRobustConnection

from app.config import settings

logger = logging.getLogger(__name__)

QUEUES: dict[str, str] = {
    "universe.fetch": "stock_bot.universe.fetch",
    "quotes.fetch": "stock_bot.quotes.fetch",
    "daily_basic.fetch": "stock_bot.daily_basic.fetch",
    "features.compute": "stock_bot.features.compute",
    "clustering.run": "stock_bot.clustering.run",
    "llm.explain": "stock_bot.llm.explain",
    "industry_metrics.fetch": "stock_bot.industry_metrics.fetch",
    "securities.fetch": "stock_bot.securities.fetch",
    "market_data.fetch": "stock_bot.market_data.fetch",
}

_connection: AbstractRobustConnection | None = None
_channel: AbstractRobustChannel | None = None


async def get_mq_connection() -> AbstractRobustConnection:
    global _connection
    if _connection is None or _connection.is_closed:
        _connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    return _connection


async def get_mq_channel() -> AbstractRobustChannel:
    global _channel
    conn = await get_mq_connection()
    if _channel is None or _channel.is_closed:
        _channel = await conn.channel()
        await _channel.set_qos(prefetch_count=10)
        exchange = await _channel.declare_exchange(
            settings.rabbitmq_exchange,
            ExchangeType.TOPIC,
            durable=True,
        )
        for queue_name in QUEUES.values():
            queue = await _channel.declare_queue(queue_name, durable=True)
            routing_key = queue_name.replace("stock_bot.", "")
            await queue.bind(exchange, routing_key=routing_key)
    return _channel


async def close_mq_connection() -> None:
    global _connection, _channel
    if _channel and not _channel.is_closed:
        await _channel.close()
        _channel = None
    if _connection and not _connection.is_closed:
        await _connection.close()
        _connection = None


async def publish_message(routing_key: str, payload: dict) -> None:
    """Publish a JSON message to the topic exchange."""
    channel = await get_mq_channel()
    exchange = await channel.get_exchange(settings.rabbitmq_exchange)

    body = json.dumps(
        {**payload, "published_at": datetime.now(UTC).isoformat()},
        ensure_ascii=False,
        default=str,
    ).encode()

    await exchange.publish(
        Message(body=body, content_type="application/json", delivery_mode=2),
        routing_key=routing_key,
    )
    logger.info("Published message to %s", routing_key)
