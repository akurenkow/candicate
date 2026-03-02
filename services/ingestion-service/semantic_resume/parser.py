"""
Parse resume text into hierarchical structure.

Rules:
- Document split by delimiter line: a line containing at least 5 consecutive dashes (e.g. "-----").
  Everything above = general; everything below = experience.
- Experience section: hierarchy by leading dashes (level 1 = company, level 2 = period or project, level 3+ = content).
- Metadata: level-1 = company_name, first level-2 = date_range, further level-2 = project_name (by hierarchy).
- Label-aware override: when line matches "Period:", "Company:", "Project:" (or RU equivalents), extract value
  into the correct metadata field regardless of hierarchy. Lines matching "Position:", "Technologies Used:" etc.
  are treated as content, not metadata.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from .dto import (
    CompanyExperience,
    ContentBlock,
    GeneralSection,
    Project,
    Resume,
)
from shared.company_normalize import normalize_company_name

MIN_DELIMITER_DASHES = 5

# Label patterns for metadata extraction (key: value). Order matters for first match.
# date_range = period of work; company_name = company; project_name = project name; 
# project_domain = project subject area/domain; role = position/role.
_LABEL_PATTERNS: list[tuple[re.Pattern, Literal["date_range", "company_name", "project_name", "project_domain", "role"]]] = [
    (re.compile(r"^(?:period|даты?|период|date\s*range)\s*:\s*(.+)$", re.I), "date_range"),
    (re.compile(r"^(?:company|компания|employer|организация|работодатель)\s*:\s*(.+)$", re.I), "company_name"),
    # Project Name (with optional #N suffix) - goes to project_name
    (re.compile(r"^(?:project\s+name(?:\s*#?\s*\d+)?|проект\s+название(?:\s*#?\s*\d+)?)\s*:\s*(.+)$", re.I), "project_name"),
    # Project Subject Area / Domain / Area - goes to project_domain
    (re.compile(r"^(?:project\s+(?:subject\s+)?(?:area|domain)|предметная\s+область\s+проекта)\s*:\s*(.+)$", re.I), "project_domain"),
    # Position / Role - goes to role (incl. duty = translation of должность)
    (re.compile(r"^(?:position|должность|role|роль|duty)\s*:\s*(.+)$", re.I), "role"),
    # Fallback: "Project:" without "Name" - goes to project_name (for backward compatibility)
    (re.compile(r"^(?:project|проект)(?:\s*#?\s*\d+)?\s*:\s*(.+)$", re.I), "project_name"),
]

# Labels that must NOT be used as metadata (treat as content)
_CONTENT_ONLY_LABELS = re.compile(
    r"^(?:technologies\s*used|используемые\s*технологии|tech\s*stack)\s*:"
    r"|^(?:responsibilities?|обязанности|задачи|duties|achievements?|достижения)\s*:"
    r"|^(?:responsibilities?\s+and\s+achievements?|goals\s+and\s+achievements|targets\s+and\s+achievements)\s*:",
    re.I,
)


def _looks_like_date_range(text: str) -> bool:
    """Heuristic: does text look like a date range (contains year digits, dash/en-dash, month names)?"""
    text_lower = text.lower()
    # Check for year (4 digits) or month names (EN/RU)
    has_year = bool(re.search(r"\b(19|20)\d{2}\b", text))
    has_month = bool(re.search(
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december|"
        r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
        r"январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)\b",
        text_lower
    ))
    has_dash = bool(re.search(r"[-–—]", text))  # hyphen, en-dash, em-dash
    has_present = "present" in text_lower or "настоящ" in text_lower or "по н.в." in text_lower
    return (has_year or has_month) and (has_dash or has_present)


def _looks_like_content_not_company(text: str) -> bool:
    """True if line is likely a responsibility/achievement sentence, not a company name."""
    t = (text or "").strip()
    if len(t) <= 30:
        return False
    if t.endswith(".") and len(t) > 40:
        return True
    if "," in t and len(t) > 50:
        return True
    if len(t) > 80:
        return True
    return False


def _parse_labeled_field(content: str) -> tuple[Literal["date_range", "company_name", "project_name", "project_domain", "role", "_content"] | None, str]:
    """
    If content matches "Label: value", return (field_key, value).
    - ("date_range"|"company_name"|"project_name"|"project_domain"|"role", value): assign to that metadata field.
    - ("_content", content): labeled but not metadata (Technologies Used, Responsibilities etc.) — do NOT assign to metadata.
    - (None, content): no label — use hierarchy-based assignment.
    """
    content = content.strip()
    if not content:
        return None, content
    if _CONTENT_ONLY_LABELS.match(content):
        return "_content", content  # Do not assign to metadata
    for pattern, field in _LABEL_PATTERNS:
        m = pattern.match(content)
        if m:
            return field, m.group(1).strip()
    return None, content


def _dash_level(line: str) -> int:
    """
    Number of leading dashes (after optional whitespace).
    "- " -> 1, "-- " -> 2, "--- " -> 3, "---- " -> 4. No dash -> 0.
    """
    stripped = line.lstrip()
    if not stripped or stripped[0] != "-":
        return 0
    count = 0
    for c in stripped:
        if c != "-":
            break
        count += 1
    return count


def _line_content(line: str, level: int) -> str:
    """Strip leading dashes and one optional space; return rest of line."""
    stripped = line.lstrip()
    if level > 0:
        removed = 0
        i = 0
        while i < len(stripped) and removed < level and stripped[i] == "-":
            i += 1
            removed += 1
        if i < len(stripped) and stripped[i] == " ":
            i += 1
        return stripped[i:].strip()
    return stripped.strip()


def _is_experience_delimiter_line(line: str) -> bool:
    """True if this line contains at least 5 consecutive dashes (delimiter for experience section)."""
    return bool(re.search(r"-{" + str(MIN_DELIMITER_DASHES) + r",}", line))


def _split_general_and_experience(text: str) -> tuple[str, str]:
    """
    Split document into general (above delimiter) and experience (below).
    Delimiter = first line that contains at least 5 consecutive dashes.
    """
    lines = text.split("\n")
    general_lines: list[str] = []
    experience_lines: list[str] = []
    found = False
    for line in lines:
        if not found and _is_experience_delimiter_line(line):
            found = True
            continue
        if found:
            experience_lines.append(line)
        else:
            general_lines.append(line)
    general_text = "\n".join(general_lines).strip()
    experience_text = "\n".join(experience_lines).strip()
    return general_text, experience_text


def split_resume_sections(text: str) -> tuple[str, str]:
    """
    Public: split document into general (above 5+dash delimiter) and experience (below).
    Use when translating sections separately so both parts are preserved in documents.content.
    """
    return _split_general_and_experience(text)


def split_experience_by_companies(experience_text: str) -> list[str]:
    """
    Split experience section into separate company blocks (each starting with level-1 dash).
    Returns list of company text blocks. Use for translating each company separately to avoid LLM context limits.
    """
    if not experience_text.strip():
        return []
    
    lines = experience_text.split("\n")
    company_blocks: list[list[str]] = []
    current_block: list[str] = []
    
    for line in lines:
        level = _dash_level(line)
        if level == 1:
            # Start of new company - save previous block and start new one
            if current_block:
                company_blocks.append(current_block)
            current_block = [line]
        else:
            # Continue current block
            current_block.append(line)
    
    # Don't forget the last block
    if current_block:
        company_blocks.append(current_block)
    
    return ["\n".join(block).strip() for block in company_blocks if block]


@dataclass
class _ParseState:
    """Mutable state during experience parse."""

    companies: list[CompanyExperience] = field(default_factory=list)
    current_company: CompanyExperience | None = None
    current_project: Project | None = None
    current_content_buffer: list[str] = field(default_factory=list)
    company_date_set: bool = False  # True after we set date_range from first level-2
    pending_project_metadata: dict[str, str] = field(default_factory=dict)  # Accumulate project metadata

    def flush_content(self) -> ContentBlock | None:
        if not self.current_content_buffer:
            return None
        text = "\n".join(self.current_content_buffer).strip()
        self.current_content_buffer.clear()
        return ContentBlock(text=text) if text else None

    def flush_to_project(self) -> None:
        block = self.flush_content()
        if block and self.current_project is not None:
            self.current_project.content_blocks.append(block)
        elif block and self.current_company is not None:
            self.current_company.experience_descriptions.append(block)

    def flush_to_company_experience(self) -> None:
        block = self.flush_content()
        if block and self.current_company is not None:
            self.current_company.experience_descriptions.append(block)

    def materialize_pending_project(self) -> None:
        """Create project from pending metadata if any metadata was collected."""
        if self.pending_project_metadata:
            if self.current_project is not None:
                self.finish_project()
            # Role comes from company metadata (applies to all projects in company)
            role = self.current_company.metadata.get("role", "") if self.current_company else ""
            self.current_project = Project(
                metadata={
                    "project_name": self.pending_project_metadata.get("project_name", ""),
                    "project_domain": self.pending_project_metadata.get("project_domain", ""),
                    "role": role,
                },
                content_blocks=[],
            )
            self.pending_project_metadata.clear()

    def finish_project(self) -> None:
        self.flush_to_project()
        if self.current_project is not None and self.current_company is not None:
            self.current_company.projects.append(self.current_project)
        self.current_project = None

    def finish_company(self) -> None:
        self.materialize_pending_project()
        self.finish_project()
        self.flush_to_company_experience()
        if self.current_company is not None:
            self.companies.append(self.current_company)
        self.current_company = None
        self.company_date_set = False
        self.pending_project_metadata.clear()


def _ensure_company(state: _ParseState) -> None:
    """If no current company (continuation part cut mid-company), create one with empty metadata."""
    if state.current_company is not None:
        return
    state.current_company = CompanyExperience(
        metadata={"company_name": "", "date_range": ""},
        projects=[],
        experience_descriptions=[],
    )
    state.company_date_set = False


def _parse_experience_section(experience_text: str) -> list[CompanyExperience]:
    """
    Parse experience by "-" hierarchy. L1 = company, L2 = date/project, L3+ = content.
    If experience does not start with L1 (part cut mid-company), create one company and fill metadata from L2.
    """
    if not experience_text.strip():
        return []
    state = _ParseState()
    lines = experience_text.split("\n")

    for raw_line in lines:
        level = _dash_level(raw_line)
        content = _line_content(raw_line, level)

        if level == 0:
            if content or raw_line.rstrip():
                state.current_content_buffer.append(raw_line.rstrip())
            continue

        if level == 1:
            raw_content = content or raw_line.strip()
            label_field, label_value = _parse_labeled_field(raw_content)
            if (
                label_field is None
                and state.current_company is not None
                and _looks_like_content_not_company(raw_content)
            ):
                state.flush_to_project()
                state.current_content_buffer.append(raw_line.rstrip())
                continue
            state.finish_company()
            company_name_val = ""
            date_range_val = ""
            if label_field == "company_name":
                company_name_val = normalize_company_name(label_value)
            elif label_field == "date_range":
                date_range_val = label_value
            elif label_field == "_content" or label_field == "project_name":
                pass
            else:
                company_name_val = normalize_company_name(raw_content)
            state.current_company = CompanyExperience(
                metadata={"company_name": company_name_val, "date_range": date_range_val},
                projects=[],
                experience_descriptions=[],
            )
            if label_field == "_content":
                state.current_content_buffer.append(raw_line.rstrip())
            state.current_project = None
            state.company_date_set = False
            continue

        if level >= 2:
            _ensure_company(state)

        if level == 2:
            state.flush_to_project()
            state.flush_to_company_experience()
            raw_content = content or raw_line.strip()
            label_field, label_value = _parse_labeled_field(raw_content)

            if not state.company_date_set:
                if label_field == "date_range":
                    state.current_company.metadata["date_range"] = label_value
                elif label_field == "company_name":
                    state.current_company.metadata["company_name"] = normalize_company_name(label_value)
                elif label_field == "role":
                    state.current_company.metadata["role"] = label_value
                elif label_field == "_content":
                    state.current_content_buffer.append(raw_line.rstrip())
                elif label_field is None:
                    if _looks_like_date_range(raw_content):
                        state.current_company.metadata["date_range"] = raw_content
                    else:
                        if not state.current_company.metadata.get("company_name"):
                            state.current_company.metadata["company_name"] = normalize_company_name(raw_content)
                        else:
                            state.current_company.metadata["date_range"] = raw_content
                state.company_date_set = True
            else:
                if label_field == "project_name":
                    state.pending_project_metadata["project_name"] = label_value
                elif label_field == "project_domain":
                    state.pending_project_metadata["project_domain"] = label_value
                elif label_field == "role":
                    state.current_company.metadata["role"] = label_value
                elif label_field == "_content":
                    state.materialize_pending_project()
                    if state.current_project is None:
                        role = state.current_company.metadata.get("role", "")
                        state.current_project = Project(
                            metadata={"project_name": "", "project_domain": "", "role": role},
                            content_blocks=[],
                        )
                    state.current_content_buffer.append(raw_line.rstrip())
                elif label_field in ("date_range", "company_name"):
                    state.materialize_pending_project()
                    state.finish_project()
                    role = state.current_company.metadata.get("role", "")
                    state.current_project = Project(
                        metadata={"project_name": "", "project_domain": "", "role": role},
                        content_blocks=[],
                    )
                    state.current_content_buffer.append(raw_line.rstrip())
                else:
                    state.materialize_pending_project()
                    state.finish_project()
                    role = state.current_company.metadata.get("role", "")
                    state.current_project = Project(
                        metadata={"project_name": raw_content, "project_domain": "", "role": role},
                        content_blocks=[],
                    )
            continue

        if level >= 3:
            _ensure_company(state)
            raw_content = content or raw_line.strip()
            label_field, label_value = _parse_labeled_field(raw_content)
            if label_field == "project_name":
                state.materialize_pending_project()
                state.finish_project()
                role = state.current_company.metadata.get("role", "") if state.current_company else ""
                state.current_project = Project(
                    metadata={"project_name": label_value, "project_domain": "", "role": role},
                    content_blocks=[],
                )
                continue
            elif label_field == "project_domain":
                if state.current_project is not None:
                    state.current_project.metadata["project_domain"] = label_value
                else:
                    state.pending_project_metadata["project_domain"] = label_value
                continue
            elif label_field == "role":
                if state.current_company is not None:
                    state.current_company.metadata["role"] = label_value
                    if state.current_project is not None:
                        state.current_project.metadata["role"] = label_value
                continue
            if state.pending_project_metadata and state.current_project is None:
                state.materialize_pending_project()
            block = state.flush_content()
            if block and state.current_project is not None:
                state.current_project.content_blocks.append(block)
            elif block and state.current_company is not None:
                state.current_company.experience_descriptions.append(block)
            if content or raw_line.rstrip():
                state.current_content_buffer.append(
                    content if content else raw_line.rstrip()
                )

    state.finish_company()
    return state.companies


def build_resume_tree(text: str) -> Resume:
    """
    Parse resume into general + experience.
    Delimiter = first line with 5+ consecutive dashes.
    General = paragraphs above delimiter. Experience = hierarchy below (level 1 = company, first level-2 = period, further level-2 = project).
    """
    general_text, experience_text = _split_general_and_experience(text)
    general_blocks = [
        ContentBlock(text=paragraph.strip())
        for paragraph in general_text.split("\n\n")
        if paragraph.strip()
    ]
    companies = _parse_experience_section(experience_text)
    if experience_text.strip() and not companies:
        fallback_blocks = [
            ContentBlock(text=paragraph.strip())
            for paragraph in experience_text.split("\n\n")
            if paragraph.strip()
        ]
        if fallback_blocks:
            companies = [
                CompanyExperience(
                    metadata={"company_name": "", "date_range": "", "fallback": "true"},
                    projects=[],
                    experience_descriptions=fallback_blocks,
                )
            ]
    return Resume(
        general=GeneralSection(content_blocks=general_blocks),
        companies=companies,
    )
