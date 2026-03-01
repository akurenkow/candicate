"""Security validator: profanity and prompt-injection checks. Returns OK/BLOCK with reasons."""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import FrozenSet, List, Pattern, Tuple

class ValidationResult(str, Enum):
    OK = "OK"
    BLOCK = "BLOCK"


@dataclass(frozen=True)
class ValidationOutcome:
    result: ValidationResult
    reasons: Tuple[str, ...] = ()

    def is_ok(self) -> bool:
        return self.result == ValidationResult.OK


# Minimal profanity blocklist (lowercase); extend via config in real deployments
PROFANITY_BLOCKLIST: FrozenSet[str] = frozenset({
    "fuck", "shit", "asshole", "damn", "crap", "bastard", "bitch", "dick", "cock",
    "pussy", "whore", "slut", "nigger", "nigga", "faggot", "retard", "cunt",
})

# Prompt-injection patterns: instruction override, role override, ignore previous
INJECTION_PATTERNS: List[Tuple[Pattern[str], str]] = [
    (re.compile(r"ignore\s+(all\s+)?(previous|above|prior)\s+instructions?", re.I), "ignore_previous_instructions"),
    (re.compile(r"disregard\s+(all\s+)?(previous|above)", re.I), "disregard_previous"),
    (re.compile(r"you\s+are\s+now\s+(a|an)\s+", re.I), "role_override"),
    (re.compile(r"pretend\s+you\s+are\s+", re.I), "pretend_role"),
    (re.compile(r"act\s+as\s+(a|an)\s+", re.I), "act_as"),
    (re.compile(r"system\s*:\s*", re.I), "system_prompt_leak"),
    (re.compile(r"<\|(?:im_start|im_end|system|user|assistant)\|>", re.I), "chat_template_marker"),
    (re.compile(r"\[INST\]|\[/INST\]|<<SYS>>|<<\/SYS>>", re.I), "instruction_marker"),
    (re.compile(r"(\badmin\s*:\s*|\broot\s*:\s*|\bsudo\s+)", re.I), "privilege_escape"),
    (re.compile(r"repeat\s+(the\s+)?(above|following|this)\s+(text|message|word)\s*(forever|infinitely)?", re.I), "repeat_attack"),
    (re.compile(r"output\s+(only\s+)?(the\s+)?(raw\s+)?(content|text)\s+above", re.I), "output_raw_override"),
]


def _check_profanity(normalized: str) -> List[str]:
    reasons: List[str] = []
    words = re.findall(r"[a-zA-Z0-9']+", normalized.lower())
    for w in words:
        if w in PROFANITY_BLOCKLIST:
            reasons.append("profanity")
            break
    return reasons


def _check_injection(normalized: str) -> List[str]:
    reasons: List[str] = []
    seen: set[str] = set()
    for pat, label in INJECTION_PATTERNS:
        if pat.search(normalized) and label not in seen:
            seen.add(label)
            reasons.append(f"prompt_injection:{label}")
    return reasons


def validate(normalized: str) -> ValidationOutcome:
    """Check normalized text for profanity and prompt-injection. Explicit OK/BLOCK with reasons."""
    if not isinstance(normalized, str):
        return ValidationOutcome(ValidationResult.BLOCK, ("invalid_input",))
    reasons: List[str] = []
    reasons.extend(_check_profanity(normalized))
    reasons.extend(_check_injection(normalized))
    if reasons:
        return ValidationOutcome(ValidationResult.BLOCK, tuple(reasons))
    return ValidationOutcome(ValidationResult.OK, ())
