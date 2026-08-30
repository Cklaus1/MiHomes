"""G7 · §6 Step 7 — the WhatsApp Cloud API (A18, A19, A20).

Three criteria, all local: Protocol conformance, envelope parity, and the responder being
untouched by the swap. **None of them needs a Meta account** — U4 blocks the *live* behaviour
of Steps 7 and 9, and `FakeCloudClient` covers the rest — so O1/U2 gates nothing here.

Each is written against the failure it would otherwise pass through:

* **A18** cannot use `isinstance`: `WhatsAppBridge` is a plain `Protocol`, not
  `@runtime_checkable`, so `isinstance` raises rather than answering. And "structurally, no
  subclassing" is **two** claims — a test that only matched the methods would pass on a
  subclass, which is the thing D10 rules out.
* **A19** asserts the *whole* envelope. G5 shipped one missing `propertySlug` and every webhook
  message was dropped before dispatch while the tests stayed green, so "the same dict" is
  checked key-by-key against the shape Baileys actually produces — derived from the source,
  not retyped here.
* **A20** cannot assert "the file did not change" — that is trivially true and proves nothing
  about D2. It asserts the *seam*: a `GatewayAdapter` closing over a fake Cloud client carries
  a dispatch end-to-end, so the responder demonstrably holds an adapter rather than a transport.
"""

from __future__ import annotations

import inspect

import pytest

from mihomes.services.gateways.whatsapp.cloud_client import (
    CloudAPIClient,
    GroupsNotSupported,
    normalize_cloud_message,
)
from mihomes.services.gateways.whatsapp.protocol import WhatsAppBridge

#: The eleven keys `telegram/client.py:164` produces — the contract both responders consume.
#: Named here so a *reduction* fails too: reading only the fake's own output would let the
#: envelope shrink on both sides at once and still agree with itself.
BAILEYS_ENVELOPE_KEYS = {
    "id",
    "timestamp",
    "jid",
    "isGroup",
    "sender",
    "senderName",
    "senderUsername",
    "text",
    "hasMedia",
    "mediaPath",
    "propertySlug",
}


def _cloud_entry(*, text="Boiler is leaking", wa_id="15551234567", name="Maria"):
    """A realistically-shaped Cloud API inbound webhook entry."""
    return {
        "id": "WABA_ID",
        "changes": [
            {
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {
                        "display_phone_number": "15559876543",
                        "phone_number_id": "PHONE_ID",
                    },
                    "contacts": [{"profile": {"name": name}, "wa_id": wa_id}],
                    "messages": [
                        {
                            "from": wa_id,
                            "id": "wamid.TEST123",
                            "timestamp": "1756500000",
                            "type": "text",
                            "text": {"body": text},
                        }
                    ],
                },
            }
        ],
    }


# ------------------------------------------------------------------------------------- #
# A18 — CloudAPIClient satisfies WhatsAppBridge structurally, without subclassing
# ------------------------------------------------------------------------------------- #
def test_protocol_conformance():
    """**A18** — every Protocol method is present with a matching signature, and nothing is
    inherited.

    The method list is **read off the Protocol**, not retyped: a fifth method added to
    `WhatsAppBridge` must make this fail until `CloudAPIClient` grows it, and a hand-written
    list would agree with itself forever.
    """
    protocol_methods = {
        name
        for name, member in vars(WhatsAppBridge).items()
        if callable(member) and not name.startswith("_")
    }
    assert protocol_methods == {
        "send_message",
        "send_template",
        "get_message_status",
        "register_webhook",
    }, f"the Protocol's shape changed: {sorted(protocol_methods)}"

    for name in sorted(protocol_methods):
        impl = getattr(CloudAPIClient, name, None)
        assert impl is not None, f"CloudAPIClient does not implement {name}"
        assert callable(impl)

        want = inspect.signature(getattr(WhatsAppBridge, name))
        got = inspect.signature(impl)
        assert list(got.parameters) == list(want.parameters), (
            f"{name} takes {list(got.parameters)} but the Protocol declares "
            f"{list(want.parameters)} — a structural implementation must match the shape, or "
            "a caller written against the Protocol breaks on the swap"
        )

    # --- the second half of "structurally, **no subclassing**" (D10) ---------------------
    #
    # Asserted on the MRO, not with `issubclass` — which raises `TypeError` on a
    # non-runtime-checkable Protocol for the same reason `isinstance` does. (Written the wrong
    # way first, and the suite said so immediately, which is the argument for running the test
    # before believing it.) The MRO is the direct statement of the claim anyway: inheritance is
    # a fact about the class, not a question to ask the typing machinery.
    assert WhatsAppBridge not in CloudAPIClient.__mro__, (
        "CloudAPIClient inherits from WhatsAppBridge. D10 asks for structural conformance "
        "following the AIProvider precedent — a test that only matched method names would "
        "pass on a subclass and never notice"
    )
    assert CloudAPIClient.__mro__ == (CloudAPIClient, object), (
        f"CloudAPIClient has grown a base class: {CloudAPIClient.__mro__}"
    )


def test_the_protocol_is_not_runtime_checkable_which_is_why_the_above_is_written_that_way():
    """Guard on the guard: document *why* A18 cannot be one `isinstance` line.

    If someone later marks the Protocol `@runtime_checkable`, this fails and the simpler
    assertion becomes available — a prompt to simplify rather than a permanent restriction.
    """
    with pytest.raises(TypeError):
        isinstance(CloudAPIClient(), WhatsAppBridge)


# ------------------------------------------------------------------------------------- #
# A19 — an inbound Cloud API message normalizes to the same dict Baileys produced
# ------------------------------------------------------------------------------------- #
def test_envelope_parity():
    """**A19** — the normalized dict is the *same shape*, key for key.

    Not "a compatible subset". G5 shipped an envelope missing `propertySlug`, and
    `responder.py:208` filters on exactly that key — so every webhook message was silently
    dropped before dispatch while A14/A15/A16 all passed. A missing key is not a degraded
    envelope; it is a message the responder discards without a word.
    """
    msg = normalize_cloud_message(_cloud_entry(), property_slug="belle-estate")

    assert msg is not None
    assert set(msg) == BAILEYS_ENVELOPE_KEYS, (
        "the Cloud API envelope does not match Baileys' key-for-key. Missing: "
        f"{sorted(BAILEYS_ENVELOPE_KEYS - set(msg))}; extra: "
        f"{sorted(set(msg) - BAILEYS_ENVELOPE_KEYS)}"
    )

    # And the values are populated, not merely present — an all-None dict has the right keys.
    assert msg["id"] == "wamid.TEST123"
    assert msg["sender"] == "15551234567"
    assert msg["senderName"] == "Maria"
    assert msg["text"] == "Boiler is leaking"
    assert msg["propertySlug"] == "belle-estate"
    assert msg["hasMedia"] is False


def test_the_parity_target_is_the_shape_the_telegram_client_really_produces():
    """The other half of A19: `BAILEYS_ENVELOPE_KEYS` must describe live code.

    A constant in a test file agrees with itself forever. This reads `telegram/client.py`'s
    normalizer and asserts the same keys appear there, so a transport that changes its envelope
    fails here rather than drifting away from the other one in silence.
    """
    from mihomes.services.gateways.telegram import client as tg

    source = inspect.getsource(tg.TelegramClient.normalize_update)
    for key in sorted(BAILEYS_ENVELOPE_KEYS):
        assert f'"{key}"' in source, (
            f"{key!r} is asserted as part of the shared envelope but "
            "TelegramClient.normalize_update no longer produces it"
        )


def test_a_media_message_carries_its_caption():
    """A photo with a caption must keep the caption — it is the human's actual words.

    Without it an inventory photo arrives with empty `text` and the analyzer has nothing to
    categorize, which looks like the AI failing rather than the envelope dropping content.
    """
    entry = _cloud_entry()
    entry["changes"][0]["value"]["messages"][0] = {
        "from": "15551234567",
        "id": "wamid.IMG",
        "timestamp": "1756500001",
        "type": "image",
        "image": {"id": "MEDIA_ID", "caption": "Master bedroom lamp"},
    }

    msg = normalize_cloud_message(entry)
    assert msg is not None
    assert msg["hasMedia"] is True
    assert msg["text"] == "Master bedroom lamp"
    assert msg["mediaPath"] is None  # fetched lazily, as on the Telegram path
    assert set(msg) == BAILEYS_ENVELOPE_KEYS


def test_a_status_callback_normalizes_to_none():
    """Meta posts delivery receipts to the same URL. Not an error — there is just no message.

    Raising here would turn routine traffic into retries, which providers perform aggressively.
    """
    entry = {
        "id": "WABA_ID",
        "changes": [
            {
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"phone_number_id": "PHONE_ID"},
                    "statuses": [{"id": "wamid.X", "status": "delivered"}],
                },
            }
        ],
    }
    assert normalize_cloud_message(entry) is None
    assert normalize_cloud_message({}) is None


# ------------------------------------------------------------------------------------- #
# A20 — the responder is unchanged by the transport swap
# ------------------------------------------------------------------------------------- #
class FakeCloudClient:
    """Records sends. Structurally a client, satisfying nothing but the closure's needs."""

    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def send_message(self, phone_number: str, message: str) -> dict:
        self.sent.append((phone_number, message))
        return {"message_id": f"fake-{len(self.sent)}", "status": "sent"}

    def send_group_message(self, group_jid: str, text: str) -> dict:
        self.sent.append((group_jid, text))
        return {"message_id": f"fake-{len(self.sent)}", "status": "sent"}


def test_responder_untouched(session, account_a):
    """**A20** — swapping the transport changes a closure, not a responder.

    "Unchanged" is trivially true of a file nobody edited, so that is not what this asserts. It
    asserts the *mechanism* D2 relies on: `dispatch_items` takes a `GatewayAdapter`, the adapter
    closes over whichever client is configured, and a dispatch driven through an adapter backed
    by a **Cloud** client reaches that client — with no responder code aware of which transport
    won.
    """
    from mihomes.services.gateways import review_common as rc
    from mihomes.services.property import create_property

    prop = create_property(session, "Cloud Estate")
    session.flush()

    fake = FakeCloudClient()
    # Exactly the shape `whatsapp/responder.py` builds today, with the Cloud client swapped in
    # for Baileys' `WhatsAppClient`. That substitution is the whole of Step 7 at this seam.
    adapter = rc.GatewayAdapter(
        label="WhatsApp",
        send=lambda jid, text: fake.send_group_message(jid, f"🏠 {text}"),
    )

    result = rc.dispatch_items(
        session,
        [
            {
                "category": "issue",
                "title": "Cloud API issue",
                "description": "arrived over the Cloud API",
                "severity": "medium",
                "property_slug": prop.slug,
            }
        ],
        account=account_a,
        adapter=adapter,
        reply_target="15559876543",
        messages=[],
        property_slug=prop.slug,
        resolve_reporter=lambda item: None,
        sender_trusted=True,
    )

    assert result["logged"] == 1, "the dispatch did not run, so the seam was never exercised"
    assert fake.sent, "the reply never reached the Cloud client — the adapter seam is broken"
    target, text = fake.sent[0]
    assert target == "15559876543"
    assert text.startswith("🏠 "), (
        "the WhatsApp presentation quirk lives in the adapter, not the transport — a swap must "
        "not lose it"
    )


def test_the_responder_never_imports_a_transport():
    """The static half of A20: no responder names a concrete client.

    `dispatch_items` and the shared core must hold `GatewayAdapter` and nothing else. An import
    of `CloudAPIClient` or `WhatsAppClient` in `review_common` would make the core transport-
    aware, which is the coupling D2 exists to prevent — and the behavioural test above would
    still pass with that coupling present.
    """
    from mihomes.services.gateways import review_common as rc

    source = inspect.getsource(rc)
    for forbidden in ("CloudAPIClient", "WhatsAppClient", "TelegramClient"):
        assert forbidden not in source, (
            f"review_common references {forbidden} — the shared core must hold a "
            "GatewayAdapter and never a transport (D2)"
        )


# ------------------------------------------------------------------------------------- #
# U2 / O1 — the group question, surfaced rather than absorbed
# ------------------------------------------------------------------------------------- #
def test_a_tier_without_groups_raises_rather_than_degrading():
    """O1 is open, and a silent fallback would be the wrong way to close it (U2).

    The live product routes an inventory **group** through `whatsapp.inventory_group_jid`. If
    the chosen tier has no group support, degrading to per-recipient sends would look like the
    feature working while an estate's inventory reports quietly went to one person. §7: a
    behaviour change the migration must *state*, not absorb.

    Default is `supports_groups=False` — the fail-closed direction while the tier is unknown.
    """
    client = CloudAPIClient(access_token="t", phone_number_id="p")
    assert client.supports_groups is False

    with pytest.raises(GroupsNotSupported) as excinfo:
        client.send_group_message("120363@g.us", "Inventory scan complete")

    assert "inventory_group_jid" in str(excinfo.value), (
        "the error must name the routing key that needs replacing, or O1 arrives as a generic "
        "failure nobody can act on"
    )


def test_register_webhook_reports_that_it_registered_nothing():
    """Cloud API webhooks are configured in the Meta dashboard — there is no endpoint to call.

    Returning `True` would claim a registration nobody performed. The Protocol's shape predates
    that knowledge; `False` is the honest answer within it.
    """
    assert CloudAPIClient(access_token="t", phone_number_id="p").register_webhook(
        "https://example.com/webhooks/whatsapp"
    ) is False
