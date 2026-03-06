# noqa: D100
"""E2E checks: telegram-bot link and phone detection and escalation (no real Telegram)."""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_root = Path(__file__).resolve().parent.parent
_telegram_bot_root = _root / "services" / "telegram-bot"
if str(_telegram_bot_root) not in sys.path:
    sys.path.insert(0, str(_telegram_bot_root))

import main as main_mod


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


def test_e2e_message_has_url():
    u = _make_update_message(text="https://example.com/job")
    u.message.entities = []
    assert main_mod._message_has_url(u) is True


def test_e2e_message_has_url_entity():
    u = _make_update_message(text="link")
    u.message.entities = [MagicMock(type="url")]
    assert main_mod._message_has_url(u) is True


def test_e2e_message_has_url_negative():
    u = _make_update_message(text="no link here")
    u.message.entities = []
    assert main_mod._message_has_url(u) is False


def test_e2e_message_has_phone():
    u = _make_update_message(text="+7 999 123-45-67")
    u.message.entities = []
    assert main_mod._message_has_phone(u) is True


def test_e2e_message_has_phone_entity():
    u = _make_update_message(text="call")
    u.message.entities = [MagicMock(type="phone_number")]
    assert main_mod._message_has_phone(u) is True


def test_e2e_message_has_phone_negative():
    u = _make_update_message(text="no number")
    u.message.entities = []
    assert main_mod._message_has_phone(u) is False


def _mock_ui_response(status_code=200, json_dict=None):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = json_dict if json_dict is not None else {}
    return m


@pytest.mark.asyncio
async def test_e2e_recruiter_message_with_url_forwards_no_chat():
    u = _make_update_message(tid=88001, text="see https://job.site", message_id=201)
    u.message.entities = [MagicMock(type="url")]
    u.message.reply_text = AsyncMock()
    ctx = MagicMock()
    ctx.bot_data = {}
    ctx.bot = MagicMock()
    ctx.bot.forward_message = AsyncMock()
    main_mod._set_state(88001, main_mod.BotState.ACTIVE)
    try:
        with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
            with patch.object(main_mod, "_ui_get", AsyncMock(return_value=_mock_ui_response(200, {"role": "recruiter", "mode": None, "dialog_owner_id": "777666", "language": "en"}))):
                with patch.object(main_mod, "_ensure_started", AsyncMock(return_value=True)):
                    with patch.object(main_mod, "_process_chat_message", AsyncMock()) as process_chat:
                        await main_mod.message_handler(u, ctx)
        ctx.bot.forward_message.assert_called_once()
        assert ctx.bot.forward_message.call_args[1]["chat_id"] == 777666
        process_chat.assert_not_called()
    finally:
        main_mod._user_states.pop(88001, None)


@pytest.mark.asyncio
async def test_e2e_recruiter_message_with_phone_replies_and_forwards():
    u = _make_update_message(tid=88002, text="call +79991234567", message_id=202)
    u.message.entities = [MagicMock(type="phone_number")]
    u.message.reply_text = AsyncMock()
    ctx = MagicMock()
    ctx.bot_data = {}
    ctx.bot = MagicMock()
    ctx.bot.forward_message = AsyncMock()
    main_mod._set_state(88002, main_mod.BotState.ACTIVE)
    try:
        with patch.dict(os.environ, {"UI_CLIENT_URL": "http://test"}, clear=False):
            with patch.object(main_mod, "_ui_get", AsyncMock(return_value=_mock_ui_response(200, {"role": "recruiter", "mode": None, "dialog_owner_id": "777555", "language": "ru"}))):
                with patch.object(main_mod, "_ensure_started", AsyncMock(return_value=True)):
                    await main_mod.message_handler(u, ctx)
        u.message.reply_text.assert_called_once()
        assert main_mod.TEXTS["ru"]["phone_escalation_reply"] in u.message.reply_text.call_args[0][0]
        ctx.bot.forward_message.assert_called_once()
        assert ctx.bot.forward_message.call_args[1]["chat_id"] == 777555
    finally:
        main_mod._user_states.pop(88002, None)
