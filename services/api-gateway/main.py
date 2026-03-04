"""
API Gateway: single entry point, validation, routing (FLOW-02 upload → RabbitMQ).
"""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import aio_pika
from fastapi import FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Histogram, generate_latest, REGISTRY
from prometheus_client import CONTENT_TYPE_LATEST

import httpx
from pydantic import BaseModel, Field
from fastapi import HTTPException

from routers import documents, resume
from shared.tracing import OP_HTTP_IN, log_trace

ENV_ALLOWED_UPLOAD_USER_IDS = "ALLOWED_UPLOAD_USER_IDS"
ENV_ALLOW_E2E_UPLOAD = "ALLOW_E2E_UPLOAD"


def _get_allowed_owner_ids() -> tuple[frozenset[str], list[str]]:
    raw = os.getenv(ENV_ALLOWED_UPLOAD_USER_IDS, "").strip()
    if not raw:
        return frozenset(), []
    ids = [s.strip() for s in raw.split(",") if s.strip()]
    return frozenset(ids), ids

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
    """Connect to RabbitMQ for document upload publishing (CTR-03)."""
    from shared.telemetry import bootstrap
    bootstrap(app)
    logger.info("Starting %s...", SERVICE_NAME)
    rabbitmq_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
    max_attempts = int(os.getenv("RABBITMQ_CONNECT_ATTEMPTS", "5"))
    retry_delay_sec = float(os.getenv("RABBITMQ_CONNECT_RETRY_DELAY", "3"))

    for attempt in range(1, max_attempts + 1):
        try:
            connection = await aio_pika.connect_robust(rabbitmq_url)
            app.state.rabbitmq = connection
            logger.info("RabbitMQ connected on attempt %s", attempt)
            break
        except Exception as e:
            logger.warning(
                "RabbitMQ connection attempt %s/%s failed: %s",
                attempt,
                max_attempts,
                e,
            )
            app.state.rabbitmq = None
            if attempt < max_attempts:
                await asyncio.sleep(retry_delay_sec)
            else:
                logger.error(
                    "RabbitMQ unavailable after %s attempts. Upload endpoint will return 503.",
                    max_attempts,
                )
    yield
    logger.info("Shutting down %s...", SERVICE_NAME)
    if getattr(app.state, "rabbitmq", None):
        await app.state.rabbitmq.close()


# Создание экземпляра FastAPI
app = FastAPI(
    title=f"{SERVICE_NAME} API",
    version="0.1.0",
    lifespan=lifespan,
    debug=DEBUG,
)


def _custom_openapi():
    from fastapi.openapi.utils import get_openapi
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        description=app.description,
        routes=app.routes,
    )
    owner_enum = _owner_id_enum()
    paths = openapi_schema.get("paths") or {}
    for path, path_item in paths.items():
        if path != "/chat":
            continue
        post_op = (path_item or {}).get("post")
        if not post_op:
            continue
        params = post_op.setdefault("parameters", [])
        owner_param = next((p for p in params if isinstance(p, dict) and p.get("name") == "owner_id"), None)
        if owner_param:
            owner_param.setdefault("schema", {})["enum"] = owner_enum
        else:
            params.insert(0, {
                "in": "query",
                "name": "owner_id",
                "required": True,
                "description": "Candidate (resume owner) context for the answer",
                "schema": {"type": "string", "enum": owner_enum},
            })
        rb = post_op.get("requestBody", {}).get("content", {})
        for ct, media in rb.items():
            sch = media.get("schema")
            if sch and "$ref" in sch:
                comp = openapi_schema.setdefault("components", {}).setdefault("schemas", {})
                ref_name = sch["$ref"].split("/")[-1]
                if ref_name in comp:
                    comp[ref_name].get("properties", {}).pop("owner_id", None)
            break
        break
    return openapi_schema


app.openapi = _custom_openapi

# CORS: из env CORS_ORIGINS (через запятую) или "*" по умолчанию.
# При origins=["*"] allow_credentials должен быть False (требование CORS).
_cors_origins_raw = os.getenv("CORS_ORIGINS", "*").strip()
if _cors_origins_raw and _cors_origins_raw != "*":
    _cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]
    _cors_allow_credentials = True
else:
    _cors_origins = ["*"]
    _cors_allow_credentials = False
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Process-Time"],
)


@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    """Middleware для замера времени выполнения и логирования."""
    # Генерация или получение trace_id
    request_id = request.headers.get("X-Request-ID", str(uuid4()))
    request.state.request_id = request_id

    # Трассировка: вход в API Gateway
    log_trace(
        SERVICE_NAME,
        request_id,
        OP_HTTP_IN,
        "api-gateway",
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


CONVERSATION_MANAGER_URL = os.getenv("CONVERSATION_MANAGER_URL", "http://conversation-manager:8000")


def _owner_id_enum():
    _, ids = _get_allowed_owner_ids()
    return ids if ids else ["unknown"]


class ChatBody(BaseModel):
    message: str = Field(..., description="User message")


@app.post("/chat")
async def chat(
    request: Request,
    body: ChatBody,
    owner_id: str = Query(..., description="Candidate (resume owner) context for the answer"),
):
    """FLOW-01: forward user message to pipeline in context of owner_id (candidate)."""
    allowed_set, _ = _get_allowed_owner_ids()
    owner_id_str = (owner_id or "").strip()
    e2e_ok = os.getenv(ENV_ALLOW_E2E_UPLOAD, "").strip().lower() in ("1", "true", "yes") and owner_id_str
    if e2e_ok and not owner_id_str.startswith("test_e2e_"):
        try:
            UUID(owner_id_str)
            e2e_ok = True
        except (ValueError, TypeError):
            e2e_ok = False
    elif e2e_ok and owner_id_str.startswith("test_e2e_"):
        e2e_ok = True
    if allowed_set and (not owner_id_str or (owner_id_str not in allowed_set and not e2e_ok)):
        raise HTTPException(status_code=403, detail="owner_id not allowed")
    request_id = getattr(request.state, "request_id", str(uuid4()))
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{CONVERSATION_MANAGER_URL.rstrip('/')}/enqueue",
            json={"message": body.message or "", "owner_id": owner_id_str},
            headers={"X-Request-ID": request_id},
        )
    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type="application/json",
    )


@app.get("/messages/{message_id}")
async def get_message_status(request: Request, message_id: str):
    """Proxy to conversation-manager for message polling."""
    request_id = getattr(request.state, "request_id", str(uuid4()))
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"{CONVERSATION_MANAGER_URL.rstrip('/')}/messages/{message_id}",
            headers={"X-Request-ID": request_id},
        )
    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type="application/json",
    )


class MatchVacancyBody(BaseModel):
    owner_id: str = ""
    vacancy_text: str = Field(..., min_length=1)
    summary: bool = False
    target_lang: str = "en"
    target_lang: str = "en"


class CoverLetterBody(BaseModel):
    owner_id: str = Field(..., min_length=1)
    vacancy_text: str = Field(..., min_length=1)


@app.post("/cover-letter")
async def cover_letter(request: Request, body: CoverLetterBody):
    """Proxy to conversation-manager: generate Executive Summary for candidate from vacancy."""
    request_id = getattr(request.state, "request_id", str(uuid4()))
    async with httpx.AsyncClient(timeout=90.0) as client:
        r = await client.post(
            f"{CONVERSATION_MANAGER_URL.rstrip('/')}/cover-letter",
            json={"owner_id": body.owner_id, "vacancy_text": body.vacancy_text},
            headers={"X-Request-ID": request_id},
        )
    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type="application/json",
    )


@app.post("/match-vacancy")
async def match_vacancy(request: Request, body: MatchVacancyBody):
    """FLOW-03: Proxy to conversation-manager for vacancy match (one at a time)."""
    request_id = getattr(request.state, "request_id", str(uuid4()))
    async with httpx.AsyncClient(timeout=90.0) as client:
        r = await client.post(
            f"{CONVERSATION_MANAGER_URL.rstrip('/')}/match-vacancy",
            json={
                "owner_id": body.owner_id or "",
                "vacancy_text": body.vacancy_text,
                "summary": body.summary,
                "target_lang": body.target_lang or "en",
            },
            headers={"X-Request-ID": request_id},
        )
    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type="application/json",
    )


app.include_router(documents.router)
app.include_router(resume.router)


if __name__ == "__main__":
    # Только для локального запуска (не используется в Docker)
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=DEBUG,
    )