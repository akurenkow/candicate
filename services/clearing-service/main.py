"""
Базовый модуль FastAPI-приложения для микросервиса.
Содержит общую инфраструктуру: middleware, health check, метрики, startup/shutdown.
"""

import logging
import os
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Histogram, generate_latest, REGISTRY
from prometheus_client import CONTENT_TYPE_LATEST

from shared.tracing import OP_HTTP_IN, log_trace

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
    Здесь инициализируем подключения к БД, очередям и т.д.
    """
    from shared.telemetry import bootstrap
    bootstrap(app)
    logger.info(f"Starting {SERVICE_NAME}...")
    # Пример: подключение к Redis (если используется)
    # app.state.redis = await aioredis.from_url(os.getenv("REDIS_URL"))
    # Подключение к БД, RabbitMQ и т.п.
    yield
    # Очистка ресурсов
    logger.info(f"Shutting down {SERVICE_NAME}...")
    # await app.state.redis.close()


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
        "clearing-service",
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


from routers.preprocess import router as preprocess_router
app.include_router(preprocess_router)
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