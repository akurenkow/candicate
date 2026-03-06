"""Resume converter service: POST /convert (file or raw_text) -> structured .txt via Celery."""

import asyncio
import base64
import logging
import time
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest, REGISTRY

from config import RESUME_CONVERT_TASK_TIMEOUT
from tasks import convert_resume_task

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

SERVICE_NAME = "resume-converter"
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "http_status"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "endpoint"],
)

app = FastAPI(title=f"{SERVICE_NAME} API", version="0.1.0")


ALLOWED_EXT = frozenset({".txt", ".pdf", ".doc", ".docx", ".rtf"})


@app.get("/health")
async def health():
    return {"status": "healthy", "service": SERVICE_NAME, "timestamp": time.time()}


@app.get("/metrics")
async def metrics():
    return PlainTextResponse(
        generate_latest(REGISTRY),
        media_type=CONTENT_TYPE_LATEST,
    )


@app.post("/convert")
async def convert(file: UploadFile = File(...)):
    """Accept resume file (.txt, .pdf, .doc, .rtf), return structured .txt body (no LLM)."""
    filename = file.filename or "resume.txt"
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format. Use one of: {', '.join(ALLOWED_EXT)}",
        )
    try:
        content = await file.read()
    except Exception as e:
        logger.exception("Read upload failed: %s", e)
        raise HTTPException(status_code=400, detail="Failed to read file")
    content_b64 = base64.b64encode(content).decode("ascii")
    task = convert_resume_task.delay(content_b64, filename)

    def get_result():
        return task.get(timeout=RESUME_CONVERT_TASK_TIMEOUT)

    try:
        result = await asyncio.get_event_loop().run_in_executor(None, get_result)
    except Exception as e:
        logger.exception("Convert task failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)[:500])
    return {"structured_text": result}


@app.post("/convert/raw")
async def convert_raw(body: dict):
    """Accept JSON { \"raw_text\": \"...\" }, return structured .txt (for backward compat)."""
    raw_text = (body.get("raw_text") or "").strip()
    if not raw_text:
        raise HTTPException(status_code=400, detail="empty_text")
    from converter_impl import convert_raw_text
    try:
        result = convert_raw_text(raw_text)
    except Exception as e:
        logger.exception("Convert raw failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)[:500])
    return {"structured_text": result}