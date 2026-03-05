"""Resume convert: structural split (general + companies) or chunked proxy to generation-service."""

import logging
import os
import re
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

# Structural split: detect experience section (RU/EN) or date-range line
_EXPERIENCE_SECTION_START = re.compile(
    r"^(Опыт\s+работы|Experience|Work\s+experience|Трудовая\s+деятельность|Employment\s+history)\s*$",
    re.I,
)
_DATE_RANGE_LINE = re.compile(
    r"^(\d{4}\s+—|(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)[^\n]*\s+—\s+|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}\s+—)",
    re.I,
)

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


def _load_prompt(env_key: str, default_relative: str) -> str:
    path = os.getenv(env_key, "").strip()
    if path:
        p = Path(path)
        if p.is_file():
            return p.read_text(encoding="utf-8", errors="replace")
    resolved = Path(__file__).resolve()
    roots = []
    for i in range(1, 6):
        try:
            roots.append(resolved.parents[i])
        except IndexError:
            break
    roots.append(Path.cwd())
    for root in roots:
        p = root / "prompts" / default_relative
        if p.is_file():
            return p.read_text(encoding="utf-8", errors="replace")
    return ""


def _split_raw_general_experience(raw_text: str) -> tuple[str, str]:
    """Split raw resume into general (above experience) and experience sections by structure hints."""
    lines = raw_text.split("\n")
    general_lines: list[str] = []
    experience_start = -1
    min_pos_for_date = 300
    pos = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        pos += len(line) + 1
        if _EXPERIENCE_SECTION_START.match(stripped):
            experience_start = i
            break
        if pos >= min_pos_for_date and _DATE_RANGE_LINE.match(stripped):
            experience_start = i
            break
        general_lines.append(line)
    if experience_start < 0:
        return raw_text.strip(), ""
    experience_lines = lines[experience_start:]
    return "\n".join(general_lines).strip(), "\n".join(experience_lines).strip()


def _split_experience_into_company_blocks(experience_text: str) -> list[str]:
    """Split experience section into blocks by date-range line or company-like line (structural boundaries)."""
    if not experience_text.strip():
        return []
    lines = experience_text.split("\n")
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        stripped = line.strip()
        is_new_block = (
            _DATE_RANGE_LINE.match(stripped)
            or (stripped.startswith("-- ") and len(stripped) > 10 and not stripped[3:10].isdigit())
            or re.match(r"^-\s+(Компания|Company|Employer)\s*:", stripped, re.I)
        )
        if is_new_block and current:
            blocks.append(current)
            current = []
        current.append(line)
    if current:
        blocks.append(current)
    return ["\n".join(b).strip() for b in blocks if any(s.strip() for s in b)]


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


async def _call_generate(
    client: httpx.AsyncClient,
    prompt: str,
    system_prompt: str,
    max_tokens: int = GENERATE_MAX_CHUNK,
) -> str:
    r = await client.post(
        f"{GENERATION_SERVICE_URL.rstrip('/')}/generate",
        json={
            "prompt": prompt,
            "system_prompt": system_prompt,
            "max_tokens": max_tokens,
        },
    )
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
            detail=(data.get("failure_reason") or "resume_convert_failed")[:500],
        )
    out = (data.get("output") or "").strip()
    if not out:
        raise HTTPException(status_code=502, detail="resume_convert_empty")
    return out


class ResumeConvertBody(BaseModel):
    raw_text: str = Field(..., min_length=1)


@router.post("/convert")
async def resume_convert(body: ResumeConvertBody):
    """Structure raw resume: structural split (general + companies) then per-part convert; single '-----' separator."""
    raw_text = body.raw_text.strip()
    if not raw_text:
        raise HTTPException(status_code=400, detail="empty_text")

    general_raw, experience_raw = _split_raw_general_experience(raw_text)
    general_prompt_text = _load_prompt("RESUME_GENERAL_PROMPT_PATH", "resume-convert-general.txt")
    experience_prompt_text = _load_prompt("RESUME_EXPERIENCE_PROMPT_PATH", "resume-convert-experience.txt")
    use_structural = (
        bool(experience_raw.strip())
        and general_prompt_text
        and experience_prompt_text
    )

    async with httpx.AsyncClient(timeout=RESUME_CONVERT_CHUNK_TIMEOUT) as client:
        if use_structural:
            try:
                general_out = await _call_generate(
                    client,
                    f"Reformat the following general/personal section of a resume.\n\n{general_raw}",
                    general_prompt_text,
                )
                company_blocks = _split_experience_into_company_blocks(experience_raw)
                company_outputs: list[str] = []
                for i, block in enumerate(company_blocks):
                    out = await _call_generate(
                        client,
                        f"Reformat this company/employment block (block {i + 1} of {len(company_blocks)}).\n\n{block}",
                        experience_prompt_text,
                    )
                    company_outputs.append(out)
                structured = general_out + "\n-----\n" + "\n\n".join(company_outputs)
            except httpx.TimeoutException:
                logger.warning("resume convert structural timeout")
                raise HTTPException(status_code=504, detail="resume_convert_timeout")
            except HTTPException:
                raise
            except Exception as e:
                logger.exception("resume convert structural failed: %s", e)
                raise HTTPException(status_code=502, detail="resume_convert_failed")
        else:
            system_prompt = _load_system_prompt()
            chunks_in = _chunk_text(raw_text, RESUME_CONVERT_CHUNK_CHARS)
            if not chunks_in:
                raise HTTPException(status_code=400, detail="empty_text")
            results: list[str] = []
            for i, fragment in enumerate(chunks_in):
                prompt = f"Restructure the following resume fragment (part {i + 1} of {len(chunks_in)}).\n\n{fragment}"
                try:
                    out = await _call_generate(client, prompt, system_prompt)
                except httpx.TimeoutException:
                    logger.warning("resume convert chunk %s timeout", i + 1)
                    raise HTTPException(status_code=504, detail="resume_convert_chunk_timeout")
                except Exception as e:
                    logger.exception("resume convert chunk %s failed: %s", i + 1, e)
                    raise
                results.append(out)
            joined = "\n\n".join(results)
            if re.search(r"-{5,}", joined):
                joined = re.sub(r"\n*-{5,}[^\n]*\n*", "\n-----\n", joined, count=1)
                joined = re.sub(r"\n*-{5,}[^\n]*", "", joined)
            structured = joined.strip()

    return {"structured_text": structured}
