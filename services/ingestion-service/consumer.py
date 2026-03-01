"""
RabbitMQ consumer for document_uploads queue (CTR-16).
Кладёт задачу в Celery и сразу ack — обработка в ingestion-worker без блокировки HTTP.
"""

import base64
import json
import logging
from typing import Any

import aio_pika
from aio_pika import IncomingMessage

from config import RESULT_QUEUE, UPLOAD_QUEUE
from db import ensure_job_pending
from shared.tracing import OP_QUEUE_CONSUME, log_trace
from celery_app import process_upload_task

logger = logging.getLogger(__name__)
SERVICE_NAME = "ingestion-service"


async def process_message(
    message: IncomingMessage,
    pool: Any,
    qdrant: Any,
    http_client: Any,
    result_channel: aio_pika.Channel,
) -> None:
    """
    Регистрируем job как pending, ставим задачу в Celery, ack сообщение.
    Обработка (перевод, эмбеддинг, запись в Qdrant/Postgres) выполняется в ingestion-worker.
    """
    try:
        body = json.loads(message.body)
        job_id = body.get("job_id", "")
        trace_id = body.get("trace_id", "")
    except Exception:
        job_id = ""
        trace_id = ""
    log_trace(
        SERVICE_NAME,
        trace_id,
        OP_QUEUE_CONSUME,
        "rabbitmq",
        target_resource=UPLOAD_QUEUE,
        extra={"job_id": job_id},
    )
    if job_id:
        try:
            await ensure_job_pending(pool, job_id)
        except Exception as e:
            logger.warning("Failed to ensure job pending for %s: %s", job_id, e)

    body_b64 = base64.b64encode(message.body).decode("ascii")
    try:
        process_upload_task.delay(body_b64)
    except Exception as e:
        logger.exception("Failed to enqueue task to Celery: %s", e)
        await message.nack(requeue=True)
        return
    await message.ack()


async def start_consumer(
    connection: aio_pika.Connection,
    pool: Any,
    qdrant: Any,
    http_client: Any,
) -> None:
    """Declare queues and consume from UPLOAD_QUEUE."""
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=1)

    upload_queue = await channel.declare_queue(UPLOAD_QUEUE, durable=True)
    await channel.declare_queue(RESULT_QUEUE, durable=True)

    async def on_message(message: IncomingMessage) -> None:
        await process_message(message, pool, qdrant, http_client, channel)

    await upload_queue.consume(on_message)
    logger.info("Consuming from queue %s", UPLOAD_QUEUE)