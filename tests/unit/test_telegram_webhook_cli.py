"""`mihomes telegram set-webhook | delete-webhook | webhook-info`.

Nothing called setWebhook before (SPEC-006 U3: "a deploy-time action nobody owns"). These pin
what the commands refuse before ever reaching Telegram, and what they send when they do.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from mihomes.cli import telegram as tg_cli

runner = CliRunner()
URL = "https://evo-dev.tail6765ef.ts.net:8443/webhooks/telegram"


class _FakeClient:
    calls: list[tuple] = []

    def __init__(self, token):
        _FakeClient.calls.append(("init", token))

    def set_webhook(self, url, secret, *, drop_pending=False):
        _FakeClient.calls.append(("set", url, secret, drop_pending))
        return {"ok": True}

    def delete_webhook(self, *, drop_pending=False):
        _FakeClient.calls.append(("delete", drop_pending))
        return {"ok": True}

    def get_webhook_info(self):
        return {"url": URL, "pending_update_count": 2, "allowed_updates": ["message"],
                "last_error_date": 1756500000, "last_error_message": "Wrong response from the webhook: 401"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    _FakeClient.calls = []
    monkeypatch.setattr(tg_cli, "TelegramClient", _FakeClient)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:platform")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret_Token-1")


@pytest.mark.parametrize(
    ("url", "fragment"),
    [
        ("http://evo-dev.example/webhooks/telegram", "https"),
        ("https://evo-dev.example:5000/webhooks/telegram", "ports"),
        ("https://evo-dev.example/", "/webhooks/telegram"),
        ("https:///webhooks/telegram", "no host"),
    ],
)
def test_bad_urls_are_refused_before_calling_telegram(url, fragment):
    assert fragment in tg_cli.webhook_url_problem(url)
    result = runner.invoke(tg_cli.app, ["set-webhook", "--url", url])
    assert result.exit_code == 1
    assert not [c for c in _FakeClient.calls if c[0] == "set"]


@pytest.mark.parametrize("url", [URL, "https://h.example/webhooks/telegram/", "https://h.example:443/webhooks/telegram"])
def test_good_urls_pass(url):
    assert tg_cli.webhook_url_problem(url) is None


@pytest.mark.parametrize(("secret", "fragment"), [("", "not set"), ("has space", "may only use")])
def test_bad_secrets_are_refused(monkeypatch, secret, fragment):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", secret)
    result = runner.invoke(tg_cli.app, ["set-webhook", "--url", URL])
    assert result.exit_code == 1
    assert fragment in result.output
    assert not [c for c in _FakeClient.calls if c[0] == "set"]


def test_set_webhook_registers_with_the_platform_token_and_secret():
    result = runner.invoke(tg_cli.app, ["set-webhook", "--url", URL, "--drop-pending"])
    assert result.exit_code == 0, result.output
    assert _FakeClient.calls == [("init", "123:platform"), ("set", URL, "s3cret_Token-1", True)]
    assert "s3cret" not in result.output  # never echo the secret


def test_no_platform_token_means_no_registration(monkeypatch):
    """No fallback to an account's telegram.bot_token: it could be a different bot."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    result = runner.invoke(tg_cli.app, ["set-webhook", "--url", URL])
    assert result.exit_code == 1
    assert "TELEGRAM_BOT_TOKEN" in result.output
    assert _FakeClient.calls == []


def test_delete_and_info():
    assert runner.invoke(tg_cli.app, ["delete-webhook"]).exit_code == 0
    assert ("delete", False) in _FakeClient.calls
    info = runner.invoke(tg_cli.app, ["webhook-info"])
    assert info.exit_code == 0, info.output
    assert "8443/webhooks/telegram" in info.output
    assert "401" in info.output
