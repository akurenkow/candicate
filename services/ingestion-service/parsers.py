"""
Extract plain text from uploaded file bytes by content type.
Supports: PDF, DOCX, TXT, MD (plan FLOW-02).
"""

import logging
from io import BytesIO

logger = logging.getLogger(__name__)


def extract_text(content_type: str, raw: bytes) -> str:
    """
    Extract text from raw file bytes.
    Raises ValueError for unsupported type or parse error.
    """
    ct = (content_type or "").strip().lower()
    if "pdf" in ct:
        return _extract_pdf(raw)
    if "wordprocessingml" in ct or "docx" in ct:
        return _extract_docx(raw)
    if "plain" in ct or "text" in ct or "utf-8" in ct or ct in ("txt", "md", "markdown"):
        return raw.decode("utf-8", errors="replace")
    if ct in ("txt", "md", "markdown"):
        return raw.decode("utf-8", errors="replace")
    raise ValueError(f"Unsupported content type: {content_type}")


def _extract_pdf(raw: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(BytesIO(raw))
    parts = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception as e:
            logger.warning("PDF page extract error: %s", e)
    return "\n\n".join(parts)


def _extract_docx(raw: bytes) -> str:
    from docx import Document
    doc = Document(BytesIO(raw))
    return "\n\n".join(p.text for p in doc.paragraphs)
