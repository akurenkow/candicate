"""Unit tests for telegram-bot: edge cases and basic use-cases."""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_service_root = Path(__file__).resolve().parent.parent
_s = str(_service_root)
if _s not in sys.path:
    sys.path.insert(0, _s)

from main import (
    BotState,
    BTN_TO_ACTION,
    MAX_RESUME_SIZE_BYTES,
    MAX_VACANCY_TEXT_CHARS,
    TEXTS,
    _do_link,
    _get_allowed_upload_user_ids,
    _get_state,
    _is_candidate_eligible,
    _is_hr_eligible,
    _lang_keyboard,
    _looks_like_token,
    _menu_keyboard,
    _set_state,
    _t,
    document_handler,
    hash_handler,
    help_handler,
    lang_callback,
    lang_handler,
    link_handler,
    match_handler,
    message_handler,
    ping_handler,
    start_handler,
    switch_handler,
    update_handler,
)
import main as main_mod


def _make_update_message(tid: int = 12345, text: str = "", chat_id: int = 1):
    u = MagicMock()
    u.message = MagicMock()
    u.message.from_user = MagicMock()
    u.message.from_user.id = tid
    u.message.text = text
    u.message.chat_id = chat_id
    u.callback_query = None
    return u


def _make_context(pool_mock):
    ctx = MagicMock()
    ctx.bot_data = {"pool": pool_mock}
    ctx.bot = MagicMock()
    ctx.args = []
    return ctx


def _make_update_callback(tid: int = 12345, data: str = "lang:ru", chat_id: int = 1):
    u = MagicMock()
    u.message = None
    u.callback_query = MagicMock()
    u.callback_query.data = data
    u.callback_query.from_user = MagicMock()
    u.callback_query.from_user.id = tid
    u.callback_query.message = MagicMock()
    u.callback_query.message.chat_id = chat_id
    return u


# --- Pure functions ---


def test_looks_like_token_valid_long_alnum():
    assert _looks_like_token("a" * 20) is True
    assert _looks_like_token("AbCdEf12345678901234567890") is True


def test_looks_like_token_valid_with_dashes():
    assert _looks_like_token("abc-def_ghi-01234567890") is True


def test_looks_like_token_too_short():
    assert _looks_like_token("a" * 19) is False
    assert _looks_like_token("short") is False


def test_looks_like_token_empty():
    assert _looks_like_token("") is False
    assert _looks_like_token("   ") is False
    assert _looks_like_token(None) is False


def test_looks_like_token_invalid_chars():
    assert _looks_like_token("a" * 20 + "!@#") is False


def test_t_ru():
    assert _t("cmd_start", "ru") == TEXTS["ru"]["cmd_start"]
    assert _t("update_resume", "RU") == TEXTS["ru"]["update_resume"]


def test_t_en():
    assert _t("cmd_start", "en") == TEXTS["en"]["cmd_start"]
    assert _t("update_resume", "EN") == TEXTS["en"]["update_resume"]


def test_t_none_falls_back_to_en():
    assert _t("cmd_start", None) == TEXTS["en"]["cmd_start"]


def test_t_unknown_lang_falls_back_to_en():
    assert _t("cmd_start", "fr") == TEXTS["en"]["cmd_start"]
    assert _t("cmd_start", "de") == TEXTS["en"]["cmd_start"]


def test_t_unknown_key_returns_key():
    assert _t("unknown_key_xyz", "en") == "unknown_key_xyz"


def test_is_candidate_eligible_candidate():
    assert _is_candidate_eligible("candidate", None) is True
    assert _is_candidate_eligible("candidate", "") is True


def test_is_candidate_eligible_admin_candidate_mode():
    assert _is_candidate_eligible("admin", "candidate") is True


def test_is_candidate_eligible_admin_hr_mode():
    assert _is_candidate_eligible("admin", "hr") is False


def test_is_candidate_eligible_recruiter():
    assert _is_candidate_eligible("recruiter", None) is False
    assert _is_candidate_eligible("recruiter", "hr") is False


def test_is_hr_eligible_recruiter():
    assert _is_hr_eligible("recruiter", None) is True
    assert _is_hr_eligible("recruiter", "hr") is True


def test_is_hr_eligible_admin_hr_mode():
    assert _is_hr_eligible("admin", "hr") is True


def test_is_hr_eligible_admin_candidate_mode():
    assert _is_hr_eligible("admin", "candidate") is False


def test_is_hr_eligible_candidate():
    assert _is_hr_eligible("candidate", None) is False


def test_menu_keyboard_candidate():
    kbd = _menu_keyboard("candidate", None, "en")
    assert kbd is not None
    bts = [b.text for row in kbd.keyboard for b in row]
    assert TEXTS["en"]["update_resume"] in bts
    assert TEXTS["en"]["generate_hash"] in bts


def test_menu_keyboard_recruiter():
    kbd = _menu_keyboard("recruiter", None, "en")
    bts = [b.text for row in kbd.keyboard for b in row]
    assert TEXTS["en"]["link_candidate"] in bts
    assert TEXTS["en"]["match"] in bts


def test_menu_keyboard_admin_hr_mode():
    kbd = _menu_keyboard("admin", "hr", "en")
    bts = [b.text for row in kbd.keyboard for b in row]
    assert TEXTS["en"]["switch_mode"] in bts
    assert TEXTS["en"]["link_candidate"] in bts
    assert TEXTS["en"]["match"] in bts


def test_menu_keyboard_admin_candidate_mode():
    kbd = _menu_keyboard("admin", "candidate", "en")
    bts = [b.text for row in kbd.keyboard for b in row]
    assert TEXTS["en"]["update_resume"] in bts
    assert TEXTS["en"]["generate_hash"] in bts


def test_lang_keyboard():
    kbd = _lang_keyboard()
    assert kbd is not None
    bts = [b.text for row in kbd.inline_keyboard for b in row]
    assert "Русский" in bts
    assert "English" in bts


def test_btn_to_action_covers_both_langs():
    for action in ("update", "hash", "help", "link", "match", "switch"):
        vals = [k for k, v in BTN_TO_ACTION.items() if v == action]
        assert len(vals) >= 1


# --- State ---


def test_state_get_set():
    tid = 999991
    assert _get_state(tid) == BotState.UNREGISTERED
    _set_state(tid, BotState.ACTIVE)
    assert _get_state(tid) == BotState.ACTIVE
    _set_state(tid, BotState.UNREGISTERED)
    main_mod._user_states.pop(tid, None)


def test_get_allowed_upload_empty():
    with patch.dict(os.environ, {"ALLOWED_UPLOAD_USER_IDS": ""}, clear=False):
        assert _get_allowed_upload_user_ids() == frozenset()


def test_get_allowed_upload_single():
    with patch.dict(os.environ, {"ALLOWED_UPLOAD_USER_IDS": "111"}, clear=False):
        assert _get_allowed_upload_user_ids() == frozenset({"111"})


def test_get_allowed_upload_multiple():
    with patch.dict(os.environ, {"ALLOWED_UPLOAD_USER_IDS": "111, 222 , 333"}, clear=False):
        assert _get_allowed_upload_user_ids() == frozenset({"111", "222", "333"})


# --- Handlers (with mocks) ---


@pytest.fixture
def pool_mock():
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value="UPDATE 1")
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=conn)
    return pool


@pytest.mark.asyncio
async def test_start_handler_no_message():
    u = MagicMock()
    u.message = None
    ctx = _make_context(AsyncMock())
    await start_handler(u, ctx)
    ctx.bot_data["pool"].acquire.assert_not_called()


@pytest.mark.asyncio
async def test_start_handler_no_from_user():
    u = MagicMock()
    u.message = MagicMock()
    u.message.from_user = None
    ctx = _make_context(AsyncMock())
    await start_handler(u, ctx)


@pytest.mark.asyncio
async def test_start_handler_new_user_shows_lang(pool_mock):
    u = _make_update_message(tid=77771)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    with patch.object(main_mod, "get_user_by_telegram_id", AsyncMock(return_value=None)):
        with patch.object(main_mod, "create_user", AsyncMock()):
            with patch.object(main_mod, "update_role", AsyncMock()):
                with patch.dict(os.environ, {"ALLOWED_UPLOAD_USER_IDS": ""}, clear=False):
                    await start_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    call_args = u.message.reply_text.call_args
    assert "language" in call_args[0][0].lower() or "язык" in call_args[0][0].lower()
    main_mod._user_states.pop(77771, None)


@pytest.mark.asyncio
async def test_start_handler_existing_user_shows_menu(pool_mock):
    u = _make_update_message(tid=77772)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    user_row = {
        "role": "candidate",
        "language": "en",
        "mode": None,
    }
    with patch.object(main_mod, "get_user_by_telegram_id", AsyncMock(return_value=user_row)):
        with patch.dict(os.environ, {"ALLOWED_UPLOAD_USER_IDS": ""}, clear=False):
            await start_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert "Ready" in u.message.reply_text.call_args[0][0] or "Готово" in u.message.reply_text.call_args[0][0]
    main_mod._user_states.pop(77772, None)


@pytest.mark.asyncio
async def test_lang_handler_invalid_args(pool_mock):
    u = _make_update_message(tid=77773, text="/lang")
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    ctx.args = ["fr"]
    await lang_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert "lang" in u.message.reply_text.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_lang_handler_valid(pool_mock):
    u = _make_update_message(tid=77774)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    ctx.args = ["ru"]
    with patch.object(main_mod, "update_language", AsyncMock(return_value=1)):
        with patch.object(main_mod, "get_user_by_telegram_id", AsyncMock(return_value={"role": "candidate", "mode": None})):
            await lang_handler(u, ctx)
    u.message.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_lang_callback_no_query():
    u = MagicMock()
    u.callback_query = None
    ctx = MagicMock()
    await lang_callback(u, ctx)


@pytest.mark.asyncio
async def test_lang_callback_valid(pool_mock):
    u = _make_update_callback(tid=77775, data="lang:en")
    u.callback_query.answer = AsyncMock()
    u.callback_query.edit_message_text = AsyncMock()
    ctx = _make_context(pool_mock)
    ctx.bot.send_message = AsyncMock()
    with patch.object(main_mod, "update_language", AsyncMock(return_value=1)):
        with patch.object(main_mod, "get_user_by_telegram_id", AsyncMock(return_value={"role": "candidate", "mode": None})):
            await lang_callback(u, ctx)
    u.callback_query.answer.assert_called_once()
    u.callback_query.edit_message_text.assert_called_once()
    ctx.bot.send_message.assert_called_once()
    main_mod._user_states.pop(77775, None)


@pytest.mark.asyncio
async def test_help_handler_no_user(pool_mock):
    u = _make_update_message(tid=77776)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    with patch.object(main_mod, "get_user_by_telegram_id", AsyncMock(return_value=None)):
        await help_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert "candidate" in u.message.reply_text.call_args[0][0].lower() or "кандидат" in u.message.reply_text.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_hash_handler_unregistered(pool_mock):
    u = _make_update_message(tid=77777)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    with patch.object(main_mod, "get_user_by_telegram_id", AsyncMock(return_value=None)):
        await hash_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert "start" in u.message.reply_text.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_hash_handler_recruiter_denied(pool_mock):
    u = _make_update_message(tid=77778)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    with patch.object(main_mod, "get_user_by_telegram_id", AsyncMock(return_value={"role": "recruiter", "mode": None})):
        await hash_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert "denied" in u.message.reply_text.call_args[0][0].lower() or "запрещён" in u.message.reply_text.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_hash_handler_candidate_success(pool_mock):
    u = _make_update_message(tid=77779)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    with patch.object(main_mod, "get_user_by_telegram_id", AsyncMock(return_value={"role": "candidate", "mode": None})):
        with patch.object(main_mod, "insert_hash", AsyncMock()):
            with patch.object(main_mod, "generate_hash_token", return_value="abcd" * 8):
                await hash_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert "abcd" in u.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_link_handler_no_args_enters_waiting(pool_mock):
    u = _make_update_message(tid=77780)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    ctx.args = []
    with patch.object(main_mod, "get_user_by_telegram_id", AsyncMock(return_value={"role": "recruiter", "mode": None})):
        await link_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert _get_state(77780) == BotState.WAITING_LINK_TOKEN
    main_mod._user_states.pop(77780, None)


@pytest.mark.asyncio
async def test_link_handler_unregistered(pool_mock):
    u = _make_update_message(tid=77781)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    ctx.args = ["token12345678901234567890"]
    with patch.object(main_mod, "get_user_by_telegram_id", AsyncMock(return_value=None)):
        await _do_link(u, ctx, "token12345678901234567890")
    u.message.reply_text.assert_called_once()
    assert "start" in u.message.reply_text.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_switch_handler_non_admin(pool_mock):
    u = _make_update_message(tid=77782)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    with patch.object(main_mod, "get_user_by_telegram_id", AsyncMock(return_value={"role": "candidate", "mode": None})):
        await switch_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert "denied" in u.message.reply_text.call_args[0][0].lower() or "запрещён" in u.message.reply_text.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_switch_handler_admin_toggles(pool_mock):
    u = _make_update_message(tid=77783)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    with patch.object(main_mod, "get_user_by_telegram_id", AsyncMock(return_value={"role": "admin", "mode": "candidate", "language": "en"})):
        with patch.object(main_mod, "update_mode", AsyncMock(return_value=1)):
            await switch_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert "hr" in u.message.reply_text.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_update_handler_recruiter_denied(pool_mock):
    u = _make_update_message(tid=77784)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    with patch.object(main_mod, "get_user_by_telegram_id", AsyncMock(return_value={"role": "recruiter", "mode": None})):
        await update_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert "denied" in u.message.reply_text.call_args[0][0].lower() or "запрещён" in u.message.reply_text.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_update_handler_candidate_asks_upload(pool_mock):
    u = _make_update_message(tid=77785)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    with patch.object(main_mod, "get_user_by_telegram_id", AsyncMock(return_value={"role": "candidate", "mode": None})):
        await update_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert ".txt" in u.message.reply_text.call_args[0][0]
    assert _get_state(77785) == BotState.RESUME_PENDING
    main_mod._user_states.pop(77785, None)


@pytest.mark.asyncio
async def test_match_handler_recruiter_no_link(pool_mock):
    u = _make_update_message(tid=77786)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    with patch.object(main_mod, "get_user_by_telegram_id", AsyncMock(return_value={"role": "recruiter", "mode": None, "dialog_owner_id": None})):
        await match_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert "link" in u.message.reply_text.call_args[0][0].lower() or "свяжитесь" in u.message.reply_text.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_match_handler_candidate_denied(pool_mock):
    u = _make_update_message(tid=77787)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    with patch.object(main_mod, "get_user_by_telegram_id", AsyncMock(return_value={"role": "candidate", "mode": None})):
        await match_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert "recruiter" in u.message.reply_text.call_args[0][0].lower() or "рекрутер" in u.message.reply_text.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_match_handler_recruiter_with_link_enters_waiting(pool_mock):
    u = _make_update_message(tid=77788)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    with patch.object(main_mod, "get_user_by_telegram_id", AsyncMock(return_value={"role": "recruiter", "mode": None, "dialog_owner_id": "owner123"})):
        await match_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert _get_state(77788) == BotState.WAITING_VACANCY_TEXT
    main_mod._user_states.pop(77788, None)


@pytest.mark.asyncio
async def test_document_handler_no_document(pool_mock):
    u = MagicMock()
    u.message = MagicMock()
    u.message.from_user = MagicMock()
    u.message.from_user.id = 77789
    u.message.document = None
    ctx = _make_context(pool_mock)
    await document_handler(u, ctx)


@pytest.mark.asyncio
async def test_document_handler_candidate_wrong_ext(pool_mock):
    u = _make_update_message(tid=77790)
    u.message.document = MagicMock()
    u.message.document.file_name = "resume.pdf"
    u.message.document.file_id = "fid"
    u.message.document.file_size = 100
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    ctx.bot.get_file = AsyncMock()
    ctx.bot.get_file.return_value.download_as_bytearray = AsyncMock(return_value=bytearray(b"x"))
    _set_state(77790, BotState.RESUME_PENDING)
    with patch.object(main_mod, "get_user_by_telegram_id", AsyncMock(return_value={"role": "candidate", "mode": None})):
        await document_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert ".txt" in u.message.reply_text.call_args[0][0] or "txt" in u.message.reply_text.call_args[0][0].lower()
    main_mod._user_states.pop(77790, None)


@pytest.mark.asyncio
async def test_document_handler_candidate_file_too_large(pool_mock):
    u = _make_update_message(tid=77791)
    u.message.document = MagicMock()
    u.message.document.file_name = "resume.txt"
    u.message.document.file_id = "fid"
    u.message.document.file_size = MAX_RESUME_SIZE_BYTES + 1
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    _set_state(77791, BotState.RESUME_PENDING)
    with patch.object(main_mod, "get_user_by_telegram_id", AsyncMock(return_value={"role": "candidate", "mode": None})):
        await document_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert "1 MB" in u.message.reply_text.call_args[0][0] or "large" in u.message.reply_text.call_args[0][0].lower()
    main_mod._user_states.pop(77791, None)


@pytest.mark.asyncio
async def test_message_handler_waiting_vacancy_too_long(pool_mock):
    u = _make_update_message(tid=77792, text="x" * (MAX_VACANCY_TEXT_CHARS + 1))
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    _set_state(77792, BotState.WAITING_VACANCY_TEXT)
    with patch.object(main_mod, "get_user_by_telegram_id", AsyncMock(return_value={"role": "recruiter", "mode": None, "dialog_owner_id": "o1"})):
        await message_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert "long" in u.message.reply_text.call_args[0][0].lower() or "больш" in u.message.reply_text.call_args[0][0].lower()
    main_mod._user_states.pop(77792, None)


@pytest.mark.asyncio
async def test_message_handler_waiting_hash_invalid_lang(pool_mock):
    u = _make_update_message(tid=77793, text="de")
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    _set_state(77793, BotState.WAITING_HASH)
    await message_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert "ru" in u.message.reply_text.call_args[0][0].lower() or "en" in u.message.reply_text.call_args[0][0].lower()
    main_mod._user_states.pop(77793, None)


@pytest.mark.asyncio
async def test_ping_handler():
    u = _make_update_message(tid=77794, text="/ping")
    u.message.reply_text = AsyncMock()
    ctx = MagicMock()
    await ping_handler(u, ctx)
    u.message.reply_text.assert_called_once_with("pong")
