"""Unit tests for telegram-bot (thin client): edge cases and basic use-cases. Mocks _ui_get/_ui_post."""

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
    MAX_CHAT_MESSAGE_CHARS,
    MAX_RESUME_SIZE_BYTES,
    MAX_VACANCY_TEXT_CHARS,
    TEXTS,
    _do_link,
    _do_match_vacancy,
    _get_state,
    _is_candidate_eligible,
    _is_hr_eligible,
    _lang_keyboard,
    _looks_like_token,
    _menu_keyboard,
    _message_has_phone,
    _message_has_url,
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


def _mock_ui_response(status_code: int = 200, json_dict: dict | list | None = None):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = json_dict if json_dict is not None else {}
    return m


def _text_matches_key(reply_text: str, key: str, lang: str | None = None) -> bool:
    """True if reply_text equals TEXTS value for key (single lang or any lang)."""
    if lang:
        return reply_text == TEXTS.get(lang, {}).get(key)
    return any(reply_text == TEXTS.get(l, {}).get(key) for l in TEXTS if key in TEXTS.get(l, {}))


def _make_update_message(tid: int = 12345, text: str = "", chat_id: int = 1, entities=None, message_id: int = 100):
    u = MagicMock()
    u.message = MagicMock()
    u.message.from_user = MagicMock()
    u.message.from_user.id = tid
    u.message.text = text
    u.message.chat_id = chat_id
    u.message.entities = entities or []
    u.message.message_id = message_id
    u.callback_query = None
    return u


def _make_context(pool_mock=None):
    ctx = MagicMock()
    ctx.bot_data = {}
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


def test_message_has_url_no_message():
    u = MagicMock()
    u.message = None
    assert _message_has_url(u) is False


def test_message_has_url_no_text():
    u = _make_update_message(text="")
    u.message.text = None
    assert _message_has_url(u) is False


def test_message_has_url_entity_url():
    u = _make_update_message(text="see https://example.com")
    u.message.entities = [MagicMock(type="url")]
    assert _message_has_url(u) is True


def test_message_has_url_entity_text_link():
    u = _make_update_message(text="click here")
    u.message.entities = [MagicMock(type="text_link")]
    assert _message_has_url(u) is True


def test_message_has_url_regex():
    u = _make_update_message(text="visit https://example.com/path")
    u.message.entities = []
    assert _message_has_url(u) is True


def test_message_has_url_no_url():
    u = _make_update_message(text="no link here")
    u.message.entities = []
    assert _message_has_url(u) is False


def test_message_has_phone_no_message():
    u = MagicMock()
    u.message = None
    assert _message_has_phone(u) is False


def test_message_has_phone_no_text():
    u = _make_update_message(text="")
    u.message.text = None
    assert _message_has_phone(u) is False


def test_message_has_phone_entity():
    u = _make_update_message(text="call me")
    u.message.entities = [MagicMock(type="phone_number")]
    assert _message_has_phone(u) is True


def test_message_has_phone_regex():
    u = _make_update_message(text="+7 999 123-45-67")
    u.message.entities = []
    assert _message_has_phone(u) is True


def test_message_has_phone_no_phone():
    u = _make_update_message(text="no number")
    u.message.entities = []
    assert _message_has_phone(u) is False


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


def test_is_hr_eligible_none_role_treated_as_recruiter():
    assert _is_hr_eligible(None, None) is True


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
    assert TEXTS["en"]["help"] in bts
    assert TEXTS["en"]["match"] not in bts


def test_menu_keyboard_recruiter_with_link():
    kbd = _menu_keyboard("recruiter", None, "en", dialog_owner_id="owner123")
    bts = [b.text for row in kbd.keyboard for b in row]
    assert TEXTS["en"]["link_candidate"] in bts
    assert TEXTS["en"]["match"] in bts
    assert TEXTS["en"]["help"] in bts


def test_menu_keyboard_none_role_shows_recruiter():
    kbd = _menu_keyboard(None, None, "en")
    bts = [b.text for row in kbd.keyboard for b in row]
    assert TEXTS["en"]["link_candidate"] in bts
    assert TEXTS["en"]["help"] in bts
    assert TEXTS["en"]["match"] not in bts


def test_menu_keyboard_admin_hr_mode():
    kbd = _menu_keyboard("admin", "hr", "en")
    bts = [b.text for row in kbd.keyboard for b in row]
    assert TEXTS["en"]["switch_mode"] in bts
    assert TEXTS["en"]["link_candidate"] in bts
    assert TEXTS["en"]["help"] in bts
    assert TEXTS["en"]["match"] not in bts


def test_menu_keyboard_admin_hr_mode_with_link():
    kbd = _menu_keyboard("admin", "hr", "en", dialog_owner_id="owner456")
    bts = [b.text for row in kbd.keyboard for b in row]
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
    ctx = _make_context()
    await start_handler(u, ctx)


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
    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_post", AsyncMock(return_value=_mock_ui_response(200, {"need_language": True, "user": {}, "menu": None}))):
            await start_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    call_args = u.message.reply_text.call_args
    assert _text_matches_key(call_args[0][0], "choose_language")
    main_mod._user_states.pop(77771, None)


@pytest.mark.asyncio
async def test_start_handler_existing_user_shows_menu(pool_mock):
    u = _make_update_message(tid=77772)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    user_row = {"role": "candidate", "language": "en", "mode": None, "dialog_owner_id": None}
    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_post", AsyncMock(return_value=_mock_ui_response(200, {"need_language": False, "user": user_row, "menu": user_row}))):
            await start_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert _text_matches_key(u.message.reply_text.call_args[0][0], "menu_ready", "en")
    main_mod._user_states.pop(77772, None)


@pytest.mark.asyncio
async def test_lang_handler_invalid_args(pool_mock):
    u = _make_update_message(tid=77773, text="/lang")
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    ctx.args = ["fr"]
    await lang_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert _text_matches_key(u.message.reply_text.call_args[0][0], "lang_usage", "en")


@pytest.mark.asyncio
async def test_lang_handler_valid(pool_mock):
    u = _make_update_message(tid=77774)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    ctx.args = ["ru"]
    user = {"role": "candidate", "mode": None, "language": "ru", "dialog_owner_id": None}
    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_post", AsyncMock(return_value=_mock_ui_response(200, {"user": user, "menu": user}))):
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
    user = {"role": "candidate", "mode": None, "language": "en", "dialog_owner_id": None}
    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_post", AsyncMock(return_value=_mock_ui_response(200, {"user": user, "menu": user}))):
            await lang_callback(u, ctx)
    u.callback_query.answer.assert_called_once()
    u.callback_query.edit_message_text.assert_called_once()
    ctx.bot.send_message.assert_called_once()
    assert _text_matches_key(ctx.bot.send_message.call_args[0][1], "menu_ready", "en")
    main_mod._user_states.pop(77775, None)


@pytest.mark.asyncio
async def test_help_handler_no_user(pool_mock):
    u = _make_update_message(tid=77776)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_get", AsyncMock(return_value=_mock_ui_response(404))):
            await help_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert _text_matches_key(u.message.reply_text.call_args[0][0], "help_recruiter", "en")


@pytest.mark.asyncio
async def test_hash_handler_unregistered(pool_mock):
    u = _make_update_message(tid=77777)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_get", AsyncMock(return_value=_mock_ui_response(404))):
            await hash_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert "start" in u.message.reply_text.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_hash_handler_recruiter_denied(pool_mock):
    u = _make_update_message(tid=77778)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_post", AsyncMock(return_value=_mock_ui_response(403))):
            await hash_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert _text_matches_key(u.message.reply_text.call_args[0][0], "candidates_only", "en")


@pytest.mark.asyncio
async def test_hash_handler_candidate_success(pool_mock):
    u = _make_update_message(tid=77779)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_post", AsyncMock(return_value=_mock_ui_response(200, {"token": "abcd" * 8}))):
            await hash_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert "abcd" in u.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_link_handler_no_args_enters_waiting(pool_mock):
    u = _make_update_message(tid=77780)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    ctx.args = []
    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_get", AsyncMock(return_value=_mock_ui_response(200, {"role": "recruiter", "mode": None, "language": "en"}))):
            await link_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert _text_matches_key(u.message.reply_text.call_args[0][0], "enter_candidate_token", "en")
    assert _get_state(77780) == BotState.WAITING_LINK_TOKEN
    main_mod._user_states.pop(77780, None)


@pytest.mark.asyncio
async def test_link_handler_unregistered(pool_mock):
    u = _make_update_message(tid=77781)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    ctx.args = ["token12345678901234567890"]
    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_post", AsyncMock(return_value=_mock_ui_response(200, {"ok": False, "message": "Invalid hash"}))):
            await _do_link(u, ctx, "token12345678901234567890")
    u.message.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_switch_handler_non_admin(pool_mock):
    u = _make_update_message(tid=77782)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_get", AsyncMock(return_value=_mock_ui_response(200, {"role": "candidate", "mode": None, "language": "en"}))):
            with patch.object(main_mod, "_ui_post", AsyncMock(return_value=_mock_ui_response(403))):
                await switch_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert _text_matches_key(u.message.reply_text.call_args[0][0], "access_denied", "en")


@pytest.mark.asyncio
async def test_switch_handler_admin_toggles(pool_mock):
    u = _make_update_message(tid=77783)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_get", AsyncMock(return_value=_mock_ui_response(200, {"role": "admin", "mode": "candidate", "language": "en"}))):
            with patch.object(main_mod, "_ui_post", AsyncMock(return_value=_mock_ui_response(200, {"ok": True, "mode": "hr", "user": {"role": "admin", "mode": "hr", "language": "en", "dialog_owner_id": None}}))):
                await switch_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert u.message.reply_text.call_args[0][0] == _t("mode_switched", "en", mode="hr")


@pytest.mark.asyncio
async def test_update_handler_recruiter_denied(pool_mock):
    u = _make_update_message(tid=77784)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_get", AsyncMock(return_value=_mock_ui_response(200, {"role": "recruiter", "mode": None, "language": "en"}))):
            await update_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert _text_matches_key(u.message.reply_text.call_args[0][0], "candidates_only", "en")


@pytest.mark.asyncio
async def test_update_handler_candidate_asks_upload(pool_mock):
    u = _make_update_message(tid=77785)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_get", AsyncMock(return_value=_mock_ui_response(200, {"role": "candidate", "mode": None, "language": "en"}))):
            await update_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert _text_matches_key(u.message.reply_text.call_args[0][0], "upload_resume_txt", "en")
    assert _get_state(77785) == BotState.RESUME_PENDING
    main_mod._user_states.pop(77785, None)


@pytest.mark.asyncio
async def test_match_handler_recruiter_no_link(pool_mock):
    u = _make_update_message(tid=77786)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_get", AsyncMock(return_value=_mock_ui_response(200, {"role": "recruiter", "mode": None, "dialog_owner_id": None, "language": "en"}))):
            await match_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert _text_matches_key(u.message.reply_text.call_args[0][0], "use_link_first", "en")


@pytest.mark.asyncio
async def test_match_handler_candidate_denied(pool_mock):
    u = _make_update_message(tid=77787)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_get", AsyncMock(return_value=_mock_ui_response(200, {"role": "candidate", "mode": None, "language": "en"}))):
            await match_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert _text_matches_key(u.message.reply_text.call_args[0][0], "recruiters_only", "en")


@pytest.mark.asyncio
async def test_match_handler_recruiter_with_link_enters_waiting(pool_mock):
    u = _make_update_message(tid=77788)
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_get", AsyncMock(return_value=_mock_ui_response(200, {"role": "recruiter", "mode": None, "dialog_owner_id": "owner123", "language": "en"}))):
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
    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_get", AsyncMock(return_value=_mock_ui_response(200, {"role": "candidate", "mode": None, "language": "en"}))):
            await document_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert _text_matches_key(u.message.reply_text.call_args[0][0], "only_txt", "en")
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
    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_get", AsyncMock(return_value=_mock_ui_response(200, {"role": "candidate", "mode": None, "language": "en"}))):
            await document_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert _text_matches_key(u.message.reply_text.call_args[0][0], "file_too_large_1mb", "en")
    main_mod._user_states.pop(77791, None)


@pytest.mark.asyncio
async def test_message_handler_waiting_vacancy_too_long(pool_mock):
    u = _make_update_message(tid=77792, text="x" * (MAX_VACANCY_TEXT_CHARS + 1))
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    _set_state(77792, BotState.WAITING_VACANCY_TEXT)
    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_get", AsyncMock(return_value=_mock_ui_response(200, {"role": "recruiter", "mode": None, "dialog_owner_id": "o1", "language": "en"}))):
            await message_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert _text_matches_key(u.message.reply_text.call_args[0][0], "description_too_long", "en")
    main_mod._user_states.pop(77792, None)


@pytest.mark.asyncio
async def test_message_handler_waiting_hash_invalid_lang(pool_mock):
    u = _make_update_message(tid=77793, text="de")
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    _set_state(77793, BotState.WAITING_HASH)
    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_get", AsyncMock(return_value=_mock_ui_response(404))):
            await message_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert _text_matches_key(u.message.reply_text.call_args[0][0], "choose_lang_ru_en", "en")
    main_mod._user_states.pop(77793, None)


@pytest.mark.asyncio
async def test_message_handler_recruiter_with_url_forwards_to_candidate(pool_mock):
    u = _make_update_message(tid=77795, text="see https://job.example.com", message_id=101)
    u.message.entities = [MagicMock(type="url")]
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    ctx.bot.forward_message = AsyncMock()
    _set_state(77795, BotState.ACTIVE)
    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_get", AsyncMock(return_value=_mock_ui_response(200, {"role": "recruiter", "mode": None, "dialog_owner_id": "999888", "language": "en"}))):
            with patch.object(main_mod, "_ensure_started", AsyncMock(return_value=True)):
                await message_handler(u, ctx)
    ctx.bot.forward_message.assert_called_once()
    call_kw = ctx.bot.forward_message.call_args[1]
    assert call_kw["chat_id"] == 999888
    assert call_kw["from_chat_id"] == u.message.chat_id
    assert call_kw["message_id"] == 101
    u.message.reply_text.assert_not_called()
    main_mod._user_states.pop(77795, None)


@pytest.mark.asyncio
async def test_message_handler_recruiter_with_phone_replies_and_forwards(pool_mock):
    u = _make_update_message(tid=77796, text="my number +7 999 123-45-67", message_id=102)
    u.message.entities = [MagicMock(type="phone_number")]
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    ctx.bot.forward_message = AsyncMock()
    _set_state(77796, BotState.ACTIVE)
    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_get", AsyncMock(return_value=_mock_ui_response(200, {"role": "recruiter", "mode": None, "dialog_owner_id": "999777", "language": "ru"}))):
            with patch.object(main_mod, "_ensure_started", AsyncMock(return_value=True)):
                await message_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert _text_matches_key(u.message.reply_text.call_args[0][0], "phone_escalation_reply", "ru")
    ctx.bot.forward_message.assert_called_once()
    call_kw = ctx.bot.forward_message.call_args[1]
    assert call_kw["chat_id"] == 999777
    assert call_kw["message_id"] == 102
    main_mod._user_states.pop(77796, None)


@pytest.mark.asyncio
async def test_message_handler_recruiter_with_url_regex_forwards(pool_mock):
    u = _make_update_message(tid=77797, text="check http://example.org", message_id=103)
    u.message.entities = []
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    ctx.bot.forward_message = AsyncMock()
    _set_state(77797, BotState.ACTIVE)
    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_get", AsyncMock(return_value=_mock_ui_response(200, {"role": "recruiter", "mode": None, "dialog_owner_id": "888777", "language": "en"}))):
            with patch.object(main_mod, "_ensure_started", AsyncMock(return_value=True)):
                await message_handler(u, ctx)
    ctx.bot.forward_message.assert_called_once()
    assert ctx.bot.forward_message.call_args[1]["chat_id"] == 888777
    main_mod._user_states.pop(77797, None)


@pytest.mark.asyncio
async def test_message_handler_recruiter_plain_text_calls_process_chat(pool_mock):
    u = _make_update_message(tid=77798, text="just a question", message_id=104)
    u.message.entities = []
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    _set_state(77798, BotState.ACTIVE)
    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_get", AsyncMock(return_value=_mock_ui_response(200, {"role": "recruiter", "mode": None, "dialog_owner_id": "888666", "language": "en"}))):
            with patch.object(main_mod, "_ensure_started", AsyncMock(return_value=True)):
                with patch.object(main_mod, "_process_chat_message", AsyncMock()) as process_chat:
                    await message_handler(u, ctx)
    process_chat.assert_called_once()
    main_mod._user_states.pop(77798, None)


@pytest.mark.asyncio
async def test_message_handler_active_long_message_candidate_replies_use_letter(pool_mock):
    """In normal chat, candidate message > MAX_CHAT_MESSAGE_CHARS gets fallback suggesting /letter."""
    long_text = "x" * (MAX_CHAT_MESSAGE_CHARS + 1)
    u = _make_update_message(tid=77800, text=long_text, message_id=105)
    u.message.entities = []
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    _set_state(77800, BotState.ACTIVE)
    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_get", AsyncMock(return_value=_mock_ui_response(200, {"role": "candidate", "mode": None, "language": "en"}))):
            with patch.object(main_mod, "_ensure_started", AsyncMock(return_value=True)):
                await message_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert _text_matches_key(u.message.reply_text.call_args[0][0], "long_message_use_letter", "en")
    main_mod._user_states.pop(77800, None)


@pytest.mark.asyncio
async def test_message_handler_active_long_message_hr_replies_use_match(pool_mock):
    """In normal chat, HR message > MAX_CHAT_MESSAGE_CHARS gets fallback suggesting /match."""
    long_text = "y" * (MAX_CHAT_MESSAGE_CHARS + 1)
    u = _make_update_message(tid=77801, text=long_text, message_id=106)
    u.message.entities = []
    u.message.reply_text = AsyncMock()
    ctx = _make_context(pool_mock)
    _set_state(77801, BotState.ACTIVE)
    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_get", AsyncMock(return_value=_mock_ui_response(200, {"role": "recruiter", "mode": None, "dialog_owner_id": "999888", "language": "ru"}))):
            with patch.object(main_mod, "_ensure_started", AsyncMock(return_value=True)):
                await message_handler(u, ctx)
    u.message.reply_text.assert_called_once()
    assert _text_matches_key(u.message.reply_text.call_args[0][0], "long_message_use_match", "ru")
    main_mod._user_states.pop(77801, None)


@pytest.mark.asyncio
async def test_do_match_vacancy_non200_json_detail_shows_msg(pool_mock):
    """When match-vacancy API returns non-200 with JSON detail[].msg, user sees that msg and trace_id."""
    u = _make_update_message(tid=77802, text="vacancy text", message_id=107)
    u.message.reply_text = AsyncMock()
    u.message.chat_id = 1
    sent_msg = MagicMock()
    sent_msg.chat_id = 1
    sent_msg.message_id = 108
    u.message.reply_text.return_value = sent_msg
    ctx = _make_context(pool_mock)
    ctx.bot.edit_message_text = AsyncMock()
    fake_response = MagicMock()
    fake_response.status_code = 502
    fake_response.text = '{"detail":[{"msg":"conversation-manager unavailable"}]}'
    fake_response.json.return_value = {"detail": [{"msg": "conversation-manager unavailable"}]}

    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_get", AsyncMock(return_value=_mock_ui_response(200, {"role": "recruiter", "mode": None, "dialog_owner_id": "o1", "language": "en"}))):
            with patch.object(main_mod, "_ui_post", AsyncMock(return_value=fake_response)):
                await _do_match_vacancy(u, ctx, "vacancy text")
    ctx.bot.edit_message_text.assert_called_once()
    edit_text = ctx.bot.edit_message_text.call_args[1]["text"]
    assert "conversation-manager unavailable" in edit_text
    assert "trace_id=" in edit_text


@pytest.mark.asyncio
async def test_do_match_vacancy_non200_non_json_uses_fallback_and_trace_id(pool_mock):
    """When match-vacancy API returns non-200 with non-JSON body, user sees generic error and trace_id."""
    u = _make_update_message(tid=77803, text="vacancy text", message_id=109)
    u.message.reply_text = AsyncMock()
    u.message.chat_id = 2
    sent_msg = MagicMock()
    sent_msg.chat_id = 2
    sent_msg.message_id = 110
    u.message.reply_text.return_value = sent_msg
    ctx = _make_context(pool_mock)
    ctx.bot.edit_message_text = AsyncMock()
    fake_response = MagicMock()
    fake_response.status_code = 502
    fake_response.text = "<html>Gateway Error</html>"
    fake_response.json.side_effect = ValueError("not json")

    with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
        with patch.object(main_mod, "_ui_get", AsyncMock(return_value=_mock_ui_response(200, {"role": "recruiter", "mode": None, "dialog_owner_id": "o2", "language": "en"}))):
            with patch.object(main_mod, "_ui_post", AsyncMock(return_value=fake_response)):
                await _do_match_vacancy(u, ctx, "vacancy text")
    ctx.bot.edit_message_text.assert_called_once()
    edit_text = ctx.bot.edit_message_text.call_args[1]["text"]
    assert "trace_id=" in edit_text
    assert TEXTS["en"]["error_generic"] in edit_text


@pytest.mark.asyncio
async def test_ping_handler():
    u = _make_update_message(tid=77794, text="/ping")
    u.message.reply_text = AsyncMock()
    ctx = MagicMock()
    await ping_handler(u, ctx)
    u.message.reply_text.assert_called_once_with("pong")
