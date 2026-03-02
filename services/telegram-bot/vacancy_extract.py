"""Extract text from vacancy files: doc, docx, pdf, txt."""

import io
import logging

logger = logging.getLogger(__name__)

VACANCY_ALLOWED_EXT = frozenset({".doc", ".docx", ".pdf", ".txt"})
MAX_VACANCY_FILE_BYTES = 1 * 1024 * 1024  # 1MB


def _extract_txt(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


def _extract_pdf(raw: bytes) -> str | None:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        parts = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                parts.append(t)
        return "\n".join(parts) if parts else None
    except Exception as e:
        logger.warning("PDF extract failed: %s", e)
        return None


def _extract_docx(raw: bytes) -> str | None:
    try:
        from docx import Document

        doc = Document(io.BytesIO(raw))
        return "\n".join(p.text for p in doc.paragraphs if p.text)
    except Exception as e:
        logger.warning("DOCX extract failed: %s", e)
        return None


def _extract_doc(raw: bytes) -> str | None:
    try:
        import mammoth

        result = mammoth.extract_raw_text(io.BytesIO(raw))
        return result.value.strip() or None
    except Exception as e:
        logger.warning("DOC extract failed: %s", e)
        return None


def extract_vacancy_text(filename: str, raw: bytes) -> str | None:
    """Extract text from vacancy file. Returns None on failure."""
    fn = (filename or "").lower()
    if fn.endswith(".txt"):
        return _extract_txt(raw)
    if fn.endswith(".pdf"):
        return _extract_pdf(raw)
    if fn.endswith(".docx"):
        return _extract_docx(raw)
    if fn.endswith(".doc"):
        return _extract_doc(raw)
    return None


def is_vacancy_file(filename: str) -> bool:
    fn = (filename or "").lower()
    return any(fn.endswith(ext) for ext in VACANCY_ALLOWED_EXT)
