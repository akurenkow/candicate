"""
Derive flat chunks with metadata from a Resume tree.
Metadata: section_kind, company_name, date_range, project_name, project_domain, role (from hierarchy only; no document-text patterns).
Blocks longer than max_chunk_size are split with sentence-aware chunking (OLLAMA_CHUNK_MAX_SIZE limit).
"""
from __future__ import annotations

from typing import Optional

from shared.company_normalize import normalize_company_name
from .dto import Resume


def _emit_block(
    result: list[tuple[str, dict]],
    text: str,
    meta: dict,
    max_chunk_size: Optional[int],
    chunk_overlap: Optional[int],
) -> None:
    if not text.strip():
        return
    if max_chunk_size and chunk_overlap is not None and len(text) > max_chunk_size:
        from chunking import chunk_text_sentence_aware
        for sub in chunk_text_sentence_aware(text, chunk_size=max_chunk_size, overlap=chunk_overlap):
            if sub.strip():
                result.append((sub, dict(meta)))
    else:
        result.append((text, dict(meta)))


def chunks_from_semantic_resume(
    resume: Resume,
    *,
    include_metadata: bool = True,
    split_technologies: bool = True,
    max_chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
) -> list[tuple[str, dict]]:
    """
    Flatten resume into (text, metadata) pairs for embedding.
    Metadata keys: section_kind (general | company | project | experience), company_name, date_range, 
                   project_name, project_domain, role.
    If max_chunk_size is set, blocks longer than it are split with sentence-aware chunking (chunk_overlap).
    split_technologies is ignored (no pattern-based tech splitting).
    """
    result: list[tuple[str, dict]] = []

    for block in resume.general.content_blocks:
        meta = {"section_kind": "general"} if include_metadata else {}
        _emit_block(result, block.text, meta, max_chunk_size, chunk_overlap)

    for company in resume.companies:
        base_meta: dict = {}
        if include_metadata:
            is_fallback = company.metadata.get("fallback") == "true"
            base_meta = {
                "section_kind": "experience" if is_fallback else "company",
                "company_name": normalize_company_name(company.metadata.get("company_name", "")),
                "date_range": company.metadata.get("date_range", ""),
            }

        for block in company.experience_descriptions:
            _emit_block(result, block.text, dict(base_meta), max_chunk_size, chunk_overlap)

        fallback_domain = ""
        for p in company.projects:
            d = (p.metadata.get("project_domain") or "").strip()
            if d:
                fallback_domain = d
                break
        if not fallback_domain:
            for p in company.projects:
                n = (p.metadata.get("project_name") or "").strip()
                if n:
                    fallback_domain = n
                    break
        for project in company.projects:
            proj_meta = dict(base_meta) if include_metadata else {}
            if include_metadata:
                proj_meta["section_kind"] = "project"
                proj_meta["project_name"] = project.metadata.get("project_name", "")
                proj_meta["project_domain"] = (project.metadata.get("project_domain") or "").strip() or fallback_domain
                proj_meta["role"] = project.metadata.get("role", "")

            for block in project.content_blocks:
                _emit_block(result, block.text, dict(proj_meta), max_chunk_size, chunk_overlap)

    return result
