#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Resume converter using DeepSeek API.
Features:
- Converts free-form resume TXT into canonical structured format.
- Supports RU and EN resumes (auto-detected by Cyrillic ratio).
- Repairs broken dash nesting (- -, - - -, etc).
- Ensures exactly one separator between GENERAL and EXPERIENCE sections.
- Moves project domain accidentally appended to role/project title.
- Output file name: resume_{timestamp}.txt
"""

import os
import sys
import re
import json
import time
import requests
from datetime import datetime

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-chat"

CANONICAL_RULES_RU = """
Структура резюме строго фиксирована.
Уровни вложенности:
0 — без дефиса
1 — "- "
2 — "-- "
3 — "--- "
4 — "---- "

Ровно один разделитель:
-----Секция опыта:-----
"""

CANONICAL_RULES_EN = """
Resume structure must follow strict dash nesting.
Levels:
0 no dash
1 "- "
2 "-- "
3 "--- "
4 "---- "

Exactly one separator:
-----Experience Section:-----
"""

GOLD_EXAMPLE_RU = """
0. Основная информация:

- ФИО:
- Пол:
- Дата рождения:

-----Секция опыта:-----

- Компания:
-- Период:
-- Должность:
-- Название проекта:
-- Предметная область проекта:
-- Обязанности и достижения:
--- Пункт
"""

GOLD_EXAMPLE_EN = """
0. General information:

- Full name:
- Gender:
- Date of birth:

-----Experience Section:-----

- Company:
-- Period:
-- Role:
-- Project name:
-- Project domain:
-- Responsibilities and achievements:
--- Item
"""


def detect_language(text: str) -> str:
    letters = re.findall(r'[A-Za-zА-Яа-я]', text)
    if not letters:
        return "en"
    cyr = re.findall(r'[А-Яа-я]', text)
    if len(cyr) / len(letters) > 0.02:
        return "ru"
    return "en"


def collapse_spaced_dashes(text: str) -> str:
    """
    Fix patterns like:
    - - item  -> -- item
    - - - item -> --- item
    """
    def fix(line):
        m = re.match(r'^(\s*)((?:-\s)+)(.*)', line)
        if not m:
            return line

        indent, dash_block, rest = m.groups()
        dash_count = dash_block.count('-')
        if dash_count > 4:
            dash_count = 4

        return indent + ('-' * dash_count) + ' ' + rest.strip()

    return "\n".join(fix(l) for l in text.splitlines())


def normalize_separator(text: str, lang: str) -> str:
    lines = []
    sep_ru = "-----Секция опыта:-----"
    sep_en = "-----Experience Section:-----"
    sep = sep_ru if lang == "ru" else sep_en

    for line in text.splitlines():
        if re.search(r'секц.*опыт|experience', line, re.I):
            lines.append(sep)
        else:
            lines.append(line)

    # ensure exactly one separator
    count = sum(1 for l in lines if l.strip() == sep)
    if count == 0:
        lines.insert(len(lines)//2, sep)
    elif count > 1:
        seen = False
        new = []
        for l in lines:
            if l.strip() == sep:
                if not seen:
                    new.append(l)
                    seen = True
                continue
            new.append(l)
        lines = new

    return "\n".join(lines)


def repair_subject_domain(text: str, lang: str) -> str:
    """
    Moves domain from role/project parentheses into explicit section.
    """
    lines = text.splitlines()
    out = []

    domain_key = "Предметная область проекта:" if lang == "ru" else "Project domain:"

    for line in lines:
        m = re.search(r'\(([^)]+)\)', line)
        if m and ("Должность" in line or "Role" in line or "Название проекта" in line or "Project name" in line):
            domain = m.group(1)
            clean = line.replace(f"({domain})", "").strip()
            out.append(clean)
            out.append("-- " + domain_key + " " + domain)
        else:
            out.append(line)

    return "\n".join(out)


def call_deepseek(prompt: str, api_key: str) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    data = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You are a resume formatting engine."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1
    }

    resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=data)
    resp.raise_for_status()

    return resp.json()["choices"][0]["message"]["content"]


def build_prompt(text: str, lang: str) -> str:
    rules = CANONICAL_RULES_RU if lang == "ru" else CANONICAL_RULES_EN
    example = GOLD_EXAMPLE_RU if lang == "ru" else GOLD_EXAMPLE_EN

    return f"""
Follow canonical resume structure rules.

Rules:
{rules}

Example:
{example}

Resume text:
{text}

Return ONLY formatted resume.
"""


def process(input_file: str):

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("Set DEEPSEEK_API_KEY env variable")

    with open(input_file, "r", encoding="utf-8") as f:
        raw = f.read()

    lang = detect_language(raw)

    prompt = build_prompt(raw, lang)

    formatted = call_deepseek(prompt, api_key)

    formatted = collapse_spaced_dashes(formatted)
    formatted = normalize_separator(formatted, lang)
    formatted = repair_subject_domain(formatted, lang)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = f"resume_{ts}.txt"

    with open(out_file, "w", encoding="utf-8") as f:
        f.write(formatted.strip() + "\n")

    print("Saved:", out_file)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python resume_converter_deepseek.py input.txt")
        sys.exit(1)

    process(sys.argv[1])
