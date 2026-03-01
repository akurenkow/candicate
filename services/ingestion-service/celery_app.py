"""
Celery app for ingestion: задача обработки документа выполняется в отдельном воркере.
HTTP-сервис остаётся отзывчивым, GET /jobs/{id} не даёт 503 во время перевода/эмбеддинга.
"""

import asyncio
import base64
import logging

from celery import Celery

from config import CELERY_BROKER_URL
from worker_runner import run_pipeline_in_worker

logger = logging.getLogger(__name__)

app = Celery(
    "ingestion",
    broker=CELERY_BROKER_URL,
    backend=CELERY_BROKER_URL,
)
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
)


@app.task(bind=True, name="ingestion.process_upload")
def process_upload_task(self, body_base64: str) -> None:
    """Запуск пайплайна в воркере. body_base64 — base64(message.body)."""
    try:
        body = base64.b64decode(body_base64, validate=True)
    except Exception as e:
        logger.exception("Invalid body_base64: %s", e)
        raise
    asyncio.run(run_pipeline_in_worker(body))
