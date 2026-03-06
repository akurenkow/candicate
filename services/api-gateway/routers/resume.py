"""Resume convert: proxy to resume-converter service (file or raw_text -> structured .txt)."""

import logging
import os

import httpx
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resume", tags=["resume"])
RESUME_CONVERTER_URL = os.getenv("RESUME_CONVERTER_URL", "http://resume-converter:8000")
RESUME_CONVERT_TIMEOUT = float(os.getenv("RESUME_CONVERT_TIMEOUT", "620.0"))


class ResumeConvertBody(BaseModel):
    raw_text: str = Field(..., min_length=1)


@router.post("/convert")
async def resume_convert(request: Request, file: UploadFile = File(None)):
    """Structure resume: multipart file -> resume-converter /convert; or form/JSON raw_text -> /convert/raw. Returns { structured_text }."""
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type == "application/json" and not file:
        body = await request.json()
        raw_text = (body.get("raw_text") or "").strip()
        if not raw_text:
            raise HTTPException(status_code=400, detail="empty_text")
        try:
            async with httpx.AsyncClient(timeout=RESUME_CONVERT_TIMEOUT) as client:
                r = await client.post(
                    f"{RESUME_CONVERTER_URL.rstrip('/')}/convert/raw",
                    json={"raw_text": raw_text},
                )
        except httpx.TimeoutException:
            logger.warning("resume convert raw timeout")
            raise HTTPException(status_code=504, detail="resume_convert_timeout")
        except Exception as e:
            logger.exception("resume convert raw error: %s", e)
            raise HTTPException(status_code=502, detail="resume_convert_failed")
        if r.status_code != 200:
            try:
                detail = r.json().get("detail", r.text)
            except Exception:
                detail = r.text
            raise HTTPException(status_code=r.status_code, detail=str(detail)[:500])
        return r.json()

    if file and file.filename:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="empty_file")
        try:
            async with httpx.AsyncClient(timeout=RESUME_CONVERT_TIMEOUT) as client:
                r = await client.post(
                    f"{RESUME_CONVERTER_URL.rstrip('/')}/convert",
                    files={"file": (file.filename, content)},
                )
        except httpx.TimeoutException:
            logger.warning("resume convert timeout")
            raise HTTPException(status_code=504, detail="resume_convert_timeout")
        except Exception as e:
            logger.exception("resume convert error: %s", e)
            raise HTTPException(status_code=502, detail="resume_convert_failed")
        if r.status_code != 200:
            try:
                detail = r.json().get("detail", r.text)
            except Exception:
                detail = r.text
            raise HTTPException(status_code=r.status_code, detail=str(detail)[:500])
        return r.json()

    raise HTTPException(status_code=400, detail="Provide file (multipart) or JSON body with raw_text")