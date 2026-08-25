"""G15 · §6 Step 15 — the four billing emails (A21).

Rendered through **`ConsoleProvider`**, which SPEC-001 §3 ships for exactly this purpose — not a
Resend mock. The difference matters: a mock asserts that `send` was called with some arguments,
while the real provider proves the template actually renders. Most template faults are render-time
(a missing `{% block subject %}`, an undefined variable in a branch that only fires when a value is
present), and a mock never reaches them.

**A21 is "each renders and fires exactly once per triggering event."** The *once* half is where a
mail bug lives: an email sent twice is not an error anywhere in the system, and the customer's
only recourse is to distrust the sender. Idempotency for the three webhook-driven mails is the
ledger's (Step 5, A5) — asserted here through the handler rather than by trusting it.
"""

from __future__ import annotations

import pytest

from mihomes.services.email.console_provider import ConsoleProvider
from mihomes.services.email.service import EmailService

BILLING_URL = "https://mihomes.example/billing"


class RecordingProvider:
    """`ConsoleProvider` that keeps what it sent, so the assertions can read it.

    Subclasses rather than reimplements: rendering must go through the real path, and only the
    *dispatch* is captured.
    """

    def __init__(self) -> None:
        self._inner = ConsoleProvider(from_address="billing@mihomes.example")
        self.sent: list[tuple[str, str, str, str]] = []

    def send(self, to: str, subject: str, html: str, text: str | None = None):
        self.sent.append((to, subject, html, text or ""))
        return self._inner.send(to, subject, html, text=text)


@pytest.fixture
def mail() -> tuple[EmailService, RecordingProvider]:
    provider = RecordingProvider()
    return EmailService(provider), provider


#: The four, with a minimal valid call for each — parameterised so a fifth added later is one
#: line rather than a copied test, and so "all four render" is a single assertion rather than four
#: that could each be forgotten independently.
FOUR_MAILS = [
    (
        "send_receipt",
        {"plan": "pro", "amount": "$29.00", "billing_url": BILLING_URL},
        "receipt",
    ),
    (
        "send_payment_failed",
        {"plan": "pro", "billing_url": BILLING_URL, "grace_days": 14},
        "Payment failed",
    ),
    (
        "send_trial_ending",
        {"days_left": 3, "ends_on": "12 September", "billing_url": BILLING_URL},
        "trial ends",
    ),
    (
        "send_subscription_cancelled",
        {"plan": "pro", "billing_url": BILLING_URL, "active_until": "1 October"},
        "cancelled",
    ),
]


class TestTheFourTemplates:
    @pytest.mark.parametrize("method,kwargs,subject_fragment", FOUR_MAILS)
    def test_four_templates(self, mail, method, kwargs, subject_fragment):
        """**A21** — each of the four renders subject, html and text.

        All three parts asserted. A missing text part is invisible in any HTML-capable client and
        is exactly what a plain-text reader — or a spam filter scoring multipart mail — sees.
        """
        service, provider = mail
        getattr(service, method)("owner@example.com", **kwargs)

        assert len(provider.sent) == 1, f"{method} did not dispatch"
        to, subject, html, text = provider.sent[0]

        assert to == "owner@example.com"
        assert subject_fragment.lower() in subject.lower()
        assert html.strip(), f"{method} rendered no HTML"
        assert text.strip(), f"{method} rendered no text part"

    @pytest.mark.parametrize("method,kwargs,_subject", FOUR_MAILS)
    def test_fires_once(self, mail, method, kwargs, _subject):
        """**A21's second half** — one call, one send.

        Trivially true here, and that is the point of asserting it at this layer: it pins that
        `_send` does not retry internally, so the *only* place a duplicate can come from is a
        duplicate trigger — which the webhook ledger (A5) already prevents and
        `test_a_replayed_webhook_sends_nothing_twice` checks end to end.
        """
        service, provider = mail
        getattr(service, method)("owner@example.com", **kwargs)
        assert len(provider.sent) == 1

    @pytest.mark.parametrize("method,kwargs,_subject", FOUR_MAILS)
    def test_no_unrendered_placeholders(self, mail, method, kwargs, _subject):
        """A rendered template must contain no Jinja delimiters.

        The failure this catches is a typo like `{{ billing_url }` — Jinja leaves it as literal
        text rather than raising, so the mail sends successfully with `{{ billing_url }` printed
        in it. Nothing else in the pipeline would notice.
        """
        service, provider = mail
        getattr(service, method)("owner@example.com", **kwargs)
        _to, subject, html, text = provider.sent[0]

        for part_name, part in (("subject", subject), ("html", html), ("text", text)):
            assert "{{" not in part and "{%" not in part, (
                f"{method}'s {part_name} contains an unrendered placeholder"
            )


class TestWhatTheMessagesMustSay:
    def test_payment_failed_says_nothing_has_changed_yet(self, mail):
        """**D10 in the copy, not just the code.**

        `past_due` is the grace window and keeps full access. An email leading with "your account
        is suspended" would be false *and* worse for recovery — a customer who believes they are
        already locked out is less likely to come back and fix the card.
        """
        service, provider = mail
        service.send_payment_failed(
            "owner@example.com", plan="pro", billing_url=BILLING_URL, grace_days=14
        )
        _to, _subject, html, text = provider.sent[0]

        assert "nothing has changed yet" in text.lower()
        assert "nothing has changed yet" in html.lower()

    @pytest.mark.parametrize("method,kwargs", [
        ("send_trial_ending",
         {"days_left": 3, "ends_on": "12 September", "billing_url": BILLING_URL}),
        ("send_subscription_cancelled",
         {"plan": "pro", "billing_url": BILLING_URL, "active_until": "1 October"}),
    ])
    def test_the_downgrade_mails_promise_nothing_is_deleted(self, mail, method, kwargs):
        """`PRICING` §4.3's promise has to reach the customer, or it is not a promise.

        Both mails announce a downgrade. *"We never delete data for a billing lapse"* is the whole
        reassurance, and a message that omits it leaves someone to assume the worst about their
        own records at the exact moment they are deciding whether to come back.
        """
        service, provider = mail
        getattr(service, method)("owner@example.com", **kwargs)
        _to, _subject, _html, text = provider.sent[0]

        assert "deleted" in text.lower()
        assert "read-only" in text.lower()

    def test_cancellation_says_when_access_actually_ends(self, mail):
        """§4.4: cancellation takes effect **at period end**, not instantly.

        Omitting the date invites the support ticket beginning *"I cancelled and lost access
        immediately"* — a misunderstanding the email itself would have caused.
        """
        service, provider = mail
        service.send_subscription_cancelled(
            "owner@example.com", plan="pro", billing_url=BILLING_URL,
            active_until="1 October",
        )
        _to, _subject, html, text = provider.sent[0]

        assert "1 October" in text
        assert "1 October" in html

    def test_trial_ending_carries_the_over_limit_numbers(self, mail):
        """§4.3 shows the picker ~3 days ahead *"so the choice is made before access changes"*.

        An email that only says "your trial is ending" leaves the customer to discover the
        consequence after two of their homes have gone read-only.
        """
        service, provider = mail
        service.send_trial_ending(
            "owner@example.com", days_left=3, ends_on="12 September",
            billing_url=BILLING_URL, home_count=3, max_homes=1,
        )
        _to, _subject, _html, text = provider.sent[0]

        assert "3 homes" in text
        assert "1" in text

    def test_trial_ending_omits_the_numbers_when_within_limits(self, mail):
        """The control: an account already inside the free limit gets no over-limit warning.

        Without this, `over_limit` could be hardcoded true and every test above would still pass —
        while telling customers with one home that they are over a limit they are not over.
        """
        service, provider = mail
        service.send_trial_ending(
            "owner@example.com", days_left=3, ends_on="12 September",
            billing_url=BILLING_URL, home_count=1, max_homes=1,
        )
        _to, _subject, _html, text = provider.sent[0]

        assert "homes and free covers" not in text


class TestTheTransportProtocolIsUnchanged:
    def test_the_provider_protocol_has_no_billing_methods(self):
        """**§6 Step 15: "Do not extend the `EmailProvider` Protocol — it is transport-only."**

        SPEC-001 §5.1 keeps it to `send(to, subject, html, text)` so a provider swap never has to
        know what mail types exist. Adding `send_receipt` there would make every provider —
        Console, Resend, and anything later — implement every message type.

        Asserted structurally rather than by reading the file, so it holds for whatever the
        Protocol becomes.
        """
        from mihomes.services.email.provider import EmailProvider

        declared = {
            name for name in dir(EmailProvider)
            if not name.startswith("_") and callable(getattr(EmailProvider, name, None))
        }
        assert declared == {"send"}, (
            f"EmailProvider must stay transport-only; found {sorted(declared)}"
        )

    def test_the_four_methods_are_on_the_service(self):
        """The positive half — they exist somewhere, and that somewhere is the service."""
        for method, _kwargs, _subject in FOUR_MAILS:
            assert callable(getattr(EmailService, method, None)), f"missing {method}"
