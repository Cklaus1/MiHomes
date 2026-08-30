"""G5 · §6 Step 5 — the webhook route (A14, A15, A16). G6 adds A17 to this file.

Every assertion here is **paired** (§0.5b), and in this file the rule does more work than usual
because all three criteria are phrased as absences: *no* DB write, *nothing* twice, *cannot*
both process. Each of those is trivially satisfied by a route that 404s, or by a handler that
never writes at all — so every negative arm below is preceded by a positive one proving the
happy path genuinely works.

**The secret is read from the environment**, not `configurations`: that table is
`TENANT_TABLES` and the secret is needed before any account exists — the same bootstrap problem
`resolve_sender` has, and the same answer.

## No test here takes the `session` fixture, and that is deliberate

Combining `session` with `web_client_factory` **leaks tenant context into every later test in
the run**. Found the expensive way: the first version of this file did exactly that, and three
tenancy tests then failed afterwards — `test_fails_closed_without_context` reporting
`DID NOT RAISE LookupError`, because `current_account` was still bound.

The mechanism is unbalanced ContextVar nesting across two fixtures, not a bug in either:

* `session` (conftest) enters `account_context(account_a)` at **setup** → token_S
* `web_client_factory`'s `make()` enters `account_context(account_a)` in the **test body** →
  token_W
* teardown unwinds in reverse-of-setup order, so token_S is reset *first* and token_W second

`ContextVar.reset()` with an out-of-order token does not raise — it restores that token's
`old_value`, and token_W's old value is `account_a`. So the final reset **re-binds** the tenant
it was supposed to clear. `test_settings.py` is unaffected because those tests take a web client
without also taking `session`.

**This is a test-fixture interaction, not a production defect**, and it is recorded here rather
than in the harness's deviation table for that reason: in a server each request has its own
context and `account_context` is balanced inside the handler. But the next person to combine
these two fixtures will hit it again, so the mechanism is written down where they will be
standing.

The counts below therefore run on `_pg_engine`'s own connection, which also fixes a second
problem: the route commits on a connection of its own, so a count read through the `session`
fixture's open transaction would see a pre-request snapshot rather than the database.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from mihomes.models.membership import Membership
from mihomes.models.telegram_link import TelegramLink
from mihomes.models.user import User
from mihomes.services.property import create_property
from mihomes.tenancy.context import account_context

SECRET = "a11-webhook-secret-token"
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"

#: Tables a dispatched message can write. Counted around every request, so "no DB write" is a
#: statement about the database rather than about one table somebody remembered to check.
WATCHED_TABLES = ("issues", "tasks", "work_orders", "notes", "audit_log")


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", SECRET)


@pytest.fixture(autouse=True)
def _no_ai(monkeypatch):
    """Stub the AI call.

    Not for speed: unstubbed it fails without an API key and its `except` branch calls
    `session.rollback()`, which discards prior writes in the same batch. That defect is recorded
    in `opportunities.md`; here it would make row counts meaningless.
    """
    from mihomes.services.gateways import review_common as rc

    monkeypatch.setattr(rc, "ai_response", lambda *a, **k: "stubbed answer")


@pytest.fixture
def sent(monkeypatch):
    """Capture every outbound Telegram message instead of sending it.

    Patched at **two** seams because the two reply paths are genuinely different, which is a
    fact about the route rather than an inconvenience:

    * `responder._get_client` — the *scoped* path. The responder builds its client from the
      account's `telegram.bot_token` config, so a linked sender's reply needs a bound account
      to even construct a client.
    * `client.TelegramClient` — the *unscoped* path. An unlinked sender has no account, so the
      route's `_reply` constructs a bare client to send the linking prompt. That asymmetry is
      D12's consequence: the one message we send to someone we cannot place.
    """
    captured: list[tuple[str, str]] = []

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def send_message(self, chat_id, text_, **kwargs):
            captured.append((str(chat_id), text_))
            return {"ok": True}

    monkeypatch.setattr(
        "mihomes.services.gateways.telegram.client.TelegramClient", _FakeClient
    )
    monkeypatch.setattr(
        "mihomes.services.gateways.telegram.responder._get_client",
        lambda session: _FakeClient(),
    )
    return captured


def _update(update_id: int, *, sender_id: str, chat_id: str, text_: str) -> bytes:
    """A real-shaped Telegram update, as raw bytes.

    Bytes rather than a dict, deliberately: N4's ordering is only meaningful if the test drives
    the route the way Telegram does, and a test that handed the handler a parsed body would be
    exercising a path production never takes.
    """
    return json.dumps(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "from": {"id": int(sender_id), "first_name": "Test", "is_bot": False},
                "chat": {"id": int(chat_id), "type": "group"},
                "date": 1756500000,
                "text": text_,
            },
        }
    ).encode("utf-8")


def _counts(engine, account_id) -> dict[str, int]:
    """Row counts per watched table for one account, on the engine's **own** connection.

    Two reasons, and the second is why this takes an engine rather than the `session` fixture:

    1. Raw SQL with an explicit filter, not the ORM: a scoped ORM read cannot see another
       account's rows, so it could never detect a leak — the measurement would inherit the
       protection it is checking.
    2. The route commits on its own connection. The `session` fixture holds an open
       transaction, so counts read through it see a snapshot from before the request rather
       than the database — and combining that fixture with `web_client_factory` also leaks
       tenant context (see this module's docstring).
    """
    with engine.connect() as conn:
        return {
            t: conn.execute(
                text(f"SELECT count(*) FROM {t} WHERE account_id = :a"),  # noqa: S608
                {"a": account_id},
            ).scalar_one()
            for t in WATCHED_TABLES
        }


def _link_sender(session, account_id, telegram_user_id: int) -> Membership:
    user = User(
        id=uuid.uuid4(),
        google_sub=f"sub-{uuid.uuid4().hex[:12]}",
        email=f"wh-{uuid.uuid4().hex[:8]}@example.com",
    )
    session.add(user)
    session.flush()
    membership = Membership(
        id=uuid.uuid4(),
        account_id=account_id,
        user_id=user.id,
        role="admin",
        status="active",
    )
    session.add(membership)
    session.flush()
    session.add(
        TelegramLink(
            id=uuid.uuid4(),
            account_id=account_id,
            membership_id=membership.id,
            telegram_user_id=telegram_user_id,
        )
    )
    session.flush()
    return membership


@pytest.fixture
def wired(_pg_engine, account_a, sent):
    """A committed property + linked sender in account A, cleaned up afterwards.

    Committed because the route opens its **own** sessions — it cannot see rows that live only
    inside a test transaction. That commit escapes the `session` fixture's rollback, so the
    teardown deletes what it made; without it the rows pollute every later test in the run
    (measured during G4, where exactly this made `test_archive.py` fail).
    """
    from sqlalchemy.orm import sessionmaker

    conn = _pg_engine.connect()
    tx = conn.begin()
    Session = sessionmaker(bind=conn, future=True, join_transaction_mode="create_savepoint")
    s = Session()
    with account_context(account_a):
        prop = create_property(s, "Webhook Estate")
        s.flush()
        _link_sender(s, account_a, 424242424)
        s.flush()
        # Read the slug **inside** the context: `commit()` expires the instance, so touching
        # `prop.slug` afterwards triggers a refresh — an ORM query with no tenant bound, which
        # fails closed with `LookupError`. The value, not the object, is what escapes.
        slug = prop.slug
        s.commit()
    s.close()
    tx.commit()
    conn.close()

    yield slug

    cleanup = _pg_engine.connect()
    ctx = cleanup.begin()
    for table in (
        *WATCHED_TABLES,
        "telegram_links",
        "memberships",
        "configurations",
        "properties",
    ):
        cleanup.execute(
            text(f"DELETE FROM {table} WHERE account_id = :a"),  # noqa: S608
            {"a": account_a},
        )
    # **`users` is GLOBAL — it has no `account_id`**, so the loop above cannot reach it and a
    # committed User outlives this test. `test_scoped_session.py::
    # test_global_tables_are_queryable_without_an_account` asserts the table is *empty*, so a
    # leftover row fails it — which is how this omission was found. Deleted by the marker email
    # this file mints, rather than by a blanket `DELETE FROM users`, so a fixture from another
    # module is never collateral damage.
    cleanup.execute(text("DELETE FROM users WHERE email LIKE 'wh-%@example.com'"))
    ctx.commit()
    cleanup.close()


# ------------------------------------------------------------------------------------- #
# A14 — a forged signature is rejected with no DB write
# ------------------------------------------------------------------------------------- #
def test_bad_signature_no_write(web_client_factory, _pg_engine, account_a, wired):
    """**A14** — a forged secret token is rejected, and nothing is written.

    Paired in both directions, because "no write" is vacuous on a route that never writes:
    a **valid** request is asserted to write first, then a forged one is asserted to write
    nothing on top of that.
    """
    client = web_client_factory()
    body = _update(9001, sender_id="424242424", chat_id="-100777", text_="Boiler is leaking")

    # --- positive: the route works when the token is right -------------------------------
    ok = client.post(
        "/webhooks/telegram", content=body, headers={SECRET_HEADER: SECRET}
    )
    assert ok.status_code == 200
    after_valid = _counts(_pg_engine, account_a)
    assert sum(after_valid.values()) > 0, (
        "a correctly-authenticated update wrote nothing — the negative arm below would then "
        "pass on a route that is simply broken"
    )

    # --- negative: a forged token is refused, and adds nothing ---------------------------
    forged = client.post(
        "/webhooks/telegram",
        content=_update(9002, sender_id="424242424", chat_id="-100777", text_="Forged"),
        headers={SECRET_HEADER: "not-the-secret"},
    )
    assert forged.status_code == 401
    assert _counts(_pg_engine, account_a) == after_valid, "a forged request wrote to the database"

    # --- and a MISSING token is refused too, not treated as "nothing to verify" ----------
    missing = client.post("/webhooks/telegram", content=body)
    assert missing.status_code == 401
    assert _counts(_pg_engine, account_a) == after_valid


def test_an_unset_secret_refuses_everything(web_client_factory, account_a, monkeypatch):
    """An unconfigured secret must fail **closed**, not open.

    `setWebhook` without a `secret_token` leaves the endpoint open to anyone who learns the URL.
    The tempting implementation — "no secret configured, so nothing to check" — turns a
    misconfiguration into an unauthenticated write endpoint, silently.
    """
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "")
    client = web_client_factory()

    r = client.post(
        "/webhooks/telegram",
        content=_update(9100, sender_id="424242424", chat_id="-100777", text_="hi"),
        headers={SECRET_HEADER: "anything"},
    )
    assert r.status_code == 401


# ------------------------------------------------------------------------------------- #
# A15 — a valid update reaches the responder under the correct account
# ------------------------------------------------------------------------------------- #
def test_routes_to_account(web_client_factory, _pg_engine, account_a, account_b, wired):
    """**A15** — the update is dispatched under the account its *sender* resolves to.

    The paired arm is the one that proves resolution ran at all: an **unlinked** sender gets the
    linking prompt and writes nothing (D12/N2). Without it, a handler that wrote into account A
    unconditionally would satisfy "a row appeared in A".
    """
    client = web_client_factory()

    before_b = _counts(_pg_engine, account_b)

    r = client.post(
        "/webhooks/telegram",
        content=_update(9200, sender_id="424242424", chat_id="-100777", text_="Boiler leaking"),
        headers={SECRET_HEADER: SECRET},
    )
    assert r.status_code == 200

    # --- positive: a row in A ------------------------------------------------------------
    assert sum(_counts(_pg_engine, account_a).values()) > 0

    # --- negative: nothing in B ----------------------------------------------------------
    assert _counts(_pg_engine, account_b) == before_b, (
        "an update from account A's sender wrote into account B"
    )


def test_an_unlinked_sender_is_prompted_and_writes_nothing(
    web_client_factory, _pg_engine, account_a, sent, wired
):
    """D12/N2 at the transport edge — the failure this whole phase exists to prevent.

    An unlinked sender must never be defaulted into an account, even when exactly one account
    plausibly fits. They are told how to link, and nothing is written anywhere.
    """
    client = web_client_factory()
    before = _counts(_pg_engine, account_a)

    r = client.post(
        "/webhooks/telegram",
        content=_update(9300, sender_id="999000111", chat_id="-100777", text_="Boiler leaking"),
        headers={SECRET_HEADER: SECRET},
    )

    # 200, not an error: an unknown sender is expected first contact, and a non-2xx would make
    # Telegram retry the same update forever.
    assert r.status_code == 200
    assert _counts(_pg_engine, account_a) == before, (
        "an unlinked sender's message was written into an account (D12/N2)"
    )
    assert any("link" in text_.lower() for _chat, text_ in sent), (
        "an unlinked sender must be told how to link, not met with silence"
    )


# ------------------------------------------------------------------------------------- #
# A16 — redelivery creates nothing twice
# ------------------------------------------------------------------------------------- #
def test_redelivery_idempotent(web_client_factory, _pg_engine, account_a, wired):
    """**A16** — the same update delivered twice creates one row, not two.

    Providers redeliver aggressively, and a 500 from us is *supposed* to be retried — so this is
    what makes the retry safe rather than duplicating an issue every time the network hiccups.

    Paired: the first delivery is asserted to have written **before** the second is asserted not
    to. A handler that wrote nothing at all would otherwise pass.
    """
    client = web_client_factory()
    body = _update(9400, sender_id="424242424", chat_id="-100777", text_="Boiler leaking badly")

    first = client.post("/webhooks/telegram", content=body, headers={SECRET_HEADER: SECRET})
    assert first.status_code == 200
    after_first = _counts(_pg_engine, account_a)
    assert sum(after_first.values()) > 0, "the first delivery wrote nothing"

    # Byte-identical redelivery — same update_id, which is what dedup keys on.
    second = client.post("/webhooks/telegram", content=body, headers={SECRET_HEADER: SECRET})
    assert second.status_code == 200
    assert _counts(_pg_engine, account_a) == after_first, (
        "redelivery of one update created rows twice — ProcessedIdStore is not being consulted"
    )


# ------------------------------------------------------------------------------------- #
# G5.4 — the Host/Origin exemption, MEASURED rather than assumed
# ------------------------------------------------------------------------------------- #
def test_the_host_guard_exemption_covers_this_path(web_client_factory):
    """The webhook prefix exemption must actually cover `/webhooks/telegram`.

    **Measured, not reasoned.** SPEC-005 C9 asked the same question of `/unsubscribe` and the
    answer was *no* — a POST there returned `400 Invalid Host` while `/webhooks/...` passed, and
    it needed its own exemption. The prefix scoping makes this path inherit it, but that is a
    prediction until a request proves it.

    A 401 here is the **pass**: it means the request reached the handler and failed
    *verification*, rather than being turned away by the Host guard before it got there.
    """
    client = web_client_factory()
    r = client.post(
        "/webhooks/telegram",
        content=b"{}",
        headers={"Host": "mihomes.ai", SECRET_HEADER: "wrong"},
    )
    assert r.status_code != 400, (
        "the Host guard rejected the gateway webhook — it needs its own exemption in "
        "`web/security.py`, exactly as `/unsubscribe` did (SPEC-005 C9)"
    )
    assert r.status_code == 401


def test_the_route_stays_under_the_exempt_prefix():
    """A drift guard the prefix exemption needs and does not have.

    `WEBHOOK_PATH_PREFIX` lives in `webhooks.py`, beside the Stripe route, *"so a rename cannot
    silently re-arm the guards"*. This route lives in a different module — which is precisely
    the separation that comment warns about. Renaming this path would silently re-arm the Host
    guard and 400 every live delivery, with nothing failing until production.
    """
    from mihomes.web.routes.gateways import router
    from mihomes.web.routes.webhooks import WEBHOOK_PATH_PREFIX

    paths = [r.path for r in router.routes]
    assert paths, "the gateway router declares no routes"
    for path in paths:
        assert path.startswith(WEBHOOK_PATH_PREFIX), (
            f"{path} is outside {WEBHOOK_PATH_PREFIX!r}, so `web/security.py`'s exemption no "
            "longer covers it and the Host guard will reject every delivery"
        )
