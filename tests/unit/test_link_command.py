"""`/link CODE` from an unlinked Telegram sender (2026-10-15).

`redeem_link_token` itself is covered by test_linking.py against Postgres. These pin the two
pieces around it that need no database: parsing the command, and the webhook's choice of reply.
"""

from __future__ import annotations

import contextlib

import pytest

from mihomes.services.gateways import linking
from mihomes.services.gateways.linking import parse_link_command
from mihomes.web.routes import gateways


@pytest.mark.parametrize(
    "text, code",
    [
        ("/link ABCD2345", "ABCD2345"),
        ("/link abcd2345", "ABCD2345"),
        ("  /link   ABCD2345  ", "ABCD2345"),
        ("/LINK ABCD2345", "ABCD2345"),
        ("/link@MiHomesBot ABCD2345", "ABCD2345"),
    ],
)
def test_parses_a_link_command(text, code):
    assert parse_link_command(text) == code


@pytest.mark.parametrize(
    "text",
    [None, "", "/link", "/link ", "hello", "link ABCD2345", "/linkABCD2345", "/link A B", "/start"],
)
def test_ignores_anything_else(text):
    assert parse_link_command(text) is None


@pytest.fixture
def no_db(monkeypatch):
    """`get_session` that yields a dummy, so the webhook helper runs without Postgres."""
    import mihomes.db

    @contextlib.contextmanager
    def _fake_session():
        yield object()

    monkeypatch.setattr(mihomes.db, "get_session", _fake_session)


def _message(text):
    return {"jid": "-100", "sender": "424242", "text": text}


def test_plain_message_gets_the_link_prompt(no_db, monkeypatch):
    called = []
    monkeypatch.setattr(linking, "redeem_link_token", lambda *a, **k: called.append(k))
    assert gateways._link_or_prompt(_message("hi there")) == gateways.LINK_PROMPT
    assert called == []


def test_valid_code_redeems_and_confirms(no_db, monkeypatch):
    calls = []
    monkeypatch.setattr(linking, "redeem_link_token", lambda s, **k: calls.append(k))
    assert gateways._link_or_prompt(_message("/link abcd2345")) == gateways.LINK_SUCCESS
    assert calls == [{"gateway": "telegram", "sender_id": "424242", "raw_token": "ABCD2345"}]


def test_a_refused_code_returns_the_refusal_sentence(no_db, monkeypatch):
    def _expired(*a, **k):
        raise linking.ExpiredCode("That code has expired. Ask for a new one.")

    monkeypatch.setattr(linking, "redeem_link_token", _expired)
    assert gateways._link_or_prompt(_message("/link ABCD2345")) == (
        "That code has expired. Ask for a new one."
    )


def test_an_unexpected_error_is_not_echoed(no_db, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("connection refused to 10.0.0.5 password=hunter2")

    monkeypatch.setattr(linking, "redeem_link_token", _boom)
    reply = gateways._link_or_prompt(_message("/link ABCD2345"))
    assert "hunter2" not in reply and "10.0.0.5" not in reply
    assert "try again" in reply
