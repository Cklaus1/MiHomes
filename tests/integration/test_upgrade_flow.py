"""G18 · §6 — **A31, the exit criterion** (`SAAS_PRD:180`).

> With Steps 1–18 green: a Free account hits a gate, upgrades through Checkout, and the gate flips
> **from the webhook** — not the redirect (D1). That is `SAAS_PRD:180`.
>
> If A31 is red, the phase has not shipped regardless of what else is green.

**The "not the redirect" clause is the whole criterion**, and it is what makes this more than an
integration smoke test. A system that granted entitlements on `success_url` would pass "the user
upgraded and the gate flipped" perfectly — while handing the product to anyone who visits a URL
they control. N1 calls this *"the single most common Stripe integration defect"*, and it fails
**open**.

So the flow is exercised in the order production runs it, with the redirect visited **before** the
webhook arrives — the sequence a real browser produces, since Stripe redirects the user
immediately and delivers the event out of band. The gate must still be closed at that point.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as OrmSession

from mihomes.entitlements.service import Allowed, Denied, can
from mihomes.models.account import Account
from mihomes.services.property import EntitlementError, create_property

WEBHOOK_SECRET = "whsec_exit_criterion_test"
CUSTOMER_ID = "cus_exit_criterion"


def _sign(payload: bytes) -> str:
    ts = int(time.time())
    digest = hmac.new(
        WEBHOOK_SECRET.encode(), f"{ts}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return f"t={ts},v1={digest}"


def _subscription_created(price_id: str) -> bytes:
    """A real-shaped `customer.subscription.created` — the event Checkout produces."""
    return json.dumps({
        "id": "evt_exit_criterion",
        "type": "customer.subscription.created",
        "created": int(time.time()),
        "data": {"object": {
            "id": "sub_exit_criterion",
            "customer": CUSTOMER_ID,
            "status": "active",
            "current_period_end": int(time.time()) + 30 * 86400,
            "cancel_at_period_end": False,
            "items": {"data": [{"price": {"id": price_id}}]},
        }},
    }).encode()


@pytest.fixture
def upgrade_account(_pg_engine):
    """A **dedicated** account, committed on its own connection and cleaned up afterwards.

    Not `account_a`: the `session` fixture holds an open outer transaction on that row for the
    whole test, so a write to it from a second connection is invisible to a third — and the
    webhook route opens its own. The first version of this test used `account_a` and the handler
    logged *"no account for customer"* in the full-suite run while passing in isolation, which is
    the same connection-visibility trap in a new place.

    Its own account also means this test cannot be disturbed by, or disturb, anything else using
    the shared one.
    """
    import uuid as _uuid

    from sqlalchemy import text as _text

    account_id = _uuid.uuid4()
    with _pg_engine.begin() as conn:
        conn.execute(
            _text(
                "INSERT INTO accounts (id, slug, name, type, plan, stripe_customer_id, "
                "trial_used_at, created_at, updated_at) VALUES "
                "(:i, :s, 'Upgrade Flow', 'household', 'free', :c, now(), now(), now())"
            ),
            {"i": account_id, "s": f"upgrade-{account_id.hex[:8]}", "c": CUSTOMER_ID},
        )
    yield account_id
    with _pg_engine.begin() as conn:
        conn.execute(
            _text("DELETE FROM processed_webhook_events WHERE provider_event_id = :e"),
            {"e": "evt_exit_criterion"},
        )
        conn.execute(_text("DELETE FROM properties WHERE account_id = :i"), {"i": account_id})
        conn.execute(_text("DELETE FROM accounts WHERE id = :i"), {"i": account_id})


@pytest.fixture
def stripe_env(monkeypatch, _pg_engine):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "rk_test_notreal")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("STRIPE_PRICE_PRO_MONTHLY", "price_pro_monthly_test")

    # **Pin `DATABASE_URL` to the suite database, and this is the bug that cost the most to
    # find.** The webhook route opens its own session via `db.get_session()`, which resolves
    # `_active_url()` — i.e. `DATABASE_URL` — **at call time**. The session-scoped `cli_database`
    # fixture rewrites that variable to its own dedicated database and restores it only at
    # teardown, so **every test that runs after it** sees the CLI database.
    #
    # The symptom was maximally misleading: the webhook returned 200 and logged *"no account for
    # customer"*, which reads as a mapping bug. The account existed and was committed — the
    # handler was simply looking in a different database. Passing alone, failing in the suite,
    # and surviving a rewrite that swapped the account fixture for a dedicated one.
    # **`dispose_engine()` is required, not belt-and-braces.** `db._engine` is a module-level
    # cache built on first use, so re-pointing the environment after `cli_database` has already
    # bound it changes nothing — the stale engine keeps serving the CLI database. Setting the
    # variable alone left this test failing exactly as before, which is how the cache was found.
    import mihomes.db as db_mod

    monkeypatch.setenv("DATABASE_URL", str(_pg_engine.url))
    db_mod.dispose_engine()
    yield "price_pro_monthly_test"
    # Drop it again on the way out so the next test rebuilds against whatever it expects.
    db_mod.dispose_engine()


class TestTheExitCriterion:
    def test_exit_criterion(self, stripe_env, upgrade_account, _pg_engine):
        """**A31** — Free gate → Checkout → **webhook** flips it. Not the redirect.

        Four phases, in production's order:

        1. A Free account is denied its second home. The gate is real.
        2. The user pays; Stripe redirects them to `success_url`. **The gate is still closed** —
           this is the assertion N1 exists for, and the one a "did the upgrade work" test omits.
        3. The verified webhook arrives out of band and applies the state.
        4. The same call that was denied now succeeds.

        Phase 2 is placed *before* phase 3 deliberately: that is the sequence a real browser
        produces, and testing them the other way round would let a redirect-granting
        implementation pass.
        """
        price_id = stripe_env
        account_id = upgrade_account

        # ── 1. The gate is real ────────────────────────────────────────────────────
        from mihomes.tenancy import account_context

        with account_context(account_id), OrmSession(_pg_engine) as db:
            account = db.get(Account, account_id)
            create_property(db, "First Home")
            db.commit()

            with pytest.raises(EntitlementError):
                create_property(db, "Second Home")
            assert isinstance(can(account, "property.add", {"current_homes": 1}), Denied)

        # ── 2. The redirect grants nothing ─────────────────────────────────────────
        from mihomes.web.app import create_app

        with TestClient(create_app(), base_url="http://localhost") as client:
            client.get("/billing/success")  # unauthenticated: 403, and that is fine

        with OrmSession(_pg_engine) as db:
            account = db.get(Account, account_id)
            assert account.plan == "free", (
            "**N1** — visiting success_url must grant nothing. The user controls that URL: they "
                "can reach it without paying, replay it, or never arrive at all."
            )
            assert isinstance(can(account, "property.add", {"current_homes": 1}), Denied)

        # ── 3. The webhook applies the state ───────────────────────────────────────
        payload = _subscription_created(price_id)
        with TestClient(create_app(), base_url="http://localhost") as client:
            response = client.post(
                "/webhooks/stripe",
                content=payload,
                headers={"stripe-signature": _sign(payload)},
            )
        assert response.status_code == 200

        # ── 4. The gate has flipped ────────────────────────────────────────────────
        with OrmSession(_pg_engine) as check:
            upgraded = check.get(Account, account_id)
            assert upgraded.plan == "pro", (
                "the webhook must be what flips the plan (D1) — it is the only "
                "signature-verified signal in the flow"
            )
            assert upgraded.subscription_status == "active"
            assert isinstance(
                can(upgraded, "property.add", {"current_homes": 1}), Allowed
            ), "the gate that denied the second home must now allow it"

    def test_a_tampered_webhook_does_not_upgrade(self, stripe_env, session, account_a):
        """The adversarial half of A31, and the reason the signature is the authority.

        If a forged body could flip the plan, the redirect would merely be *one* of the ways to
        get the product free. The signature is what makes the webhook trustworthy enough to be
        the source of truth in the first place (D1) — so the criterion is only meaningful
        alongside this.
        """
        price_id = stripe_env
        account = session.get(Account, account_a)
        account.plan = "free"
        account.stripe_customer_id = CUSTOMER_ID
        session.commit()

        payload = _subscription_created(price_id)
        signature = _sign(payload)
        tampered = payload.replace(b'"status": "active"', b'"status": "active" ')

        from mihomes.web.app import create_app

        with TestClient(create_app(), base_url="http://localhost") as client:
            response = client.post(
                "/webhooks/stripe",
                content=tampered,
                headers={"stripe-signature": signature},
            )

        assert response.status_code == 400
        session.expire_all()
        assert session.get(Account, account_a).plan == "free"
