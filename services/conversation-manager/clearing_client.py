"""Client for Clearing Service: normalize + validate. Returns normalized text and OK/BLOCK with reasons."""

import logging
from dataclasses import dataclass
from typing import List

import httpx

from config import CLEARING_SERVICE_URL

logger = logging.getLogger(__name__)

PREPROCESS_TIMEOUT = 10.0


@dataclass
class PreprocessResult:
    normalized: str
    result: str  # "OK" | "BLOCK"
    reasons: List[str]


async def preprocess(text: str, trace_id: str = "") -> PreprocessResult:
    """Call Clearing Service /preprocess. On failure returns BLOCK with single reason."""
    url = f"{CLEARING_SERVICE_URL.rstrip('/')}/preprocess"
    headers = {"X-Request-ID": trace_id} if trace_id else {}
    try:
        async with httpx.AsyncClient(timeout=PREPROCESS_TIMEOUT) as client:
            r = await client.post(url, json={"text": text or ""}, headers=headers)
            r.raise_for_status()
            data = r.json()
            return PreprocessResult(
                normalized=data.get("normalized", ""),
                result=data.get("validation_result", "BLOCK"),
                reasons=list(data.get("reasons", [])),
            )
    except Exception as e:
        logger.warning("clearing preprocess failed: %s", e)
        return PreprocessResult(
            normalized=text or "",
            result="BLOCK",
            reasons=["preprocess_unavailable"],
        )
