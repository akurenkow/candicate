#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
resume_converter_deepseek_v2.py

Design goals:
- Use LLM only for information extraction
- Python builds the canonical structure deterministically
- Eliminates dash spacing errors (- -, - - -)
- Guarantees separator correctness
- Supports RU and EN resumes
- Output file: resume_{timestamp}.txt
"""

import os
import sys
import re
import json
import requests
from datetime import datetime

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-chat"


def detect_language(text: str) -> str:
    letters = re.findall(r"[A-Za-zА-Яа-я]", text)
    if not letters:
        return "en"
    cyr = re.findall(r"[А-Яа-я]", text)
    return "ru" if len(cyr) / len(letters) > 0.02 else "en"


def call_deepseek(prompt: str, api_key: str) -> str:

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You extract structured resume data."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1
    }

    r = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload)
    r.raise_for_status()

    return r.json()["choices"][0]["message"]["content"]


def build_extraction_prompt(text, lang):

    if lang == "ru":
        return f"""
Извлеки данные из резюме и верни JSON.

Структура:
{{
 "general": {{}},
 "skills": [],
 "experience": [
   {{
     "company": "",
     "period": "",
     "role": "",
     "projects":[
        {{
          "name":"",
          "domain":"",
          "achievements":[],
          "technologies":[]
        }}
     ]
   }}
 ]
}}

Резюме:
{text}
"""
    else:
        return f"""
Extract resume data and return JSON.

Structure:
{{
 "general": {{}},
 "skills": [],
 "experience": [
   {{
     "company": "",
     "period": "",
     "role": "",
     "projects":[
        {{
          "name":"",
          "domain":"",
          "achievements":[],
          "technologies":[]
        }}
     ]
   }}
 ]
}}

Resume text:
{text}
"""


def normalize_text_line(s):
    return re.sub(r"\s+", " ", s).strip()


def dash(level):
    return "-" * level + " " if level > 0 else ""


def build_resume(data, lang):

    lines = []

    if lang == "ru":
        sep = "-----Секция опыта:-----"
        general_title = "Основная информация:"
    else:
        sep = "-----Experience Section:-----"
        general_title = "General information:"

    lines.append(general_title)
    lines.append("")

    general = data.get("general", {})

    for k, v in general.items():
        lines.append(f"- {k}: {v}")

    skills = data.get("skills", [])
    if skills:
        lines.append("")
        lines.append("- Навыки:" if lang == "ru" else "- Skills:")
        for s in skills:
            lines.append("-- " + normalize_text_line(s))

    lines.append("")
    lines.append(sep)
    lines.append("")

    for company in data.get("experience", []):

        lines.append("- Компания: " + normalize_text_line(company.get("company","")) if lang=="ru"
                     else "- Company: " + normalize_text_line(company.get("company","")))

        lines.append("-- Период: " + company.get("period","") if lang=="ru"
                     else "-- Period: " + company.get("period",""))

        lines.append("-- Должность: " + company.get("role","") if lang=="ru"
                     else "-- Role: " + company.get("role",""))

        for project in company.get("projects", []):

            lines.append("-- Название проекта: " + project.get("name","") if lang=="ru"
                         else "-- Project name: " + project.get("name",""))

            lines.append("-- Предметная область проекта: " + project.get("domain","") if lang=="ru"
                         else "-- Project domain: " + project.get("domain",""))

            lines.append("-- Обязанности и достижения:" if lang=="ru"
                         else "-- Responsibilities and achievements:")

            for ach in project.get("achievements", []):
                lines.append("--- " + normalize_text_line(ach))

            tech = project.get("technologies", [])
            if tech:
                lines.append("-- Использованные технологии: " + ", ".join(tech) if lang=="ru"
                             else "-- Technologies: " + ", ".join(tech))

        lines.append("")

    return "\n".join(lines)


def parse_json(text):

    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        return json.loads(text[start:end])
    except:
        raise RuntimeError("Failed to parse JSON from LLM output")


def main(input_file):

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("Set DEEPSEEK_API_KEY")

    with open(input_file, encoding="utf-8") as f:
        raw = f.read()

    lang = detect_language(raw)

    prompt = build_extraction_prompt(raw, lang)

    llm_output = call_deepseek(prompt, api_key)

    data = parse_json(llm_output)

    resume = build_resume(data, lang)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = f"resume_{ts}.txt"

    with open(out, "w", encoding="utf-8") as f:
        f.write(resume)

    print("Saved:", out)


if __name__ == "__main__":

    if len(sys.argv) < 2:
        print("Usage: python resume_converter_deepseek_v2.py input.txt")
        sys.exit(1)

    main(sys.argv[1])
