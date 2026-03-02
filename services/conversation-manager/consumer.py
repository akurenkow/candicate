"""Queue consumer: acquire lock, preprocess (normalize + validate), then process or block."""

import json
import logging
import time
from datetime import datetime, timezone
from uuid import UUID

import aio_pika
from aio_pika import IncomingMessage

import asyncpg

from config import RAG_QUEUE, FALLBACK_RESPONSE, NO_DOCUMENTS_STUB_RESPONSE, LOW_RELEVANCE_STUB_RESPONSE, MODEL_WORKING_LANGUAGE
from db import get_message, update_status, create_pool
from lock import try_acquire_lock, release_lock, set_processing, release_stale_locks
from state_machine import STATUS_LOCKED, STATUS_PROCESSING, STATUS_COMPLETED, STATUS_ESCALATED
from clearing_client import preprocess as clearing_preprocess
from lang_detect import detect as detect_lang
from translation_client import translate as translate_query
from query_decomposition import decompose, Subtask
from subtask_retrieval import run_retrieval
from subtask_result import SubtaskResult, aggregate_results, FAILED_RETRIEVAL, FAILED_LOW_SCORE, FAILED_NO_USABLE_TEXT, FAILED_GENERATION
from subtask_manager import SubtaskManager
from post_process import post_process
from response_cache import get_cached, set_cached

from shared.tracing import OP_QUEUE_CONSUME, log_trace
from shared.telemetry import record_rag_pipeline_duration

logger = logging.getLogger(__name__)
SERVICE_NAME = "conversation-manager"


def _single_subtask(query: str, parent_message_id: UUID) -> list[Subtask]:
    from uuid import uuid4
    return [Subtask(subtask_id=uuid4(), query=query.strip(), parent_message_id=parent_message_id, index=0)]


async def _process_subtask(
    pool: asyncpg.Pool,
    subtask: Subtask,
    lifecycle: dict,
) -> SubtaskResult:
    """Retrieval + generation. Returns failed_retrieval when no documents found."""
    trace_id = lifecycle.get("trace_id", "")
    owner_id = (lifecycle.get("owner_id") or "").strip()
    return await run_retrieval(subtask, trace_id=trace_id, owner_id=owner_id)


async def _finalize_lifecycle(
    conn: asyncpg.Connection,
    message_id: UUID,
    worker_id: str,
    from_status: str,
    to_status: str,
    assistant_text: str,
    assistant_text_translated: str | None,
    *,
    error_message: str | None = None,
    failed_service: str | None = None,
) -> None:
    """Persist assistant output, update status, release lock. Idempotent; safe to call once."""
    rows = await update_status(
        conn,
        message_id,
        from_status,
        to_status,
        assistant_text=assistant_text,
        assistant_text_translated=assistant_text_translated,
        error_message=error_message,
        failed_service=failed_service,
    )
    if rows > 0:
        await conn.execute(
            "UPDATE rag_messages SET processing_lock = NULL, locked_at = NULL, updated_at = $1 WHERE id = $2",
            datetime.now(timezone.utc),
            message_id,
        )


async def process_one(
    pool: asyncpg.Pool,
    message_id: UUID,
    worker_id: str,
    trace_id: str = "",
) -> None:
    """Acquire lock, preprocess; if BLOCK -> completed with fallback; else processing -> completed, release."""
    async with pool.acquire() as conn:
        acquired = await try_acquire_lock(conn, message_id, worker_id)
        if not acquired:
            return
        row = await get_message(conn, message_id)
        if not row:
            await release_lock(conn, message_id, worker_id, back_to_queued=True)
            return
        raw = row.get("user_raw_text") or ""
        owner_id = (row.get("owner_id") or "").strip()

    preprocess_result = await clearing_preprocess(raw, trace_id=trace_id)

    if preprocess_result.result == "BLOCK":
        async with pool.acquire() as conn:
            await update_status(
                conn,
                message_id,
                STATUS_LOCKED,
                STATUS_COMPLETED,
                user_normalized_text=preprocess_result.normalized,
                assistant_text=FALLBACK_RESPONSE,
            )
            await conn.execute(
                "UPDATE rag_messages SET processing_lock = NULL, locked_at = NULL, updated_at = $1 WHERE id = $2",
                datetime.now(timezone.utc),
                message_id,
            )
        return

    normalized = preprocess_result.normalized
    detected_lang = detect_lang(normalized)
    if detected_lang != MODEL_WORKING_LANGUAGE:
        trans_result = await translate_query(
            normalized, detected_lang, MODEL_WORKING_LANGUAGE, trace_id=trace_id
        )
        working_query = trans_result.translated_text if trans_result.error is None else normalized
    else:
        working_query = normalized

    async with pool.acquire() as conn:
        ok = await set_processing(conn, message_id, worker_id)
        if not ok:
            await release_lock(conn, message_id, worker_id, back_to_queued=True)
            return
        now = datetime.now(timezone.utc)
        await conn.execute(
            """UPDATE rag_messages SET user_normalized_text = $1, working_query = $2, detected_lang = $3, updated_at = $4 WHERE id = $5""",
            normalized,
            working_query,
            detected_lang,
            now,
            message_id,
        )

    cached_raw = get_cached(working_query, trace_id=trace_id, owner_id=owner_id)
    if cached_raw is not None:
        assistant_text_raw = cached_raw
        from_cache = True
        should_escalate = False
        results = []
    else:
        subtasks = decompose(working_query, message_id)
        if not subtasks:
            subtasks = _single_subtask(working_query, message_id)
        lifecycle = {"message_id": message_id, "trace_id": trace_id, "worker_id": worker_id, "owner_id": owner_id}
        results: list[SubtaskResult] = []
        t0 = time.perf_counter()
        for st in subtasks:
            results.append(await _process_subtask(pool, st, lifecycle))
        assistant_text_raw, should_escalate = aggregate_results(results, FALLBACK_RESPONSE)
        record_rag_pipeline_duration(time.perf_counter() - t0, "query", SERVICE_NAME)
        from_cache = False

    pp = await post_process(assistant_text_raw, detected_lang, trace_id=trace_id)
    should_escalate = should_escalate or pp.translation_failed

    _MAX_ERR_REASON = 500
    err_msg: str | None = None
    fail_svc: str | None = None
    if should_escalate:
        if pp.translation_failed:
            reason = (pp.translation_failure_reason or "unknown")[: _MAX_ERR_REASON]
            err_msg = f"response_translation_failed:{reason}"
            fail_svc = "translation-service"
        else:
            kinds = [r.kind for r in results]
            if all(k == FAILED_RETRIEVAL for k in kinds):
                err_msg, fail_svc = "no_documents_found", "retrieval-service"
            elif all(k == FAILED_NO_USABLE_TEXT for k in kinds):
                err_msg, fail_svc = "chunks_no_usable_text", "retrieval-service"
            elif all(k == FAILED_GENERATION for k in kinds):
                err_msg, fail_svc = "generation_failed", "generation-service"
            else:
                err_msg = "subtask_failures:" + ",".join(sorted(set(kinds)))
                fail_svc = "conversation-manager"

    if not from_cache and pp.stored and pp.stored != FALLBACK_RESPONSE:
        set_cached(working_query, pp.stored, trace_id=trace_id, owner_id=owner_id)

    # Нет документов по owner_id — не эскалируем, возвращаем заглушку «сначала загрузите резюме».
    no_documents_stub = (
        should_escalate
        and err_msg == "no_documents_found"
        and fail_svc == "retrieval-service"
    )
    low_relevance_stub = (
        should_escalate
        and bool(results)
        and all(r.kind == FAILED_LOW_SCORE for r in results)
    )
    if no_documents_stub:
        should_escalate = False
        err_msg = None
        fail_svc = None
        final_assistant_text = NO_DOCUMENTS_STUB_RESPONSE
        final_assistant_text_translated = None
    elif low_relevance_stub:
        should_escalate = False
        err_msg = None
        fail_svc = None
        final_assistant_text = LOW_RELEVANCE_STUB_RESPONSE
        final_assistant_text_translated = None
    elif should_escalate and pp.translation_failed:
        final_assistant_text = ""
        final_assistant_text_translated = None
    else:
        final_assistant_text = pp.stored
        # При эскалации из-за retrieval/generation fallback не переводим — в БД только stored.
        final_assistant_text_translated = None if assistant_text_raw == FALLBACK_RESPONSE else pp.translated

    async with pool.acquire() as conn:
        to_status = STATUS_ESCALATED if should_escalate else STATUS_COMPLETED
        await _finalize_lifecycle(
            conn,
            message_id,
            worker_id,
            STATUS_PROCESSING,
            to_status,
            final_assistant_text,
            final_assistant_text_translated,
            error_message=err_msg,
            failed_service=fail_svc,
        )


async def recover_stale_locks(pool: asyncpg.Pool) -> None:
    """Call on startup or periodically to release expired locks."""
    async with pool.acquire() as conn:
        await release_stale_locks(conn)


async def on_message(
    message: IncomingMessage,
    pool: asyncpg.Pool,
    worker_id: str,
) -> None:
    try:
        body = json.loads(message.body)
        mid_str = body.get("message_id")
        trace_id = body.get("trace_id", "")
    except Exception:
        mid_str = None
        trace_id = ""
    log_trace(SERVICE_NAME, trace_id, OP_QUEUE_CONSUME, "rabbitmq", target_resource=RAG_QUEUE)
    if not mid_str:
        await message.ack()
        return
    try:
        message_id = UUID(mid_str)
    except (ValueError, TypeError):
        await message.ack()
        return
    try:
        await process_one(pool, message_id, worker_id, trace_id=trace_id)
    except Exception as e:
        logger.exception("process_one failed: %s", e)
        async with pool.acquire() as conn:
            await release_lock(conn, message_id, worker_id, back_to_queued=True)
        await message.nack(requeue=True)
        return
    await message.ack()


async def start_consumer(
    connection: aio_pika.Connection,
    pool: asyncpg.Pool,
    worker_id: str,
) -> None:
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=1)
    queue = await channel.declare_queue(RAG_QUEUE, durable=True)

    async def callback(msg: IncomingMessage) -> None:
        await on_message(msg, pool, worker_id)

    await queue.consume(callback)
