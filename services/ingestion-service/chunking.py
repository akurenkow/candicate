"""
Split text into overlapping chunks (FLOW-02).
Uses OLLAMA_CHUNK_MAX_SIZE and OLLAMA_CHUNK_OVERLAP from config.
Sentence-boundary chunking avoids cutting words or sentences.
"""

import re
from typing import Optional

from config import CHUNK_OVERLAP, CHUNK_SIZE  # CHUNK_* are aliases for OLLAMA_CHUNK_*

# Split on sentence boundaries: . ! ? or newline (keep delimiter with preceding text)
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?\n])\s+|\n+")


def _sentences(text: str) -> list[str]:
    """Split text into sentences (by . ! ? and newlines). Never splits mid-word."""
    text = text.strip()
    if not text:
        return []
    parts = _SENTENCE_BOUNDARY.split(text)
    return [p.strip() for p in parts if p.strip()]


def chunk_text(
    text: str,
    chunk_size: Optional[int] = None,
    overlap: Optional[int] = None,
) -> list[str]:
    """
    Split text into chunks by character count with overlap.
    Returns list of non-empty strings. May cut mid-sentence (legacy).
    """
    size = chunk_size if chunk_size is not None else CHUNK_SIZE
    ov = overlap if overlap is not None else CHUNK_OVERLAP
    if size <= 0:
        return [text] if text.strip() else []
    chunks: list[str] = []
    start = 0
    text = text.strip()
    if not text:
        return []
    while start < len(text):
        end = start + size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        start = end - ov
        if start >= len(text):
            break
    return chunks


def chunk_text_sentence_aware(
    text: str,
    chunk_size: Optional[int] = None,
    overlap: Optional[int] = None,
) -> list[str]:
    """
    Split text into chunks at sentence boundaries. Never cuts in the middle
    of a word or sentence. Uses overlap in characters; overlap is realized
    by re-including whole sentences from the previous chunk.
    Returns list of non-empty strings.
    """
    size = chunk_size if chunk_size is not None else CHUNK_SIZE
    ov = overlap if overlap is not None else CHUNK_OVERLAP
    if size <= 0:
        return [text] if text.strip() else []
    text = text.strip()
    if not text:
        return []
    sentences = _sentences(text)
    if not sentences:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    overlap_sentences: list[str] = []

    for sent in sentences:
        sent_len = len(sent) + 1  # +1 for space
        if current_len + sent_len <= size:
            current.append(sent)
            current_len += sent_len
            if ov > 0:
                overlap_sentences.append(sent)
                while overlap_sentences and sum(len(s) + 1 for s in overlap_sentences) > ov:
                    overlap_sentences.pop(0)
            continue
        if current:
            chunk = " ".join(current)
            if chunk.strip():
                chunks.append(chunk)
            if ov > 0 and overlap_sentences:
                current = list(overlap_sentences)
                current_len = sum(len(s) + 1 for s in current) - 1
            else:
                current = []
                current_len = 0
            overlap_sentences = []
        if sent_len > size:
            chunks.append(sent)
            continue
        current = [sent]
        current_len = sent_len
        if ov > 0:
            overlap_sentences = [sent]

    if current:
        chunk = " ".join(current)
        if chunk.strip():
            chunks.append(chunk)
    return chunks
