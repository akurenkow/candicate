#!/usr/bin/env python3
"""Telegram bot: /start, /lang, /update (resume upload), in-memory state machine."""

import asyncio
import io
import logging
import os
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


# ru/en localization (UI only)
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


def _t(key: str, lang: str | None) -> str:
    l = (lang or "en").lower()
    if l not in TEXTS:
        l = "en"
    return TEXTS[l].get(key, TEXTS["en"].get(key, key))


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
            return t or "Нет ответа. / No response."
        if status == "escalated":
            return "Ошибка перевода ответа. Попробуйте позже или обратитесь к администратору. / Response translation failed. Try again later or contact support."
        if status == "failed":
            return data.get("assistant_text") or "Ошибка. / Error."
    return "Таймаут. / Timeout."


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
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"Ошибка индексации: {err}.\nIndexing failed.",
                )
            except Exception:
                pass
            return


_user_states: dict[int, BotState] = {}


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
            "Выберите язык / Choose language:",
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
    if role == "admin":
        m = str(user.get("mode") or "candidate")
        menu = f"Режим: {m}. /switch — переключить." if lang == "ru" else f"Mode: {m}. /switch to toggle."
    else:
        menu = "Готово. /lang — язык." if lang == "ru" else "Ready. /lang for language."
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
        text = "Язык обновлён. / Language updated." if lang == "en" else "Язык обновлён."
        await query.edit_message_text(text)
        role = user.get("role") if user else None
        mode = str(user.get("mode") or "") if user else ""
        kbd = _menu_keyboard(role, mode, lang)
        menu = "Готово. /lang — язык." if lang == "ru" else "Ready. /lang for language."
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=menu,
            reply_markup=kbd,
        )
    else:
        await query.edit_message_text("Ошибка. / Error.")


async def _do_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    hash_value: str,
) -> None:
    if not update.message or not update.message.from_user:
        return
    tid = update.message.from_user.id
    pool: asyncpg.Pool = context.bot_data["pool"]
    async with pool.acquire() as conn:
        user = await get_user_by_telegram_id(conn, str(tid))
    if user is None:
        await update.message.reply_text("Сначала /start. / Use /start first.")
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
        await update.message.reply_text("Доступ запрещён. / Access denied.")
        return
    cur = str(user.get("mode") or "candidate")
    new_mode = "hr" if cur == "candidate" else "candidate"
    async with pool.acquire() as conn:
        await update_mode(conn, str(tid), new_mode)
    lang = str(user.get("language") or "en")
    kbd = _menu_keyboard("admin", new_mode, lang)
    msg = f"Режим: {new_mode}." if lang == "ru" else f"Mode: {new_mode}."
    await update.message.reply_text(msg, reply_markup=kbd)


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
        await update.message.reply_text(
            "Введите токен кандидата. / Enter candidate token.",
        )
        return
    await _do_link(update, context, args[0])


async def hash_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate hash for candidate. Only candidate or admin in candidate mode. Block recruiters."""
    if not update.message or not update.message.from_user:
        return
    tid = update.message.from_user.id
    pool: asyncpg.Pool = context.bot_data["pool"]

    async with pool.acquire() as conn:
        user = await get_user_by_telegram_id(conn, str(tid))

    if user is None:
        await update.message.reply_text("Сначала /start. / Use /start first.")
        return

    role = user.get("role")
    mode = user.get("mode")
    if not _is_candidate_eligible(role, str(mode) if mode else None):
        await update.message.reply_text("Доступ запрещён. / Access denied.")
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
            "Использование: /lang ru или /lang en\nUsage: /lang ru or /lang en",
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
        await update.message.reply_text("Пользователь не найден. / User not found.")


async def match_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Match candidate (dialog_owner_id) to vacancy. Recruiter only. Use /link first."""
    if not update.message or not update.message.from_user:
        return
    tid = update.message.from_user.id
    pool: asyncpg.Pool = context.bot_data["pool"]
    async with pool.acquire() as conn:
        user = await get_user_by_telegram_id(conn, str(tid))
    if user is None:
        await update.message.reply_text("Сначала /start. / Use /start first.")
        return
    if not _is_hr_eligible(user.get("role"), str(user.get("mode")) if user.get("mode") else None):
        await update.message.reply_text("Только для рекрутеров. / Recruiters only.")
        return
    dialog_owner_id = user.get("dialog_owner_id")
    if not dialog_owner_id:
        await update.message.reply_text(
            "Свяжитесь с кандидатом через /link.\nUse /link to link to a candidate.",
        )
        return
    _set_state(tid, BotState.WAITING_VACANCY_TEXT)
    await update.message.reply_text(
        "Отправьте файл в формате doc(x)/pdf/txt или краткое описание (не больше 1 сообщения телеграм).\n"
        "Send a file (doc/docx/pdf/txt) or brief description (max 1 Telegram message).",
    )


async def update_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask candidate to upload .txt resume."""
    if not update.message or not update.message.from_user:
        return
    tid = update.message.from_user.id
    pool: asyncpg.Pool = context.bot_data["pool"]

    async with pool.acquire() as conn:
        user = await get_user_by_telegram_id(conn, str(tid))

    if user is None:
        await update.message.reply_text("Сначала /start. / Use /start first.")
        return

    role = user.get("role")
    mode = user.get("mode")
    if not _is_candidate_eligible(role, str(mode) if mode else None):
        await update.message.reply_text("Доступ запрещён. / Access denied.")
        return

    _set_state(tid, BotState.RESUME_PENDING)
    await update.message.reply_text(
        "Загрузите резюме в формате .txt (макс. 1 МБ).\n"
        "Upload your resume as .txt file (max 1 MB).",
    )


VACANCY_REJECT_MSG = (
    "Формат не поддерживается. Используйте doc(x)/pdf/txt.\n"
    "Format not supported. Use doc/docx/pdf/txt."
)


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
    dialog_owner_id = user.get("dialog_owner_id")
    if not dialog_owner_id:
        await update.message.reply_text(
            "Связь с кандидатом потеряна. Используйте /link.\nLink lost. Use /link.",
        )
        return
    base_url = os.getenv("BASE_API_URL", "").rstrip("/")
    if not base_url:
        await update.message.reply_text("Ошибка сервиса. / Service error.")
        return
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{base_url}/match-vacancy",
                json={"owner_id": str(dialog_owner_id), "vacancy_text": vacancy_text, "summary": False},
            )
    except Exception as e:
        logger.exception("Match API error: %s", e)
        await update.message.reply_text("Ошибка запроса. / Request error.")
        return
    if r.status_code != 200:
        try:
            d = r.json().get("detail", r.text) if r.text else "Unknown error"
            d = d[0] if isinstance(d, list) and d else d
            err = d.get("msg", str(d)) if isinstance(d, dict) else str(d)
        except Exception:
            err = "Ошибка. / Error."
        await update.message.reply_text(str(err)[:500])
        return
    data = r.json()
    if not isinstance(data, list):
        await update.message.reply_text("Ошибка формата. / Invalid response.")
        return
    if not data:
        await update.message.reply_text(
            "Нет результатов (нет документов кандидата).\nNo results (no candidate documents).",
        )
        return
    item = data[0]
    score = item.get("relevance_score", 0)
    snippets = item.get("matched_snippets") or []
    lines = [f"Релевантность: {score:.2f}\nRelevance: {score:.2f}"]
    for i, s in enumerate(snippets[:5], 1):
        t = str(s)
        lines.append(f"\n[{i}] {t[:300]}{'...' if len(t) > 300 else ''}")
    await update.message.reply_text("\n".join(lines) if lines else "OK")


async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle resume file upload or vacancy file for /match."""
    if not update.message or not update.message.from_user or not update.message.document:
        return
    tid = update.message.from_user.id
    doc = update.message.document
    state = _get_state(tid)
    pool: asyncpg.Pool = context.bot_data["pool"]

    async with pool.acquire() as conn:
        user = await get_user_by_telegram_id(conn, str(tid))
    if user is None:
        return

    filename = doc.file_name or ""
    hr_with_dialog = _is_hr_eligible(
        user.get("role"), str(user.get("mode")) if user.get("mode") else None
    ) and bool(user.get("dialog_owner_id"))

    if hr_with_dialog:
        if not is_vacancy_file(filename):
            await update.message.reply_text(VACANCY_REJECT_MSG)
            return
        if doc.file_size and doc.file_size > MAX_VACANCY_FILE_BYTES:
            await update.message.reply_text(
                f"Файл слишком большой (макс. 5 МБ).\nFile too large (max 5 MB).",
            )
            return
        try:
            tg_file = await context.bot.get_file(doc.file_id)
            raw = await tg_file.download_as_bytearray()
        except Exception as e:
            logger.exception("Failed to download vacancy file: %s", e)
            await update.message.reply_text("Не удалось загрузить файл. / Failed to download file.")
            return
        vacancy_text = extract_vacancy_text(filename, bytes(raw))
        if not vacancy_text or not vacancy_text.strip():
            await update.message.reply_text(VACANCY_REJECT_MSG)
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
        await update.message.reply_text("Только .txt. / Only .txt files allowed.")
        return

    if doc.file_size and doc.file_size > MAX_RESUME_SIZE_BYTES:
        _set_state(tid, BotState.ACTIVE)
        await update.message.reply_text(
            f"Файл слишком большой (макс. 1 МБ). / File too large (max 1 MB).",
        )
        return

    base_url = os.getenv("BASE_API_URL", "").rstrip("/")
    if not base_url:
        _set_state(tid, BotState.ACTIVE)
        await update.message.reply_text("Ошибка сервиса. / Service error.")
        logger.error("BASE_API_URL not set")
        return

    try:
        tg_file = await context.bot.get_file(doc.file_id)
        raw = await tg_file.download_as_bytearray()
    except Exception as e:
        logger.exception("Failed to download file: %s", e)
        _set_state(tid, BotState.ACTIVE)
        await update.message.reply_text("Не удалось загрузить файл. / Failed to download file.")
        return

    if len(raw) > MAX_RESUME_SIZE_BYTES:
        _set_state(tid, BotState.ACTIVE)
        await update.message.reply_text("Файл слишком большой (макс. 1 МБ). / File too large (max 1 MB).")
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
        await update.message.reply_text("Ошибка при загрузке. / Upload error.")
        return

    _set_state(tid, BotState.ACTIVE)

    if r.status_code == 202:
        body = r.json()
        job_id = body.get("job_id", "")
        sent = await update.message.reply_text(
            f"Резюме принято (job_id: {job_id}).\nResume accepted.",
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
        await update.message.reply_text(f"Ошибка загрузки: {d}.\nUpload failed.")


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.from_user:
        return
    tid = update.message.from_user.id
    state = _get_state(tid)
    text = (update.message.text or "").strip()

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
            await update.message.reply_text("Ошибка. / Error.")
            return
        dialog_owner_id = user.get("dialog_owner_id")
        if not dialog_owner_id:
            _set_state(tid, BotState.ACTIVE)
            await update.message.reply_text(
                "Связь с кандидатом потеряна. Используйте /link.\nLink lost. Use /link.",
            )
            return
        if len(text) >= MAX_VACANCY_TEXT_CHARS:
            await update.message.reply_text(
                "Описание слишком большое, отправьте файл в формате doc(x)/pdf/txt.\n"
                "Description too long. Send a file in doc/docx/pdf/txt format.",
            )
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
            await update.message.reply_text("Доступ запрещён. / Access denied.")
            return

    if state == BotState.WAITING_HASH:
        t = text.lower()
        if t in ("ru", "en"):
            pool: asyncpg.Pool = context.bot_data["pool"]
            async with pool.acquire() as conn:
                await update_language(conn, str(tid), t)
            await update.message.reply_text("Язык выбран. Ожидание хэша.")
        else:
            await update.message.reply_text(
                "Выберите язык: ru или en\nChoose language: ru or en",
                reply_markup=_lang_keyboard(),
            )
        return

    if state == BotState.ACTIVE and text:
        pool = context.bot_data["pool"]
        async with pool.acquire() as conn:
            user = await get_user_by_telegram_id(conn, str(tid))
        if user is not None:
            role = user.get("role")
            mode = str(user.get("mode")) if user.get("mode") else None
            owner_id: str | None = None
            if _is_candidate_eligible(role, mode):
                owner_id = str(tid)
            elif _is_hr_eligible(role, mode):
                owner_id = user.get("dialog_owner_id")
                if not owner_id:
                    await update.message.reply_text(
                        "Свяжитесь с кандидатом через /link.\nUse /link to link to a candidate.",
                    )
                    return
            if owner_id:
                base_url = os.getenv("BASE_API_URL", "").rstrip("/")
                if not base_url:
                    await update.message.reply_text("Ошибка сервиса. / Service error.")
                    return
                sent = await update.message.reply_text("Обработка... / Processing...")
                try:
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        r = await client.post(
                            f"{base_url}/chat",
                            params={"owner_id": owner_id},
                            json={"message": text},
                        )
                except Exception as e:
                    logger.exception("Chat API error: %s", e)
                    if sent:
                        await context.bot.edit_message_text(
                            chat_id=sent.chat_id,
                            message_id=sent.message_id,
                            text="Ошибка запроса. / Request error.",
                        )
                    return
                if r.status_code != 202:
                    err = "Ошибка. / Error."
                    try:
                        d = r.json().get("detail", r.text) if r.text else ""
                        d = d[0] if isinstance(d, list) and d else d
                        d = d.get("msg", str(d)) if isinstance(d, dict) else str(d)
                        if d:
                            err = str(d)[:500]
                    except Exception:
                        pass
                    if sent:
                        await context.bot.edit_message_text(
                            chat_id=sent.chat_id,
                            message_id=sent.message_id,
                            text=err,
                        )
                    return
                body = r.json()
                msg_id = body.get("message_id", "")
                if not msg_id:
                    if sent:
                        await context.bot.edit_message_text(
                            chat_id=sent.chat_id,
                            message_id=sent.message_id,
                            text="Ошибка ответа. / Response error.",
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
