"""
RabbitMQ consumer for translation_tasks queue.
Receives tasks, enqueues to Celery, acks message.
"""

import json
import logging
from typing import Any

import aio_pika
from aio_pika import IncomingMessage

from config import TRANSLATION_TASKS_QUEUE, TRANSLATION_RESULTS_QUEUE
from celery_app import translate_task

logger = logging.getLogger(__name__)


async def process_translation_message(message: IncomingMessage) -> None:
    """Parse message, enqueue Celery task, ack."""
    try:
        body = json.loads(message.body)
    except Exception as e:
        logger.exception("Invalid message body: %s", e)
        await message.nack(requeue=False)
        return

    task_id = body.get("task_id") or body.get("job_id") or ""
    if not task_id:
        logger.warning("Message missing task_id/job_id, rejecting")
        await message.nack(requeue=False)
        return

    text = body.get("text", "")
    source_lang = body.get("source_lang", "ru")
    target_lang = body.get("target_lang", "en")
    model = body.get("model")
    temperature = body.get("temperature")
    job_id = body.get("job_id")
    trace_id = body.get("trace_id")

    try:
        translate_task.delay(
            task_id=task_id,
            text=text,
            source_lang=source_lang,
            target_lang=target_lang,
            model=model,
            temperature=temperature,
            job_id=job_id,
            trace_id=trace_id,
        )
    except Exception as e:
        logger.exception("Failed to enqueue translation task: %s", e)
        await message.nack(requeue=True)
        return
    await message.ack()


async def start_consumer(connection: aio_pika.Connection) -> None:
    """Declare queues and consume from TRANSLATION_TASKS_QUEUE."""
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=1)
    tasks_queue = await channel.declare_queue(TRANSLATION_TASKS_QUEUE, durable=True)
    await channel.declare_queue(TRANSLATION_RESULTS_QUEUE, durable=True)

    async def on_message(message: IncomingMessage) -> None:
        await process_translation_message(message)

    await tasks_queue.consume(on_message)
    logger.info("Consuming from queue %s", TRANSLATION_TASKS_QUEUE)
