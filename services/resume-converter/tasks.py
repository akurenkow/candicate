"""Celery task: run resume conversion in dedicated worker (queue resume_convert)."""

import base64
import logging
import tempfile
from pathlib import Path

from celery_app import app
from converter_impl import convert_file

logger = logging.getLogger(__name__)


@app.task(bind=True, name="tasks.convert_resume_task")
def convert_resume_task(self, content_base64: str, filename: str) -> str:
    """Decode file, run converter (no LLM), return structured .txt string."""
    try:
        content = base64.b64decode(content_base64, validate=True)
    except Exception as e:
        logger.exception("Invalid base64: %s", e)
        raise
    suffix = Path(filename).suffix if filename else ".txt"
    if not suffix or suffix not in {".txt", ".pdf", ".rtf", ".doc", ".docx"}:
        suffix = ".txt"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(content)
        path = Path(f.name)
    try:
        return convert_file(path)
    finally:
        path.unlink(missing_ok=True)
