#!/usr/bin/env python3
"""Telegram bot: /start, /lang, /update (resume upload), in-memory state machine."""

import asyncio
import io
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from enum import Enum

import httpx
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from vacancy_extract import (
    MAX_VACANCY_FILE_BYTES,
    extract_vacancy_text,
    is_vacancy_file,
)
from resume_convert import (
    MAX_RESUME_CONVERT_BYTES,
    extract_resume_text,
    is_resume_convert_file,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

SOURCE_TELEGRAM = "telegram"


def _ui_client_url() -> str:
    return os.getenv("UI_CLIENT_URL", "").rstrip("/")


async def _ui_get(path: str, timeout: float = 10.0) -> httpx.Response:
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.get(_ui_client_url() + path)


async def _ui_post(path: str, json: dict | None = None, data: dict | None = None, files: dict | None = None, timeout: float = 30.0) -> httpx.Response:
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.post(_ui_client_url() + path, json=json, data=data, files=files)


# ru/en localization (UI only); default language if not set: en
TEXTS = {
    "ru": {
        "update_resume": "Обновить резюме",
        "generate_hash": "Сгенерировать токен",
        "help": "Помощь",
        "link_candidate": "Привязать нового кандидата (фигурально)",
        "match": "Сопоставить",
        "switch_mode": "Переключить режим",
        "help_candidate": "Кандидат: /convert — конвертировать резюме (hh.ru) в наш формат; /update — загрузить резюме (.txt); /hash — токен; /letter — сопроводительное письмо.",
        "help_recruiter": "Рекрутер: /link [токен] — связаться с кандидатом, /match — сопоставить с вакансией (сначала /link).",
        "help_admin": "Админ: /switch — переключить режим (кандидат ↔ рекрутер). В candidate: /convert, /update, /hash. В hr: /link, /match.",
        "help_common": "🚀 /start — открыть главное меню\n🌐 /lang — переключить язык (en / ru)\n❓ /help — список команд",
        "help_candidate_block": "♻️ /convert — конвертировать резюме в наш формат\n📄 /update — загрузить резюме\n🔑 /hash — получить токен для передачи рекрутеру\n💌 /letter — сгенерировать сопроводительное письмо по вакансии",
        "help_recruiter_block": "🔗 /link — привязаться к кандидату по его токену\n🎯 /match — сопоставить кандидата с вакансией",
        "help_admin_block": "👤 Админ: /switch — переключить режим (кандидат ↔ рекрутер). В режиме кандидата: /convert, /update, /hash, /letter. В режиме рекрутера: /link, /match.",
        "help_disclaimer": "Пользуясь сервисом, вы даете согласие на обработку данных: ferus.tech/pdn/",
        "cover_letter_btn": "Сопровод. письмо",
        "cmd_start": "Запуск",
        "cmd_help": "Помощь",
        "cmd_lang": "Язык",
        "cmd_convert": "Конвертировать резюме",
        "cmd_update": "Загрузить резюме",
        "cmd_hash": "Токен для рекрутера",
        "cmd_letter": "Сопровод. письмо по вакансии",
        "cmd_link": "Привязаться к кандидату",
        "cmd_match": "Сопоставить с вакансией",
        "cmd_switch": "Переключить режим (админ)",
        "choose_language": "Я вас внимательно слушаю. Выберите язык / Choose language:",
        "menu_ready": "Готово. /lang — язык.",
        "menu_mode_admin": "Режим: {mode}. /switch — переключить.",
        "access_denied": "Доступ запрещён. / Access denied.",
        "enter_candidate_token": "Введите код допуска (токен кандидата).",
        "lang_usage": "Использование: /lang ru или /lang en",
        "language_updated": "Язык обновлён.",
        "error_generic": "Я не упал, шеф, — меня уронили... (error_generic)",
        "user_not_found": "Пользователь не найден.",
        "recruiters_only": "Доступ только для офицеров связи... (только для рекрутеров).",
        "candidates_only": "Свой среди чужих... (только для кандидатов).",
        "use_link_first": "Свяжитесь с кандидатом через /link.",
        "send_vacancy_file": "Отправьте файл в формате doc(x)/pdf/txt или краткое описание (не больше 1 сообщения телеграм).",
        "send_cover_letter_vacancy": "Отправьте описание вакансии: файл (doc/docx/pdf/txt) или текст в одном сообщении.",
        "upload_resume_txt": "Загрузите резюме в формате .txt (макс. 1 МБ).",
        "vacancy_format": "Формат не поддерживается. Используйте doc(x)/pdf/txt.",
        "vacancy_extract_failed": "Не удалось извлечь текст из файла. Убедитесь, что PDF содержит слой текста (не скан).",
        "file_too_large_1mb": "Маловато будет? Нет. Великовато. Макс. 1 МБ.",
        "download_failed": "Не удалось загрузить файл.",
        "only_txt": "Только .txt.",
        "service_error": "Я не упал, шеф, — меня уронили... (service_error)",
        "request_error": "Я не упал, шеф, — меня уронили... (request_error)",
        "response_error": "Я не упал, шеф, — меня уронили... (response_error)",
        "processing": "Спокойствие, только спокойствие! (идет анализ данных...)",
        "language_chosen_waiting_hash": "Язык выбран. Ожидание хэша.",
        "choose_lang_ru_en": "Выберите язык: ru или en",
        "mode_switched": "Режим: {mode}.",
        "description_too_long": "Описание слишком большое, отправьте файл в формате doc(x)/pdf/txt.",
        "link_lost": "Канал связи разорван. Требуется повторная стыковка. Используйте /link.",
        "upload_error": "Я не упал, шеф, — меня уронили... (upload_error)",
        "upload_failed": "Я не упал, шеф, — меня уронили... (upload_failed)",
        "indexing_failed": "Я не упал, шеф, — меня уронили... (indexing_failed)",
        "resume_indexed": "Документ переведен и структурирован. Сегментов: {n}.",
        "invalid_response": "Я не упал, шеф, — меня уронили... (invalid_response)",
        "no_response": "Я не упал, шеф, — меня уронили... (no_response)",
        "timeout": "Я не упал, шеф, — меня уронили... (timeout)",
        "translation_failed": "Я не упал, шеф, — меня уронили... (translation_failed)",
        "no_results": "Совпадений не обнаружено.",
        "relevance": "Совпадение? Не думаю. Релевантность: {score:.2f}",
        "resume_accepted": "Резюме принято (job_id: {job_id}). Дождитесь завершения индексации.",
        "no_documents_stub": "Утром деньги, вечером стулья... Сначала загрузите резюмe.",
        "phone_escalation_reply": "Спасибо, свяжусь с Вами в ближайшее время.",
        "convert_resume": "Конвертировать резюме (hh.ru)",
        "send_resume_to_convert": "Отправьте файл резюме: doc, rtf, pdf или txt (макс. 2 МБ).",
        "convert_processing": "Конвертирую резюме в наш формат…",
        "convert_done": "Готово. Проверьте документ, при необходимости дополните и загрузите через «Обновить резюме».",
        "convert_failed": "Не удалось структурировать резюме.",
        "convert_timeout": "Таймаут при конвертации резюме (один из блоков не успел). Попробуйте снова или сократите текст.",
        "convert_extract_failed": "Не удалось извлечь текст из файла.",
        "convert_format": "Для конвертации нужен файл doc, rtf, pdf или txt.",
        "match_timeout": "Таймаут при анализе вакансии. Попробуйте снова.",
        "match_summary_failed": "Анализ вакансии не завершён: {error}.",
        "long_message_use_letter": "Сообщение слишком длинное для чата. Используйте команду /letter для генерации сопроводительного письма по вакансии.",
        "long_message_use_match": "Сообщение слишком длинное для чата. Используйте команду /match сопоставления резюме кандидата с описанием вакансии.",
        "restart_notice": "🔄 Сервис был перезагружен {timestamp}. Повторите отправку сообщения или команды, если ожидаете ответа.",
    },
    "en": {
        "update_resume": "Update resume",
        "generate_hash": "Generate hash",
        "help": "Help",
        "link_candidate": "Link candidate",
        "match": "Match",
        "switch_mode": "Switch mode",
        "help_candidate": "Candidate: /convert — convert resume (hh.ru) to our format; /update — upload resume (.txt); /hash — token; /letter — cover letter.",
        "help_recruiter": "Recruiter: /link [token] — link to candidate, /match — match with vacancy (use /link first).",
        "help_admin": "Admin: /switch — toggle mode (candidate ↔ recruiter). In candidate: /convert, /update, /hash. In hr: /link, /match.",
        "help_common": "🚀 /start — open main menu\n🌐 /lang — switch language (en / ru)\n❓ /help — list of commands",
        "help_candidate_block": "♻️ /convert — convert CV to our format\n📄 /update — upload CV\n🔑 /hash — get a token to share with recruiter\n💌 /letter — generate cover letter for a vacancy",
        "help_recruiter_block": "🔗 /link — link to candidate by their token\n🎯 /match — match candidate with vacancy",
        "help_admin_block": "👤 Admin: /switch — switch mode (candidate ↔ recruiter). In candidate mode: /convert, /update, /hash, /letter. In recruiter mode: /link, /match.",
        "help_disclaimer": "By using the service, you consent to data processing: ferus.tech/pdn/",
        "cover_letter_btn": "Cover letter",
        "cmd_start": "Start",
        "cmd_help": "Help",
        "cmd_lang": "Language",
        "cmd_convert": "Convert CV to our format",
        "cmd_update": "Upload CV",
        "cmd_hash": "Get token for recruiter",
        "cmd_letter": "Generate cover letter",
        "cmd_link": "Link to candidate",
        "cmd_match": "Match with vacancy",
        "cmd_switch": "Switch mode (admin)",
        "choose_language": "Choose language:",
        "menu_ready": "Ready. /lang for language.",
        "menu_mode_admin": "Mode: {mode}. /switch to toggle.",
        "access_denied": "Access denied.",
        "enter_candidate_token": "Enter candidate token.",
        "lang_usage": "Usage: /lang ru or /lang en",
        "language_updated": "Language updated.",
        "error_generic": "Error.",
        "user_not_found": "User not found.",
        "recruiters_only": "Recruiters only.",
        "candidates_only": "Candidates only.",
        "use_link_first": "Use /link to link to a candidate.",
        "send_vacancy_file": "Send a file (doc/docx/pdf/txt) or brief description (max 1 Telegram message).",
        "send_cover_letter_vacancy": "Send the job description: file (doc/docx/pdf/txt) or paste text in one message.",
        "upload_resume_txt": "Upload your resume as .txt file (max 1 MB).",
        "vacancy_format": "Format not supported. Use doc/docx/pdf/txt.",
        "vacancy_extract_failed": "Could not extract text from file. Ensure the PDF has a text layer (not a scan).",
        "file_too_large_1mb": "File too large (max 1 MB).",
        "download_failed": "Failed to download file.",
        "only_txt": "Only .txt files allowed.",
        "service_error": "Service error.",
        "request_error": "Request error.",
        "response_error": "Response error.",
        "processing": "Processing...",
        "language_chosen_waiting_hash": "Language chosen. Waiting for hash.",
        "choose_lang_ru_en": "Choose language: ru or en",
        "mode_switched": "Mode: {mode}.",
        "description_too_long": "Description too long. Send a file in doc/docx/pdf/txt format.",
        "link_lost": "Link lost. Use /link.",
        "upload_error": "Upload error.",
        "upload_failed": "Upload failed: {detail}.",
        "indexing_failed": "Indexing failed: {detail}.",
        "resume_indexed": "Resume indexed and split into {n} chunks.",
        "invalid_response": "Invalid response.",
        "no_response": "No response.",
        "timeout": "Timeout.",
        "translation_failed": "Response translation failed. Try again later or contact support.",
        "no_results": "No results (no candidate documents).",
        "relevance": "Relevance: {score:.2f}",
        "resume_accepted": "Resume accepted (job_id: {job_id}).",
        "no_documents_stub": "Please upload your resume first (/update).",
        "phone_escalation_reply": "Thank you, I will contact you shortly.",
        "convert_resume": "Convert resume (hh.ru)",
        "send_resume_to_convert": "Send resume file: doc, rtf, pdf or txt (max 2 MB).",
        "convert_processing": "Converting resume to our format…",
        "convert_done": "Done. Review the document, edit if needed, then upload via «Update resume».",
        "convert_failed": "Failed to structure resume.",
        "convert_timeout": "Resume conversion timed out (one of the blocks did not complete). Try again or shorten the text.",
        "convert_extract_failed": "Could not extract text from file.",
        "convert_format": "For conversion use doc, rtf, pdf or txt file.",
        "match_timeout": "Match analysis timed out. Try again.",
        "match_summary_failed": "Match analysis did not complete: {error}.",
        "long_message_use_letter": "Message too long for chat. Use /letter to generate a cover letter for the vacancy.",
        "long_message_use_match": "Message too long for chat. Use /match to run vacancy match.",
        "restart_notice": "🔄 Service was restarted at {timestamp}. Please resend your message or command if you are waiting for a response.",
    },
}

BTN_TO_ACTION: dict[str, str] = {}
for _lang, _d in TEXTS.items():
    BTN_TO_ACTION[_d["update_resume"]] = "update"
    BTN_TO_ACTION[_d["generate_hash"]] = "hash"
    BTN_TO_ACTION[_d["help"]] = "help"
    BTN_TO_ACTION[_d["link_candidate"]] = "link"
    BTN_TO_ACTION[_d["match"]] = "match"
    BTN_TO_ACTION[_d["switch_mode"]] = "switch"
    BTN_TO_ACTION[_d["convert_resume"]] = "convert"
    BTN_TO_ACTION[_d["cover_letter_btn"]] = "letter"

# Варианты заглушки «нет документов», которые может вернуть CM; бот подменяет их на _t("no_documents_stub", lang)
NO_DOCUMENTS_STUB_CM_VARIANTS = frozenset({
    TEXTS["en"]["no_documents_stub"],
    TEXTS["ru"]["no_documents_stub"],
    "Сначала загрузите резюме (/update). / Please upload your resume first (/update).",
    f"{TEXTS['ru']['no_documents_stub']} / {TEXTS['en']['no_documents_stub']}",
})


def _t(key: str, lang: str | None, **kwargs: str | int) -> str:
    l = (lang or "en").lower()
    if l not in TEXTS:
        l = "en"
    s = TEXTS[l].get(key, TEXTS["en"].get(key, key))
    if kwargs:
        try:
            return s.format(**kwargs)
        except KeyError:
            return s
    return s


# Ключи сообщений об ошибках бота: в ответ пользователю добавляется trace_id для поиска в логах
BOT_ERROR_KEYS = frozenset({
    "error_generic", "service_error", "request_error", "response_error",
    "upload_error", "upload_failed", "indexing_failed", "invalid_response",
    "no_response", "timeout", "translation_failed",
})


def _new_trace_id() -> str:
    return uuid.uuid4().hex[:8]


def _bot_error_message(key: str, lang: str, **kwargs: str | int) -> str:
    """Возвращает текст ошибки из TEXTS с добавлением trace_id; пишет trace_id в лог для администратора."""
    trace_id = _new_trace_id()
    logger.warning("bot_error key=%s trace_id=%s", key, trace_id)
    return _t(key, lang, **kwargs) + f" trace_id={trace_id}"


def _menu_keyboard(role: str | None, mode: str | None, lang: str | None, dialog_owner_id: str | None = None) -> ReplyKeyboardMarkup:
    effective = "candidate" if (role == "admin" and mode != "hr") else (role or "recruiter")
    if role == "admin":
        rows = [[KeyboardButton(_t("switch_mode", lang))]]
        if mode == "hr":
            rows.extend([
                [KeyboardButton(_t("link_candidate", lang))],
                [KeyboardButton(_t("match", lang)), KeyboardButton(_t("help", lang))] if dialog_owner_id else [KeyboardButton(_t("help", lang))],
            ])
        else:
            rows.extend([
                [KeyboardButton(_t("convert_resume", lang)), KeyboardButton(_t("update_resume", lang))],
                [KeyboardButton(_t("generate_hash", lang)), KeyboardButton(_t("cover_letter_btn", lang))],
                [KeyboardButton(_t("help", lang))],
            ])
    elif effective == "recruiter":
        rows = [
            [KeyboardButton(_t("link_candidate", lang))],
            [KeyboardButton(_t("match", lang)), KeyboardButton(_t("help", lang))] if dialog_owner_id else [KeyboardButton(_t("help", lang))],
        ]
    else:
        rows = [
            [KeyboardButton(_t("convert_resume", lang)), KeyboardButton(_t("update_resume", lang))],
            [KeyboardButton(_t("generate_hash", lang)), KeyboardButton(_t("cover_letter_btn", lang))],
            [KeyboardButton(_t("help", lang))],
        ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


class BotState(str, Enum):
    UNREGISTERED = "unregistered"
    WAITING_HASH = "waiting_hash"
    WAITING_LINK_TOKEN = "waiting_link_token"
    ACTIVE = "active"
    RESUME_PENDING = "resume_pending"
    WAITING_RESUME_TO_CONVERT = "waiting_resume_to_convert"
    WAITING_VACANCY_TEXT = "waiting_vacancy_text"
    WAITING_COVER_LETTER_VACANCY = "waiting_cover_letter_vacancy"


def _looks_like_token(text: str) -> bool:
    t = (text or "").strip()
    return len(t) >= 20 and t.replace("-", "").replace("_", "").isalnum()


def _message_has_url(update: Update) -> bool:
    if not update.message or not update.message.text:
        return False
    entities = update.message.entities or []
    if any(getattr(e, "type", None) in ("url", "text_link") for e in entities):
        return True
    return bool(re.search(r"https?://\S+", update.message.text))


def _message_has_phone(update: Update) -> bool:
    if not update.message or not update.message.text:
        return False
    entities = update.message.entities or []
    if any(getattr(e, "type", None) == "phone_number" for e in entities):
        return True
    return bool(re.search(r"\+?[\d\s\-\(\)]{10,}", update.message.text))


MAX_RESUME_SIZE_BYTES = 1024 * 1024  # 1MB
MAX_VACANCY_TEXT_CHARS = 4096  # Telegram max message length (single message)
MAX_CHAT_MESSAGE_CHARS = 1000  # In normal chat, messages longer than this get fallback to /letter or /match


def _truncate_at_boundary(text: str, max_chars: int) -> str:
    """Truncate to max_chars at paragraph or line boundary to avoid cutting mid-sentence."""
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars + 1]
    for sep in ("\n\n", "\n", ". ", " "):
        idx = cut.rfind(sep)
        if idx > max_chars // 2:
            out = cut[: idx + len(sep)].rstrip() if sep != " " else cut[:idx].rstrip()
            return out if out else text[:max_chars]
    return text[:max_chars]


def _split_match_reply_by_sections(reply_text: str, max_chars: int) -> list[str]:
    """Split match reply by section headers (2. / 3.) so each message stays under max_chars.
    Handles both \\n\\n and \\n before headers (e.g. after translation)."""
    if len(reply_text) <= max_chars:
        return [reply_text]
    for sep2, sep3 in (("\n\n2. ", "\n\n3. "), ("\n2. ", "\n3. ")):
        idx2 = reply_text.find(sep2)
        idx3 = reply_text.find(sep3)
        if idx2 > 0 and idx3 > idx2:
            part1 = reply_text[:idx2].strip()
            part2 = (reply_text[idx2:idx3]).strip()
            part3 = (reply_text[idx3:]).strip()
            out = []
            for block in (part1, part2, part3):
                if not block:
                    continue
                while block:
                    if len(block) <= max_chars:
                        out.append(block)
                        break
                    out.append(_truncate_at_boundary(block, max_chars))
                    consumed = len(out[-1])
                    block = block[consumed:].lstrip() if consumed < len(block) else ""
            if out:
                return out
    chunks = []
    rest = reply_text
    while rest:
        chunks.append(_truncate_at_boundary(rest, max_chars))
        consumed = len(chunks[-1])
        rest = rest[consumed:].lstrip() if consumed < len(rest) else ""
    return chunks


POLL_INTERVAL_SEC = 2
POLL_MAX_ATTEMPTS = 90  # ~3 min
MESSAGE_POLL_MAX_ATTEMPTS = 45  # ~1.5 min for RAG


async def _poll_message_and_get_answer(message_id: str) -> str:
    for _ in range(MESSAGE_POLL_MAX_ATTEMPTS):
        await asyncio.sleep(POLL_INTERVAL_SEC)
        try:
            r = await _ui_get(f"/messages/{message_id}")
        except Exception as e:
            logger.warning("Poll message status failed: %s", e)
            continue
        if r.status_code not in (200, 404, 503):
            continue
        if r.status_code != 200:
            continue
        data = r.json()
        status = data.get("status", "")
        if status == "completed":
            t = (data.get("assistant_text_translated") or data.get("assistant_text") or "").strip()
            return t or _bot_error_message("no_response", "en")
        if status == "escalated":
            return _bot_error_message("translation_failed", "en")
        if status == "failed":
            fallback = data.get("assistant_text") or _t("error_generic", "en")
            trace_id = _new_trace_id()
            logger.warning("bot_error poll status=failed trace_id=%s", trace_id)
            return fallback + f" trace_id={trace_id}"
    return _bot_error_message("timeout", "en")


async def _poll_and_edit_chat_response(
    bot,
    message_id: str,
    chat_id: int,
    msg_id: int,
    lang: str,
) -> None:
    answer = await _poll_message_and_get_answer(message_id)
    if answer.strip() in NO_DOCUMENTS_STUB_CM_VARIANTS:
        answer = _t("no_documents_stub", lang)
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=answer,
        )
    except Exception as e:
        logger.warning("edit_message_text for chat failed: %s", e)


async def _process_chat_message(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    tid: int,
    text: str,
    lang: str,
) -> None:
    if not _ui_client_url():
        await context.bot.send_message(chat_id=chat_id, text=_bot_error_message("service_error", lang))
        return
    try:
        r = await _ui_get(f"/users/me?external_id={tid}&source={SOURCE_TELEGRAM}")
    except Exception as e:
        logger.exception("ui-client users/me error: %s", e)
        await context.bot.send_message(chat_id=chat_id, text=_bot_error_message("service_error", lang))
        return
    if r.status_code == 404:
        try:
            start_r = await _ui_post("/users/start", json={"external_id": str(tid), "source": SOURCE_TELEGRAM})
        except Exception as e:
            logger.exception("ui-client users/start error: %s", e)
            await context.bot.send_message(chat_id=chat_id, text=_bot_error_message("service_error", lang))
            return
        if start_r.status_code != 200:
            await context.bot.send_message(chat_id=chat_id, text=_bot_error_message("service_error", lang))
            return
        body = start_r.json()
        if body.get("need_language"):
            _set_state(tid, BotState.WAITING_HASH)
            await context.bot.send_message(
                chat_id=chat_id,
                text=_t("choose_language", "en"),
                reply_markup=_lang_keyboard(),
            )
            if text.strip():
                _pending_message[tid] = text.strip()
            return
    else:
        if r.status_code != 200:
            await context.bot.send_message(chat_id=chat_id, text=_bot_error_message("service_error", lang))
            return
        user = r.json()
    role = user.get("role")
    mode = str(user.get("mode") or "")
    owner_id: str | None = None
    if _is_candidate_eligible(role, mode):
        owner_id = str(tid)
    elif _is_hr_eligible(role, mode):
        owner_id = user.get("dialog_owner_id")
        if not owner_id:
            await context.bot.send_message(chat_id=chat_id, text=_t("use_link_first", lang))
            return
    if not owner_id:
        return
    sent = await context.bot.send_message(chat_id=chat_id, text=_t("processing", lang))
    try:
        r = await _ui_post("/chat", json={"owner_id": owner_id, "message": text})
    except Exception as e:
        logger.exception("ui-client chat error: %s", e)
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=sent.message_id,
            text=_bot_error_message("request_error", lang),
        )
        return
    if r.status_code != 200:
        err = _t("error_generic", lang)
        try:
            d = r.json().get("detail", r.text) if r.text else ""
            d = d[0] if isinstance(d, list) and d else d
            d = d.get("msg", str(d)) if isinstance(d, dict) else str(d)
            if d:
                err = str(d)[:500]
        except Exception:
            pass
        trace_id = _new_trace_id()
        logger.warning("bot_error chat non-200 trace_id=%s detail=%.200s", trace_id, err)
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=sent.message_id,
            text=err + f" trace_id={trace_id}",
        )
        return
    body = r.json()
    msg_id = body.get("message_id", "")
    if not msg_id:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=sent.message_id,
            text=_bot_error_message("response_error", lang),
        )
        return
    asyncio.create_task(
        _poll_and_edit_chat_response(
            context.bot,
            msg_id,
            sent.chat_id,
            sent.message_id,
            lang,
        )
    )


async def _poll_job_and_edit_message(
    bot,
    job_id: str,
    chat_id: int,
    message_id: int,
    lang: str,
) -> None:
    for _ in range(POLL_MAX_ATTEMPTS):
        await asyncio.sleep(POLL_INTERVAL_SEC)
        try:
            r = await _ui_get(f"/documents/tasks/{job_id}")
        except Exception as e:
            logger.warning("Poll task status failed: %s", e)
            continue
        if r.status_code != 200:
            continue
        data = r.json()
        status = data.get("status", "")
        if status == "completed":
            n = data.get("chunk_count", 0)
            text = _t("resume_indexed", lang, n=n)
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                )
            except Exception as e:
                logger.warning("edit_message_text failed: %s", e)
            return
        if status == "failed":
            err = data.get("error_message", "Error")
            trace_id = _new_trace_id()
            logger.warning("bot_error indexing_failed trace_id=%s err=%s", trace_id, err)
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=_t("indexing_failed", "en", detail=err) + f" trace_id={trace_id}",
                )
            except Exception:
                pass
            return


_user_states: dict[int, BotState] = {}
# Pending message text for implicit /start: after user picks language we process this message
_pending_message: dict[int, str] = {}


def _get_state(telegram_id: int) -> BotState:
    return _user_states.get(telegram_id, BotState.UNREGISTERED)


def _set_state(telegram_id: int, state: BotState) -> None:
    _user_states[telegram_id] = state


def _is_candidate_eligible(role, mode) -> bool:
    if role == "candidate":
        return True
    if role == "admin" and mode == "candidate":
        return True
    return False


def _is_hr_eligible(role, mode) -> bool:
    if role == "recruiter":
        return True
    if role is None:
        # Пользователь не в списке кандидатов — считаем рекрутером до первой привязки по токену
        return True
    if role == "admin" and mode == "hr":
        return True
    return False


def _lang_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Русский", callback_data="lang:ru"),
            InlineKeyboardButton("English", callback_data="lang:en"),
        ],
    ])


async def _ensure_started(update: Update, context: ContextTypes.DEFAULT_TYPE, pending_text: str | None = None) -> bool:
    if not update.message or not update.message.from_user:
        return True
    tid = update.message.from_user.id
    if _get_state(tid) != BotState.UNREGISTERED:
        return True
    if not _ui_client_url():
        return True
    try:
        r = await _ui_get(f"/users/me?external_id={tid}&source={SOURCE_TELEGRAM}")
    except Exception:
        return True
    if r.status_code == 200:
        _set_state(tid, BotState.ACTIVE)
        return True
    if r.status_code != 404:
        return True
    try:
        start_r = await _ui_post("/users/start", json={"external_id": str(tid), "source": SOURCE_TELEGRAM})
    except Exception:
        return True
    if start_r.status_code != 200:
        return True
    body = start_r.json()
    if body.get("need_language"):
        _set_state(tid, BotState.WAITING_HASH)
        if pending_text and pending_text.strip():
            _pending_message[tid] = pending_text.strip()
        await update.message.reply_text(
            _t("choose_language", "en"),
            reply_markup=_lang_keyboard(),
        )
        return False
    _set_state(tid, BotState.ACTIVE)
    return True


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.from_user:
        return
    tid = update.message.from_user.id
    if not _ui_client_url():
        await update.message.reply_text(_bot_error_message("service_error", "en"))
        return
    try:
        r = await _ui_post("/users/start", json={"external_id": str(tid), "source": SOURCE_TELEGRAM})
    except Exception as e:
        logger.exception("ui-client start error: %s", e)
        await update.message.reply_text(_bot_error_message("service_error", "en"))
        return
    if r.status_code != 200:
        await update.message.reply_text(_bot_error_message("service_error", "en"))
        return
    body = r.json()
    if body.get("need_language"):
        _set_state(tid, BotState.WAITING_HASH)
        await update.message.reply_text(
            _t("choose_language", "en"),
            reply_markup=_lang_keyboard(),
        )
        return
    user = body.get("user") or {}
    _set_state(tid, BotState.ACTIVE)
    lang = str(user.get("language") or "en")
    role = user.get("role")
    mode = str(user.get("mode") or "")
    kbd = _menu_keyboard(role, mode, lang, user.get("dialog_owner_id"))
    menu = _t("menu_mode_admin", lang, mode=mode) if role == "admin" else _t("menu_ready", lang)
    await update.message.reply_text(menu, reply_markup=kbd)


async def lang_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("lang:"):
        return
    await query.answer()
    lang = query.data.split(":")[1]
    tid = query.from_user.id if query.from_user else 0
    try:
        r = await _ui_post("/users/language", json={"external_id": str(tid), "source": SOURCE_TELEGRAM, "language": lang})
    except Exception as e:
        logger.exception("ui-client language error: %s", e)
        await query.edit_message_text(_bot_error_message("error_generic", "en"))
        return
    if r.status_code != 200:
        await query.edit_message_text(_bot_error_message("error_generic", "en"))
        return
    body = r.json()
    user = (body.get("user") or body.get("menu")) or {}
    _set_state(tid, BotState.ACTIVE)
    await query.edit_message_text(_t("language_updated", lang))
    kbd = _menu_keyboard(user.get("role"), str(user.get("mode") or ""), lang, user.get("dialog_owner_id"))
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=_t("menu_ready", lang),
        reply_markup=kbd,
    )
    pending = _pending_message.pop(tid, None)
    if pending:
        await _process_chat_message(
            context,
            query.message.chat_id,
            tid,
            pending,
            lang,
        )


async def _do_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    hash_value: str,
) -> None:
    if not update.message or not update.message.from_user:
        return
    tid = update.message.from_user.id
    if not await _ensure_started(update, context):
        return
    try:
        r = await _ui_post("/users/link", json={"external_id": str(tid), "source": SOURCE_TELEGRAM, "token": hash_value})
    except Exception as e:
        logger.exception("ui-client link error: %s", e)
        await update.message.reply_text(_bot_error_message("error_generic", "en"))
        return
    if r.status_code != 200:
        await update.message.reply_text(_bot_error_message("error_generic", "en"))
        return
    body = r.json()
    ok = body.get("ok", False)
    msg = body.get("message", "")
    owner_id = body.get("owner_id")
    user = body.get("user") or {}
    sent = await update.message.reply_text(msg)
    if ok and sent:
        try:
            await context.bot.pin_chat_message(
                chat_id=update.message.chat_id,
                message_id=sent.message_id,
            )
        except Exception:
            pass
        lang = str(user.get("language") or "en")
        kbd = _menu_keyboard(user.get("role"), str(user.get("mode") or ""), lang, owner_id)
        await update.message.reply_text(_t("menu_ready", lang), reply_markup=kbd)


async def switch_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.from_user:
        return
    tid = update.message.from_user.id
    try:
        r = await _ui_get(f"/users/me?external_id={tid}&source={SOURCE_TELEGRAM}")
    except Exception as e:
        logger.exception("ui-client me error: %s", e)
        await update.message.reply_text(_t("access_denied", "en"))
        return
    if r.status_code != 200:
        await update.message.reply_text(_t("access_denied", "en"))
        return
    user = r.json()
    if user.get("role") != "admin":
        await update.message.reply_text(_t("access_denied", str(user.get("language") or "en")))
        return
    try:
        sw = await _ui_post(f"/users/switch-mode?external_id={tid}&source={SOURCE_TELEGRAM}")
    except Exception as e:
        logger.exception("ui-client switch-mode error: %s", e)
        await update.message.reply_text(_t("access_denied", "en"))
        return
    if sw.status_code != 200:
        await update.message.reply_text(_t("access_denied", str(user.get("language") or "en")))
        return
    body = sw.json()
    new_mode = body.get("mode", "candidate")
    u = body.get("user") or user
    lang = str(u.get("language") or "en")
    kbd = _menu_keyboard("admin", new_mode, lang, u.get("dialog_owner_id") if new_mode == "hr" else None)
    await update.message.reply_text(_t("mode_switched", lang, mode=new_mode), reply_markup=kbd)


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.from_user:
        return
    tid = update.message.from_user.id
    try:
        r = await _ui_get(f"/users/me?external_id={tid}&source={SOURCE_TELEGRAM}")
    except Exception:
        r = None
    user = r.json() if r and r.status_code == 200 else None
    lang = str(user.get("language") or "en") if user else "en"
    role = user.get("role") if user else None
    mode = str(user.get("mode") or "") if user else ""
    common = _t("help_common", lang)
    if role == "admin":
        role_block = _t("help_admin_block", lang)
    elif role == "recruiter" or (role == "admin" and mode == "hr"):
        role_block = _t("help_recruiter_block", lang)
    elif role == "candidate":
        role_block = _t("help_candidate_block", lang)
    else:
        role_block = _t("help_recruiter_block", lang)
    disclaimer = _t("help_disclaimer", lang)
    text = f"{common}\n\n{role_block}\n\n{disclaimer}"
    kbd = _menu_keyboard(role, mode, lang, user.get("dialog_owner_id") if user else None) if user else None
    await update.message.reply_text(text, reply_markup=kbd)


async def link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.from_user:
        return
    tid = update.message.from_user.id
    args = (context.args or [])
    if not args:
        _set_state(tid, BotState.WAITING_LINK_TOKEN)
        lang = "en"
        try:
            r = await _ui_get(f"/users/me?external_id={tid}&source={SOURCE_TELEGRAM}")
            if r.status_code == 200:
                lang = str(r.json().get("language") or "en")
        except Exception:
            pass
        await update.message.reply_text(_t("enter_candidate_token", lang))
        return
    await _do_link(update, context, args[0])


async def hash_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.from_user:
        return
    tid = update.message.from_user.id
    if not await _ensure_started(update, context):
        return
    try:
        r = await _ui_post(f"/hash/generate?external_id={tid}&source={SOURCE_TELEGRAM}")
    except Exception as e:
        logger.exception("ui-client hash/generate error: %s", e)
        await update.message.reply_text(_bot_error_message("service_error", "en"))
        return
    if r.status_code == 403:
        try:
            u = (await _ui_get(f"/users/me?external_id={tid}&source={SOURCE_TELEGRAM}")).json()
            lang = str(u.get("language") or "en")
        except Exception:
            lang = "en"
        await update.message.reply_text(_t("candidates_only", lang))
        return
    if r.status_code != 200:
        await update.message.reply_text(_bot_error_message("service_error", "en"))
        return
    token = r.json().get("token", "")
    await update.message.reply_text(f"<code>{token}</code>", parse_mode="HTML")


async def lang_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.from_user:
        return
    tid = update.message.from_user.id
    args = (context.args or [])
    if not args or args[0] not in ("ru", "en"):
        await update.message.reply_text(_t("lang_usage", "en"))
        return
    lang = args[0]
    try:
        r = await _ui_post("/users/language", json={"external_id": str(tid), "source": SOURCE_TELEGRAM, "language": lang})
    except Exception as e:
        logger.exception("ui-client language error: %s", e)
        await update.message.reply_text(_t("user_not_found", "en"))
        return
    if r.status_code != 200:
        await update.message.reply_text(_t("user_not_found", "en"))
        return
    body = r.json()
    user = body.get("user") or body.get("menu") or {}
    kbd = _menu_keyboard(user.get("role"), str(user.get("mode") or ""), lang, user.get("dialog_owner_id"))
    text = "Язык обновлён." if lang == "ru" else "Language updated."
    await update.message.reply_text(text, reply_markup=kbd)


async def match_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.from_user:
        return
    tid = update.message.from_user.id
    if not await _ensure_started(update, context):
        return
    try:
        r = await _ui_get(f"/users/me?external_id={tid}&source={SOURCE_TELEGRAM}")
    except Exception:
        return
    if r.status_code != 200:
        return
    user = r.json()
    if not _is_hr_eligible(user.get("role"), str(user.get("mode") or "")):
        await update.message.reply_text(_t("recruiters_only", str(user.get("language") or "en")))
        return
    if not user.get("dialog_owner_id"):
        await update.message.reply_text(_t("use_link_first", str(user.get("language") or "en")))
        return
    _set_state(tid, BotState.WAITING_VACANCY_TEXT)
    await update.message.reply_text(_t("send_vacancy_file", str(user.get("language") or "en")))


async def cover_letter_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.from_user:
        return
    tid = update.message.from_user.id
    if not await _ensure_started(update, context):
        return
    try:
        r = await _ui_get(f"/users/me?external_id={tid}&source={SOURCE_TELEGRAM}")
    except Exception:
        return
    if r.status_code != 200:
        return
    user = r.json()
    if not _is_candidate_eligible(user.get("role"), str(user.get("mode") or "")):
        await update.message.reply_text(_t("candidates_only", str(user.get("language") or "en")))
        return
    _set_state(tid, BotState.WAITING_COVER_LETTER_VACANCY)
    try:
        await _ui_post(f"/users/set-pending-state?external_id={tid}&source={SOURCE_TELEGRAM}&state={BotState.WAITING_COVER_LETTER_VACANCY.value}")
    except Exception:
        pass
    await update.message.reply_text(_t("send_cover_letter_vacancy", str(user.get("language") or "en")))


async def convert_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.from_user:
        return
    tid = update.message.from_user.id
    if not await _ensure_started(update, context):
        return
    try:
        r = await _ui_get(f"/users/me?external_id={tid}&source={SOURCE_TELEGRAM}")
    except Exception:
        return
    if r.status_code != 200:
        return
    user = r.json()
    if not _is_candidate_eligible(user.get("role"), str(user.get("mode") or "")):
        await update.message.reply_text(_t("candidates_only", str(user.get("language") or "en")))
        return
    _set_state(tid, BotState.WAITING_RESUME_TO_CONVERT)
    await update.message.reply_text(_t("send_resume_to_convert", str(user.get("language") or "en")))


async def update_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.from_user:
        return
    tid = update.message.from_user.id
    if not await _ensure_started(update, context):
        return
    try:
        r = await _ui_get(f"/users/me?external_id={tid}&source={SOURCE_TELEGRAM}")
    except Exception:
        return
    if r.status_code != 200:
        return
    user = r.json()
    if not _is_candidate_eligible(user.get("role"), str(user.get("mode") or "")):
        await update.message.reply_text(_t("candidates_only", str(user.get("language") or "en")))
        return
    _set_state(tid, BotState.RESUME_PENDING)
    await update.message.reply_text(_t("upload_resume_txt", str(user.get("language") or "en")))


async def _do_match_vacancy(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    vacancy_text: str,
) -> None:
    tid = update.message.from_user.id
    try:
        r = await _ui_get(f"/users/me?external_id={tid}&source={SOURCE_TELEGRAM}")
    except Exception:
        return
    if r.status_code != 200:
        return
    user = r.json()
    if not _is_hr_eligible(user.get("role"), str(user.get("mode") or "")):
        return
    lang = str(user.get("language") or "en")
    if not user.get("dialog_owner_id"):
        await update.message.reply_text(_t("link_lost", lang))
        return
    if not _ui_client_url():
        await update.message.reply_text(_bot_error_message("service_error", lang))
        return
    sent = await update.message.reply_text(_t("processing", lang))
    try:
        match_r = await _ui_post("/match", json={"external_id": str(tid), "source": SOURCE_TELEGRAM, "vacancy_text": vacancy_text}, timeout=float(os.getenv("MATCH_REQUEST_TIMEOUT", "360.0")))
    except httpx.TimeoutException:
        logger.warning("Match API timeout")
        try:
            await context.bot.edit_message_text(chat_id=sent.chat_id, message_id=sent.message_id, text=_t("match_timeout", lang))
        except Exception as ed:
            logger.warning("edit_message_text after match timeout failed: %s", ed)
        return
    except Exception as e:
        logger.exception("Match API error: %s", e)
        try:
            await context.bot.edit_message_text(chat_id=sent.chat_id, message_id=sent.message_id, text=_bot_error_message("request_error", lang))
        except Exception as ed:
            logger.warning("edit_message_text after match error failed: %s", ed)
        return
    if match_r.status_code != 200:
        trace_id = _new_trace_id()
        err = _t("error_generic", lang)
        try:
            d = match_r.json().get("detail", match_r.text)
            d = d[0] if isinstance(d, list) and d else d
            err = d.get("msg", str(d)) if isinstance(d, dict) else str(d)
        except Exception:
            pass
        logger.warning("bot_error match_vacancy non-200 trace_id=%s", trace_id)
        try:
            await context.bot.edit_message_text(chat_id=sent.chat_id, message_id=sent.message_id, text=str(err)[:500] + f" trace_id={trace_id}")
        except Exception as ed:
            logger.warning("edit_message_text after match non-200 failed: %s", ed)
        return
    data = match_r.json()
    if data.get("no_results"):
        try:
            await context.bot.edit_message_text(chat_id=sent.chat_id, message_id=sent.message_id, text=_t("no_results", lang))
        except Exception as ed:
            logger.warning("edit_message_text failed: %s", ed)
        return
    parts = data.get("parts") or []
    if not parts:
        try:
            await context.bot.edit_message_text(chat_id=sent.chat_id, message_id=sent.message_id, text=_bot_error_message("invalid_response", lang))
        except Exception:
            pass
        return
    try:
        await context.bot.edit_message_text(chat_id=sent.chat_id, message_id=sent.message_id, text=parts[0])
        for part in parts[1:]:
            await context.bot.send_message(chat_id=sent.chat_id, text=part)
    except Exception as e:
        logger.warning("match result send failed: %s", e)


async def _do_cover_letter(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    vacancy_text: str,
) -> None:
    tid = update.message.from_user.id
    try:
        u = await _ui_get(f"/users/me?external_id={tid}&source={SOURCE_TELEGRAM}")
    except Exception:
        return
    if u.status_code != 200:
        return
    user = u.json()
    lang = str(user.get("language") or "en")
    if not _ui_client_url():
        await update.message.reply_text(_bot_error_message("service_error", lang))
        return
    await update.message.reply_text(_t("processing", lang))
    try:
        r = await _ui_post("/cover-letter", json={"external_id": str(tid), "source": SOURCE_TELEGRAM, "vacancy_text": vacancy_text.strip()}, timeout=90.0)
    except Exception as e:
        logger.exception("Cover letter API error: %s", e)
        await update.message.reply_text(_bot_error_message("request_error", lang))
        return
    if r.status_code != 200:
        try:
            d = r.json().get("detail", r.text) if r.text else "Unknown error"
            d = d[0] if isinstance(d, list) and d else d
            err = d.get("msg", str(d)) if isinstance(d, dict) else str(d)
        except Exception:
            err = _t("error_generic", lang)
        trace_id = _new_trace_id()
        logger.warning("bot_error cover_letter non-200 trace_id=%s detail=%.200s", trace_id, err)
        await update.message.reply_text(str(err)[:500] + f" trace_id={trace_id}")
        return
    try:
        data = r.json()
    except Exception:
        await update.message.reply_text(_bot_error_message("invalid_response", lang))
        return
    letter = data.get("letter")
    err = data.get("error")
    if letter:
        await update.message.reply_text(_truncate_at_boundary(letter, MAX_VACANCY_TEXT_CHARS))
        return
    if err == "no_documents":
        await update.message.reply_text(_t("no_documents_stub", lang))
        return
    await update.message.reply_text(_bot_error_message("service_error", lang))


async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle resume file upload or vacancy file for /match."""
    if not update.message or not update.message.from_user or not update.message.document:
        return
    tid = update.message.from_user.id
    if not await _ensure_started(update, context):
        return
    doc = update.message.document
    state = _get_state(tid)
    try:
        ur = await _ui_get(f"/users/me?external_id={tid}&source={SOURCE_TELEGRAM}")
    except Exception:
        return
    if ur.status_code != 200:
        return
    user = ur.json()

    lang = str(user.get("language") or "en")
    filename = doc.file_name or ""
    candidate_eligible = _is_candidate_eligible(
        user.get("role"), str(user.get("mode")) if user.get("mode") else None
    )
    # Restore WAITING_COVER_LETTER_VACANCY after restart (state is in DB as pending_state)
    effective_state = state
    if candidate_eligible and state == BotState.ACTIVE and user.get("pending_state") == BotState.WAITING_COVER_LETTER_VACANCY.value:
        effective_state = BotState.WAITING_COVER_LETTER_VACANCY
    if candidate_eligible and effective_state == BotState.WAITING_COVER_LETTER_VACANCY:
        if not is_vacancy_file(filename):
            await update.message.reply_text(_t("vacancy_format", lang))
            return
        if doc.file_size and doc.file_size > MAX_VACANCY_FILE_BYTES:
            await update.message.reply_text(_t("file_too_large_1mb", lang))
            return
        try:
            tg_file = await context.bot.get_file(doc.file_id)
            raw = await tg_file.download_as_bytearray()
        except Exception as e:
            logger.exception("Failed to download vacancy file for cover letter: %s", e)
            await update.message.reply_text(_t("download_failed", lang))
            return
        vacancy_text = extract_vacancy_text(filename, bytes(raw))
        if not vacancy_text or not vacancy_text.strip():
            await update.message.reply_text(_t("vacancy_extract_failed", lang))
            return
        _set_state(tid, BotState.ACTIVE)
        try:
            await _ui_post(f"/users/set-pending-state?external_id={tid}&source={SOURCE_TELEGRAM}")
        except Exception:
            pass
        await _do_cover_letter(update, context, vacancy_text.strip())
        return

    hr_with_dialog = _is_hr_eligible(
        user.get("role"), str(user.get("mode")) if user.get("mode") else None
    ) and bool(user.get("dialog_owner_id"))

    if hr_with_dialog:
        if not is_vacancy_file(filename):
            await update.message.reply_text(_t("vacancy_format", lang))
            return
        if doc.file_size and doc.file_size > MAX_VACANCY_FILE_BYTES:
            await update.message.reply_text(_t("file_too_large_1mb", lang))
            return
        try:
            tg_file = await context.bot.get_file(doc.file_id)
            raw = await tg_file.download_as_bytearray()
        except Exception as e:
            logger.exception("Failed to download vacancy file: %s", e)
            await update.message.reply_text(_t("download_failed", lang))
            return
        vacancy_text = extract_vacancy_text(filename, bytes(raw))
        if not vacancy_text or not vacancy_text.strip():
            await update.message.reply_text(_t("vacancy_extract_failed", lang))
            return
        _set_state(tid, BotState.ACTIVE)
        await _do_match_vacancy(update, context, vacancy_text.strip())
        return

    if not candidate_eligible:
        await update.message.reply_text(_t("candidates_only", lang))
        return

    if state == BotState.WAITING_RESUME_TO_CONVERT:
        if not is_resume_convert_file(doc.file_name or ""):
            await update.message.reply_text(_t("convert_format", lang))
            return
        if doc.file_size and doc.file_size > MAX_RESUME_CONVERT_BYTES:
            await update.message.reply_text(_t("file_too_large_1mb", lang))
            return
        try:
            tg_file = await context.bot.get_file(doc.file_id)
            raw = await tg_file.download_as_bytearray()
        except Exception as e:
            logger.exception("Download resume for convert failed: %s", e)
            _set_state(tid, BotState.ACTIVE)
            await update.message.reply_text(_t("download_failed", lang))
            return
        raw_text = extract_resume_text(doc.file_name or "", bytes(raw))
        if not raw_text or not raw_text.strip():
            _set_state(tid, BotState.ACTIVE)
            await update.message.reply_text(_t("convert_extract_failed", lang))
            return
        if not _ui_client_url():
            _set_state(tid, BotState.ACTIVE)
            await update.message.reply_text(_bot_error_message("service_error", lang))
            return
        sent = await update.message.reply_text(_t("convert_processing", lang))
        try:
            conv_r = await _ui_post("/resume/convert", json={"raw_text": raw_text.strip()}, timeout=600.0)
        except Exception as e:
            logger.exception("resume/convert error: %s", e)
            _set_state(tid, BotState.ACTIVE)
            await context.bot.edit_message_text(chat_id=sent.chat_id, message_id=sent.message_id, text=_t("convert_failed", lang))
            return
        _set_state(tid, BotState.ACTIVE)
        if conv_r.status_code != 200:
            await context.bot.edit_message_text(chat_id=sent.chat_id, message_id=sent.message_id, text=_t("convert_failed", lang))
            return
        result = conv_r.json()
        if result.get("success") and result.get("structured_text"):
            try:
                await context.bot.delete_message(chat_id=sent.chat_id, message_id=sent.message_id)
            except Exception:
                pass
            name = (doc.file_name or "resume").rsplit(".", 1)[0]
            out_name = f"{name}_resume.txt"
            st = result["structured_text"]
            if len(st) <= 4096:
                await update.message.reply_text(st)
            else:
                await update.message.reply_document(
                    document=InputFile(st.encode("utf-8"), filename=out_name),
                )
            await update.message.reply_text(_t("convert_done", lang))
        else:
            err = result.get("error") or ""
            msg = _t("convert_timeout", lang) if "timeout" in err.lower() else _t("convert_failed", lang) + (f" ({err})" if err else "")
            await context.bot.edit_message_text(chat_id=sent.chat_id, message_id=sent.message_id, text=msg)
        return

    filename = doc.file_name or "resume.txt"
    if not filename.lower().endswith(".txt"):
        _set_state(tid, BotState.ACTIVE)
        await update.message.reply_text(_t("only_txt", lang))
        return

    if doc.file_size and doc.file_size > MAX_RESUME_SIZE_BYTES:
        _set_state(tid, BotState.ACTIVE)
        await update.message.reply_text(_t("file_too_large_1mb", lang))
        return

    if not _ui_client_url():
        _set_state(tid, BotState.ACTIVE)
        await update.message.reply_text(_bot_error_message("service_error", lang))
        return

    try:
        tg_file = await context.bot.get_file(doc.file_id)
        raw = await tg_file.download_as_bytearray()
    except Exception as e:
        logger.exception("Failed to download file: %s", e)
        _set_state(tid, BotState.ACTIVE)
        await update.message.reply_text(_t("download_failed", lang))
        return

    if len(raw) > MAX_RESUME_SIZE_BYTES:
        _set_state(tid, BotState.ACTIVE)
        await update.message.reply_text(_t("file_too_large_1mb", lang))
        return

    sent = await update.message.reply_text(_t("processing", lang))
    try:
        r = await _ui_post(
            "/documents/upload",
            data={"external_id": str(tid), "source": SOURCE_TELEGRAM},
            files={"file": (filename, io.BytesIO(bytes(raw)), "text/plain")},
        )
    except Exception as e:
        logger.exception("Resume upload API error: %s", e)
        _set_state(tid, BotState.ACTIVE)
        try:
            await context.bot.edit_message_text(
                chat_id=sent.chat_id,
                message_id=sent.message_id,
                text=_bot_error_message("upload_error", lang),
            )
        except Exception:
            await update.message.reply_text(_bot_error_message("upload_error", lang))
        return

    _set_state(tid, BotState.ACTIVE)

    if r.status_code == 200:
        body = r.json()
        job_id = body.get("job_id", "")
        try:
            await context.bot.edit_message_text(
                chat_id=sent.chat_id,
                message_id=sent.message_id,
                text=_t("resume_accepted", lang, job_id=job_id),
            )
        except Exception:
            sent = await update.message.reply_text(_t("resume_accepted", lang, job_id=job_id))
        if sent:
            asyncio.create_task(
                _poll_job_and_edit_message(
                    context.bot,
                    job_id,
                    chat_id=sent.chat_id,
                    message_id=sent.message_id,
                    lang=lang,
                )
            )
    else:
        try:
            d = r.json().get("detail", r.text) if r.text else "Unknown error"
        except Exception:
            d = r.text or "Unknown error"
        d = d[0] if isinstance(d, list) and d else d
        d = d.get("msg", str(d)) if isinstance(d, dict) else str(d)
        try:
            await context.bot.edit_message_text(
                chat_id=sent.chat_id,
                message_id=sent.message_id,
                text=_bot_error_message("upload_failed", lang, detail=d),
            )
        except Exception:
            await update.message.reply_text(_bot_error_message("upload_failed", lang, detail=d))


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.from_user:
        return
    tid = update.message.from_user.id
    text = (update.message.text or "").strip()
    if not await _ensure_started(update, context, pending_text=text):
        return
    state = _get_state(tid)
    user = None
    try:
        ur = await _ui_get(f"/users/me?external_id={tid}&source={SOURCE_TELEGRAM}")
        if ur.status_code == 200:
            user = ur.json()
    except Exception:
        pass
    effective_state = state
    if state == BotState.ACTIVE and user and _is_candidate_eligible(user.get("role"), str(user.get("mode") or "")) and user.get("pending_state") == BotState.WAITING_COVER_LETTER_VACANCY.value:
        effective_state = BotState.WAITING_COVER_LETTER_VACANCY

    if effective_state == BotState.WAITING_LINK_TOKEN:
        _set_state(tid, BotState.ACTIVE)
        await _do_link(update, context, text)
        return
    if effective_state == BotState.WAITING_COVER_LETTER_VACANCY:
        if user is None or not _is_candidate_eligible(user.get("role"), str(user.get("mode") or "")):
            _set_state(tid, BotState.ACTIVE)
            try:
                await _ui_post(f"/users/set-pending-state?external_id={tid}&source={SOURCE_TELEGRAM}")
            except Exception:
                pass
            await update.message.reply_text(_bot_error_message("error_generic", str(user.get("language") or "en") if user else "en"))
            return
        lang = str(user.get("language") or "en")
        if len(text) >= MAX_VACANCY_TEXT_CHARS:
            await update.message.reply_text(_t("description_too_long", lang))
            return
        _set_state(tid, BotState.ACTIVE)
        try:
            await _ui_post(f"/users/set-pending-state?external_id={tid}&source={SOURCE_TELEGRAM}")
        except Exception:
            pass
        await _do_cover_letter(update, context, text)
        return
    if state == BotState.WAITING_VACANCY_TEXT:
        try:
            ur = await _ui_get(f"/users/me?external_id={tid}&source={SOURCE_TELEGRAM}")
        except Exception:
            ur = None
        user = ur.json() if ur and ur.status_code == 200 else None
        if user is None or not _is_hr_eligible(user.get("role"), str(user.get("mode") or "")):
            _set_state(tid, BotState.ACTIVE)
            await update.message.reply_text(_bot_error_message("error_generic", str(user.get("language") or "en") if user else "en"))
            return
        lang = str(user.get("language") or "en")
        if not user.get("dialog_owner_id"):
            _set_state(tid, BotState.ACTIVE)
            await update.message.reply_text(_t("link_lost", lang))
            return
        if len(text) >= MAX_VACANCY_TEXT_CHARS:
            await update.message.reply_text(_t("description_too_long", lang))
            return
        _set_state(tid, BotState.ACTIVE)
        await _do_match_vacancy(update, context, text)
        return
    if state == BotState.ACTIVE and _looks_like_token(text):
        await _do_link(update, context, text)
        return

    if state == BotState.ACTIVE and text and text in BTN_TO_ACTION:
        try:
            ur = await _ui_get(f"/users/me?external_id={tid}&source={SOURCE_TELEGRAM}")
        except Exception:
            ur = None
        user = ur.json() if ur and ur.status_code == 200 else None
        role = user.get("role") if user else None
        mode = str(user.get("mode") or "") if user else ""
        action = BTN_TO_ACTION.get(text)
        if not action:
            return
        if action == "help":
            await help_handler(update, context)
            return
        if action == "link" and _is_hr_eligible(role, mode if mode else None):
            await link_handler(update, context)
            return
        if action == "match" and _is_hr_eligible(role, mode if mode else None):
            await match_handler(update, context)
            return
        if user:
            if action == "update" and _is_candidate_eligible(role, mode if mode else None):
                await update_handler(update, context)
                return
            if action == "convert" and _is_candidate_eligible(role, mode if mode else None):
                await convert_handler(update, context)
                return
            if action == "hash" and _is_candidate_eligible(role, mode if mode else None):
                await hash_handler(update, context)
                return
            if action == "letter" and _is_candidate_eligible(role, mode if mode else None):
                await cover_letter_handler(update, context)
                return
            if action == "switch" and role == "admin":
                await switch_handler(update, context)
                return
            msg_key = "candidates_only" if action in ("update", "hash", "convert", "letter") else "access_denied"
            await update.message.reply_text(_t(msg_key, str(user.get("language") or "en")))
        else:
            await update.message.reply_text(_t("access_denied", "en"))

    if state == BotState.WAITING_HASH:
        t = text.lower()
        if t in ("ru", "en"):
            try:
                await _ui_post("/users/language", json={"external_id": str(tid), "source": SOURCE_TELEGRAM, "language": t})
            except Exception:
                pass
            await update.message.reply_text(_t("language_chosen_waiting_hash", t))
        else:
            await update.message.reply_text(
                _t("choose_lang_ru_en", "en"),
                reply_markup=_lang_keyboard(),
            )
        return

    if state == BotState.ACTIVE and text:
        try:
            ur = await _ui_get(f"/users/me?external_id={tid}&source={SOURCE_TELEGRAM}")
        except Exception:
            ur = None
        user = ur.json() if ur and ur.status_code == 200 else None
        if user is not None:
            lang = str(user.get("language") or "en")
            if len(text) > MAX_CHAT_MESSAGE_CHARS:
                if _is_candidate_eligible(user.get("role"), str(user.get("mode")) if user.get("mode") else None):
                    await update.message.reply_text(_t("long_message_use_letter", lang))
                elif _is_hr_eligible(user.get("role"), str(user.get("mode")) if user.get("mode") else None):
                    await update.message.reply_text(_t("long_message_use_match", lang))
                else:
                    await update.message.reply_text(_t("long_message_use_match", lang))
                return
            dialog_owner_id = user.get("dialog_owner_id")
            if _is_hr_eligible(user.get("role"), str(user.get("mode")) if user.get("mode") else None) and dialog_owner_id:
                if _message_has_url(update):
                    try:
                        await context.bot.forward_message(
                            chat_id=int(dialog_owner_id),
                            from_chat_id=update.message.chat_id,
                            message_id=update.message.message_id,
                        )
                    except Exception as e:
                        logger.warning("Forward link to candidate failed: %s", e)
                    return
                if _message_has_phone(update):
                    await update.message.reply_text(_t("phone_escalation_reply", lang))
                    try:
                        await context.bot.forward_message(
                            chat_id=int(dialog_owner_id),
                            from_chat_id=update.message.chat_id,
                            message_id=update.message.message_id,
                        )
                    except Exception as e:
                        logger.warning("Forward phone message to candidate failed: %s", e)
                    return
            await _process_chat_message(
                context,
                update.message.chat_id,
                tid,
                text,
                lang,
            )


async def ping_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("pong")


async def _send_restart_notices(app: Application) -> None:
    if not _ui_client_url():
        return
    bot = app.bot
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    msg_ru = _t("restart_notice", "ru", timestamp=ts)
    msg_en = _t("restart_notice", "en", timestamp=ts)
    text = f"{msg_ru}\n\n{msg_en}"
    try:
        r = await _ui_get(f"/users/restart-notify?source={SOURCE_TELEGRAM}")
    except Exception as e:
        logger.warning("Restart notify list failed: %s", e)
        return
    if r.status_code != 200:
        return
    users = r.json()
    for u in users:
        external_id = u.get("external_id")
        last_msg_id = u.get("last_restart_message_id")
        try:
            chat_id = int(external_id)
        except (ValueError, TypeError):
            continue
        if last_msg_id is not None:
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=last_msg_id, text=text)
                continue
            except Exception as e:
                logger.debug("Restart notice edit failed for %s (msg %s): %s", external_id, last_msg_id, e)
        try:
            sent = await bot.send_message(chat_id=chat_id, text=text)
            if sent and sent.message_id:
                try:
                    await _ui_post("/users/restart-notify/store", json={"external_id": external_id, "source": SOURCE_TELEGRAM, "message_id": sent.message_id})
                except Exception:
                    pass
        except Exception as e:
            logger.warning("Restart notice send failed for %s: %s", external_id, e)


async def post_init(app: Application) -> None:
    await _send_restart_notices(app)
    cmds_en = [
        BotCommand("start", TEXTS["en"]["cmd_start"]),
        BotCommand("help", TEXTS["en"]["cmd_help"]),
        BotCommand("lang", TEXTS["en"]["cmd_lang"]),
        BotCommand("convert", TEXTS["en"]["cmd_convert"]),
        BotCommand("update", TEXTS["en"]["cmd_update"]),
        BotCommand("hash", TEXTS["en"]["cmd_hash"]),
        BotCommand("letter", TEXTS["en"]["cmd_letter"]),
        BotCommand("link", TEXTS["en"]["cmd_link"]),
        BotCommand("match", TEXTS["en"]["cmd_match"]),
        BotCommand("switch", TEXTS["en"]["cmd_switch"]),
    ]
    cmds_ru = [
        BotCommand("start", TEXTS["ru"]["cmd_start"]),
        BotCommand("help", TEXTS["ru"]["cmd_help"]),
        BotCommand("lang", TEXTS["ru"]["cmd_lang"]),
        BotCommand("convert", TEXTS["ru"]["cmd_convert"]),
        BotCommand("update", TEXTS["ru"]["cmd_update"]),
        BotCommand("hash", TEXTS["ru"]["cmd_hash"]),
        BotCommand("letter", TEXTS["ru"]["cmd_letter"]),
        BotCommand("link", TEXTS["ru"]["cmd_link"]),
        BotCommand("match", TEXTS["ru"]["cmd_match"]),
        BotCommand("switch", TEXTS["ru"]["cmd_switch"]),
    ]
    await app.bot.set_my_commands(cmds_en, language_code="en")
    await app.bot.set_my_commands(cmds_ru, language_code="ru")


async def post_shutdown(app: Application) -> None:
    pass


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    ui_url = os.getenv("UI_CLIENT_URL", "")
    logger.info(
        "Starting telegram-bot (thin client): UI_CLIENT_URL=%s",
        ui_url or "(empty)",
    )
    app = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("link", link_handler))
    app.add_handler(CommandHandler("hash", hash_handler))
    app.add_handler(CommandHandler("lang", lang_handler))
    app.add_handler(CommandHandler("switch", switch_handler))
    app.add_handler(CommandHandler("match", match_handler))
    app.add_handler(CommandHandler("letter", cover_letter_handler))
    app.add_handler(CommandHandler("convert", convert_handler))
    app.add_handler(CommandHandler("update", update_handler))
    app.add_handler(CommandHandler("ping", ping_handler))
    app.add_handler(CallbackQueryHandler(lang_callback, pattern="^lang:"))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    logger.info("telegram-bot started, polling")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
