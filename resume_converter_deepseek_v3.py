#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests
from pypdf import PdfReader

DEEPSEEK_API_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
REQUEST_TIMEOUT = int(os.getenv("DEEPSEEK_TIMEOUT", "180"))
MAX_TOKENS = int(os.getenv("DEEPSEEK_MAX_TOKENS", "8000"))


def run_cmd(cmd: List[str], timeout: int = 120) -> Tuple[int, str, str]:
    p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    return p.returncode, p.stdout.decode("utf-8", "ignore"), p.stderr.decode("utf-8", "ignore")


def normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def detect_language(text: str) -> str:
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", text)
    if not letters:
        return "en"
    cyr = re.findall(r"[А-Яа-яЁё]", text)
    return "ru" if (len(cyr) / max(1, len(letters))) > 0.02 else "en"


def read_text_bytes(path: Path) -> bytes:
    return path.read_bytes()


def looks_like_rtf(raw: bytes) -> bool:
    return raw[:5] == b"{\\rtf"


def extract_text_from_txt(path: Path) -> str:
    raw = read_text_bytes(path)
    for enc in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
        try:
            return normalize_whitespace(raw.decode(enc))
        except Exception:
            pass
    return normalize_whitespace(raw.decode("utf-8", "ignore"))


def extract_text_from_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return normalize_whitespace("\n".join(pages))


def cleanup_unrtf_output(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if line.startswith("###  Translation from RTF performed by UnRTF"):
            continue
        if line.startswith("### font table contains"):
            continue
        if re.match(r"^-{5,}$", line.strip()):
            continue
        if re.search(r"picture data found", line, re.I):
            continue
        if re.fullmatch(r"pict\d+\.\w+", line.strip()):
            continue
        lines.append(line)
    return normalize_whitespace("\n".join(lines))


def extract_text_from_rtf(path: Path) -> str:
    rc, out, err = run_cmd(["unrtf", "--text", str(path)])
    if rc == 0 and out.strip():
        return cleanup_unrtf_output(out)

    raw = read_text_bytes(path).decode("latin-1", "ignore")
    raw = re.sub(r"\\par[d]? ?", "\n", raw)
    raw = re.sub(r"\\'[0-9a-fA-F]{2}", "", raw)
    raw = re.sub(r"\\[a-zA-Z]+\d* ?", "", raw)
    raw = raw.replace("{", "").replace("}", "")
    return normalize_whitespace(raw)


def extract_text_from_doc(path: Path) -> str:
    raw = read_text_bytes(path)
    if looks_like_rtf(raw):
        return extract_text_from_rtf(path)

    rc, out, err = run_cmd(["antiword", str(path)])
    if rc == 0 and out.strip():
        return normalize_whitespace(out)

    with tempfile.TemporaryDirectory() as tmp:
        rc, out, err = run_cmd([
            "soffice", "--headless", "--convert-to", "txt:Text",
            "--outdir", tmp, str(path)
        ], timeout=180)
        txt_path = Path(tmp) / (path.stem + ".txt")
        if txt_path.exists():
            return extract_text_from_txt(txt_path)

    raise RuntimeError(f"Не удалось извлечь текст из DOC: {path.name}")


def extract_text_from_docx(path: Path) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        rc, out, err = run_cmd([
            "soffice", "--headless", "--convert-to", "txt:Text",
            "--outdir", tmp, str(path)
        ], timeout=180)
        txt_path = Path(tmp) / (path.stem + ".txt")
        if txt_path.exists():
            return extract_text_from_txt(txt_path)
    raise RuntimeError(f"Failed to extract text from DOCX: {path.name}")


def html_to_text(value: str) -> str:
    value = re.sub(r"(?is)<script.*?>.*?</script>", " ", value)
    value = re.sub(r"(?is)<style.*?>.*?</style>", " ", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</p\s*>", "\n", value)
    value = re.sub(r"(?i)</div\s*>", "\n", value)
    value = re.sub(r"(?i)</li\s*>", "\n", value)
    value = re.sub(r"(?is)<[^>]+>", " ", value)
    value = html.unescape(value)
    return normalize_whitespace(value)


def extract_text_from_html(path: Path) -> str:
    raw = read_text_bytes(path)
    for enc in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
        try:
            return html_to_text(raw.decode(enc))
        except Exception:
            pass
    return html_to_text(raw.decode("utf-8", "ignore"))


def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {".txt", ".md"}:
        return extract_text_from_txt(path)
    if ext == ".pdf":
        return extract_text_from_pdf(path)
    if ext == ".rtf":
        return extract_text_from_rtf(path)
    if ext == ".doc":
        return extract_text_from_doc(path)
    if ext == ".docx":
        return extract_text_from_docx(path)
    if ext in {".html", ".htm", ".mhtml"}:
        return extract_text_from_html(path)

    raw = read_text_bytes(path)
    if looks_like_rtf(raw):
        return extract_text_from_rtf(path)
    return extract_text_from_txt(path)


CANONICAL_RULES_RU = """
Exactly one separator: -----Секция опыта:-----
GENERAL before separator. EXPERIENCE after separator.
Education and skills MUST stay in GENERAL, never in EXPERIENCE.
No spaced dash prefixes like "- -" or "- - -".
Only dash nesting levels 0..4.
Return strict JSON only.
"""

CANONICAL_RULES_EN = """
Exactly one separator: -----Experience Section:-----
GENERAL before separator. EXPERIENCE after separator.
Education and skills MUST stay in GENERAL, never in EXPERIENCE.
No spaced dash prefixes like "- -" or "- - -".
Only dash nesting levels 0..4.
Return strict JSON only.
"""

EXTRACTION_SCHEMA_HINT = """
Return a JSON object with this schema:

{
  "language": "ru|en",
  "general": {
    "full_name": "",
    "gender": "",
    "birth_date": "",
    "phone": "",
    "email": "",
    "other_contacts": [],
    "location": "",
    "citizenship": "",
    "relocation": "",
    "total_experience": "",
    "employment_type": "",
    "hiring_arrangement": "",
    "work_format": "",
    "availability": "",
    "education": [],
    "military_status": "",
    "skills": {
      "languages": "",
      "core_technologies": ""
    }
  },
  "additional": {
    "about": "",
    "desired_positions": [],
    "salary": {
      "minimum": "",
      "comfortable": ""
    },
    "payment_currency": "",
    "reason_for_change": "",
    "what_wants": "",
    "what_avoids": "",
    "interesting_tasks": "",
    "key_skills": []
  },
  "experience": [
    {
      "company": "",
      "period": "",
      "role": "",
      "projects": [
        {
          "name": "",
          "domain": "",
          "responsibilities": [],
          "technologies": ""
        }
      ],
      "additional_activity": {
        "title": "",
        "responsibilities": []
      }
    }
  ]
}
"""


def call_deepseek(messages: List[Dict[str, str]], api_key: str, json_mode: bool = False) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0,
        "max_tokens": MAX_TOKENS,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    r = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    body = r.json()
    content = body["choices"][0]["message"]["content"]
    if content is None:
        return ""
    return content


def build_extraction_prompt(source_text: str, lang: str) -> List[Dict[str, str]]:
    rules = CANONICAL_RULES_RU if lang == "ru" else CANONICAL_RULES_EN
    system = (
        "You are a strict resume information extraction engine. "
        "Return strict json only. No markdown. No explanations. No comments. "
        "All strings must be valid JSON strings with escaped double quotes where needed."
    )
    user = f"""
{rules}

{EXTRACTION_SCHEMA_HINT}

Extraction rules:
- Preserve resume language
- Detect company boundaries correctly
- Detect project boundaries correctly
- If a company has only one project, keep one project
- If a company has multiple projects, return multiple project objects
- If domain is merged into role or project name in parentheses, move it to project.domain
- If domain is unknown, leave it empty
- Do not place education or skills inside experience
- Return json only

Source resume text:
<<<BEGIN_RESUME>>>
{source_text}
<<<END_RESUME>>>
"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def extract_json_candidate(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        return m.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]
    return text.strip()


def repair_json_text_basic(text: str) -> str:
    text = extract_json_candidate(text)
    text = text.replace("\ufeff", "")
    text = text.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    text = re.sub(r"(?m)^\s*#.*$", "", text)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return text.strip()


def parse_json_loose(text: str) -> Dict[str, Any]:
    candidate = repair_json_text_basic(text)
    try:
        return json.loads(candidate)
    except Exception:
        pass
    candidate = re.sub(r'(?<=\{|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s*:', r'"\1":', candidate)
    candidate = re.sub(r",(\s*[}\]])", r"\1", candidate)
    return json.loads(candidate)


def repair_json_with_llm(bad_output: str, api_key: str) -> Dict[str, Any]:
    messages = [
        {
            "role": "system",
            "content": (
                "You repair malformed json. Return valid strict json only. "
                "Escape all inner double quotes inside JSON string values."
            ),
        },
        {
            "role": "user",
            "content": (
                "Repair this malformed json and return only valid json.\n\n"
                f"{bad_output}"
            ),
        },
    ]
    repaired = call_deepseek(messages, api_key, json_mode=True)
    return parse_json_loose(repaired)


def clean_inline(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value).replace("\r", " ").replace("\n", " ")
    s = re.sub(r"\s+", " ", s).strip(" \t-–—;")
    return s.strip()


def as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [clean_inline(x) for x in value if clean_inline(x)]
    s = clean_inline(value)
    return [s] if s else []


def split_contacts(value: Any) -> List[str]:
    if isinstance(value, list):
        return [clean_inline(x) for x in value if clean_inline(x)]
    s = clean_inline(value)
    if not s:
        return []
    parts = re.split(r"\s*[;•|]\s*|\s{2,}", s)
    return [p for p in (clean_inline(x) for x in parts) if p]


def normalize_project(project: Any) -> Dict[str, Any]:
    if not isinstance(project, dict):
        project = {}
    name = clean_inline(project.get("name", ""))
    domain = clean_inline(project.get("domain", ""))
    role_hint = clean_inline(project.get("role", ""))

    if not domain:
        for source in (name, role_hint):
            m = re.search(r"\(([^()]{2,80})\)$", source)
            if m:
                domain = clean_inline(m.group(1))
                if source == name:
                    name = clean_inline(re.sub(r"\s*\([^()]{2,80}\)$", "", name))
                break

    return {
        "name": name,
        "domain": domain,
        "responsibilities": as_list(project.get("responsibilities") or project.get("achievements")),
        "technologies": clean_inline(project.get("technologies", "")),
    }


def normalize_company(company: Any) -> Dict[str, Any]:
    if not isinstance(company, dict):
        company = {}
    role = clean_inline(company.get("role", ""))
    role_domain = ""
    m = re.search(r"\(([^()]{2,80})\)$", role)
    if m:
        role_domain = clean_inline(m.group(1))
        role = clean_inline(re.sub(r"\s*\([^()]{2,80}\)$", "", role))

    projects = company.get("projects", [])
    if isinstance(projects, dict):
        projects = [projects]
    if not isinstance(projects, list):
        projects = []

    normalized_projects = [normalize_project(p) for p in projects]
    if not normalized_projects:
        normalized_projects = [{
            "name": clean_inline(company.get("project_name", "")),
            "domain": clean_inline(company.get("project_domain", "")) or role_domain,
            "responsibilities": as_list(company.get("responsibilities") or company.get("achievements")),
            "technologies": clean_inline(company.get("technologies", "")),
        }]

    if len(normalized_projects) == 1 and not normalized_projects[0]["domain"] and role_domain:
        normalized_projects[0]["domain"] = role_domain

    add = company.get("additional_activity") or {}
    if not isinstance(add, dict):
        add = {"title": clean_inline(add), "responsibilities": []}

    return {
        "company": clean_inline(company.get("company", "")),
        "period": clean_inline(company.get("period", "")),
        "role": role,
        "projects": normalized_projects,
        "additional_activity": {
            "title": clean_inline(add.get("title", "")),
            "responsibilities": as_list(add.get("responsibilities")),
        },
    }


def normalize_schema(data: Dict[str, Any], lang_hint: str) -> Dict[str, Any]:
    if not isinstance(data, dict):
        data = {}
    general = data.get("general", {}) if isinstance(data.get("general"), dict) else {}
    additional = data.get("additional", {}) if isinstance(data.get("additional"), dict) else {}
    skills = general.get("skills", {}) if isinstance(general.get("skills"), dict) else {}

    normalized = {
        "language": clean_inline(data.get("language")) or lang_hint,
        "general": {
            "full_name": clean_inline(general.get("full_name") or general.get("name")),
            "gender": clean_inline(general.get("gender")),
            "birth_date": clean_inline(general.get("birth_date") or general.get("date_of_birth")),
            "phone": clean_inline(general.get("phone")),
            "email": clean_inline(general.get("email")),
            "other_contacts": split_contacts(general.get("other_contacts")),
            "location": clean_inline(general.get("location") or general.get("residence")),
            "citizenship": clean_inline(general.get("citizenship")),
            "relocation": clean_inline(general.get("relocation") or general.get("willingness_to_relocate")),
            "total_experience": clean_inline(general.get("total_experience")),
            "employment_type": clean_inline(general.get("employment_type")),
            "hiring_arrangement": clean_inline(general.get("hiring_arrangement")),
            "work_format": clean_inline(general.get("work_format")),
            "availability": clean_inline(general.get("availability")),
            "education": as_list(general.get("education")),
            "military_status": clean_inline(general.get("military_status")),
            "skills": {
                "languages": clean_inline(skills.get("languages")),
                "core_technologies": clean_inline(skills.get("core_technologies") or skills.get("technologies")),
            },
        },
        "additional": {
            "about": clean_inline(additional.get("about")),
            "desired_positions": as_list(additional.get("desired_positions")),
            "salary": {
                "minimum": clean_inline((additional.get("salary") or {}).get("minimum")),
                "comfortable": clean_inline((additional.get("salary") or {}).get("comfortable")),
            },
            "payment_currency": clean_inline(additional.get("payment_currency")),
            "reason_for_change": clean_inline(additional.get("reason_for_change")),
            "what_wants": clean_inline(additional.get("what_wants")),
            "what_avoids": clean_inline(additional.get("what_avoids")),
            "interesting_tasks": clean_inline(additional.get("interesting_tasks")),
            "key_skills": as_list(additional.get("key_skills")),
        },
        "experience": [normalize_company(x) for x in (data.get("experience") or [])],
    }
    if normalized["language"] not in {"ru", "en"}:
        normalized["language"] = lang_hint
    return normalized


def repair_data_deterministically(data: Dict[str, Any], lang: str) -> Dict[str, Any]:
    general = data.get("general", {})
    if not isinstance(general.get("education"), list):
        general["education"] = as_list(general.get("education"))
    if "skills" not in general or not isinstance(general.get("skills"), dict):
        general["skills"] = {"languages": "", "core_technologies": ""}

    for company in data.get("experience", []):
        projects = company.get("projects", [])
        for project in projects:
            if not project.get("domain"):
                m = re.search(r"\(([^()]{2,80})\)$", project.get("name", ""))
                if m:
                    project["domain"] = clean_inline(m.group(1))
                    project["name"] = clean_inline(re.sub(r"\s*\([^()]{2,80}\)$", "", project["name"]))
        if company.get("role") and len(projects) == 1 and not projects[0].get("domain"):
            m = re.search(r"\(([^()]{2,80})\)$", company.get("role", ""))
            if m:
                projects[0]["domain"] = clean_inline(m.group(1))
                company["role"] = clean_inline(re.sub(r"\s*\([^()]{2,80}\)$", "", company["role"]))
    data["language"] = lang
    return data


def render_general(data: Dict[str, Any], lang: str) -> List[str]:
    g = data["general"]
    a = data["additional"]

    if lang == "ru":
        lines = [
            "Основная информация:",
            f"- ФИО: {g['full_name']}",
            f"- Пол: {g['gender']}",
            f"- Дата рождения: {g['birth_date']}",
            f"- Телефон: {g['phone']}",
            f"- Email: {g['email']}",
            f"- Прочие контакты: {'; '.join(g['other_contacts'])}",
            f"- Проживание: {g['location']}",
            f"- Гражданство: {g['citizenship']}",
            f"- Готовность к переезду: {g['relocation']}",
            f"- Общий опыт работы: {g['total_experience']}",
            f"- Желаемый тип занятости: {g['employment_type']}",
            f"- Желаемый формат оформления в штат: {g['hiring_arrangement']}",
            f"- Желаемый формат работы, готов ли посещать офис: {g['work_format']}",
            f"- Когда готов приступить к работе: {g['availability']}",
            "- Образование:",
        ]
        for item in g["education"]:
            lines.append(f"-- {item}")
        lines.extend([
            f"- Наличие военного билета: {g['military_status']}",
            "- Навыки:",
            f"-- Языки: {g['skills']['languages']}",
            f"-- Ключевые технологии: {g['skills']['core_technologies']}",
            "",
            f"Дополнительная информация (Обо мне): {a['about']}",
            "",
            "Желаемые должности:",
        ])
        for item in a["desired_positions"]:
            lines.append(f"- {item}")
        lines.extend([
            "",
            "Желаемая зарплата:",
            f"- Минимальная сумма: {a['salary']['minimum']}",
            f"- Комфортная сумма: {a['salary']['comfortable']}",
            f"Валюта оплаты: {a['payment_currency']}",
            "",
            f"Причина ухода с последнего места работы, причина смены места работы: {a['reason_for_change']}",
            f"Что хочет от нового места работы (что ищет): {a['what_wants']}",
            f"С чем не хочет работать: {a['what_avoids']}",
            f"С чем хочет работать (какие задачи интересуют): {a['interesting_tasks']}",
            "",
            "Ключевые навыки:",
        ])
        for item in a["key_skills"]:
            lines.append(f"- {item}")
        return lines

    lines = [
        "General information:",
        f"- Full name: {g['full_name']}",
        f"- Gender: {g['gender']}",
        f"- Date of birth: {g['birth_date']}",
        f"- Phone: {g['phone']}",
        f"- Email: {g['email']}",
        f"- Other contacts: {'; '.join(g['other_contacts'])}",
        f"- Location: {g['location']}",
        f"- Citizenship: {g['citizenship']}",
        f"- Willingness to relocate: {g['relocation']}",
        f"- Total work experience: {g['total_experience']}",
        f"- Desired employment type: {g['employment_type']}",
        f"- Desired hiring arrangement: {g['hiring_arrangement']}",
        f"- Desired work format, office attendance: {g['work_format']}",
        f"- Availability: {g['availability']}",
        "- Education:",
    ]
    for item in g["education"]:
        lines.append(f"-- {item}")
    lines.extend([
        f"- Military ID / military status: {g['military_status']}",
        "- Skills:",
        f"-- Languages: {g['skills']['languages']}",
        f"-- Core technologies: {g['skills']['core_technologies']}",
        "",
        f"Additional information (About me): {a['about']}",
        "",
        "Desired positions:",
    ])
    for item in a["desired_positions"]:
        lines.append(f"- {item}")
    lines.extend([
        "",
        "Desired salary:",
        f"- Minimum amount: {a['salary']['minimum']}",
        f"- Comfortable amount: {a['salary']['comfortable']}",
        f"Payment currency: {a['payment_currency']}",
        "",
        f"Reason for leaving last job / reason for job change: {a['reason_for_change']}",
        f"What the candidate wants from the new job: {a['what_wants']}",
        f"What the candidate does not want to work with: {a['what_avoids']}",
        f"What the candidate wants to work with / interesting tasks: {a['interesting_tasks']}",
        "",
        "Key skills:",
    ])
    for item in a["key_skills"]:
        lines.append(f"- {item}")
    return lines


def render_experience(data: Dict[str, Any], lang: str) -> List[str]:
    lines: List[str] = []
    for idx, company in enumerate(data["experience"]):
        if lang == "ru":
            lines.append(f"- Компания: {company['company']}")
            lines.append(f"-- Период: {company['period']}")
            lines.append(f"-- Должность: {company['role']}")
        else:
            lines.append(f"- Company: {company['company']}")
            lines.append(f"-- Period: {company['period']}")
            lines.append(f"-- Role: {company['role']}")

        projects = company["projects"] or [{"name": "", "domain": "", "responsibilities": [], "technologies": ""}]
        if len(projects) == 1:
            p = projects[0]
            if lang == "ru":
                lines.append(f"-- Название проекта: {p['name']}")
                lines.append(f"-- Предметная область проекта: {p['domain']}")
                lines.append("-- Обязанности и достижения:")
                for item in p["responsibilities"]:
                    lines.append(f"--- {item}")
                if p["technologies"]:
                    lines.append(f"-- Использованные технологии: {p['technologies']}")
            else:
                lines.append(f"-- Project name: {p['name']}")
                lines.append(f"-- Project domain: {p['domain']}")
                lines.append("-- Responsibilities and achievements:")
                for item in p["responsibilities"]:
                    lines.append(f"--- {item}")
                if p["technologies"]:
                    lines.append(f"-- Technologies used: {p['technologies']}")
        else:
            for p_idx, p in enumerate(projects, start=1):
                if lang == "ru":
                    lines.append(f"--- Название проекта № {p_idx}: {p['name']}")
                    lines.append(f"--- Предметная область проекта: {p['domain']}")
                    lines.append("--- Обязанности и достижения:")
                    for item in p["responsibilities"]:
                        lines.append(f"---- {item}")
                    if p["technologies"]:
                        lines.append(f"--- Использованные технологии: {p['technologies']}")
                else:
                    lines.append(f"--- Project name #{p_idx}: {p['name']}")
                    lines.append(f"--- Project domain: {p['domain']}")
                    lines.append("--- Responsibilities and achievements:")
                    for item in p["responsibilities"]:
                        lines.append(f"---- {item}")
                    if p["technologies"]:
                        lines.append(f"--- Technologies used: {p['technologies']}")

            add = company.get("additional_activity", {})
            add_title = clean_inline(add.get("title"))
            add_resp = as_list(add.get("responsibilities"))
            if add_title or add_resp:
                if lang == "ru":
                    lines.append(f"--- Дополнительная активность в компании (что еще я делал): {add_title}")
                    lines.append("--- Обязанности и достижения:")
                    for item in add_resp:
                        lines.append(f"---- {item}")
                else:
                    lines.append(f"--- Additional activity in company: {add_title}")
                    lines.append("--- Responsibilities and achievements:")
                    for item in add_resp:
                        lines.append(f"---- {item}")

        if idx != len(data["experience"]) - 1:
            lines.append("")
    return lines


def collapse_spaced_dashes_in_line(line: str) -> str:
    if not re.match(r"^\s*-\s+-", line):
        return line
    m = re.match(r"^(\s*(?:-\s+){1,4})(.*)$", line)
    if not m:
        return line
    prefix, rest = m.groups()
    dash_count = prefix.count("-")
    dash_count = max(1, min(4, dash_count))
    return f"{'-' * dash_count} {rest.strip()}"


def normalize_rendered_text(text: str, lang: str) -> str:
    sep = "-----Секция опыта:-----" if lang == "ru" else "-----Experience Section:-----"
    lines = [collapse_spaced_dashes_in_line(x.rstrip()) for x in text.splitlines()]
    normalized = []
    seen_sep = False
    for line in lines:
        if re.search(r"секц.*опыт|experience section|work experience", line, re.I):
            if not seen_sep:
                normalized.append(sep)
                seen_sep = True
            continue
        normalized.append(re.sub(r"[ \t]+$", "", line))
    if not seen_sep:
        insert_at = len(normalized)
        for i, line in enumerate(normalized):
            if line.startswith("- Компания:") or line.startswith("- Company:"):
                insert_at = i
                break
        normalized.insert(insert_at, sep)

    out = []
    prev_blank = False
    for line in normalized:
        if not line.strip():
            if not prev_blank:
                out.append("")
            prev_blank = True
        else:
            out.append(line)
            prev_blank = False
    return "\n".join(out).strip() + "\n"


def validate_output(text: str, lang: str) -> List[str]:
    issues = []
    sep = "-----Секция опыта:-----" if lang == "ru" else "-----Experience Section:-----"
    if text.count(sep) != 1:
        issues.append("Separator count must be exactly 1")
    if re.search(r"(?m)^\s*-\s+-", text):
        issues.append("Found spaced dash prefixes")
    if re.search(r"(?m)^-{5,}(?!Секция опыта:-----|Experience Section:-----)", text):
        issues.append("Found too deep dash nesting")

    try:
        before, after = text.split(sep)
    except ValueError:
        before, after = text, ""

    for kw in ("Образование:", "Education:", "Навыки:", "Skills:"):
        if kw in after:
            issues.append(f"{kw} found in experience section")
    return issues


def render_resume(data: Dict[str, Any], lang: str) -> str:
    sep = "-----Секция опыта:-----" if lang == "ru" else "-----Experience Section:-----"
    parts = []
    parts.extend(render_general(data, lang))
    parts.append("")
    parts.append(sep)
    parts.append("")
    parts.extend(render_experience(data, lang))
    return normalize_rendered_text("\n".join(parts), lang)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_file")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    input_path = Path(args.input_file)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("Set DEEPSEEK_API_KEY environment variable")

    source_text = extract_text(input_path)
    if not source_text.strip():
        raise RuntimeError("Failed to extract text from input file")

    lang = detect_language(source_text)

    llm_output = call_deepseek(build_extraction_prompt(source_text, lang), api_key, json_mode=True)
    if args.debug:
        Path("debug_llm_output.txt").write_text(llm_output, encoding="utf-8")

    if not llm_output.strip():
        llm_output = call_deepseek(build_extraction_prompt(source_text, lang), api_key, json_mode=False)
        if args.debug:
            Path("debug_llm_output_fallback.txt").write_text(llm_output, encoding="utf-8")

    try:
        raw_data = parse_json_loose(llm_output)
    except Exception:
        raw_data = repair_json_with_llm(llm_output, api_key)

    data = normalize_schema(raw_data, lang)
    data = repair_data_deterministically(data, lang)

    if args.debug:
        Path("debug_extracted.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    output_text = render_resume(data, lang)
    issues = validate_output(output_text, lang)

    if args.debug:
        Path("debug_validation.txt").write_text(
            "\n".join(issues) if issues else "OK", encoding="utf-8"
        )

    if issues:
        raise RuntimeError("Validation failed: " + "; ".join(issues))

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = input_path.parent / f"resume_{ts}.txt"
    out_path.write_text(output_text, encoding="utf-8")
    print(str(out_path))


if __name__ == "__main__":
    main()
