"""Resume parser/converter: extract text from doc/rtf/pdf/txt, structure via LLM (hh.ru → internal format)."""

import io
import logging
import os
from dataclasses import dataclass

import httpx
from vacancy_extract import extract_vacancy_text, is_vacancy_file

logger = logging.getLogger(__name__)

RESUME_CONVERT_EXT = frozenset({".doc", ".docx", ".rtf", ".pdf", ".txt"})
MAX_RESUME_CONVERT_BYTES = 2 * 1024 * 1024  # 2MB


def _extract_rtf(raw: bytes) -> str | None:
    try:
        from striprtf.striprtf import rtf_to_text
        return rtf_to_text(raw.decode("utf-8", errors="replace")).strip() or None
    except Exception as e:
        logger.warning("RTF extract failed: %s", e)
        return None


def extract_resume_text(filename: str, raw: bytes) -> str | None:
    """Extract plain text from resume file. Returns None on failure."""
    fn = (filename or "").lower()
    if fn.endswith(".rtf"):
        return _extract_rtf(raw)
    return extract_vacancy_text(filename, raw)


def is_resume_convert_file(filename: str) -> bool:
    fn = (filename or "").lower()
    return any(fn.endswith(ext) for ext in RESUME_CONVERT_EXT)


@dataclass
class ConvertResult:
    success: bool
    structured_text: str
    error: str | None = None


RESUME_CONVERT_REQUEST_TIMEOUT = float(os.getenv("RESUME_CONVERT_REQUEST_TIMEOUT", "600.0"))


async def structure_resume_via_api(base_url: str, raw_text: str) -> ConvertResult:
    """POST raw_text to BASE_API_URL/resume/convert (chunked on gateway). Timeout is for the whole request."""
    url = f"{base_url.rstrip('/')}/resume/convert"
    try:
        async with httpx.AsyncClient(timeout=RESUME_CONVERT_REQUEST_TIMEOUT) as client:
            r = await client.post(url, json={"raw_text": raw_text})
        if r.status_code != 200:
            try:
                d = r.json()
                err = d.get("detail", r.text)
            except Exception:
                err = r.text or str(r.status_code)
            err_str = str(err)[:500] if not isinstance(err, str) else err[:500]
            if r.status_code == 504:
                err_str = "resume_convert_chunk_timeout"
            return ConvertResult(success=False, structured_text="", error=err_str)
        data = r.json()
        out = (data.get("structured_text") or "").strip()
        if not out:
            return ConvertResult(success=False, structured_text="", error="empty_response")
        return ConvertResult(success=True, structured_text=out)
    except httpx.TimeoutException as e:
        logger.warning("resume/convert request timeout: %s", e)
        return ConvertResult(success=False, structured_text="", error="resume_convert_chunk_timeout")
    except Exception as e:
        logger.exception("resume/convert request failed: %s", e)
        return ConvertResult(success=False, structured_text="", error=str(e))
