"""Resume convert: chunked proxy to generation-service with structure prompt."""

import logging
import os
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resume", tags=["resume"])
GENERATION_SERVICE_URL = os.getenv("GENERATION_SERVICE_URL", "http://generation-service:8000")
GENERATE_MAX_CHUNK = int(os.getenv("GENERATE_MAX_CHUNK", "1000"))
RESUME_CONVERT_CHUNK_CHARS = int(os.getenv("RESUME_CONVERT_CHUNK_CHARS", "2500"))
RESUME_CONVERT_CHUNK_TIMEOUT = float(os.getenv("RESUME_CONVERT_CHUNK_TIMEOUT", "90.0"))

_DEFAULT_SYSTEM_PROMPT = """You are a resume structuring assistant. Reformat the given resume fragment into the strict template. Do not add, remove, or paraphrase factual content. Only reorganize and apply markup.

Hierarchy is defined ONLY by leading dash count (indentation and tabs are cosmetic and must not affect parsing):
- No leading dash = top-level element (e.g. a heading or block title).
- One dash "-" = level 1 (e.g. personal info block, experience block).
- Two dashes "--" = subsection (e.g. a company or major block).
- Three dashes "---" = sub-subsection (e.g. a project or role within a company).
- Four dashes "----" = list items or details under a project/role.
- A line with five or more consecutive dashes (e.g. "-----") is a section delimiter: general (above) vs experience (below).

Output format:
1. First block — general information (name, contacts, location, desired position, skills, languages).
2. Second block — work experience: each employer as "--", under each period/role then "---" projects, "----" for items.
3. Education, courses, certificates: same "-" / "--" nesting.
4. Preserve all original wording; only assign structure. Blank lines between major sections. No preamble or commentary.

Output only the restructured fragment, no preamble."""


def _load_system_prompt() -> str:
    path = os.getenv("RESUME_STRUCTURE_PROMPT_PATH", "").strip()
    if path:
        p = Path(path)
        if p.is_file():
            return p.read_text(encoding="utf-8", errors="replace")
    return _DEFAULT_SYSTEM_PROMPT


def _chunk_text(text: str, max_chars: int) -> list[str]:
    """Split text into chunks of at most max_chars, preferring line boundaries."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            last_newline = text.rfind("\n", start, end + 1)
            if last_newline > start:
                end = last_newline + 1
        chunks.append(text[start:end].strip())
        start = end
    return [c for c in chunks if c]


class ResumeConvertBody(BaseModel):
    raw_text: str = Field(..., min_length=1)


@router.post("/convert")
async def resume_convert(body: ResumeConvertBody):
    """Structure raw resume text via LLM in chunks; returns structured_text. Timeout applies per chunk."""
    system_prompt = _load_system_prompt()
    chunks_in = _chunk_text(body.raw_text, RESUME_CONVERT_CHUNK_CHARS)
    if not chunks_in:
        raise HTTPException(status_code=400, detail="empty_text")
    results: list[str] = []
    for i, fragment in enumerate(chunks_in):
        prompt = f"Restructure the following resume fragment (part {i + 1} of {len(chunks_in)}).\n\n{fragment}"
        try:
            async with httpx.AsyncClient(timeout=RESUME_CONVERT_CHUNK_TIMEOUT) as client:
                r = await client.post(
                    f"{GENERATION_SERVICE_URL.rstrip('/')}/generate",
                    json={
                        "prompt": prompt,
                        "system_prompt": system_prompt,
                        "max_tokens": GENERATE_MAX_CHUNK,
                    },
                )
        except httpx.TimeoutException:
            logger.warning("resume convert chunk %s timeout", i + 1)
            raise HTTPException(
                status_code=504,
                detail="resume_convert_chunk_timeout",
            )
        except Exception as e:
            logger.exception("resume convert chunk %s failed: %s", i + 1, e)
            raise HTTPException(status_code=502, detail="resume_convert_chunk_failed")
        if r.status_code != 200:
            detail = r.text
            try:
                d = r.json()
                detail = d.get("detail", d.get("failure_reason", r.text))
            except Exception:
                pass
            raise HTTPException(status_code=502, detail=str(detail)[:500])
        data = r.json()
        if not data.get("success"):
            raise HTTPException(
                status_code=502,
                detail=(data.get("failure_reason") or "resume_convert_chunk_failed")[:500],
            )
        out = (data.get("output") or "").strip()
        if not out:
            raise HTTPException(status_code=502, detail="resume_convert_chunk_empty")
        results.append(out)
    separator = "\n\n-----\n\n" if len(results) > 1 else "\n\n"
    structured = separator.join(results)
    return {"structured_text": structured}
