"""Redis cache for completed RAG responses. Key: hash(normalized query)."""

import hashlib
import logging
from typing import Optional

import redis

from config import get_env
from shared.tracing import OP_CACHE_READ, OP_CACHE_WRITE, log_trace

logger = logging.getLogger(__name__)
SERVICE_NAME = "conversation-manager"

REDIS_URL = get_env("REDIS_URL", "redis://redis:6379/1")
CACHE_TTL = int(get_env("RESPONSE_CACHE_TTL_SEC", "3600"))
PREFIX = "rag:response:"
OWNER_KEYS_PREFIX = "rag:response:keys:"


_redis: Optional[redis.Redis] = None


def _get_redis() -> Optional[redis.Redis]:
    global _redis
    if _redis is None:
        try:
            _redis = redis.from_url(REDIS_URL, decode_responses=True)
            _redis.ping()
        except Exception as e:
            logger.warning("Response cache Redis unavailable: %s", e)
    return _redis


def _cache_key(normalized_query: str, owner_id: str = "") -> str:
    q = (normalized_query or "").strip().lower()
    o = (owner_id or "").strip()
    h = hashlib.sha256((o + ":" + q).encode("utf-8")).hexdigest()
    return f"{PREFIX}{h}"


def get_cached(normalized_query: str, trace_id: str = "", owner_id: str = "") -> Optional[str]:
    r = _get_redis()
    if not r:
        return None
    try:
        key = _cache_key(normalized_query, owner_id)
        val = r.get(key)
        log_trace(SERVICE_NAME, trace_id, OP_CACHE_READ, "redis", target_resource=key)
        return val
    except Exception as e:
        logger.debug("Response cache get failed: %s", e)
        return None


def set_cached(normalized_query: str, response: str, trace_id: str = "", owner_id: str = "") -> None:
    r = _get_redis()
    if not r:
        return
    try:
        key = _cache_key(normalized_query, owner_id)
        r.setex(key, CACHE_TTL, (response or "").strip())
        o = (owner_id or "").strip()
        if o:
            r.sadd(f"{OWNER_KEYS_PREFIX}{o}", key)
        log_trace(SERVICE_NAME, trace_id, OP_CACHE_WRITE, "redis", target_resource=key)
    except Exception as e:
        logger.debug("Response cache set failed: %s", e)


def delete_by_owner(owner_id: str) -> None:
    r = _get_redis()
    if not r:
        return
    o = (owner_id or "").strip()
    if not o:
        return
    try:
        set_key = f"{OWNER_KEYS_PREFIX}{o}"
        keys = r.smembers(set_key)
        if keys:
            r.delete(*keys)
        r.delete(set_key)
    except Exception as e:
        logger.debug("Response cache delete_by_owner failed: %s", e)
