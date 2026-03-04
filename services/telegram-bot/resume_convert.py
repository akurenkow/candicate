"""Resume parser/converter: extract text from doc/rtf/pdf/txt, structure via LLM (hh.ru → internal format)."""

import io
import logging
from dataclasses import dataclass

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


async def structure_resume_via_api(base_url: str, raw_text: str) -> ConvertResult:
    """POST raw_text to BASE_API_URL/resume/convert, return structured text."""
    url = f"{base_url.rstrip('/')}/resume/convert"
    try:
        import httpx
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(url, json={"raw_text": raw_text})
        if r.status_code != 200:
            try:
                d = r.json()
                err = d.get("detail", r.text)
            except Exception:
                err = r.text or str(r.status_code)
            return ConvertResult(success=False, structured_text="", error=str(err)[:500])
        data = r.json()
        out = (data.get("structured_text") or "").strip()
        if not out:
            return ConvertResult(success=False, structured_text="", error="empty_response")
        return ConvertResult(success=True, structured_text=out)
    except Exception as e:
        logger.exception("resume/convert request failed: %s", e)
        return ConvertResult(success=False, structured_text="", error=str(e))
