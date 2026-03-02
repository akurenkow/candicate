#!/usr/bin/env python3
"""Telegram bot: /start, /lang, /update (resume upload), in-memory state machine."""

import asyncio
import io
import logging
import os
import uuid
from enum import Enum

import asyncpg
import httpx
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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
from db import (
    create_pool,
    create_user,
    generate_hash_token,
    get_chunk_count_by_document_id,
    get_user_by_telegram_id,
    insert_hash,
    link_hash,
    update_language,
    update_mode,
    update_role,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ru/en localization (UI only); default language if not set: en
TEXTS = {
    "ru": {
        "update_resume": "Обновить резюме",
        "generate_hash": "Сгенерировать хэш",
        "help": "Помощь",
        "link_candidate": "Связать кандидата",
        "match": "Match",
        "switch_mode": "Переключить режим",
        "help_candidate": "Кандидат: /update — загрузить резюме (.txt), /hash — сгенерировать токен для связи с HR.",
        "help_recruiter": "Рекрутер: /link [токен] — связаться с кандидатом, /match — сопоставить с вакансией (сначала /link).",
        "help_admin": "Админ: /switch — переключить режим (кандидат ↔ рекрутер). В candidate: /update, /hash. В hr: /link, /match.",
        "cmd_start": "Запуск",
        "cmd_help": "Помощь",
        "cmd_lang": "Язык",
        "choose_language": "Выберите язык / Choose language:",
        "menu_ready": "Готово. /lang — язык.",
        "menu_mode_admin": "Режим: {mode}. /switch — переключить.",
        "access_denied": "Доступ запрещён. / Access denied.",
        "enter_candidate_token": "Введите токен кандидата.",
        "lang_usage": "Использование: /lang ru или /lang en",
        "language_updated": "Язык обновлён.",
        "error_generic": "Я не упал, шеф, — меня уронили... (error_generic)",
        "user_not_found": "Пользователь не найден.",
        "recruiters_only": "Только для рекрутеров.",
        "use_link_first": "Свяжитесь с кандидатом через /link.",
        "send_vacancy_file": "Отправьте файл в формате doc(x)/pdf/txt или краткое описание (не больше 1 сообщения телеграм).",
        "upload_resume_txt": "Загрузите резюме в формате .txt (макс. 1 МБ).",
        "vacancy_format": "Формат не поддерживается. Используйте doc(x)/pdf/txt.",
        "file_too_large_1mb": "Файл слишком большой (макс. 1 МБ).",
        "download_failed": "Не удалось загрузить файл.",
        "only_txt": "Только .txt.",
        "service_error": "Я не упал, шеф, — меня уронили... (service_error)",
        "request_error": "Я не упал, шеф, — меня уронили... (request_error)",
        "response_error": "Я не упал, шеф, — меня уронили... (response_error)",
        "processing": "Обработка...",
        "language_chosen_waiting_hash": "Язык выбран. Ожидание хэша.",
        "choose_lang_ru_en": "Выберите язык: ru или en",
        "mode_switched": "Режим: {mode}.",
        "description_too_long": "Описание слишком большое, отправьте файл в формате doc(x)/pdf/txt.",
        "link_lost": "Связь с кандидатом потеряна. Используйте /link.",
        "upload_error": "Я не упал, шеф, — меня уронили... (upload_error)",
        "upload_failed": "Я не упал, шеф, — меня уронили... (upload_failed)",
        "indexing_failed": "Я не упал, шеф, — меня уронили... (indexing_failed)",
        "resume_indexed": "Резюме проиндексировано и разбито на {n} чанков.",
        "invalid_response": "Я не упал, шеф, — меня уронили... (invalid_response)",
        "no_response": "Я не упал, шеф, — меня уронили... (no_response)",
        "timeout": "Я не упал, шеф, — меня уронили... (timeout)",
        "translation_failed": "Я не упал, шеф, — меня уронили... (translation_failed)",
        "no_results": "Нет результатов (нет документов кандидата).",
        "relevance": "Релевантность: {score:.2f}",
        "resume_accepted": "Резюме принято (job_id: {job_id}). Дождитесь завершения индексации.",
    },
    "en": {
        "update_resume": "Update resume",
        "generate_hash": "Generate hash",
        "help": "Help",
        "link_candidate": "Link candidate",
        "match": "Match",
        "switch_mode": "Switch mode",
        "help_candidate": "Candidate: /update — upload resume (.txt), /hash — generate token for HR link.",
        "help_recruiter": "Recruiter: /link [token] — link to candidate, /match — match with vacancy (use /link first).",
        "help_admin": "Admin: /switch — toggle mode (candidate ↔ recruiter). In candidate: /update, /hash. In hr: /link, /match.",
        "cmd_start": "Start",
        "cmd_help": "Help",
        "cmd_lang": "Language",
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
        "use_link_first": "Use /link to link to a candidate.",
        "send_vacancy_file": "Send a file (doc/docx/pdf/txt) or brief description (max 1 Telegram message).",
        "upload_resume_txt": "Upload your resume as .txt file (max 1 MB).",
        "vacancy_format": "Format not supported. Use doc/docx/pdf/txt.",
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


def _menu_keyboard(role: str | None, mode: str | None, lang: str | None) -> ReplyKeyboardMarkup:
    effective = "candidate" if (role == "admin" and mode != "hr") else (role or "candidate")
    if role == "admin":
        rows = [[KeyboardButton(_t("switch_mode", lang))]]
        if mode == "hr":
            rows.extend([
                [KeyboardButton(_t("link_candidate", lang))],
                [KeyboardButton(_t("match", lang)), KeyboardButton(_t("help", lang))],
            ])
        else:
            rows.extend([
                [KeyboardButton(_t("update_resume", lang)), KeyboardButton(_t("generate_hash", lang))],
                [KeyboardButton(_t("help", lang))],
            ])
    elif effective == "recruiter":
        rows = [
            [KeyboardButton(_t("link_candidate", lang))],
            [KeyboardButton(_t("match", lang)), KeyboardButton(_t("help", lang))],
        ]
    else:
        rows = [
            [KeyboardButton(_t("update_resume", lang)), KeyboardButton(_t("generate_hash", lang))],
            [KeyboardButton(_t("help", lang))],
        ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


class BotState(str, Enum):
    UNREGISTERED = "unregistered"
    WAITING_HASH = "waiting_hash"
    WAITING_LINK_TOKEN = "waiting_link_token"
    ACTIVE = "active"
    RESUME_PENDING = "resume_pending"
    WAITING_VACANCY_TEXT = "waiting_vacancy_text"


def _looks_like_token(text: str) -> bool:
    t = (text or "").strip()
    return len(t) >= 20 and t.replace("-", "").replace("_", "").isalnum()


MAX_RESUME_SIZE_BYTES = 1024 * 1024  # 1MB
MAX_VACANCY_TEXT_CHARS = 4000  # ~1 Telegram message
POLL_INTERVAL_SEC = 2
POLL_MAX_ATTEMPTS = 90  # ~3 min
MESSAGE_POLL_MAX_ATTEMPTS = 45  # ~1.5 min for RAG


async def _poll_message_and_get_answer(
    base_url: str,
    message_id: str,
) -> str:
    for _ in range(MESSAGE_POLL_MAX_ATTEMPTS):
        await asyncio.sleep(POLL_INTERVAL_SEC)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"{base_url}/messages/{message_id}")
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
    base_url: str,
    message_id: str,
    chat_id: int,
    msg_id: int,
) -> None:
    answer = await _poll_message_and_get_answer(base_url, message_id)
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
    """Send user message to chat API, show processing, then poll and edit with response. Uses lang for UI strings."""
    pool: asyncpg.Pool = context.bot_data["pool"]
    async with pool.acquire() as conn:
        user = await get_user_by_telegram_id(conn, str(tid))
    if user is None:
        async with pool.acquire() as conn:
            await create_user(conn, str(tid))
            if str(tid) in _get_allowed_upload_user_ids():
                await update_role(conn, str(tid), "candidate")
        _set_state(tid, BotState.WAITING_HASH)
        await context.bot.send_message(
            chat_id=chat_id,
            text=_t("choose_language", "en"),
            reply_markup=_lang_keyboard(),
        )
        if text.strip():
            _pending_message[tid] = text.strip()
        return
    role = user.get("role")
    mode = str(user.get("mode")) if user.get("mode") else None
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
    base_url = os.getenv("BASE_API_URL", "").rstrip("/")
    if not base_url:
        await context.bot.send_message(chat_id=chat_id, text=_bot_error_message("service_error", lang))
        return
    sent = await context.bot.send_message(chat_id=chat_id, text=_t("processing", lang))
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{base_url}/chat",
                params={"owner_id": owner_id},
                json={"message": text},
            )
    except Exception as e:
        logger.exception("Chat API error: %s", e)
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=sent.message_id,
            text=_bot_error_message("request_error", lang),
        )
        return
    if r.status_code != 202:
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
        logger.warning("bot_error chat non-202 trace_id=%s detail=%.200s", trace_id, err)
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
            base_url,
            msg_id,
            sent.chat_id,
            sent.message_id,
        )
    )


async def _poll_job_and_edit_message(
    bot,
    base_url: str,
    job_id: str,
    chat_id: int,
    message_id: int,
    pool: asyncpg.Pool,
) -> None:
    for _ in range(POLL_MAX_ATTEMPTS):
        await asyncio.sleep(POLL_INTERVAL_SEC)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"{base_url}/documents/tasks/{job_id}/status")
        except Exception as e:
            logger.warning("Poll task status failed: %s", e)
            continue
        if r.status_code != 200:
            continue
        data = r.json()
        status = data.get("status", "")
        if status == "completed":
            doc_id = data.get("document_id")
            n = 0
            if doc_id:
                try:
                    async with pool.acquire() as conn:
                        n = await get_chunk_count_by_document_id(conn, doc_id)
                except Exception as e:
                    logger.warning("get_chunk_count failed: %s", e)
            text = (
                f"Резюме проиндексировано и разбито на {n} чанков.\n"
                f"Resume indexed and split into {n} chunks."
            )
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


def _get_allowed_upload_user_ids() -> frozenset[str]:
    raw = os.getenv("ALLOWED_UPLOAD_USER_IDS", "").strip()
    if not raw:
        return frozenset()
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


def _is_candidate_eligible(role, mode) -> bool:
    if role == "candidate":
        return True
    if role == "admin" and mode == "candidate":
        return True
    return False


def _is_hr_eligible(role, mode) -> bool:
    if role == "recruiter":
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
    """If user is UNREGISTERED: new user → show language choice and store pending_text (return False); existing user (e.g. after restart) → set ACTIVE (return True). Returns True if handler should continue processing the message."""
    if not update.message or not update.message.from_user:
        return True
    tid = update.message.from_user.id
    if _get_state(tid) != BotState.UNREGISTERED:
        return True
    pool: asyncpg.Pool = context.bot_data["pool"]
    async with pool.acquire() as conn:
        user = await get_user_by_telegram_id(conn, str(tid))
    if user is None:
        async with pool.acquire() as conn:
            await create_user(conn, str(tid))
            if str(tid) in _get_allowed_upload_user_ids():
                await update_role(conn, str(tid), "candidate")
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
    pool: asyncpg.Pool = context.bot_data["pool"]

    async with pool.acquire() as conn:
        user = await get_user_by_telegram_id(conn, str(tid))

    if user is None:
        async with pool.acquire() as conn:
            await create_user(conn, str(tid))
            if str(tid) in _get_allowed_upload_user_ids():
                await update_role(conn, str(tid), "candidate")
        _set_state(tid, BotState.WAITING_HASH)
        await update.message.reply_text(
            _t("choose_language", "en"),
            reply_markup=_lang_keyboard(),
        )
        return

    allowed = _get_allowed_upload_user_ids()
    role = user.get("role")
    if str(tid) in allowed and role != "candidate":
        async with pool.acquire() as conn:
            await update_role(conn, str(tid), "candidate")
        role = "candidate"
    _set_state(tid, BotState.ACTIVE)
    lang = str(user.get("language") or "en")
    kbd = _menu_keyboard(role, str(user.get("mode") or ""), lang)
    menu = _t("menu_mode_admin", lang, mode=str(user.get("mode") or "candidate")) if role == "admin" else _t("menu_ready", lang)
    await update.message.reply_text(menu, reply_markup=kbd)


async def lang_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("lang:"):
        return
    await query.answer()
    lang = query.data.split(":")[1]
    tid = query.from_user.id if query.from_user else 0
    pool: asyncpg.Pool = context.bot_data["pool"]

    async with pool.acquire() as conn:
        n = await update_language(conn, str(tid), lang)
        user = await get_user_by_telegram_id(conn, str(tid)) if n > 0 else None

    if n > 0:
        _set_state(tid, BotState.ACTIVE)
        await query.edit_message_text(_t("language_updated", lang))
        role = user.get("role") if user else None
        mode = str(user.get("mode") or "") if user else ""
        kbd = _menu_keyboard(role, mode, lang)
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
    else:
        await query.edit_message_text(_bot_error_message("error_generic", "en"))


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
    pool: asyncpg.Pool = context.bot_data["pool"]
    async with pool.acquire() as conn:
        user = await get_user_by_telegram_id(conn, str(tid))
    if user is None:
        return
    async with pool.acquire() as conn:
        ok, msg, _ = await link_hash(conn, str(tid), hash_value)
    sent = await update.message.reply_text(msg)
    if ok and sent:
        try:
            await context.bot.pin_chat_message(
                chat_id=update.message.chat_id,
                message_id=sent.message_id,
            )
        except Exception:
            pass


async def switch_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle mode between candidate/hr. Admin only."""
    if not update.message or not update.message.from_user:
        return
    tid = update.message.from_user.id
    pool: asyncpg.Pool = context.bot_data["pool"]
    async with pool.acquire() as conn:
        user = await get_user_by_telegram_id(conn, str(tid))
    if user is None or user.get("role") != "admin":
        await update.message.reply_text(_t("access_denied", str(user.get("language") or "en") if user else "en"))
        return
    cur = str(user.get("mode") or "candidate")
    new_mode = "hr" if cur == "candidate" else "candidate"
    async with pool.acquire() as conn:
        await update_mode(conn, str(tid), new_mode)
    lang = str(user.get("language") or "en")
    kbd = _menu_keyboard("admin", new_mode, lang)
    await update.message.reply_text(_t("mode_switched", lang, mode=new_mode), reply_markup=kbd)


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.from_user:
        return
    tid = update.message.from_user.id
    pool: asyncpg.Pool = context.bot_data["pool"]
    async with pool.acquire() as conn:
        user = await get_user_by_telegram_id(conn, str(tid))
    lang = str(user.get("language") or "en") if user else "en"
    role = user.get("role") if user else None
    mode = str(user.get("mode") or "") if user else ""
    if role == "admin":
        text = _t("help_admin", lang)
    elif role == "recruiter" or (role == "admin" and mode == "hr"):
        text = _t("help_recruiter", lang)
    else:
        text = _t("help_candidate", lang)
    kbd = _menu_keyboard(role, mode, lang) if user else None
    await update.message.reply_text(text, reply_markup=kbd)


async def link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Link HR to candidate via hash. /link [hash]"""
    if not update.message or not update.message.from_user:
        return
    tid = update.message.from_user.id
    args = (context.args or [])
    if not args:
        _set_state(tid, BotState.WAITING_LINK_TOKEN)
        lang = "en"
        pool = context.bot_data["pool"]
        async with pool.acquire() as conn:
            u = await get_user_by_telegram_id(conn, str(tid))
            if u:
                lang = str(u.get("language") or "en")
        await update.message.reply_text(_t("enter_candidate_token", lang))
        return
    await _do_link(update, context, args[0])


async def hash_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate hash for candidate. Only candidate or admin in candidate mode. Block recruiters."""
    if not update.message or not update.message.from_user:
        return
    tid = update.message.from_user.id
    if not await _ensure_started(update, context):
        return
    pool: asyncpg.Pool = context.bot_data["pool"]

    async with pool.acquire() as conn:
        user = await get_user_by_telegram_id(conn, str(tid))

    if user is None:
        return

    role = user.get("role")
    mode = user.get("mode")
    if not _is_candidate_eligible(role, str(mode) if mode else None):
        await update.message.reply_text(_t("access_denied", str(user.get("language") or "en")))
        return

    token = generate_hash_token()
    owner_id = str(tid)

    async with pool.acquire() as conn:
        await insert_hash(conn, owner_id, token)

    await update.message.reply_text(f"<code>{token}</code>", parse_mode="HTML")


async def lang_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.from_user:
        return
    tid = update.message.from_user.id
    args = (context.args or [])
    if not args or args[0] not in ("ru", "en"):
        await update.message.reply_text(
            _t("lang_usage", "en"),
        )
        return
    lang = args[0]
    pool: asyncpg.Pool = context.bot_data["pool"]

    user = None
    async with pool.acquire() as conn:
        n = await update_language(conn, str(tid), lang)
        if n > 0:
            user = await get_user_by_telegram_id(conn, str(tid))

    if n > 0:
        kbd = _menu_keyboard(
            user.get("role") if user else None,
            str(user.get("mode") or "") if user else "",
            lang,
        )
        text = "Язык обновлён." if lang == "ru" else "Language updated."
        await update.message.reply_text(text, reply_markup=kbd)
    else:
        await update.message.reply_text(_t("user_not_found", "en"))


async def match_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Match candidate (dialog_owner_id) to vacancy. Recruiter only. Use /link first."""
    if not update.message or not update.message.from_user:
        return
    tid = update.message.from_user.id
    if not await _ensure_started(update, context):
        return
    pool: asyncpg.Pool = context.bot_data["pool"]
    async with pool.acquire() as conn:
        user = await get_user_by_telegram_id(conn, str(tid))
    if user is None:
        return
    if not _is_hr_eligible(user.get("role"), str(user.get("mode")) if user.get("mode") else None):
        await update.message.reply_text(_t("recruiters_only", str(user.get("language") or "en")))
        return
    dialog_owner_id = user.get("dialog_owner_id")
    if not dialog_owner_id:
        await update.message.reply_text(_t("use_link_first", str(user.get("language") or "en")))
        return
    _set_state(tid, BotState.WAITING_VACANCY_TEXT)
    await update.message.reply_text(_t("send_vacancy_file", str(user.get("language") or "en")))


async def update_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask candidate to upload .txt resume."""
    if not update.message or not update.message.from_user:
        return
    tid = update.message.from_user.id
    if not await _ensure_started(update, context):
        return
    pool: asyncpg.Pool = context.bot_data["pool"]

    async with pool.acquire() as conn:
        user = await get_user_by_telegram_id(conn, str(tid))

    if user is None:
        return

    role = user.get("role")
    mode = user.get("mode")
    if not _is_candidate_eligible(role, str(mode) if mode else None):
        await update.message.reply_text(_t("access_denied", str(user.get("language") or "en")))
        return

    _set_state(tid, BotState.RESUME_PENDING)
    await update.message.reply_text(_t("upload_resume_txt", str(user.get("language") or "en")))


async def _do_match_vacancy(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    pool: asyncpg.Pool,
    vacancy_text: str,
) -> None:
    tid = update.message.from_user.id
    async with pool.acquire() as conn:
        user = await get_user_by_telegram_id(conn, str(tid))
    if user is None or not _is_hr_eligible(user.get("role"), str(user.get("mode")) if user.get("mode") else None):
        return
    lang = str(user.get("language") or "en")
    dialog_owner_id = user.get("dialog_owner_id")
    if not dialog_owner_id:
        await update.message.reply_text(_t("link_lost", lang))
        return
    base_url = os.getenv("BASE_API_URL", "").rstrip("/")
    if not base_url:
        await update.message.reply_text(_bot_error_message("service_error", lang))
        return
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{base_url}/match-vacancy",
                json={"owner_id": str(dialog_owner_id), "vacancy_text": vacancy_text, "summary": False},
            )
    except Exception as e:
        logger.exception("Match API error: %s", e)
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
        logger.warning("bot_error match_vacancy non-200 trace_id=%s detail=%.200s", trace_id, err)
        await update.message.reply_text(str(err)[:500] + f" trace_id={trace_id}")
        return
    data = r.json()
    if not isinstance(data, list):
        await update.message.reply_text(_bot_error_message("invalid_response", lang))
        return
    if not data:
        await update.message.reply_text(_t("no_results", lang))
        return
    item = data[0]
    score = item.get("relevance_score", 0)
    snippets = item.get("matched_snippets") or []
    lines = [_t("relevance", lang, score=score)]
    for i, s in enumerate(snippets[:5], 1):
        t = str(s)
        lines.append(f"\n[{i}] {t[:300]}{'...' if len(t) > 300 else ''}")
    await update.message.reply_text("\n".join(lines) if lines else "OK")


async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle resume file upload or vacancy file for /match."""
    if not update.message or not update.message.from_user or not update.message.document:
        return
    tid = update.message.from_user.id
    if not await _ensure_started(update, context):
        return
    doc = update.message.document
    state = _get_state(tid)
    pool: asyncpg.Pool = context.bot_data["pool"]

    async with pool.acquire() as conn:
        user = await get_user_by_telegram_id(conn, str(tid))
    if user is None:
        return

    lang = str(user.get("language") or "en")
    filename = doc.file_name or ""
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
            await update.message.reply_text(_t("vacancy_format", lang))
            return
        _set_state(tid, BotState.ACTIVE)
        await _do_match_vacancy(update, context, pool, vacancy_text.strip())
        return

    candidate_eligible = _is_candidate_eligible(
        user.get("role"), str(user.get("mode")) if user.get("mode") else None
    )
    if not candidate_eligible:
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

    base_url = os.getenv("BASE_API_URL", "").rstrip("/")
    if not base_url:
        _set_state(tid, BotState.ACTIVE)
        await update.message.reply_text(_bot_error_message("service_error", lang))
        logger.error("BASE_API_URL not set")
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

    owner_id = str(tid)
    files = {"file": (filename, io.BytesIO(bytes(raw)), "text/plain")}
    data = {"owner_id": owner_id}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{base_url}/documents/upload",
                files=files,
                data=data,
            )
    except Exception as e:
        logger.exception("Resume upload API error: %s", e)
        _set_state(tid, BotState.ACTIVE)
        await update.message.reply_text(_bot_error_message("upload_error", lang))
        return

    _set_state(tid, BotState.ACTIVE)

    if r.status_code == 202:
        body = r.json()
        job_id = body.get("job_id", "")
        sent = await update.message.reply_text(
            _t("resume_accepted", lang, job_id=job_id),
        )
        if sent:
            asyncio.create_task(
                _poll_job_and_edit_message(
                    context.bot,
                    base_url,
                    job_id,
                    chat_id=sent.chat_id,
                    message_id=sent.message_id,
                    pool=pool,
                )
            )
    else:
        try:
            d = r.json().get("detail", r.text) if r.text else "Unknown error"
        except Exception:
            d = r.text or "Unknown error"
        d = d[0] if isinstance(d, list) and d else d
        d = d.get("msg", str(d)) if isinstance(d, dict) else str(d)
        await update.message.reply_text(_bot_error_message("upload_failed", lang, detail=d))


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.from_user:
        return
    tid = update.message.from_user.id
    text = (update.message.text or "").strip()
    if not await _ensure_started(update, context, pending_text=text):
        return
    state = _get_state(tid)

    if state == BotState.WAITING_LINK_TOKEN:
        _set_state(tid, BotState.ACTIVE)
        await _do_link(update, context, text)
        return
    if state == BotState.WAITING_VACANCY_TEXT:
        pool = context.bot_data["pool"]
        async with pool.acquire() as conn:
            user = await get_user_by_telegram_id(conn, str(tid))
        if user is None or not _is_hr_eligible(user.get("role"), str(user.get("mode")) if user.get("mode") else None):
            _set_state(tid, BotState.ACTIVE)
            await update.message.reply_text(_bot_error_message("error_generic", str(user.get("language") or "en") if user else "en"))
            return
        lang = str(user.get("language") or "en")
        dialog_owner_id = user.get("dialog_owner_id")
        if not dialog_owner_id:
            _set_state(tid, BotState.ACTIVE)
            await update.message.reply_text(_t("link_lost", lang))
            return
        if len(text) >= MAX_VACANCY_TEXT_CHARS:
            await update.message.reply_text(_t("description_too_long", lang))
            return
        _set_state(tid, BotState.ACTIVE)
        await _do_match_vacancy(update, context, pool, text)
        return
    if state == BotState.ACTIVE and _looks_like_token(text):
        await _do_link(update, context, text)
        return

    if state == BotState.ACTIVE and text and text in BTN_TO_ACTION:
        pool = context.bot_data["pool"]
        async with pool.acquire() as conn:
            user = await get_user_by_telegram_id(conn, str(tid))
        if user:
            role = user.get("role")
            mode = str(user.get("mode") or "")
            action = BTN_TO_ACTION[text]
            if action == "update" and _is_candidate_eligible(role, mode if mode else None):
                await update_handler(update, context)
                return
            if action == "hash" and _is_candidate_eligible(role, mode if mode else None):
                await hash_handler(update, context)
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
            if action == "switch" and role == "admin":
                await switch_handler(update, context)
                return
            await update.message.reply_text(_t("access_denied", str(user.get("language") or "en")))
            return

    if state == BotState.WAITING_HASH:
        t = text.lower()
        if t in ("ru", "en"):
            pool: asyncpg.Pool = context.bot_data["pool"]
            async with pool.acquire() as conn:
                await update_language(conn, str(tid), t)
            await update.message.reply_text(_t("language_chosen_waiting_hash", t))
        else:
            await update.message.reply_text(
                _t("choose_lang_ru_en", "en"),
                reply_markup=_lang_keyboard(),
            )
        return

    if state == BotState.ACTIVE and text:
        pool = context.bot_data["pool"]
        async with pool.acquire() as conn:
            user = await get_user_by_telegram_id(conn, str(tid))
        if user is not None:
            lang = str(user.get("language") or "en")
            await _process_chat_message(
                context,
                update.message.chat_id,
                tid,
                text,
                lang,
            )


async def ping_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("pong")


async def post_init(app: Application) -> None:
    pool = await create_pool()
    app.bot_data["pool"] = pool
    cmds_en = [
        BotCommand("start", TEXTS["en"]["cmd_start"]),
        BotCommand("help", TEXTS["en"]["cmd_help"]),
        BotCommand("lang", TEXTS["en"]["cmd_lang"]),
    ]
    cmds_ru = [
        BotCommand("start", TEXTS["ru"]["cmd_start"]),
        BotCommand("help", TEXTS["ru"]["cmd_help"]),
        BotCommand("lang", TEXTS["ru"]["cmd_lang"]),
    ]
    await app.bot.set_my_commands(cmds_en, language_code="en")
    await app.bot.set_my_commands(cmds_ru, language_code="ru")


async def post_shutdown(app: Application) -> None:
    pool: asyncpg.Pool | None = app.bot_data.get("pool")
    if pool:
        await pool.close()


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    base_api_url = os.getenv("BASE_API_URL", "")
    logger.info(
        "Starting telegram-bot: BASE_API_URL=%s",
        base_api_url or "(empty)",
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
    app.add_handler(CommandHandler("update", update_handler))
    app.add_handler(CommandHandler("ping", ping_handler))
    app.add_handler(CallbackQueryHandler(lang_callback, pattern="^lang:"))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    logger.info("telegram-bot started, polling")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
