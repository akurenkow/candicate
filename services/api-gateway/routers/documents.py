"""
Document upload endpoint (FLOW-02, CTR-03).
Validates file, publishes upload_document to RabbitMQ, returns 202 with job_id.
"""

import base64
import json
import logging
import os
from typing import Optional
from uuid import UUID, uuid4

import aio_pika
import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from shared.tracing import OP_HTTP_OUT, OP_QUEUE_PUBLISH, log_trace

logger = logging.getLogger(__name__)
SERVICE_NAME = os.getenv("SERVICE_NAME", "api-gateway")

router = APIRouter(prefix="/documents", tags=["documents"])

# Allowed extensions and content types
ALLOWED_EXTENSIONS = frozenset({"pdf", "docx", "txt", "md"})
ALLOWED_CONTENT_TYPES = frozenset({
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/markdown",
    "text/x-markdown",
})
DEFAULT_MAX_SIZE_MB = 25
DEFAULT_UPLOAD_QUEUE = "document_uploads"
ENV_ALLOWED_UPLOAD_USER_IDS = "ALLOWED_UPLOAD_USER_IDS"
ENV_ALLOW_E2E_UPLOAD = "ALLOW_E2E_UPLOAD"
ENV_INGESTION_SERVICE_URL = "INGESTION_SERVICE_URL"
DEFAULT_INGESTION_SERVICE_URL = "http://ingestion-service:8000"


def _get_allowed_upload_user_ids() -> frozenset:
    """Список внешних ID пользователей, имеющих право загружать документы (резюме)."""
    raw = os.getenv(ENV_ALLOWED_UPLOAD_USER_IDS, "").strip()
    if not raw:
        return frozenset()
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


def _is_e2e_test_owner(owner_id: str) -> bool:
    """Разрешить загрузку/чат для эфемерного owner_id (UUID или test_e2e_*) при включённом E2E."""
    if not os.getenv(ENV_ALLOW_E2E_UPLOAD, "").strip().lower() in ("1", "true", "yes"):
        return False
    s = (owner_id or "").strip()
    if s.startswith("test_e2e_"):
        return True
    try:
        UUID(s)
        return True
    except (ValueError, TypeError):
        return False


def _get_extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _validate_file(
    filename: str,
    content_type: str,
    size: int,
    max_size_mb: int = DEFAULT_MAX_SIZE_MB,
) -> None:
    ext = _get_extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type not allowed. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )
    if content_type and content_type.split(";")[0].strip().lower() not in ALLOWED_CONTENT_TYPES:
        if ext in ALLOWED_EXTENSIONS:
            pass  # allow by extension if content-type is wrong
        else:
            raise HTTPException(status_code=400, detail="Content type not allowed")
    max_bytes = max_size_mb * 1024 * 1024
    if size > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Max size: {max_size_mb} MB",
        )


@router.post("/upload")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    owner_id: str = Form(""),
    external_id: Optional[str] = Form(None),
):
    """
    Accept file upload, validate, publish to RabbitMQ (upload_document), return 202.
    FLOW-02, CTR-03. Только owner_id из списка ALLOWED_UPLOAD_USER_IDS допускаются к загрузке (403 иначе).
    """
    allowed_ids = _get_allowed_upload_user_ids()
    owner_stripped = (owner_id or "").strip()
    if allowed_ids and not owner_stripped:
        raise HTTPException(status_code=403, detail="Upload not allowed for this user")
    if allowed_ids and owner_stripped not in allowed_ids and not _is_e2e_test_owner(owner_stripped):
        raise HTTPException(
            status_code=403,
            detail="Upload not allowed for this user",
        )

    max_size_mb = int(os.getenv("MAX_REQUEST_SIZE_MB", str(DEFAULT_MAX_SIZE_MB)))
    upload_queue = os.getenv("UPLOAD_QUEUE", DEFAULT_UPLOAD_QUEUE)
    trace_id = getattr(request.state, "request_id", str(uuid4()))
    job_id = str(uuid4())

    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")
    content_type = file.content_type or "application/octet-stream"
    raw = await file.read()
    _validate_file(file.filename, content_type, len(raw), max_size_mb)

    payload = {
        "command": "upload_document",
        "job_id": job_id,
        "trace_id": trace_id,
        "owner_id": owner_id or "anonymous",
        "external_id": external_id,
        "filename": file.filename,
        "content_type": content_type,
        "content_base64": base64.b64encode(raw).decode("ascii"),
    }
    body = json.dumps(payload).encode("utf-8")

    rabbitmq = getattr(request.app.state, "rabbitmq", None)
    if rabbitmq is None:
        logger.error("RabbitMQ not connected")
        raise HTTPException(
            status_code=503,
            detail="Service unavailable (message broker not connected)",
        )

    try:
        log_trace(
            SERVICE_NAME,
            trace_id,
            OP_QUEUE_PUBLISH,
            "rabbitmq",
            target_resource=upload_queue,
            extra={"job_id": job_id},
        )
        channel = await rabbitmq.channel()
        await channel.declare_queue(upload_queue, durable=True)
        await channel.default_exchange.publish(
            aio_pika.Message(
                body=body,
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=upload_queue,
        )
    except Exception as e:
        logger.exception("Failed to publish to RabbitMQ: %s", e)
        raise HTTPException(status_code=503, detail="Service unavailable") from e

    return JSONResponse(
        content={"job_id": job_id, "status": "accepted"},
        status_code=202,
    )


@router.get("/tasks/{task_id}/status")
async def get_task_status(request: Request, task_id: str):
    """
    Проверка статуса задачи индексации по task_id (job_id из POST /documents/upload).
    Проксирует запрос в Ingestion Service. Пока идёт обработка в воркере, при недоступности
    ingestion возвращаем status "pending", чтобы клиент мог продолжать опрос.
    """
    base_url = os.getenv(ENV_INGESTION_SERVICE_URL, DEFAULT_INGESTION_SERVICE_URL).rstrip("/")
    url = f"{base_url}/jobs/{task_id}"
    trace_id = getattr(request.state, "request_id", str(uuid4()))
    log_trace(
        SERVICE_NAME,
        trace_id,
        OP_HTTP_OUT,
        "ingestion-service",
        target_resource=f"/jobs/{task_id}",
    )
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, headers={"X-Request-ID": trace_id})
    except httpx.RequestError as e:
        logger.warning("Ingestion service request failed (returning pending): %s", e)
        return JSONResponse(
            content={"job_id": task_id, "status": "pending"},
            status_code=200,
        )
    if r.status_code == 503:
        return JSONResponse(
            content={"job_id": task_id, "status": "pending"},
            status_code=200,
        )
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Task not found")
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="Ingestion service error")
    return r.json()
