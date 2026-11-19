"""`review_common.handle_inbound` — the one inbound loop every channel's responder calls.

The Telegram and WhatsApp responders each carried a copy of this loop and differed only in values
that now live on the `ChannelAdapter`. These tests drive the loop through the adapter's hooks, so a
new channel (SMS, email) that supplies only `label`, `channel` and `send` is covered too.
"""

from __future__ import annotations

import pytest

from mihomes.services.ai.provider import AIProviderError
from mihomes.services.gateways import review_common as rc
from mihomes.services.property import create_property


@pytest.fixture
def prop(session):
    return create_property(session, "Belle Estate")


def _msg(prop_slug: str | None, jid: str = "-100", text: str = "The gate is stuck") -> dict:
    return {
        "id": f"{jid}:1", "timestamp": "2025-08-29T20:40:00+00:00", "channel": "telegram",
        "jid": jid, "isGroup": True, "sender": "7001", "senderName": "Ana",
        "senderUsername": "", "text": text, "hasMedia": False, "mediaPath": None,
        "propertySlug": prop_slug,
    }


def _adapter(sent: list, **hooks) -> rc.GatewayAdapter:
    return rc.GatewayAdapter(
        label="Test", channel="telegram",
        send=lambda target, text: sent.append((target, text)), **hooks,
    )


def _issue(title: str):
    return lambda *a, **k: {"items": [{"category": "issue", "title": title, "severity": "medium"}]}


def test_a_minimal_adapter_logs_and_confirms(session, prop):
    sent: list = []
    # Callers always pass the property (the pollers group by it; the webhook resolves it).
    out = rc.handle_inbound(
        session, [_msg(prop.slug)], adapter=_adapter(sent, analyze=_issue("Gate stuck")),
        property_slug=prop.slug,
    )
    assert out["logged"] == 1
    assert sent and sent[0][0] == "-100" and "Gate stuck" in sent[0][1]


@pytest.mark.parametrize(
    ("exc", "kind"),
    [(AIProviderError("provider down"), "AI provider error"), (ValueError("bad"), "Unexpected error")],
)
def test_an_analysis_failure_sends_the_adapters_error_text(session, prop, exc, kind):
    sent: list = []

    def _fail(*a, **k):
        raise exc

    out = rc.handle_inbound(
        session, [_msg(prop.slug)],
        adapter=_adapter(sent, analyze=_fail, error_text="⚠️ custom notice"),
    )
    assert sent == [("-100", "⚠️ custom notice")]
    assert out["logged"] == 0
    assert out["errors"] and out["errors"][0].startswith(kind)


def test_the_inventory_conversation_goes_to_the_scanner_not_the_analyzer(session, prop):
    sent: list = []
    scanned: list = []

    def _never(*a, **k):
        raise AssertionError("the inventory conversation must not be analyzed")

    def _scan(sess, msgs, property_slug, target):
        scanned.append((property_slug, target, len(msgs)))
        return {"replied": 1, "logged": 3, "errors": []}

    out = rc.handle_inbound(
        session, [_msg(prop.slug, jid="-555")],
        adapter=_adapter(sent, analyze=_never, inventory_target="-555", scan_inventory=_scan),
    )
    assert scanned == [(prop.slug, "-555", 1)]
    assert out == {"replied": 1, "logged": 3, "errors": []}


def test_the_reporter_hook_is_built_per_conversation_and_used(session, prop):
    sent: list = []
    built: list = []
    asked: list = []

    def _reporter_for(sess, convo):
        built.append([m["jid"] for m in convo])

        def _resolve(item):
            asked.append(item["title"])
            return None
        return _resolve

    rc.handle_inbound(
        session, [_msg(prop.slug, jid="-1"), _msg(prop.slug, jid="-2")],
        adapter=_adapter(sent, analyze=_issue("Leak"), reporter_for=_reporter_for),
        property_slug=prop.slug,
    )
    assert sorted(built) == [["-1"], ["-2"]]
    assert asked == ["Leak", "Leak"]


def test_messages_with_no_property_are_dropped(session):
    sent: list = []
    out = rc.handle_inbound(session, [_msg(None)], adapter=_adapter(sent, analyze=_issue("x")))
    assert out == {"replied": 0, "logged": 0, "errors": ["No linked chat found"]}
    assert sent == []


def test_the_argument_property_covers_messages_without_their_own(session, prop):
    sent: list = []
    out = rc.handle_inbound(
        session, [_msg(None)], adapter=_adapter(sent, analyze=_issue("Pool pump")),
        property_slug=prop.slug,
    )
    assert out["logged"] == 1


def test_whatsapp_error_notice_is_unchanged_by_the_refactor(session, prop, monkeypatch):
    """The WhatsApp responder used to send "🏠 ⚠️ Bot error — …" straight through its client."""
    from mihomes.services.gateways.whatsapp import responder as wa

    sent: list = []

    class _Client:
        def send_group_message(self, jid, text):
            sent.append((jid, text))

    def _fail(*a, **k):
        raise AIProviderError("down")

    monkeypatch.setattr(wa, "WhatsAppClient", _Client)
    monkeypatch.setattr(wa, "analyze_messages", _fail)
    wa.process_and_respond(session, [_msg(prop.slug, jid="123@g.us")])
    assert sent == [("123@g.us", "🏠 ⚠️ Bot error — couldn't process message(s). Please log manually.")]
