"""The Telegram bot's reply logic, run offline (no network, no API key).

Networking (getUpdates/sendMessage) is a thin httpx wrapper and is not exercised here.
"""
from app.config import settings
from app.integrations.telegram import Bot


def _bot(monkeypatch, email="safety@festos.test"):
    monkeypatch.setattr(settings, "telegram_as_email", email)
    monkeypatch.setattr(settings, "anthropic_api_key", "")  # Force the assistant's offline routing.
    return Bot()


def test_help_is_returned(monkeypatch):
    bot = _bot(monkeypatch)
    assert "FestOS Command bot" in bot._reply_for("/help")
    assert "FestOS Command bot" in bot._reply_for("/start")


def test_unknown_command_is_rejected(monkeypatch):
    assert "don't know" in _bot(monkeypatch)._reply_for("/teleport")


def test_status_and_free_text_answer_from_data(monkeypatch):
    bot = _bot(monkeypatch)
    assert bot._reply_for("/status").strip()
    # A free-text question the Safety role can see routes through the assistant.
    assert "incident" in bot._reply_for("which safety incidents are open?").lower()


def test_permission_is_enforced_per_role(monkeypatch):
    # The Safety Coordinator has no accommodation access, so the bot refuses that topic.
    assert "access" in _bot(monkeypatch)._reply_for("how many participants still need a bed?").lower()


def test_misconfigured_account_is_reported(monkeypatch):
    assert "not configured" in _bot(monkeypatch, "nobody@festos.test")._reply_for("/status")


def test_enabled_and_allow_list_parsing(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "x")
    monkeypatch.setattr(settings, "telegram_as_email", "safety@festos.test")
    monkeypatch.setattr(settings, "telegram_allowed_chat_ids", "123, -456, nope")
    bot = Bot()
    assert bot.enabled
    assert bot.allowed == {123, -456}
