"""Redis cache for translation results."""

import hashlib
import json
import logging
from typing import Optional

import redis

from config import CACHE_TTL_SEC, ENABLE_CACHE, REDIS_URL

logger = logging.getLogger(__name__)

_redis: Optional[redis.Redis] = None


def _get_redis() -> Optional[redis.Redis]:
    global _redis
    if _redis is None and ENABLE_CACHE:
        try:
            _redis = redis.from_url(REDIS_URL, decode_responses=True)
            _redis.ping()
        except Exception as e:
            logger.warning("Redis cache unavailable: %s", e)
    return _redis


def _cache_key(text: str, source_lang: str, target_lang: str, model: str) -> str:
    h = hashlib.sha256(f"{text}|{source_lang}|{target_lang}|{model}".encode("utf-8")).hexdigest()
    return f"translation:{h}"


def get_cached(text: str, source_lang: str, target_lang: str, model: str) -> Optional[str]:
    if not ENABLE_CACHE:
        return None
    r = _get_redis()
    if not r:
        return None
    try:
        key = _cache_key(text, source_lang, target_lang, model)
        val = r.get(key)
        return val
    except Exception as e:
        logger.debug("Cache get failed: %s", e)
        return None


def set_cached(
    text: str, source_lang: str, target_lang: str, model: str, result: str
) -> None:
    if not ENABLE_CACHE:
        return
    r = _get_redis()
    if not r:
        return
    try:
        key = _cache_key(text, source_lang, target_lang, model)
        r.setex(key, CACHE_TTL_SEC, result)
    except Exception as e:
        logger.debug("Cache set failed: %s", e)
