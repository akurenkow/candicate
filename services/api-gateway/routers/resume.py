"""Resume convert: proxy to generation-service with structure prompt."""

import logging
import os
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resume", tags=["resume"])
GENERATION_SERVICE_URL = os.getenv("GENERATION_SERVICE_URL", "http://generation-service:8000")

_DEFAULT_SYSTEM_PROMPT = """You are a resume structuring assistant. Your task is to reformat the given resume text into a strict template. Do not add, remove, or paraphrase any factual content. Only reorganize and apply the markup below.

Output format rules:
1. Use exactly the following hierarchy with leading dashes:
   - One dash "-" for top-level sections (e.g. personal info block, experience block).
   - Two dashes "--" for subsection (e.g. a company or a major block inside a section).
   - Three dashes "---" for sub-subsections (e.g. a project or role within a company).
   - Four dashes "----" for list items or details under a project/role.

2. First block — general information (metadata):
   - Full name, contacts (phone, email), location if present.
   - Desired position / title, salary/relocation if mentioned.
   - Key skills, technologies, languages (as a structured list with "-" / "--" nesting).

3. Second block — work experience:
   - Each employer as a subsection "--".
   - Under each employer: period, role; then one or more projects as "---".
   - Under each project: "----" for responsibilities, achievements, stack.

4. Education, courses, certificates: use the same "-" / "--" nesting as appropriate.

5. Preserve all original wording; only assign structure. Use blank lines between major sections. No extra commentary.

Output only the restructured resume text, no preamble."""


def _load_system_prompt() -> str:
    path = os.getenv("RESUME_STRUCTURE_PROMPT_PATH", "").strip()
    if path:
        p = Path(path)
        if p.is_file():
            return p.read_text(encoding="utf-8", errors="replace")
    return _DEFAULT_SYSTEM_PROMPT


class ResumeConvertBody(BaseModel):
    raw_text: str = Field(..., min_length=1)


@router.post("/convert")
async def resume_convert(body: ResumeConvertBody):
    """Structure raw resume text via LLM; returns structured_text."""
    system_prompt = _load_system_prompt()
    prompt = f"Restructure the following resume.\n\n{body.raw_text}"
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(
                f"{GENERATION_SERVICE_URL.rstrip('/')}/generate",
                json={
                    "prompt": prompt,
                    "system_prompt": system_prompt,
                },
            )
    except Exception as e:
        logger.exception("resume convert generation call failed: %s", e)
        raise HTTPException(status_code=502, detail="generation_unavailable")
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
            detail=(data.get("failure_reason") or "generation_failed")[:500],
        )
    out = (data.get("output") or "").strip()
    if not out:
        raise HTTPException(status_code=502, detail="empty_output")
    return {"structured_text": out}
