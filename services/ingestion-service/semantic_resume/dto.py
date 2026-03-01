"""
Serialization-friendly DTOs for semantic resume structure.
JSON-like; suitable for downstream embedding and chunking.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ContentBlock:
    """A single preserved text block (exact copy)."""

    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text}


@dataclass
class Project:
    """Project subsection under a company."""

    metadata: dict[str, str]  # at least "project_name"
    content_blocks: list[ContentBlock] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": dict(self.metadata),
            "content_blocks": [b.to_dict() for b in self.content_blocks],
        }


@dataclass
class CompanyExperience:
    """One company experience section."""

    metadata: dict[str, str]  # "company_name", "date_range"
    projects: list[Project] = field(default_factory=list)
    experience_descriptions: list[ContentBlock] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": dict(self.metadata),
            "projects": [p.to_dict() for p in self.projects],
            "experience_descriptions": [b.to_dict() for b in self.experience_descriptions],
        }


@dataclass
class GeneralSection:
    """Single general information section (one logical block)."""

    content_blocks: list[ContentBlock] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"content_blocks": [b.to_dict() for b in self.content_blocks]}


@dataclass
class Resume:
    """Root semantic resume: general section + company experiences."""

    general: GeneralSection = field(default_factory=GeneralSection)
    companies: list[CompanyExperience] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "general": self.general.to_dict(),
            "companies": [c.to_dict() for c in self.companies],
        }
