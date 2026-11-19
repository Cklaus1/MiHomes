"""G9 · §6 Step 9 — RFC 8058 one-click unsubscribe (A18, B4, N9, N10).

`SAAS_PRD:167` gives "email opt-out" three words in an NFR table and no design anywhere. This is
the requirement written properly: two headers on lifecycle mail and neither on transactional, a
POST that completes in one request, and a token that cannot be forged.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from mihomes.models.email_suppression import EmailSuppression
from mihomes.services.email import EmailService
from mihomes.services.email.provider import EmailResult
from mihomes.services.email.suppression import unsubscribe_headers, unsubscribe_token
from mihomes.tenancy.context import account_context

WELCOME = {"account_name": "Belle", "dashboard_url": "https://x/", "name": None}

pytestmark = pytest.mark.usefixtures("signing_key")


@pytest.fixture
def signing_key(monkeypatch):
    """A real Fernet-shaped key. Tokens are HMACs over it, so it must be present."""
    monkeypatch.setenv("MIHOMES_SECRET_KEY", "k" * 43 + "=")


class RecordingProvider:
    """Records what reached the provider, headers included."""

    provider_name = "recording"

    def __init__(self):
        self.calls = []

    def send(self, to, subject, html, *, text=None, reply_to=None, headers=None):
        self.calls.append({"to": to, "headers": headers})
        return EmailResult(provider_message_id="rec-1", provider=self.provider_name)


def _send_and_drain(session, account_id, klass: str) -> RecordingProvider:
    provider = RecordingProvider()
    service = EmailService(provider, session=session)
    with account_context(account_id):
        service._send("alex@example.com", "welcome", WELCOME, klass=klass)
        service.drain()
    return provider


# --- A18: the headers, and their absence ---------------------------------------------------


def test_headers_by_class(session, account_a):
    """**A18** — lifecycle mail carries both RFC 8058 headers; transactional carries neither.

    The asymmetry is the legally meaningful part (D13). A receipt carrying `List-Unsubscribe`
    invites a customer to opt out of a record they are owed, and mailbox providers count the
    unsubscribe against the sender either way — wrong and costly at once.
    """
    lifecycle = _send_and_drain(session, account_a, "lifecycle")
    headers = lifecycle.calls[0]["headers"]

    assert headers, "a lifecycle send must carry unsubscribe headers"
    assert set(headers) == {"List-Unsubscribe", "List-Unsubscribe-Post"}
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert headers["List-Unsubscribe"].startswith("<")
    assert headers["List-Unsubscribe"].endswith(">")

    transactional = _send_and_drain(session, account_a, "transactional")
    assert not transactional.calls[0]["headers"], (
        "a receipt must not offer to unsubscribe from receipts"
    )


def test_both_headers_or_neither():
    """`List-Unsubscribe` alone is the older RFC 2369 form and renders as a link, not a button.

    Sending it without `List-Unsubscribe-Post` silently downgrades every unsubscribe to a
    multi-step flow — the deliverability cost N10 describes, reached by a different route. Set
    equality rather than containment, so dropping either one fails.
    """
    headers = unsubscribe_headers("alex@example.com")

    assert set(headers) == {"List-Unsubscribe", "List-Unsubscribe-Post"}


def test_the_header_carries_a_token_never_the_bare_address(session, account_a):
    """**N9** — `?email=` alone lets anyone unsubscribe anyone, and addresses are enumerable."""
    provider = _send_and_drain(session, account_a, "lifecycle")
    link = provider.calls[0]["headers"]["List-Unsubscribe"]

    assert "token=" in link
    assert unsubscribe_token("alex@example.com") in link


# --- N10: one click means one click ---------------------------------------------------------


def test_the_post_completes_the_unsubscribe(web_client_as, session, account_a):
    """**N10** — the POST completes it. No confirmation page, no redirect.

    A confirmation step makes mailbox providers treat the header as broken, which costs
    deliverability: the mechanism meant to honour opt-outs makes the sender look worse at
    honouring them.
    """
    client = web_client_as(role="owner")
    address = f"click-{uuid.uuid4().hex[:8]}@example.com"

    response = client.post(
        "/unsubscribe",
        data={"email": address, "token": unsubscribe_token(address)},
        follow_redirects=False,
    )

    assert response.status_code == 200, response.text
    assert response.status_code not in (301, 302, 303, 307, 308), "one click, no redirect"


def test_a_forged_token_suppresses_nothing(web_client_as):
    """The token is the whole authentication (N9).

    Returns 200 regardless, deliberately: a 403 would tell a prober which addresses are on
    file, and a legitimate mail client cannot fix a bad token either way. What must differ is
    the *effect*, which is what this asserts.
    """
    client = web_client_as(role="owner")
    address = f"forged-{uuid.uuid4().hex[:8]}@example.com"

    response = client.post(
        "/unsubscribe", data={"email": address, "token": "deadbeef"}
    )

    assert response.status_code == 200
    check = web_client_as.session_for_scope()
    assert check.execute(
        select(EmailSuppression).where(EmailSuppression.address == address)
    ).scalar_one_or_none() is None, "a forged token must suppress nothing"


def test_a_valid_token_suppresses(web_client_as):
    """The positive half — without it, a route that suppressed nothing would pass the test
    above perfectly."""
    client = web_client_as(role="owner")
    address = f"valid-{uuid.uuid4().hex[:8]}@example.com"

    client.post(
        "/unsubscribe", data={"email": address, "token": unsubscribe_token(address)}
    )

    check = web_client_as.session_for_scope()
    row = check.execute(
        select(EmailSuppression).where(EmailSuppression.address == address)
    ).scalar_one_or_none()
    assert row is not None
    assert row.reason == "unsubscribe"


def test_clicking_twice_is_not_an_error(web_client_as):
    """A re-opened email is an ordinary case, not a 500."""
    client = web_client_as(role="owner")
    address = f"twice-{uuid.uuid4().hex[:8]}@example.com"
    token = unsubscribe_token(address)

    first = client.post("/unsubscribe", data={"email": address, "token": token})
    second = client.post("/unsubscribe", data={"email": address, "token": token})

    assert first.status_code == 200
    assert second.status_code == 200


def test_the_get_renders_a_form_and_suppresses_nothing(web_client_as):
    """A GET must never unsubscribe.

    Mail clients and security scanners prefetch links, and a prefetched GET that suppressed
    the address would opt out people who never clicked anything. RFC 8058's one-click path is
    the POST; this is the human-visible footer link.
    """
    client = web_client_as(role="owner")
    address = f"prefetch-{uuid.uuid4().hex[:8]}@example.com"

    response = client.get(
        "/unsubscribe", params={"email": address, "token": unsubscribe_token(address)}
    )

    assert response.status_code == 200
    assert "<form" in response.text and 'method=\'post\'' in response.text
    check = web_client_as.session_for_scope()
    assert check.execute(
        select(EmailSuppression).where(EmailSuppression.address == address)
    ).scalar_one_or_none() is None, "a prefetched GET must not unsubscribe anyone"


def test_the_form_escapes_what_it_reflects(web_client_as):
    """Both parameters arrive in a query string on an unauthenticated route and land in HTML
    attributes — the textbook reflection shape."""
    client = web_client_as(role="owner")

    response = client.get(
        "/unsubscribe",
        params={"email": "x' onfocus='alert(1)", "token": "<script>alert(2)</script>"},
    )

    assert "<script>alert(2)</script>" not in response.text
    assert "onfocus='alert(1)" not in response.text


# --- the request shape a mail client actually sends -----------------------------------------


def test_a_mail_clients_post_is_not_blocked_by_the_host_guard(web_client_as):
    """**C9's open question, answered by measurement.**

    A mail client POSTs from its own infrastructure to the public hostname with no Origin — so
    the Host guard would 400 it, exactly as it would every Stripe webhook. Measured before the
    exemption was added: `/unsubscribe` with `Host: mihomes.ai` returned `400 Invalid Host`
    while `/webhooks/...` passed, which settles whether `WEBHOOK_PATH_PREFIX` already covered
    this path. It did not.
    """
    client = web_client_as(role="owner")
    address = f"host-{uuid.uuid4().hex[:8]}@example.com"

    response = client.post(
        "/unsubscribe",
        data={"email": address, "token": unsubscribe_token(address)},
        headers={"host": "mihomes.ai"},
    )

    assert response.status_code == 200, (
        f"a mail client's POST must not be rejected by the Host guard: {response.text}"
    )
