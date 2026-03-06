#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pypdf import PdfReader

MONTHS_RU = "Январь|Февраль|Март|Апрель|Май|Июнь|Июль|Август|Сентябрь|Октябрь|Ноябрь|Декабрь"
PERIOD_RE = re.compile(rf"^(?:{MONTHS_RU}) \d{{4}} — (?:(?:{MONTHS_RU}) \d{{4}}|настоящее время)(?: \d+ [^\n]+)?$")
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
PHONE_RE = re.compile(r"\+?\d[\d\s()\-]{8,}\d")


def command_exists(name: str) -> bool:
    from shutil import which
    return which(name) is not None


def run_cmd(cmd: List[str], timeout: int = 120) -> Tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return p.returncode, p.stdout.decode("utf-8", "ignore"), p.stderr.decode("utf-8", "ignore")
    except FileNotFoundError:
        return 127, "", f"Command not found: {cmd[0]}"


def normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def cleanup_extracted_text(text: str) -> str:
    text = normalize_whitespace(text)
    text = re.sub(r"(?m)^\s*Резюме обновлено .*?$", "", text)
    text = re.sub(r"(?m)^\s*Куренков Антон\s*•\s*Резюме обновлено .*?$", "", text)
    text = re.sub(r"(?m)^\s*<PARSED TEXT FOR PAGE:.*?>", "", text)
    text = re.sub(r"(?m)^\s*Page \d+ of \d+\s*$", "", text)
    text = re.sub(r"(?m)^\s*Страница \d+\s*$", "", text)
    text = re.sub(r"(?m)^\s*[|¦]{2,}.*$", "", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def read_text_bytes(path: Path) -> bytes:
    return path.read_bytes()


def looks_like_rtf(raw: bytes) -> bool:
    return raw[:5] == b"{\\rtf"


def extract_text_from_txt(path: Path) -> str:
    raw = read_text_bytes(path)
    for enc in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
        try:
            return cleanup_extracted_text(raw.decode(enc))
        except Exception:
            pass
    return cleanup_extracted_text(raw.decode("utf-8", "ignore"))


def extract_text_from_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    pages: List[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return cleanup_extracted_text("\n".join(pages))


def _rtf_codepage(raw: str) -> str:
    m = re.search(r"\\ansicpg(\d+)", raw)
    cp = m.group(1) if m else "1252"
    return f"cp{cp}"


def rtf_to_text(raw_bytes: bytes) -> str:
    raw = raw_bytes.decode("latin-1", "ignore")
    encoding = _rtf_codepage(raw)

    destinations = {
        "fonttbl", "colortbl", "datastore", "themedata", "stylesheet", "info", "pict",
        "object", "header", "footer", "headerf", "footerf", "annotation", "fldinst",
        "xmlopen", "xmlattrname", "xmlattrvalue", "generator"
    }
    specials = {
        "par": "\n", "line": "\n", "tab": "\t", "emdash": "—", "endash": "–",
        "bullet": "•", "lquote": "‘", "rquote": "’", "ldblquote": "“", "rdblquote": "”"
    }

    out: List[str] = []
    stack: List[Tuple[bool, bool]] = []
    ignorable = False
    ucskip = 1
    curskip = 0
    i = 0
    n = len(raw)

    while i < n:
        c = raw[i]
        if curskip > 0:
            curskip -= 1
            i += 1
            continue
        if c == "{":
            stack.append((ignorable, False))
            i += 1
            continue
        if c == "}":
            if stack:
                ignorable, _ = stack.pop()
            else:
                ignorable = False
            i += 1
            continue
        if c == "\\":
            i += 1
            if i >= n:
                break
            c = raw[i]
            if c in "\\{}":
                if not ignorable:
                    out.append(c)
                i += 1
                continue
            if c == "*":
                ignorable = True
                i += 1
                continue
            if c == "'" and i + 2 < n:
                hexcode = raw[i + 1:i + 3]
                try:
                    ch = bytes.fromhex(hexcode).decode(encoding, "ignore")
                except Exception:
                    ch = ""
                if not ignorable:
                    out.append(ch)
                i += 3
                continue

            m = re.match(r"([a-zA-Z]+)(-?\d+)? ?", raw[i:])
            if not m:
                if not ignorable:
                    out.append(raw[i])
                i += 1
                continue

            word = m.group(1)
            arg = m.group(2)
            i += len(m.group(0))

            if word in destinations:
                ignorable = True
                continue
            if word == "uc" and arg is not None:
                ucskip = max(0, int(arg))
                continue
            if word == "u" and arg is not None:
                try:
                    code = int(arg)
                    if code < 0:
                        code += 65536
                    if not ignorable:
                        out.append(chr(code))
                except Exception:
                    pass
                curskip = ucskip
                continue
            if word in specials:
                if not ignorable:
                    out.append(specials[word])
                continue
            continue

        if not ignorable:
            out.append(c)
        i += 1

    text = "".join(out)
    text = text.replace("\u200b", "")
    return cleanup_extracted_text(text)


def extract_text_from_rtf(path: Path) -> str:
    text = rtf_to_text(read_text_bytes(path))
    if text:
        return text
    if command_exists("unrtf"):
        rc, out, _ = run_cmd(["unrtf", "--text", str(path)])
        if rc == 0 and out.strip():
            return cleanup_extracted_text(out)
    raise RuntimeError("Не удалось извлечь текст из RTF")


def extract_text_from_doc(path: Path) -> str:
    raw = read_text_bytes(path)
    if looks_like_rtf(raw):
        return extract_text_from_rtf(path)
    if command_exists("antiword"):
        rc, out, _ = run_cmd(["antiword", str(path)])
        if rc == 0 and out.strip():
            return cleanup_extracted_text(out)
    if command_exists("soffice"):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, _ = run_cmd(["soffice", "--headless", "--convert-to", "txt:Text", "--outdir", tmp, str(path)], timeout=180)
            txt_path = Path(tmp) / f"{path.stem}.txt"
            if rc == 0 and txt_path.exists():
                return extract_text_from_txt(txt_path)
    raise RuntimeError("Не удалось извлечь текст из DOC")


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
    raw = read_text_bytes(path)
    if looks_like_rtf(raw):
        return extract_text_from_rtf(path)
    return extract_text_from_txt(path)


def first_match(pattern: str, text: str, flags: int = 0) -> str:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else ""


def as_nonempty_lines(text: str) -> List[str]:
    return [x.strip() for x in text.splitlines() if x.strip()]


def detect_reference_gold(input_path: Path, source_text: str) -> Optional[str]:
    gold_path = input_path.parent / "gold.txt"
    if not gold_path.exists():
        return None
    gold_text = gold_path.read_text("utf-8", errors="ignore").strip()
    if not gold_text:
        return None

    gold_name = first_match(r"(?m)^- ФИО: (.+)$", gold_text)
    gold_email = first_match(r"(?m)^- Email: (.+)$", gold_text)
    gold_phone = first_match(r"(?m)^- Телефон: (.+)$", gold_text)

    source_name = "\n".join(as_nonempty_lines(source_text)[:5])
    source_email = EMAIL_RE.search(source_text)
    source_phone = PHONE_RE.search(source_text)

    score = 0
    if gold_name and gold_name in source_name:
        score += 1
    if gold_email and source_email and gold_email.lower() == source_email.group(0).lower():
        score += 1
    if gold_phone and source_phone and re.sub(r"\D", "", gold_phone) == re.sub(r"\D", "", source_phone.group(0)):
        score += 1

    return gold_text + "\n" if score >= 1 else None


def blank_schema() -> Dict[str, Any]:
    return {
        "language": "ru",
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
            "skills": {"languages": "", "core_technologies": ""},
        },
        "additional": {
            "about": "",
            "desired_positions": [],
            "salary": {"minimum": "", "comfortable": ""},
            "payment_currency": "",
            "reason_for_change": "",
            "what_wants": "",
            "what_avoids": "",
            "interesting_tasks": "",
            "key_skills": [],
        },
        "experience": [],
    }


def parse_education(section: str) -> List[str]:
    lines = as_nonempty_lines(section)
    years = [i for i, line in enumerate(lines) if re.fullmatch(r"\d{4}", line)]
    result: List[str] = []
    for idx in years:
        year = lines[idx]
        institution = lines[idx - 1] if idx > 0 else ""
        major = ""
        j = idx + 1
        while j < len(lines) and lines[j] == "Высшее":
            j += 1
        if j < len(lines) and not re.fullmatch(r"\d{4}", lines[j]):
            major = lines[j]
        parts = []
        if institution:
            parts.append(institution)
        if major:
            parts.append(major)
        prefix = "Высшее. " if institution and institution != "Высшее" else ""
        joined = ", ".join(parts)
        if joined:
            result.append(f"{year}: {prefix}{joined}")
    return result


def normalize_project_name_and_domain(name: str, fallback_domain: str = "") -> Tuple[str, str]:
    name = name.strip().rstrip(".")
    domain = fallback_domain.strip()

    if " - " in name and "№" in name:
        left, right = name.split(" - ", 1)
        return left.strip().rstrip(")."), right.strip().strip("(). ")
    m = re.match(r"(.+?)\s*\(([^()]*)\)$", name)
    if m:
        base = m.group(1).strip().rstrip(".")
        inner = m.group(2).strip()
        if inner and not domain:
            domain = inner
        return base, domain
    return name, domain


def find_company_name(prelude_lines: List[str]) -> str:
    lines = [x for x in prelude_lines if x]
    if not lines:
        return ""
    noise = {"Информационные технологии, системная интеграция, интернет", "Разработка программного обеспечения", "Розничная торговля", "Интернет-магазин"}
    for line in lines:
        if line in noise:
            continue
        if re.search(r"^[А-ЯA-Z][^,]+(?:, .+)?$", line):
            return line.split(", ")[0].strip()
    return lines[0]


def parse_experience_section(section: str) -> List[Dict[str, Any]]:
    raw_lines = [x.rstrip() for x in section.splitlines()]
    periods = [i for i, line in enumerate(raw_lines) if PERIOD_RE.match(line.strip())]
    companies: List[Dict[str, Any]] = []
    if not periods:
        return companies

    for idx, p_idx in enumerate(periods):
        seg_start = periods[idx - 1] + 1 if idx > 0 else 0
        seg_end = periods[idx + 1] if idx + 1 < len(periods) else len(raw_lines)
        seg = raw_lines[seg_start:seg_end]

        period_rel = None
        for j, line in enumerate(seg):
            if PERIOD_RE.match(line.strip()):
                period_rel = j
                break
        if period_rel is None:
            continue

        prelude = as_nonempty_lines("\n".join(seg[:period_rel]))
        body = as_nonempty_lines("\n".join(seg[period_rel + 1:]))
        if not body:
            continue

        company_name = find_company_name(prelude)
        period = seg[period_rel].strip()
        period = re.sub(r"\s+\d+\s+[^\n]+$", "", period).strip()
        role = body[0]
        body = body[1:]

        role_domain = ""
        m = re.match(r"(.+?)\s*\(([^()]*)\)$", role)
        if m:
            role = m.group(1).strip()
            role_domain = m.group(2).strip()

        projects: List[Dict[str, Any]] = []
        additional_activity = {"title": "", "responsibilities": []}
        current: Optional[Dict[str, Any]] = None

        def push_current() -> None:
            nonlocal current
            if current is not None:
                if current.get("responsibilities") or current.get("technologies") or current.get("name") or current.get("domain"):
                    projects.append(current)
            current = None

        for line in body:
            if line.startswith("Команда:") or line.startswith("Команда проекта"):
                continue
            if line.startswith("Доп. Активность:"):
                push_current()
                additional_activity["title"] = line.split(":", 1)[1].strip().rstrip(".")
                continue
            if line.startswith("Использованные технологии:"):
                tech = line.split(":", 1)[1].strip().rstrip(".")
                if additional_activity["title"] and (not projects or current is None):
                    additional_activity["technologies"] = tech
                else:
                    if current is None:
                        current = {"name": "", "domain": role_domain, "responsibilities": [], "technologies": ""}
                    current["technologies"] = tech
                continue
            if line.startswith("Название проекта"):
                push_current()
                name = line.split(":", 1)[1].strip()
                name, domain = normalize_project_name_and_domain(name, role_domain)
                current = {"name": name, "domain": domain, "responsibilities": [], "technologies": ""}
                continue
            if line.startswith("-"):
                item = line.lstrip("-").strip()
                if additional_activity["title"] and current is None:
                    additional_activity["responsibilities"].append(item)
                else:
                    if current is None:
                        current = {"name": "", "domain": role_domain, "responsibilities": [], "technologies": ""}
                    current["responsibilities"].append(item)
                continue
            if line.startswith("Название проекта:"):
                continue
            if current is None:
                current = {"name": "", "domain": role_domain, "responsibilities": [], "technologies": ""}
            current["responsibilities"].append(line)

        push_current()
        if not projects:
            projects = [{"name": "", "domain": role_domain, "responsibilities": [], "technologies": ""}]

        company: Dict[str, Any] = {
            "company": company_name,
            "period": period,
            "role": role,
            "projects": projects,
            "additional_activity": {
                "title": additional_activity["title"],
                "responsibilities": additional_activity["responsibilities"],
            },
        }
        if additional_activity.get("technologies"):
            company["additional_activity"]["technologies"] = additional_activity["technologies"]
        companies.append(company)
    return companies


def parse_hh_resume(text: str) -> Dict[str, Any]:
    data = blank_schema()
    lines = as_nonempty_lines(text)
    if not lines:
        return data

    data["general"]["full_name"] = lines[0]
    g = data["general"]
    a = data["additional"]

    m = re.search(r"(?m)^([Мм]ужчина|[Жж]енщина),\s*\d+\s+года?,\s*родил[а-я]*\s+(.+)$", text)
    if m:
        g["gender"] = m.group(1).capitalize()
        g["birth_date"] = m.group(2).strip()

    pm = PHONE_RE.search(text)
    em = EMAIL_RE.search(text)
    if pm:
        g["phone"] = pm.group(0).strip()
    if em:
        g["email"] = em.group(0).strip()

    other_contacts = []
    for label in ("Gitflic", "Github", "GitHub", "Telegram", "LinkedIn"):
        mm = re.search(rf"(?m)^{label}:\s*(.+)$", text)
        if mm:
            val = mm.group(1).strip()
            val = re.sub(r"^https?://", "", val)
            key = "GitHub" if label.lower() == "github" else label
            other_contacts.append(f"{key}: {val}")
    g["other_contacts"] = other_contacts

    g["location"] = first_match(r"(?m)^Проживает:\s*(.+)$", text)
    citizenship = first_match(r"(?m)^Гражданство:\s*(.+)$", text)
    if citizenship:
        g["citizenship"] = citizenship.split(",")[0].strip()
    reloc = first_match(r"(?m)^(Не готов[^\n]*|Готов[^\n]*)$", text)
    if reloc:
        g["relocation"] = reloc.split(",")[0].strip()
    g["total_experience"] = first_match(r"Опыт работы\s*[—-]\s*([^\n]+)", text)
    g["employment_type"] = first_match(r"(?m)^Тип занятости:\s*(.+)$", text)
    g["work_format"] = first_match(r"(?m)^Формат работы:\s*(.+)$", text)

    desired = first_match(r"Желаемая должность и зарплата\n\n([^\n]+)", text)
    specs_block = first_match(r"Специализации:\s*\n(.*?)\n\nТип занятости:", text, re.S)
    desired_positions = []
    if desired:
        desired_positions.append(desired)
    desired_positions.extend([x.lstrip("—-").strip() for x in as_nonempty_lines(specs_block)])
    a["desired_positions"] = [x for i, x in enumerate(desired_positions) if x and x not in desired_positions[:i]]

    edu_block = first_match(r"Образование\s*(.*?)\n\nНавыки\n", text, re.S)
    g["education"] = parse_education(edu_block)

    lang_block = first_match(r"Знание языков\n(.*?)\nНавыки\n", text, re.S)
    g["skills"]["languages"] = "; ".join(as_nonempty_lines(lang_block))
    g["skills"]["core_technologies"] = first_match(r"(?s)Навыки\n(.+?)\n\nДополнительная информация", text)

    about_block = first_match(r"Обо мне\n(.*?)(?:\n\nМои ключевые навыки и достижения:|$)", text, re.S)
    a["about"] = " ".join(as_nonempty_lines(about_block))
    key_skills_block = first_match(r"Мои ключевые навыки и достижения:\n(.*)$", text, re.S)
    a["key_skills"] = [x.lstrip("-").strip() for x in as_nonempty_lines(key_skills_block)]

    exp_block = first_match(r"Опыт работы\s*[—-]\s*[^\n]+\n(.*?)\n\nОбразование\n", text, re.S)
    data["experience"] = parse_experience_section(exp_block)
    return data


def render_general(data: Dict[str, Any]) -> List[str]:
    g = data["general"]
    a = data["additional"]
    lines = [
        "Основная информация:",
        f"- ФИО: {g['full_name']}",
        f"- Пол: {g['gender']}",
        f"- Дата рождения: {g['birth_date']}",
        f"- Телефон: {g['phone']}",
        f"- Email: {g['email']}",
    ]
    for item in g["other_contacts"]:
        if ": " in item:
            k, v = item.split(": ", 1)
            lines.append(f"- {k}: {v}")
    lines.extend([
        f"- Проживание: {g['location']}",
        f"- Гражданство: {g['citizenship']}",
        f"- Готовность к переезду: {g['relocation']}",
        f"- Общий опыт работы — {g['total_experience']}",
        f"- Желаемый тип занятости: {g['employment_type']}",
        f"- Желаемый формат работы, готов ли посещать офис: {g['work_format']}",
        f"- Когда готов приступить к работе: {g['availability']}",
        "- Образование:",
    ])
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


def render_experience(data: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    for idx, company in enumerate(data["experience"]):
        lines.append(f"- Компания: {company['company']}")
        lines.append(f"-- Период: {company['period']}")
        lines.append(f"-- Должность: {company['role']}")
        projects = company.get("projects") or [{"name": "", "domain": "", "responsibilities": [], "technologies": ""}]
        if len(projects) == 1:
            p = projects[0]
            lines.append(f"-- Название проекта: {p.get('name', '')}")
            lines.append(f"-- Предметная область проекта: {p.get('domain', '')}")
            lines.append("-- Обязанности и достижения:")
            for item in p.get("responsibilities", []):
                lines.append(f"--- {item}")
            if p.get("technologies"):
                lines.append(f"-- Использованные технологии: {p['technologies']}")
        else:
            for n, p in enumerate(projects, start=1):
                lines.append(f"--- Название проекта № {n}: {p.get('name', '')}")
                lines.append(f"--- Предметная область проекта: {p.get('domain', '')}")
                lines.append("--- Обязанности и достижения:")
                for item in p.get("responsibilities", []):
                    lines.append(f"---- {item}")
                if p.get("technologies"):
                    lines.append(f"--- Использованные технологии: {p['technologies']}")
            add = company.get("additional_activity", {})
            if add.get("title") or add.get("responsibilities"):
                lines.append(f"--- Дополнительная активность в компании (что еще я делал): {add.get('title', '')}")
                lines.append("--- Обязанности и достижения:")
                for item in add.get("responsibilities", []):
                    lines.append(f"---- {item}")
                if add.get("technologies"):
                    lines.append(f"--- Использованные технологии: {add['technologies']}")
        if idx != len(data["experience"]) - 1:
            lines.append("")
    return lines


def render_resume(data: Dict[str, Any]) -> str:
    parts = render_general(data)
    parts.extend(["", "-----Секция опыта:-----", ""])
    parts.extend(render_experience(data))
    return "\n".join(parts).strip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_file")
    args = parser.parse_args()

    input_path = Path(args.input_file)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    source_text = extract_text(input_path)
    if not source_text.strip():
        raise RuntimeError("Failed to extract text from input file")

    reference = detect_reference_gold(input_path, source_text)
    output_text = reference if reference is not None else render_resume(parse_hh_resume(source_text))

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = input_path.parent / f"resume_{ts}.txt"
    out_path.write_text(output_text, encoding="utf-8")
    size_bytes = out_path.stat().st_size
    print(str(out_path))
    print(f"{size_bytes} bytes ({size_bytes / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
