"""
Celery app for translation-service: consume translation_tasks, publish to translation_results.
"""

import json
import logging
from typing import Any, Dict, Optional

import pika
from celery import Celery

from config import (
    CELERY_BROKER_URL,
    DEFAULT_MODEL,
    RABBITMQ_URL,
    TRANSLATION_RESULTS_QUEUE,
    TRANSLATION_TASKS_QUEUE,
)
from ollama_client import translate_via_ollama
from cache import get_cached, set_cached

logger = logging.getLogger(__name__)

app = Celery(
    "translation",
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


def _translate_and_maybe_cache(
    text: str,
    source_lang: str = "ru",
    target_lang: str = "en",
    model: Optional[str] = None,
    temperature: Optional[float] = None,
) -> str:
    m = model or DEFAULT_MODEL
    cached = get_cached(text, source_lang, target_lang, m)
    if cached is not None:
        return cached
    result = translate_via_ollama(
        text,
        source_lang=source_lang,
        target_lang=target_lang,
        model=model,
        temperature=temperature,
    )
    set_cached(text, source_lang, target_lang, m, result)
    return result


def _publish_result(payload: Dict[str, Any]) -> None:
    params = pika.URLParameters(RABBITMQ_URL)
    conn = pika.BlockingConnection(params)
    ch = conn.channel()
    ch.queue_declare(queue=TRANSLATION_RESULTS_QUEUE, durable=True)
    ch.basic_publish(
        exchange="",
        routing_key=TRANSLATION_RESULTS_QUEUE,
        body=json.dumps(payload).encode("utf-8"),
        properties=pika.BasicProperties(delivery_mode=2),
    )
    conn.close()


@app.task(bind=True, name="translation.translate_task")
def translate_task(
    self,
    task_id: str,
    text: str,
    source_lang: str = "ru",
    target_lang: str = "en",
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    job_id: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> None:
    """
    Translate text and publish result to translation_results.
    Message format: task_id, text, source_lang, target_lang, model?, temperature?, job_id?, trace_id?
    Result: task_id, job_id?, trace_id?, translated_text, error?
    """
    try:
        translated = _translate_and_maybe_cache(
            text,
            source_lang=source_lang,
            target_lang=target_lang,
            model=model,
            temperature=temperature,
        )
        _publish_result({
            "task_id": task_id,
            "job_id": job_id,
            "trace_id": trace_id,
            "translated_text": translated,
            "source_lang": source_lang,
            "target_lang": target_lang,
        })
    except Exception as e:
        logger.exception("Translation task failed: %s", e)
        _publish_result({
            "task_id": task_id,
            "job_id": job_id,
            "trace_id": trace_id,
            "error": str(e),
        })
        raise
