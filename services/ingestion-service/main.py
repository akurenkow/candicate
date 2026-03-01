"""
Ingestion Service: FLOW-02 document upload and indexing.
Consumes upload_document from RabbitMQ, chunks, embeds, writes to Qdrant and PostgreSQL.
"""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from uuid import uuid4

import aio_pika
import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Histogram, generate_latest, REGISTRY
from prometheus_client import CONTENT_TYPE_LATEST

from config import RABBITMQ_URL
from consumer import start_consumer
from shared.tracing import OP_HTTP_IN, log_trace
from db import create_pool, get_job_status
from qdrant_setup import ensure_collection, get_qdrant_client

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Метрики Prometheus
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "http_status"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "endpoint"],
)

# Переменные окружения (можно вынести в отдельный конфиг)
SERVICE_NAME = os.getenv("SERVICE_NAME", "unknown-service")
DEBUG = os.getenv("DEBUG", "false").lower() == "true"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Connect to PostgreSQL, Qdrant, RabbitMQ; ensure collection; start consumer."""
    logger.info("Starting %s...", SERVICE_NAME)
    pool = await create_pool()
    qdrant = get_qdrant_client()
    ensure_collection(qdrant)
    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    http_client = httpx.AsyncClient(timeout=120.0)
    app.state.pool = pool
    app.state.qdrant = qdrant
    app.state.rabbitmq = connection
    app.state.http_client = http_client
    consumer_task = asyncio.create_task(
        start_consumer(connection, pool, qdrant, http_client)
    )
    yield
    logger.info("Shutting down %s...", SERVICE_NAME)
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass
    await connection.close()
    await http_client.aclose()
    await pool.close()


# Создание экземпляра FastAPI
app = FastAPI(
    title=f"{SERVICE_NAME} API",
    version="0.1.0",
    lifespan=lifespan,
    debug=DEBUG,
)

# Middleware для CORS (в разработке разрешаем всё)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    """Middleware для замера времени выполнения и логирования."""
    # Генерация или получение trace_id
    request_id = request.headers.get("X-Request-ID", str(uuid4()))
    request.state.request_id = request_id

    log_trace(
        SERVICE_NAME,
        request_id,
        OP_HTTP_IN,
        "ingestion-service",
        target_resource=request.url.path,
    )
    logger.info(f"Request {request_id} started: {request.method} {request.url.path}")

    start_time = time.time()
    try:
        response = await call_next(request)
    except Exception as e:
        logger.exception(f"Request {request_id} failed: {e}")
        response = Response("Internal Server Error", status_code=500)
    process_time = time.time() - start_time

    # Добавляем заголовки
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time"] = str(process_time)

    # Запись метрик
    REQUEST_COUNT.labels(
        method=request.method,
        endpoint=request.url.path,
        http_status=response.status_code,
    ).inc()
    REQUEST_LATENCY.labels(
        method=request.method,
        endpoint=request.url.path,
    ).observe(process_time)

    logger.info(
        f"Request {request_id} finished: "
        f"status={response.status_code} time={process_time:.3f}s"
    )
    return response


@app.get("/health")
async def health_check():
    """Эндпоинт для проверки здоровья сервиса."""
    return {
        "status": "healthy",
        "service": SERVICE_NAME,
        "timestamp": time.time(),
    }


@app.get("/metrics")
async def metrics():
    """Эндпоинт для сбора метрик Prometheus."""
    return Response(
        content=generate_latest(REGISTRY),
        media_type=CONTENT_TYPE_LATEST,
    )


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    """
    Возвращает статус задачи индексации по job_id (для api-gateway GET /documents/tasks/{task_id}/status).
    404 если задача неизвестна (ещё не взята в обработку или неверный id).
    """
    pool = getattr(app.state, "pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Service unavailable")
    row = await get_job_status(pool, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")
    # Serialize datetime for JSON
    updated_at = row.get("updated_at")
    if hasattr(updated_at, "isoformat"):
        row = {**row, "updated_at": updated_at.isoformat()}
    return row


# Здесь будут подключаться специфичные для сервиса роутеры
# Например:
# from .routers import chat, documents
# app.include_router(chat.router, prefix="/chat", tags=["chat"])
# app.include_router(documents.router, prefix="/documents", tags=["documents"])


if __name__ == "__main__":
    # Только для локального запуска (не используется в Docker)
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=DEBUG,
    )