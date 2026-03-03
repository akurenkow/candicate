"""
Базовый модуль FastAPI-приложения для микросервиса.
Содержит общую инфраструктуру: middleware, health check, метрики, startup/shutdown.
"""

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from uuid import uuid4

import aio_pika
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from prometheus_client import Counter, Histogram, generate_latest, REGISTRY
from prometheus_client import CONTENT_TYPE_LATEST

from config import RABBITMQ_URL, RAG_QUEUE, RETRIEVAL_TOP_K
from db import create_pool, create_message, get_message
from consumer import start_consumer
from response_cache import delete_by_owner
from shared.tracing import OP_HTTP_IN, log_trace
from match_vacancy import run_match_vacancy
from cover_letter import run_cover_letter

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
    """
    Управление жизненным циклом приложения.
    Подключение к БД и RabbitMQ для FLOW-01 enqueue.
    """
    from shared.telemetry import bootstrap
    bootstrap(app)
    logger.info(f"Starting {SERVICE_NAME}...")
    app.state.pool = await create_pool()
    rabbitmq_url = os.getenv("RABBITMQ_URL", RABBITMQ_URL)
    max_attempts = int(os.getenv("RABBITMQ_CONNECT_ATTEMPTS", "5"))
    retry_delay = float(os.getenv("RABBITMQ_CONNECT_RETRY_DELAY", "3"))
    app.state.rabbitmq = None
    for attempt in range(1, max_attempts + 1):
        try:
            app.state.rabbitmq = await aio_pika.connect_robust(rabbitmq_url)
            logger.info("RabbitMQ connected on attempt %s", attempt)
            break
        except Exception as e:
            logger.warning("RabbitMQ attempt %s/%s failed: %s", attempt, max_attempts, e)
            if attempt < max_attempts:
                await asyncio.sleep(retry_delay)
    consumer_task = None
    if getattr(app.state, "rabbitmq", None) and getattr(app.state, "pool", None):
        consumer_task = asyncio.create_task(
            start_consumer(app.state.rabbitmq, app.state.pool, worker_id=str(uuid4()))
        )
        logger.info("RAG queue consumer started")
    yield
    logger.info(f"Shutting down {SERVICE_NAME}...")
    if consumer_task is not None:
        consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass
    if getattr(app.state, "rabbitmq", None):
        await app.state.rabbitmq.close()
    if getattr(app.state, "pool", None):
        await app.state.pool.close()


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
        "conversation-manager",
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


class EnqueueBody(BaseModel):
    message: str
    owner_id: str = ""


@app.post("/enqueue")
async def enqueue(request: Request, body: EnqueueBody):
    """FLOW-01: create rag_messages record and publish to RAG_QUEUE (owner_id scopes candidate)."""
    pool = getattr(request.app.state, "pool", None)
    rabbitmq = getattr(request.app.state, "rabbitmq", None)
    if not pool or not rabbitmq:
        return Response("Service Unavailable", status_code=503)
    request_id = getattr(request.state, "request_id", str(uuid4()))
    owner_id = (body.owner_id or "").strip()
    async with pool.acquire() as conn:
        mid = await create_message(conn, user_raw_text=body.message or "", owner_id=owner_id)
    channel = await rabbitmq.channel()
    await channel.set_qos(prefetch_count=1)
    queue = await channel.declare_queue(RAG_QUEUE, durable=True)
    await channel.default_exchange.publish(
        aio_pika.Message(body=json.dumps({"message_id": str(mid), "trace_id": request_id, "owner_id": owner_id}).encode()),
        routing_key=queue.name,
    )
    await channel.close()
    return Response(
        content=json.dumps({"message_id": str(mid)}),
        status_code=202,
        media_type="application/json",
    )


@app.get("/messages/{message_id}")
async def get_message_status(message_id: str):
    """Return message status and assistant text for polling after enqueue."""
    from uuid import UUID
    from fastapi import HTTPException
    pool = getattr(app.state, "pool", None)
    if pool is None:
        return Response("Service Unavailable", status_code=503)
    try:
        mid = UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid message_id")
    async with pool.acquire() as conn:
        row = await get_message(conn, mid)
    if row is None:
        raise HTTPException(status_code=404, detail="Message not found")
    # При эскалации ответ пользователю не отдаём (даже если в БД что-то осталось).
    status = row["status"]
    if status == "escalated":
        out = {
            "id": str(row["id"]),
            "status": status,
            "assistant_text": "",
            "assistant_text_translated": "",
        }
    else:
        out = {
            "id": str(row["id"]),
            "status": status,
            "assistant_text": row["assistant_text"] or "",
            "assistant_text_translated": row["assistant_text_translated"] or "",
        }
    for k in ("created_at", "updated_at"):
        if row.get(k) and hasattr(row[k], "isoformat"):
            out[k] = row[k].isoformat()
    return out


@app.delete("/internal/cache/by-owner/{owner_id}")
async def clear_cache_by_owner(owner_id: str):
    """Remove response cache entries for the given owner_id (e.g. e2e cleanup)."""
    delete_by_owner(owner_id)
    return {"ok": True}


_match_vacancy_lock: asyncio.Lock | None = None


def _get_match_vacancy_lock() -> asyncio.Lock:
    global _match_vacancy_lock
    if _match_vacancy_lock is None:
        _match_vacancy_lock = asyncio.Lock()
    return _match_vacancy_lock


class MatchVacancyBody(BaseModel):
    owner_id: str = ""
    vacancy_text: str = Field(..., min_length=1)
    summary: bool = False


class CoverLetterBody(BaseModel):
    owner_id: str = Field(..., min_length=1)
    vacancy_text: str = Field(..., min_length=1)


@app.post("/cover-letter")
async def cover_letter(request: Request, body: CoverLetterBody):
    """Generate Executive Summary (cover letter) for candidate owner_id from vacancy_text. RAG over owner docs."""
    lock = _get_match_vacancy_lock()
    async with lock:
        trace_id = getattr(request.state, "request_id", str(uuid4()))
        try:
            result = await run_cover_letter(
                owner_id=body.owner_id,
                vacancy_text=body.vacancy_text,
                trace_id=trace_id,
            )
        except Exception as e:
            logger.exception("cover_letter failed: %s", e)
            return Response(
                content=json.dumps({"owner_id": body.owner_id, "error": "internal_error"}),
                status_code=200,
                media_type="application/json",
            )
        return Response(
            content=json.dumps(result),
            status_code=200,
            media_type="application/json",
        )


@app.post("/match-vacancy")
async def match_vacancy(request: Request, body: MatchVacancyBody):
    """FLOW-03: Match candidate (owner_id) to vacancy; return relevance and optional summary. One at a time."""
    lock = _get_match_vacancy_lock()
    async with lock:
        trace_id = getattr(request.state, "request_id", str(uuid4()))
        try:
            results = await run_match_vacancy(
                owner_id=body.owner_id or "",
                vacancy_text=body.vacancy_text,
                include_summary=body.summary,
                trace_id=trace_id,
                top_k=RETRIEVAL_TOP_K,
            )
        except Exception as e:
            logger.exception("match_vacancy failed: %s", e)
            return Response(
                content=json.dumps([]),
                status_code=200,
                media_type="application/json",
            )
        return Response(
            content=json.dumps(results),
            status_code=200,
            media_type="application/json",
        )


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