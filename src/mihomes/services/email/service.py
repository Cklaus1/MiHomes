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
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from mihomes.models.email_delivery import EmailDelivery
from mihomes.services.email.outbox import drain as outbox_drain
from mihomes.services.email.outbox import enqueue
from mihomes.services.email.provider import (
    EmailProvider,
    EmailResult,
    EmailSendError,
)
from mihomes.services.email.render import render_template
from mihomes.services.email.suppression import is_suppressed
from mihomes.tenancy.context import require_account

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

#: The two message classes. `lifecycle` is suppressible, `transactional` is not (D13/N3).
#:
#: Declared as data rather than checked inline so `_send`'s validation and the tests that
#: enumerate the classes read from one place: a third class added here without a suppression
#: decision fails `test_klass_required`'s membership check rather than silently defaulting to
#: whichever branch the `if` happened to take.
MESSAGE_CLASSES = ("lifecycle", "transactional")

logger = logging.getLogger(__name__)


class EmailService:
    def __init__(
        self, provider: EmailProvider, session: "Session | None" = None
    ) -> None:
        self.provider = provider
        # Optional so the landing app — a separate one-table tree with no product session
        # (SPEC-001 D1/D3) — keeps constructing this service unchanged. `_send` treats a
        # missing session as "cannot check", which is why the *absence* is loud: see below.
        self.session = session

    def _send(
        self, to: str, template: str, data: dict, *, klass: str, **provider_kwargs
    ) -> None:
        """Render and dispatch. Catches EmailSendError, logs, never raises to the caller.

        **`klass` is required and keyword-only, never defaulted** (D13, A3). A default would
        silently pick a suppression policy for every future call site, and picking wrong is bad
        in both directions: a suppressed receipt is a billing dispute, an unsuppressed drip is a
        CAN-SPAM violation. There is no safe default, so there is no default.

        **This is the single choke point** the suppression check lives at. Putting it in each
        `send_*` method would work until the next one forgot — and the failure would be silent
        mail going to someone who asked to stop, which nothing raises about.
        """
        if klass not in MESSAGE_CLASSES:
            raise ValueError(
                f"unknown message class {klass!r}; expected one of {MESSAGE_CLASSES}"
            )

        if klass == "lifecycle":
            if self.session is None:
                # Refuse rather than send. A lifecycle send with no way to check the list is
                # exactly the case that mails a complainer, and failing open here would make
                # every future caller that forgot the session into a silent CAN-SPAM problem.
                # Transactional mail is unaffected: it is not suppressible, so it needs no list.
                logger.error(
                    "lifecycle email not sent: no session to check suppression "
                    "(template=%s)", template,
                )
                return
            if is_suppressed(self.session, to):
                logger.info("lifecycle email suppressed: template=%s", template)
                return

        # **Enqueue, never send** (D12/N2). Step 4 moved the provider call into
        # `outbox.drain`: a send inside a web request makes a slow provider a slow page and a
        # failed provider a failed checkout, and an in-process retry dies with the request.
        #
        # Rendering moves with it. The row carries the render *context*, so a template fix
        # repairs mail that is already queued (§4.1) — which also means a broken template no
        # longer silently drops a message here, it fails loudly at drain time where the row
        # records why.
        if self.session is None:
            # Transactional mail with no session: the landing app's waitlist confirmation,
            # which exists before any account does and has no queue to sit in. Sent inline,
            # the one remaining direct provider call.
            self._send_inline(to, template, data, **provider_kwargs)
            return

        try:
            account_id = require_account()
        except LookupError:
            # No account bound and a session present — a CLI job or a test that forgot to
            # bind. Inline rather than dropped: the message still matters, and an outbox row
            # with no owner could never be drained (RLS would never select it).
            self._send_inline(to, template, data, **provider_kwargs)
            return

        enqueue(
            self.session,
            to=to,
            template=template,
            context=data,
            klass=klass,
            account_id=account_id,
            now=datetime.now(UTC),
        )
        logger.info("email queued: template=%s klass=%s", template, klass)

    def _send_inline(self, to: str, template: str, data: dict, **provider_kwargs) -> None:
        """Render and send immediately, with no outbox row.

        The narrow path for mail that has no account to queue under. It keeps SPEC-001's
        waitlist confirmation working unchanged and is deliberately not reachable from
        anything that has a session and a bound account — N2 owns that case.
        """
        try:
            subject, html, text = render_template(template, data)
        except Exception:
            logger.exception("email template render failed: template=%s to=%s", template, to)
            return

        try:
            result = self.provider.send(to, subject, html, text=text, **provider_kwargs)
        except EmailSendError:
            logger.exception("email send failed: template=%s to=%s", template, to)
            return

        self._record_delivery(to, template, result)
        logger.info(
            "email sent: template=%s to=%s provider=%s id=%s",
            template, to, result.provider, result.provider_message_id,
        )

    def drain(self, *, now: datetime | None = None, limit: int = 100):
        """Send this account's queued mail. The other half of `_send` (D12).

        A method on the service rather than a bare call to `outbox.drain` so the delivery
        write stays here: A19's "exactly one row per send" now happens inside the drain, and
        `_record_delivery` is what the outbox calls back into.
        """
        return outbox_drain(
            self.session,
            self.provider,
            limit=limit,
            now=now or datetime.now(UTC),
            record_delivery=lambda row, result: self._record_delivery(
                row.to_address, row.template, result
            ),
        )

    def _record_delivery(self, to: str, template: str, result: EmailResult) -> None:
        """Write the `EmailDelivery` row. Never raises — logging a send must not fail it.

        The same discipline as `_send`'s own error handling (§5.3, BILLING §2.4): the message
        has already left. Losing the record of it is bad; turning a delivered email into a
        500 for the request that triggered it is worse, and no retry can un-send the mail.

        Flushed rather than committed, so the row joins the caller's transaction — a webhook
        handler recording a receipt commits the delivery row with its own work, atomically.
        """
        if self.session is None:
            # The landing app has no product session (SPEC-001 D1/D3). Debug rather than
            # error, unlike the lifecycle-suppression branch: there the missing session means
            # a message went out that should not have, here it means one went out untracked.
            logger.debug("delivery not recorded: no session (template=%s)", template)
            return

        try:
            account_id = require_account()
        except LookupError:
            # `current_account` has no default and raises when unset — the module's
            # deliberate fail-closed shape. Caught explicitly rather than read with a falsy
            # check, so the unscoped path is visible in the code (tenancy/context.py says
            # exactly this). The landing app and CLI jobs both reach here legitimately.
            logger.debug("delivery not recorded: no account bound (template=%s)", template)
            return

        try:
            self.session.add(
                EmailDelivery(
                    account_id=account_id,
                    to_address=to,
                    template=template,
                    sent_at=datetime.now(UTC),
                    provider=result.provider,
                    provider_message_id=result.provider_message_id,
                )
            )
            self.session.flush()
        except Exception:
            logger.exception("failed to record delivery: template=%s", template)

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
            # D13: The double opt-in confirmation IS the consent step (D7). Suppressing it would mean an
            # address that unsubscribed could never subscribe again — and there is nothing to
            # suppress yet, because they have not consented to anything.
            klass="transactional",
        )

    # ── SPEC-003 §6 Step 12 — the three Phase 2 mail types ────────────────────

    def send_welcome(self, to: str, *, account_name: str, dashboard_url: str,
                     name: str | None = None) -> None:
        """Sent once, when onboarding creates the account (Step 11)."""
        self._send(
            to,
            "welcome",
            {"account_name": account_name, "dashboard_url": dashboard_url, "name": name},
            # D13: Sent once, on account creation. A record of an account existing in their name.
            klass="transactional",
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
            # D13: Carries the only copy of the plaintext token — suppressing it strands the invitation
            # with no way to recover it (D5).
            klass="transactional",
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
            # D13: A security notice to the inviter, carrying §6.3's mismatch warning. Withholding it on
            # an unsubscribe would silence exactly the signal that catches a stolen invite.
            klass="transactional",
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
            # D13: A receipt for money taken. N3 names this one directly: not marketing, and owed.
            klass="transactional",
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
            # D13: The card was declined. A customer who unsubscribed from marketing still needs to know
            # their payment failed, or they lose access without ever being told why.
            klass="transactional",
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
            # D13: Access is about to change and a choice must be made before it does (§4.3). Consequence
            # of a contract, not a campaign.
            klass="transactional",
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
            # D13: Confirms a subscription ended and until when. The record of a billing state change.
            klass="transactional",
        )
