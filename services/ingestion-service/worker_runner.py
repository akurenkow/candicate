"""
Async runner for ingestion pipeline in Celery worker process.
Creates own connections (pool, qdrant, http, rabbitmq), runs pipeline, updates job status.
"""

import asyncio
import json
import logging
from typing import Any

import aio_pika
import httpx

from config import RABBITMQ_URL, RESULT_QUEUE
from db import (
    create_pool,
    ensure_job_pending,
    set_job_completed_by_pool,
    set_job_failed_by_pool,
)
from pipeline import run_pipeline
from qdrant_setup import ensure_collection, get_qdrant_client

logger = logging.getLogger(__name__)


async def _publish_result(channel: aio_pika.Channel, payload: dict[str, Any]) -> None:
    await channel.default_exchange.publish(
        aio_pika.Message(
            body=json.dumps(payload).encode("utf-8"),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        ),
        routing_key=RESULT_QUEUE,
    )


async def run_pipeline_in_worker(body: bytes) -> None:
    """
    Run full ingestion pipeline in worker process: own pool, qdrant, http, rabbitmq.
    Updates ingestion_jobs (completed/failed) and publishes to ingestion_results on failure.
    """
    pool = await create_pool()
    qdrant = get_qdrant_client()
    ensure_collection(qdrant)
    http_client = httpx.AsyncClient(timeout=120.0)
    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    channel = await connection.channel()
    await channel.declare_queue(RESULT_QUEUE, durable=True)

    try:
        payload = json.loads(body)
    except Exception:
        payload = {}
    job_id = payload.get("job_id", "")

    if job_id:
        try:
            await ensure_job_pending(pool, job_id)
        except Exception as e:
            logger.warning("Failed to ensure job pending for %s: %s", job_id, e)

    async def publish_result(payload: dict[str, Any]) -> None:
        await _publish_result(channel, payload)

    try:
        success, document_id, error_message = await run_pipeline(
            body, pool, qdrant, http_client, publish_result
        )
    except Exception as e:
        logger.exception("Pipeline exception in worker: %s", e)
        success = False
        document_id = None
        error_message = str(e)

    try:
        if success and job_id and document_id:
            await set_job_completed_by_pool(pool, job_id, document_id)
        elif job_id:
            await set_job_failed_by_pool(pool, job_id, error_message or "Unknown error")
            trace_id = payload.get("trace_id", "")
            await _publish_result(
                channel,
                {
                    "event": "indexing_failed",
                    "job_id": job_id,
                    "document_id": document_id,
                    "trace_id": trace_id,
                    "error": error_message or "Unknown error",
                },
            )
    except Exception as e:
        logger.exception("Failed to update job status or publish result: %s", e)
    finally:
        await connection.close()
        await http_client.aclose()
        await pool.close()
