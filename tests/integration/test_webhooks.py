"""G4 · §6 Step 4 — the webhook route (A4).

**A4 asserts a negative that most webhook tests get wrong**: *"a tampered body is rejected **with
no DB write**."* Checking the status code alone would pass on an implementation that recorded the
event first and rejected afterwards — which is the defect, not the feature. So the ledger count is
measured on both sides of the request.

The signatures here are computed with a real HMAC against a known test secret, not mocked. That
matters because N3's rule is about *bytes*: a test that stubs verification proves the handler
calls a function, never that the function is fed the unparsed body. Signing the exact bytes and
then mutating one of them is the only way to show the difference.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from mihomes.models.processed_webhook_event import ProcessedWebhookEvent

WEBHOOK_SECRET = "whsec_test_notreal_0123456789abcdef"


def _sign(payload: bytes, secret: str = WEBHOOK_SECRET, timestamp: int | None = None) -> str:
    """Build a `Stripe-Signature` header the real SDK will accept.

    Stripe signs `"{timestamp}.{payload}"` with HMAC-SHA256 and sends
    `t=<timestamp>,v1=<hex>`. Reproduced rather than imported because the point of these tests is
    that our route hands the SDK the untouched bytes — deriving the header from the same bytes
    the request body carries is what makes a tampering test meaningful.
    """
    ts = timestamp if timestamp is not None else int(time.time())
    signed_payload = f"{ts}.".encode() + payload
    digest = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


def _event_bytes(event_id: str = "evt_test_1", event_type: str = "invoice.paid") -> bytes:
    """A minimal Stripe-shaped event, serialized once.

    Returned as **bytes**, and every helper below passes these exact bytes as the request body —
    re-serializing a dict between signing and sending is precisely the bug N3 describes.
    """
    return json.dumps({
        "id": event_id,
        "type": event_type,
        "created": int(time.time()),
        "data": {"object": {"customer": "cus_test_1", "id": "in_test_1"}},
    }).encode()


@pytest.fixture
def webhook_client(monkeypatch, app_engine):
    """A client with Stripe env configured, mounted on the real app.

    `app_engine` rather than a bare `create_app()` so the route reaches the same database the
    ledger assertions read — otherwise "no DB write" would be trivially true against a database
    nothing could have written to.
    """
    monkeypatch.setenv("STRIPE_SECRET_KEY", "rk_test_notreal")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)

    from mihomes.web.app import create_app

    with TestClient(create_app(), base_url="http://localhost") as client:
        yield client


def _ledger_count(session) -> int:
    return session.execute(
        select(func.count()).select_from(ProcessedWebhookEvent)
    ).scalar_one()


class TestSignatureVerification:
    def test_bad_signature_no_write(self, webhook_client, session):
        """**A4** — a tampered body is rejected **and writes nothing**.

        The body is signed, then a byte is changed. Both halves are asserted: the 400, and that
        the ledger did not grow. An implementation that recorded first and verified second would
        pass the first assertion and fail the second — and it is the second that describes the
        actual risk, since a recorded event is one that will never be reprocessed when the
        legitimate delivery arrives.
        """
        payload = _event_bytes()
        signature = _sign(payload)
        tampered = payload.replace(b"cus_test_1", b"cus_attacker")

        before = _ledger_count(session)
        response = webhook_client.post(
            "/webhooks/stripe", content=tampered, headers={"stripe-signature": signature},
        )
        session.expire_all()

        assert response.status_code == 400
        assert _ledger_count(session) == before, (
            "a rejected webhook must leave no trace — recording it first would consume the "
            "event id, and the legitimate delivery would then be deduplicated away"
        )

    def test_missing_signature_header_is_rejected(self, webhook_client):
        """No header is not a verification *failure* — there is nothing to verify.

        Handled explicitly so the adapter is never asked to verify an empty string, which is a
        code path whose behaviour would then depend on the SDK's error handling rather than on
        ours.
        """
        response = webhook_client.post("/webhooks/stripe", content=_event_bytes())
        assert response.status_code == 400

    def test_wrong_secret_is_rejected(self, webhook_client, session):
        """A correctly-formed signature from the wrong key. The nearest thing to a real attack
        this suite can stage, and the case a stubbed verifier would wave through."""
        payload = _event_bytes()
        before = _ledger_count(session)

        response = webhook_client.post(
            "/webhooks/stripe",
            content=payload,
            headers={"stripe-signature": _sign(payload, secret="whsec_wrong_key")},
        )
        session.expire_all()

        assert response.status_code == 400
        assert _ledger_count(session) == before

    def test_valid_signature_reaches_the_service(self, webhook_client):
        """The positive half. Without it every assertion above passes on a route that rejects
        everything — the classic vacuous security test."""
        payload = _event_bytes()
        response = webhook_client.post(
            "/webhooks/stripe", content=payload, headers={"stripe-signature": _sign(payload)},
        )
        assert response.status_code == 200

    def test_ignored_event_type_is_acked(self, webhook_client):
        """An event type absent from `_EVENT_TYPE_MAP` must return 2xx, not an error.

        Stripe retries any non-2xx with backoff for days. Erroring on the many event types a
        subscription product does not care about would turn ordinary traffic into an indefinite
        retry storm.
        """
        payload = _event_bytes(event_id="evt_ignored", event_type="charge.succeeded")
        response = webhook_client.post(
            "/webhooks/stripe", content=payload, headers={"stripe-signature": _sign(payload)},
        )
        assert response.status_code == 200


class TestTheRouteHasNoSession:
    def test_no_cookie_required(self, webhook_client):
        """The route works with no session cookie at all — the whole premise of Step 4.

        Every other route in the app resolves a principal first. This one cannot: Stripe has no
        account here, and `require_account()` would raise before the handler ran.
        """
        assert not webhook_client.cookies
        payload = _event_bytes(event_id="evt_no_cookie")
        response = webhook_client.post(
            "/webhooks/stripe", content=payload, headers={"stripe-signature": _sign(payload)},
        )
        assert response.status_code == 200

    def test_host_guard_does_not_block_a_public_hostname(self, webhook_client):
        """**The finding that would have broken this in production, not in tests.**

        `HostAndOriginGuardMiddleware` 400s any request whose `Host` is not loopback (H30,
        DNS-rebinding). Stripe POSTs to whatever public hostname the endpoint is registered
        under, so *every live webhook would have been rejected before reaching the route* — and
        no test would have caught it, because the test client's base URL is `localhost`.

        Asserted with an explicit non-loopback Host, which is the only way to reproduce what
        Stripe actually sends.
        """
        payload = _event_bytes(event_id="evt_public_host")
        response = webhook_client.post(
            "/webhooks/stripe",
            content=payload,
            headers={"stripe-signature": _sign(payload), "host": "mihomes.example.com"},
        )
        assert response.status_code == 200, (
            "the webhook path must be exempt from the Host guard — Stripe posts to the public "
            "hostname the endpoint is registered under, never to localhost"
        )

    def test_cross_site_post_is_allowed_here_only(self, webhook_client):
        """The Origin half of the same exemption.

        Safe because CSRF is *the browser attaching the user's cookies to a forged request*, and
        this route reads no cookie and trusts no caller identity. Its authentication is the
        signature, which an attacker cannot forge — strictly stronger than an Origin header,
        which is advisory and unauthenticated.
        """
        payload = _event_bytes(event_id="evt_cross_site")
        response = webhook_client.post(
            "/webhooks/stripe",
            content=payload,
            headers={
                "stripe-signature": _sign(payload),
                "sec-fetch-site": "cross-site",
                "origin": "https://stripe.com",
            },
        )
        assert response.status_code == 200

    def test_other_routes_still_reject_a_bad_host(self, webhook_client):
        """**The guard on the exemption.** H30 must still bite everywhere else.

        Without this, a prefix typo or an over-broad match would disable DNS-rebinding
        protection app-wide and every other test would still pass — the exemption's blast radius
        is the thing worth pinning, not the exemption itself.
        """
        response = webhook_client.get("/", headers={"host": "evil.example.com"})
        assert response.status_code == 400
