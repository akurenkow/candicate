"""
Semantic resume ingestion: parse resume text into a hierarchical tree,
validate structure, and derive chunks with context for embedding.
Composable; does not replace existing chunking flow.
"""

from .chunks import chunks_from_semantic_resume
from .dto import (
    CompanyExperience,
    ContentBlock,
    GeneralSection,
    Project,
    Resume,
)
from .parser import build_resume_tree, split_resume_sections, split_experience_by_companies
from .validation import (
    ValidationError,
    validate_resume,
    validate_resume_strict,
)

__all__ = [
    "build_resume_tree",
    "split_resume_sections",
    "split_experience_by_companies",
    "chunks_from_semantic_resume",
    "CompanyExperience",
    "ContentBlock",
    "GeneralSection",
    "Project",
    "Resume",
    "ValidationError",
    "validate_resume",
    "validate_resume_strict",
]
