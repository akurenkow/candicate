"""Celery app for resume conversion: dedicated queue, independent of other workers."""

from celery import Celery

from config import CELERY_BROKER_URL, RESUME_CONVERT_QUEUE

app = Celery(
    "resume_converter",
    broker=CELERY_BROKER_URL,
    backend=CELERY_BROKER_URL,
    include=["tasks"],
)
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_routes={"tasks.convert_resume_task": {"queue": RESUME_CONVERT_QUEUE}},
    task_default_queue=RESUME_CONVERT_QUEUE,
)
