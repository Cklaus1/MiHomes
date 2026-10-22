"""Which bot token the Telegram paths use.

`_reply` (the webhook's unlinked-sender path) used to build `TelegramClient()` with no token;
the TypeError was swallowed, so the link prompt never reached anyone. It now reads the
deployment's `TELEGRAM_BOT_TOKEN`, and the scoped client prefers the same token so every
webhook reply comes from the one bot the webhook is registered on.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

from mihomes.services.gateways.telegram import responder
from mihomes.services.gateways.telegram.client import platform_bot_token
from mihomes.web.routes import gateways as route


class _Recorder:
    built: list[str] = []
    sent: list[tuple[str, str]] = []

    def __init__(self, token):
        _Recorder.built.append(token)

    def send_message(self, chat_id, text):
        _Recorder.sent.append((chat_id, text))


def _reset():
    _Recorder.built.clear()
    _Recorder.sent.clear()


def test_platform_token_reads_and_trims_the_environment(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "  123:abc \n")
    assert platform_bot_token() == "123:abc"
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    assert platform_bot_token() == ""


def test_reply_sends_with_the_platform_token(monkeypatch):
    _reset()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setattr("mihomes.services.gateways.telegram.client.TelegramClient", _Recorder)
    route._reply("-100", route.LINK_PROMPT)
    assert _Recorder.built == ["123:abc"]
    assert _Recorder.sent == [("-100", route.LINK_PROMPT)]


def test_reply_without_a_token_warns_instead_of_failing_silently(monkeypatch, caplog):
    _reset()
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr("mihomes.services.gateways.telegram.client.TelegramClient", _Recorder)
    with caplog.at_level(logging.WARNING):
        route._reply("-100", route.LINK_PROMPT)
    assert _Recorder.built == []
    assert "TELEGRAM_BOT_TOKEN is not set" in caplog.text


def test_scoped_client_prefers_the_platform_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:platform")
    with patch("mihomes.services.config_service.get_config", return_value="999:account"):
        assert responder._get_client(session=None).token == "123:platform"


def test_scoped_client_falls_back_to_account_config(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with patch("mihomes.services.config_service.get_config", return_value="999:account"):
        assert responder._get_client(session=None).token == "999:account"
