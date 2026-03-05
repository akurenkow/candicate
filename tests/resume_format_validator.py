"""
Validate structured resume text against the target format (converter output).

Rules enforced:
- Exactly one separator line of 5 dashes "-----" (and only 5) between general and experience.
- General: top-level blocks with "-" (level 1); in Education/Skills "--" (level 2) allowed;
  main blocks separated by blank line; no skipped nesting levels.
- Experience: L1 = company, L2 = period/position/project (or for multi-project: position/period, then L3 = projects, L4 = achievements).
"""

from __future__ import annotations

import re
from typing import List


# Allowed general main blocks (labels that may appear at L1)
GENERAL_MAIN_LABELS = [
    "ФИО", "Пол", "Дата рождения", "Телефон", "Email", "Образование", "Навыки",
    "Дополнительная информация (Обо мне)", "Желаемые должности", "Желаемая зарплата",
    "Причина ухода с последнего места работы", "Что хочет от нового места работы",
    "С чем не хочет работать", "С чем хочет работать", "Ключевые навыки",
    "Основная информация", "Специализации",
    # EN variants
    "Full name", "Education", "Skills", "Desired positions", "Desired salary",
    "Key skills", "Additional information", "About me",
]


def _dash_level(line: str) -> int:
    """Number of leading dashes (after optional whitespace)."""
    stripped = line.lstrip()
    if not stripped or stripped[0] != "-":
        return 0
    count = 0
    for c in stripped:
        if c != "-":
            break
        count += 1
    return count


def _is_five_dash_separator(line: str) -> bool:
    """True if line is the section separator: exactly 5 dashes, optionally followed by label (e.g. Experience:)."""
    s = line.strip()
    if not s or len(s) < 5:
        return False
    if s == "-----":
        return True
    return bool(re.match(r"^-{5}([^-]|$)", s))


def _find_five_dash_lines(text: str) -> List[tuple[int, str]]:
    """Return list of (line_index_0based, line_content) for lines that are the 5-dash separator."""
    result: List[tuple[int, str]] = []
    for i, line in enumerate(text.split("\n")):
        if _is_five_dash_separator(line):
            result.append((i, line.strip()))
    return result


def validate_resume_structured_format(text: str) -> List[str]:
    """
    Validate resume text against the target converter format.
    Returns list of error messages (empty if valid).
    """
    errors: List[str] = []
    lines = text.split("\n")
    if not lines:
        errors.append("Empty document")
        return errors

    # --- 1. Exactly one separator of 5 dashes between general and experience ---
    five_dash = _find_five_dash_lines(text)
    if len(five_dash) == 0:
        errors.append("Missing separator: exactly one line '-----' between general and experience")
        return errors
    if len(five_dash) > 1:
        errors.append(
            "Separator must appear only once: found {} lines with 5+ dashes (expected 1). Use exactly '-----' to separate general and experience.".format(
                len(five_dash)
            )
        )
    # Require separator to be exactly 5 dashes (not 6+)
    _, first_sep = five_dash[0]
    if re.match(r"^-{6,}", first_sep):
        errors.append("Separator must be exactly 5 dashes '-----', not more")

    # Split into general and experience
    delim_idx = five_dash[0][0]
    general_lines = [lines[i] for i in range(delim_idx) if i < len(lines)]
    experience_start = delim_idx + 1
    experience_lines = [lines[i] for i in range(experience_start, len(lines))] if experience_start < len(lines) else []

    general_text = "\n".join(general_lines).strip()
    experience_text = "\n".join(experience_lines).strip()

    # --- 2. General: no level-0 content lines (all content under "-" blocks); blank line between main "-" blocks ---
    gen_lines = general_text.split("\n")
    prev_level = -1
    i = 0
    while i < len(gen_lines):
        line = gen_lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        level = _dash_level(line)
        if level == 0:
            errors.append("General section: all items must have at least level-1 nesting ('-'). Found top-level line: '{}'".format(stripped[:60]))
        if level == 1 and prev_level == 1 and i > 0:
            # Check for blank line between L1 blocks (recommended)
            prev_line = gen_lines[i - 1].strip()
            if prev_line and not prev_line.startswith("-"):
                pass  # already error
            # Allow consecutive "-" blocks; blank between is recommendation, not hard error
        prev_level = level
        i += 1

    # --- 3. Experience: L1 = company (must start with "- " and contain company-like label or name) ---
    if experience_text:
        exp_lines = experience_text.split("\n")
        seen_l1 = False
        for line in exp_lines:
            s = line.strip()
            if not s:
                continue
            lvl = _dash_level(line)
            if lvl == 1:
                seen_l1 = True
                if not s.startswith("-"):
                    errors.append("Experience: level-1 must start with single dash (company line)")
            if lvl > 4:
                errors.append("Experience: max nesting is 4 levels (----). Found level {}: '{}'".format(lvl, s[:50]))

    return errors
