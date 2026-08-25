"""EmailService — renders a template key and dispatches it via a provider.

**Delivery semantics (BILLING §2.4): a call from a request handler must not block
or fail the caller.** `_send` catches `EmailSendError`, logs the template key and
recipient, and returns. A failed confirmation email must never roll back the
signup row — the user can request a resend, but a lost signup is unrecoverable.

`EmailAuthError` is deliberately NOT swallowed. A missing or rejected API key is a
deployment fault, not a transient delivery failure, and hiding it would produce a
launch where every signup succeeds and no mail ever arrives.
"""

from __future__ import annotations

import logging

from mihomes.services.email.provider import EmailProvider, EmailSendError
from mihomes.services.email.render import render_template

logger = logging.getLogger(__name__)


class EmailService:
    def __init__(self, provider: EmailProvider) -> None:
        self.provider = provider

    def _send(self, to: str, template: str, data: dict) -> None:
        """Render and dispatch. Catches EmailSendError, logs, never raises to the caller."""
        try:
            subject, html, text = render_template(template, data)
        except Exception:
            # A template fault is our bug, not the recipient's problem, and it is
            # not recoverable by retrying. Log with the key and move on rather
            # than taking down the request that triggered it.
            logger.exception("email template render failed: template=%s to=%s", template, to)
            return

        try:
            result = self.provider.send(to, subject, html, text=text)
        except EmailSendError:
            logger.exception("email send failed: template=%s to=%s", template, to)
            return

        logger.info(
            "email sent: template=%s to=%s provider=%s id=%s",
            template, to, result.provider, result.provider_message_id,
        )

    def send_waitlist_confirmation(
        self, to: str, *, confirm_url: str, position: int | None = None
    ) -> None:
        """Double opt-in confirmation (D7).

        `position` is computed but not displayed by default — O4 in §1.3 leaves
        showing it to the founder, and the template tolerates its absence.
        """
        self._send(
            to,
            "waitlist_confirmation",
            {"confirm_url": confirm_url, "position": position},
        )

    # ── SPEC-003 §6 Step 12 — the three Phase 2 mail types ────────────────────

    def send_welcome(self, to: str, *, account_name: str, dashboard_url: str,
                     name: str | None = None) -> None:
        """Sent once, when onboarding creates the account (Step 11)."""
        self._send(
            to,
            "welcome",
            {"account_name": account_name, "dashboard_url": dashboard_url, "name": name},
        )

    def send_staff_invite(
        self, to: str, *, account_name: str, accept_url: str, role: str,
        inviter_name: str | None = None,
    ) -> None:
        """The invitation itself — **carries the only copy of the plaintext token**.

        `accept_url` embeds it, and nothing else in the system can reproduce it: only the hash is
        stored (D5). A failure to send is therefore not merely a delivery problem, it strands the
        invitation — which is why `_send` logs rather than swallowing silently, and why the UI
        offers resend.
        """
        self._send(
            to,
            "staff_invite",
            {
                "account_name": account_name,
                "accept_url": accept_url,
                "role": role,
                "inviter_name": inviter_name,
            },
        )

    def send_invite_accepted(
        self, to: str, *, account_name: str, member_email: str, role: str,
        invited_email: str | None = None,
    ) -> None:
        """Notify the inviter — and carry §6.3's **mismatch notice**.

        D5 makes the token the authority, so an invitation accepted from a different address is
        allowed rather than blocked; forwarding one to the address you actually sign in with is
        legitimate. Passing `invited_email` lets the template say so when the two differ, which
        is what turns a stolen invitation from silent into visible. Omitting it would leave the
        mitigation §6.3 pairs with D5 unimplemented while the feature looked complete.
        """
        self._send(
            to,
            "invite_accepted",
            {
                "account_name": account_name,
                "member_email": member_email,
                "role": role,
                "invited_email": invited_email,
            },
        )

    # ── SPEC-004 §6 Step 15 — the four billing mails ──────────────────────────
    #
    # **Methods here, not on the `EmailProvider` Protocol.** SPEC-001 §5.1 keeps that Protocol
    # transport-only — `send(to, subject, html, text)` and nothing else — so a provider swap
    # (Console → Resend → anything) never has to know what mail types exist. Adding
    # `send_receipt` there would make every provider implement every message.
    #
    # **Three fire from webhooks, one from the scheduler**, and B2 corrects the spec's own
    # summary on that point: `trial_ending` has no Stripe event behind it, because a card-less
    # trial has no Stripe subscription (F3). `cli/jobs.py::trial_sweep` is its only trigger.

    def send_receipt(self, to: str, *, plan: str, amount: str, billing_url: str,
                     period_end: str | None = None) -> None:
        """`invoice.paid` — the payment went through.

        `amount` is a **pre-formatted string**, not a number: Stripe reports minor units in the
        customer's currency, and formatting that correctly is a presentation concern the caller
        already has to solve for the billing page. Passing an int here would put currency
        formatting inside a mail template, where it would be wrong for every non-USD account.
        """
        self._send(
            to,
            "receipt",
            {
                "plan": plan,
                "amount": amount,
                "billing_url": billing_url,
                "period_end": period_end,
            },
        )

    def send_payment_failed(self, to: str, *, plan: str, billing_url: str,
                            grace_days: int | None = None) -> None:
        """`invoice.payment_failed` — the card was declined and dunning has started.

        **Says nothing has changed yet**, because it has not (D10): `past_due` is the grace
        window and keeps full access while Stripe retries. Leading with "your account is
        suspended" would be false *and* worse for recovery — a customer who believes they are
        already locked out is less likely to come back and fix the card.
        """
        self._send(
            to,
            "payment_failed",
            {"plan": plan, "billing_url": billing_url, "grace_days": grace_days},
        )

    def send_trial_ending(self, to: str, *, days_left: int, ends_on: str, billing_url: str,
                          home_count: int | None = None,
                          max_homes: int | None = None) -> None:
        """The trial is nearly over — **sent by the scheduler, never by a webhook** (F3, B2).

        Carries the over-limit numbers when they apply, because §4.3 shows the home-picker ~3
        days ahead *"so the choice is made before access changes rather than after"*. An email
        that only says "your trial is ending" leaves the customer to discover the consequence
        themselves, after two of their homes have gone read-only.
        """
        self._send(
            to,
            "trial_ending",
            {
                "days_left": days_left,
                "ends_on": ends_on,
                "billing_url": billing_url,
                "home_count": home_count,
                "max_homes": max_homes,
                "over_limit": bool(
                    home_count is not None and max_homes is not None and home_count > max_homes
                ),
            },
        )

    def send_subscription_cancelled(self, to: str, *, plan: str, billing_url: str,
                                    active_until: str | None = None) -> None:
        """`customer.subscription.deleted` — cancelled, and what that actually means.

        `active_until` matters: §4.4 says cancellation takes effect **at period end**, not
        instantly, because the customer has already paid for the rest of the period. Omitting it
        invites the support ticket that begins *"I cancelled and lost access immediately"* — which
        would be a misunderstanding the email caused.
        """
        self._send(
            to,
            "subscription_cancelled",
            {"plan": plan, "billing_url": billing_url, "active_until": active_until},
        )
