"""
Distributed tracing: structured logs for inter-service and external calls.

Log format: trace_id, service (caller), operation, target_service, target_resource.
target_resource: table (PG), collection (Qdrant), queue (RabbitMQ), model (Ollama), cache key pattern (Redis).
Used by all services; ensure X-Request-ID (trace_id) is propagated on HTTP and in message payloads.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Operation types for trace events
OP_HTTP_OUT = "http_out"
OP_HTTP_IN = "http_in"
OP_QUEUE_PUBLISH = "queue_publish"
OP_QUEUE_CONSUME = "queue_consume"
OP_DB_WRITE = "db_write"
OP_DB_READ = "db_read"
OP_VECTOR_WRITE = "vector_write"
OP_VECTOR_READ = "vector_read"
OP_CACHE_READ = "cache_read"
OP_CACHE_WRITE = "cache_write"
OP_LLM_CALL = "llm_call"


def log_trace(
    service_name: str,
    trace_id: str,
    operation: str,
    target_service: str,
    target_resource: Optional[str] = None,
    extra: Optional[dict] = None,
) -> None:
    """
    Emit a single trace log line for observability (Loki, grep by trace_id).

    Args:
        service_name: Caller service (e.g. api-gateway, ingestion-service).
        trace_id: Request/scenario id (X-Request-ID).
        operation: One of OP_* or custom (http_out, db_write, vector_write, queue_publish, llm_call, etc.).
        target_service: Downstream system (e.g. conversation-manager, postgres, qdrant, rabbitmq, ollama, redis).
        target_resource: Optional detail: table name, collection, queue name, model name, Redis key/db.
        extra: Optional dict of additional fields to log.
    """
    if not trace_id:
        trace_id = ""
    parts = [
        f"trace_id={trace_id}",
        f"service={service_name}",
        f"operation={operation}",
        f"target_service={target_service}",
    ]
    if target_resource:
        parts.append(f"target_resource={target_resource}")
    if extra:
        for k, v in sorted(extra.items()):
            if v is not None:
                parts.append(f"{k}={v}")
    msg = " | ".join(parts)
    logger.info(msg, extra={"trace_id": trace_id} if trace_id else None)
