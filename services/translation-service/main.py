"""
Translation service: FastAPI app with /translate, health, metrics.
Uses Ollama for LLM translation, Redis for cache.
Consumes translation_tasks from RabbitMQ and delegates to Celery.
"""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from uuid import uuid4

import aio_pika
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Histogram, generate_latest, REGISTRY
from prometheus_client import CONTENT_TYPE_LATEST

from config import RABBITMQ_URL
from consumer import start_consumer
from routers.translate import router as translate_router
from shared.tracing import OP_HTTP_IN, log_trace

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

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

SERVICE_NAME = os.getenv("SERVICE_NAME", "translation-service")
DEBUG = os.getenv("DEBUG", "false").lower() == "true"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s...", SERVICE_NAME)
    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    consumer_task = asyncio.create_task(start_consumer(connection))
    yield
    logger.info("Shutting down %s...", SERVICE_NAME)
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass
    await connection.close()


app = FastAPI(
    title=f"{SERVICE_NAME} API",
    version="0.1.0",
    lifespan=lifespan,
    debug=DEBUG,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid4()))
    request.state.request_id = request_id
    log_trace(
        SERVICE_NAME,
        request_id,
        OP_HTTP_IN,
        "translation-service",
        target_resource=request.url.path,
    )
    logger.info("Request %s started: %s %s", request_id, request.method, request.url.path)
    start_time = time.time()
    try:
        response = await call_next(request)
    except Exception as e:
        logger.exception("Request %s failed: %s", request_id, e)
        response = Response("Internal Server Error", status_code=500)
    process_time = time.time() - start_time
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time"] = str(process_time)
    REQUEST_COUNT.labels(
        method=request.method,
        endpoint=request.url.path,
        http_status=response.status_code,
    ).inc()
    REQUEST_LATENCY.labels(method=request.method, endpoint=request.url.path).observe(
        process_time
    )
    logger.info("Request %s finished: status=%s time=%.3fs", request_id, response.status_code, process_time)
    return response


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": SERVICE_NAME,
        "timestamp": time.time(),
    }


@app.get("/metrics")
async def metrics():
    return Response(
        content=generate_latest(REGISTRY),
        media_type=CONTENT_TYPE_LATEST,
    )


app.include_router(translate_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=DEBUG,
    )
