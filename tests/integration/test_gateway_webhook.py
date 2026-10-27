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
#: **`audit_log` is deliberately absent.** It was here in the first version and it made every
#: count in this file meaningless: sender resolution writes audit rows, so `sum(counts) > 0`
#: was satisfied *before dispatch ever ran*. The webhook path was in fact dropping every
#: message at `responder.py:208`'s `propertySlug` filter and returning "No linked chat found",
#: and A14/A15/A16 all passed anyway — a criterion met by the wrong mechanism.
#:
#: Counting only tables a **dispatched item** creates is what makes these assertions mean what
#: they say. Found while writing A19, whose "the same dict, not a compatible subset" claim is
#: the same defect stated from the other side.
WATCHED_TABLES = ("issues", "tasks", "work_orders", "notes")


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", SECRET)


@pytest.fixture(autouse=True)
def _pin_engine_to_test_db():
    """Pin `mihomes.db`'s global engine to `TEST_DATABASE_URL` for every test here.

    **The route opens its own sessions via `get_session()`**, which resolves `DATABASE_URL` at
    call time — and `cli_database` (root conftest, session-scoped) repoints that env var at a
    *different* dedicated database the moment any of its consumer modules runs, restoring it
    only at session teardown. `test_cli.py` collects before this file alphabetically, so the
    ambient value is already the CLI database by the time these tests run.

    The symptom is precise and misleading: A17 passed alone and failed in the full suite with
    *"the poller's store does not know the webhook handled this update"* — because the webhook
    wrote its `ProcessedIdStore` entry into the CLI database while the assertion read
    `TEST_DATABASE_URL`. Same account, wrong database, and it reads exactly like the dedup
    mechanism being broken.

    `test_dedup.py::isolated_db` documents this hazard and solves it the same way; this is that
    fix applied to the route's sessions rather than to the store's.
    """
    import os

    from mihomes import db

    prev_engine, prev_factory = db._engine, db._SessionLocal
    my_engine = db.get_engine(os.environ["TEST_DATABASE_URL"])
    try:
        yield
    finally:
        my_engine.dispose()
        db._engine, db._SessionLocal = prev_engine, prev_factory


@pytest.fixture(autouse=True)
def _no_ai(monkeypatch):
    """Stub the AI, at **the responder's own seam**.

    `analyze_messages` is what `process_and_respond` calls to turn raw messages into
    categorized items; without an API key it raises `AIProviderError`, the batch is abandoned,
    and nothing is dispatched. Stubbing `review_common.ai_response` alone is not enough — that
    is the *reply* path, one layer down, and stubbing only that is why an earlier version of
    this file measured a webhook that never dispatched anything at all.

    Returns one `issue` item so a delivered update produces exactly one row in a watched table,
    which is what makes "wrote something" and "did not write twice" both meaningful.
    """
    from mihomes.services.gateways import review_common as rc

    monkeypatch.setattr(rc, "ai_response", lambda *a, **k: "stubbed answer")

    def _fake_analyze(session, messages, *, property_name=None, property_slug=None, **kw):
        return {
            "items": [
                {
                    "category": "issue",
                    "title": (messages[0].get("text") or "Webhook issue")[:80],
                    "description": "created by the webhook test's analyze stub",
                    "severity": "medium",
                    "property_slug": property_slug,
                }
            ]
        }

    monkeypatch.setattr(
        "mihomes.services.gateways.telegram.responder.analyze_messages", _fake_analyze
    )


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

    # The real constructor signature, not `*a, **k`. The permissive fake is what hid `_reply`
    # building `TelegramClient()` with no token: the TypeError was swallowed by `_reply`'s
    # except-all, so unlinked senders never got the link prompt in production.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:platform-token")

    class _FakeClient:
        def __init__(self, token):
            assert token, "a client built without a token can never send"
            self.token = token

        def send_message(self, chat_id, text_, **kwargs):
            captured.append((str(chat_id), text_))
            return {"ok": True}

    monkeypatch.setattr(
        "mihomes.services.gateways.telegram.client.TelegramClient", _FakeClient
    )
    monkeypatch.setattr(
        "mihomes.services.gateways.telegram.responder._get_client",
        lambda session: _FakeClient("123:platform-token"),
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
    # `audit_log` is named explicitly rather than inherited from `WATCHED_TABLES`, and the two
    # lists are deliberately different: what this test *counts* must exclude audit rows (they
    # are written by sender resolution, not by dispatch, and their inclusion is what let the
    # G5 defect hide), while what it *cleans up* must include them — a committed audit row
    # outlives the test and made `test_archive.py::test_counts_eligible_rows` see 11 rows
    # instead of 2. Measured both ways.
    for table in (
        *WATCHED_TABLES,
        "audit_log",
        "chat_messages",
        "telegram_chat_bindings",
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


# ------------------------------------------------------------------------------------- #
# A17 (G6) — the webhook and a running poller cannot both process one update
# ------------------------------------------------------------------------------------- #
def test_no_double_transport(web_client_factory, _pg_engine, account_a, wired):
    """**A17** — an update handled by the webhook is not re-handled by the poller.

    N6: *"Do not run the webhook and the poller against one bot simultaneously."* Telegram
    itself refuses `getUpdates` while a webhook is registered, so on this transport the provider
    supplies an interlock — **but the WhatsApp path has none**, and N6 says so explicitly, which
    is why the guarantee cannot rest on Telegram's behaviour.

    Step 6 words the criterion as *"cannot both **process** one update"*, not "cannot both run".
    That is the claim tested here, and the mechanism is the **shared `ProcessedIdStore`**:
    whichever transport sees an update first records its id, and the other skips it.

    Which makes the key the whole thing. An earlier draft of the webhook route used its own key
    (`gateway.telegram.processed`) while the monitor used `telegram.processed_ids` — two
    disjoint stores, so an update processed by one was invisible to the other. That is exactly
    the M22 defect `dedup.py` was written to fix, reintroduced. Both now import
    `PROCESSED_IDS_KEY`, and `test_both_transports_share_one_store` below makes the divergence
    impossible to reintroduce silently.
    """
    from mihomes.services.gateways.dedup import ProcessedIdStore
    from mihomes.services.gateways.telegram.extractor import (
        MAX_PROCESSED_IDS,
        PROCESSED_IDS_KEY,
    )

    client = web_client_factory()
    update_id = 9500

    # --- the webhook processes the update ------------------------------------------------
    r = client.post(
        "/webhooks/telegram",
        content=_update(update_id, sender_id="424242424", chat_id="-100777", text_="Leak"),
        headers={SECRET_HEADER: SECRET},
    )
    assert r.status_code == 200
    after_webhook = _counts(_pg_engine, account_a)
    assert sum(after_webhook.values()) > 0, (
        "the webhook wrote nothing, so 'the poller does not re-process it' would be vacuous"
    )

    # --- the poller's OWN dedup check now reports it as already handled -------------------
    # Asserted against `ProcessedIdStore` directly rather than by driving `_run_loop`: that
    # function is a closure over a live Telegram client and is not callable in a test, and the
    # store is the actual mechanism either way.
    # The ids are the ones the POLLER's own normalizer produces for these updates — not literals.
    # This test used to check `str(update_id)` and have the "poller" record "9600", while the real
    # poller records "<chat>:<message_id>": one store, two id schemes, and the two transports
    # could never match a message (it passed against a fiction). Both now share
    # `normalize_message`, so the poller's id is computed exactly as the poller computes it.
    from mihomes.services.gateways.telegram.client import normalize_message

    def _poller_id(uid: int, text_: str) -> str:
        raw = _update(uid, sender_id="424242424", chat_id="-100777", text_=text_)
        return normalize_message(json.loads(raw))["id"]

    with account_context(account_a):
        poller_store = ProcessedIdStore(PROCESSED_IDS_KEY, cap=MAX_PROCESSED_IDS)
        assert poller_store.contains(_poller_id(update_id, "Leak")), (
            "the poller's store does not know the webhook handled this update — the two "
            "transports key on different stores or different ids and would both process it"
        )

        # --- and the reverse direction: an id the POLLER records is skipped by the webhook --
        poller_store.add([_poller_id(9600, "Poller saw this")])

    before = _counts(_pg_engine, account_a)
    r2 = client.post(
        "/webhooks/telegram",
        content=_update(9600, sender_id="424242424", chat_id="-100777", text_="Poller saw this"),
        headers={SECRET_HEADER: SECRET},
    )
    assert r2.status_code == 200
    assert _counts(_pg_engine, account_a) == before, (
        "the webhook re-processed an update the poller had already handled"
    )


def test_both_transports_share_one_store():
    """The derived gate for A17: one key and one cap, read from the source.

    A17's behavioural test above could be satisfied for the wrong reason — a webhook that
    silently failed to write would also "not double-process". This asserts the *mechanism*
    instead, and it is the assertion that fails the moment someone gives either transport its
    own key again.

    Read out of the modules rather than hardcoded: a test that restated the key would agree
    with itself while the code drifted.
    """
    import inspect

    from mihomes.cli import telegram as cli_telegram
    from mihomes.services.gateways.telegram.extractor import (
        MAX_PROCESSED_IDS,
        PROCESSED_IDS_KEY,
    )
    from mihomes.web.routes import gateways as route

    for module, name in ((cli_telegram, "the CLI poller"), (route, "the webhook route")):
        source = inspect.getsource(module)
        assert "PROCESSED_IDS_KEY" in source, (
            f"{name} does not use PROCESSED_IDS_KEY — a local key makes its dedup store "
            "disjoint from the other transport's, and both will process the same update (M22)"
        )
        assert "MAX_PROCESSED_IDS" in source, (
            f"{name} does not use MAX_PROCESSED_IDS — two caps on one key means the shorter "
            "one evicts ids the longer one still considers handled"
        )

    # And the constants are real, so the greps above are not matching a comment in a file that
    # imports nothing.
    assert PROCESSED_IDS_KEY == "telegram.processed_ids"
    assert MAX_PROCESSED_IDS >= 2000


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


# ------------------------------------------------------------------------------------- #
# The unscoped bootstrap reads must work under the PRODUCTION role, not only as superuser
# ------------------------------------------------------------------------------------- #
@pytest.fixture
def app_role(app_engine):
    """`app_engine`, with its pool disposed afterwards.

    These tests stamp `app.current_account` on app-role connections; the test engine has no
    pool-checkin reset, so a returned connection would carry the GUC (as '' rather than NULL)
    into `test_rls.py::test_unset_guc_returns_empty`, which needs a pristine connection.
    """
    yield app_engine
    app_engine.dispose()


def test_sender_resolution_works_under_the_app_role(app_role, account_a, wired):
    """`resolve_sender` runs before any account is bound — that is its whole job.

    Every other test here connects as `postgres`, which bypasses RLS, so they cannot see whether
    the unscoped lookup survives `FORCE ROW LEVEL SECURITY` on `telegram_links` under the
    non-superuser role production runs as (`verify_runtime_role`, N5).
    """
    from sqlalchemy.orm import Session

    from mihomes.services.gateways.identity import resolve_sender

    with Session(app_role) as s:
        resolved = resolve_sender(s, gateway="telegram", sender_id="424242424")
    assert resolved.account_id == account_a


def _issue_code(_pg_engine, account_a) -> str:
    """A fresh staff membership with an unredeemed Telegram link code, committed (raw code)."""
    from sqlalchemy.orm import sessionmaker

    from mihomes.services.gateways.linking import issue_link_token

    conn = _pg_engine.connect()
    tx = conn.begin()
    s = sessionmaker(bind=conn, future=True, join_transaction_mode="create_savepoint")()
    with account_context(account_a):
        user = User(id=uuid.uuid4(), google_sub=f"sub-{uuid.uuid4().hex[:12]}",
                    email=f"wh-{uuid.uuid4().hex[:8]}@example.com")
        s.add(user)
        s.flush()
        membership = Membership(id=uuid.uuid4(), account_id=account_a, user_id=user.id,
                                role="staff", status="active")
        s.add(membership)
        s.flush()
        code = issue_link_token(s, account_a, membership.id, gateway="telegram")
        s.commit()
    s.close()
    tx.commit()
    conn.close()
    return code


def test_the_whole_webhook_path_works_under_the_app_role(
    web_client_factory, _pg_engine, app_role, account_a, wired, sent
):
    """Unlinked → `/link CODE` → linked message → issue row, with the ROUTE on the app role.

    Every other webhook test runs the route's own sessions as `postgres`, which bypasses RLS —
    so the suite was green while, in production, no sender could resolve and no code redeem.
    """
    from mihomes import db

    code = _issue_code(_pg_engine, account_a)
    db._engine, db._SessionLocal = app_role, None  # restored by `_pin_engine_to_test_db`
    client = web_client_factory()
    before = _counts(_pg_engine, account_a)
    try:
        r = client.post(
            "/webhooks/telegram",
            content=_update(9801, sender_id="777000111", chat_id="-100777", text_=f"/link {code}"),
            headers={SECRET_HEADER: SECRET},
        )
        assert r.status_code == 200
        assert any("You're linked" in t for _c, t in sent), sent

        r = client.post(
            "/webhooks/telegram",
            content=_update(9802, sender_id="777000111", chat_id="-100777",
                            text_="The boiler in the cellar is leaking"),
            headers={SECRET_HEADER: SECRET},
        )
        assert r.status_code == 200
        assert _counts(_pg_engine, account_a)["issues"] == before["issues"] + 1
    finally:
        with _pg_engine.begin() as cleanup:
            cleanup.execute(text("DELETE FROM gateway_link_tokens WHERE account_id = :a"),
                            {"a": account_a})


def test_link_redemption_works_under_the_app_role(_pg_engine, app_role, account_a, wired):
    """`/link CODE` runs before any account is bound: the code is what reveals the account.

    Under the production role the token lookup used to find nothing (every code "not valid"),
    and the link insert needs the account GUC for RLS's WITH CHECK.
    """
    from sqlalchemy.orm import Session

    from mihomes.services.gateways.linking import redeem_link_token

    code = _issue_code(_pg_engine, account_a)
    try:
        with Session(app_role) as app_s:
            resolved = redeem_link_token(app_s, gateway="telegram", sender_id="555000111",
                                         raw_token=code)
            app_s.commit()
        assert resolved.account_id == account_a

        with _pg_engine.connect() as check:
            linked = check.execute(
                text("SELECT count(*) FROM telegram_links WHERE telegram_user_id = 555000111")
            ).scalar_one()
        assert linked == 1
    finally:
        with _pg_engine.begin() as cleanup:
            cleanup.execute(text("DELETE FROM gateway_link_tokens WHERE account_id = :a"),
                            {"a": account_a})


# ------------------------------------------------------------------------------------- #
# Group binding — /linkchat, /unlinkchat, and an unlinked member of a bound group
# ------------------------------------------------------------------------------------- #
GROUP = "-100888"
ADMIN_TG = "424242424"  # linked as admin in account A by `wired`


def _commit_link(_pg_engine, account_id, telegram_user_id: int, role: str) -> None:
    """A committed user + membership(role) + Telegram link in `account_id`."""
    with _pg_engine.begin() as conn:
        uid, mid = uuid.uuid4(), uuid.uuid4()
        conn.execute(text("INSERT INTO users (id, google_sub, email) VALUES (:u, :s, :e)"),
                     {"u": uid, "s": f"sub-{uid.hex[:10]}", "e": f"wh-{uid.hex[:8]}@example.com"})
        conn.execute(text("INSERT INTO memberships (id, account_id, user_id, role, status) "
                          "VALUES (:m, :a, :u, :r, 'active')"),
                     {"m": mid, "a": account_id, "u": uid, "r": role})
        conn.execute(text("INSERT INTO telegram_links (id, account_id, membership_id, telegram_user_id) "
                          "VALUES (:i, :a, :m, :t)"),
                     {"i": uuid.uuid4(), "a": account_id, "m": mid, "t": telegram_user_id})


def _say(client, update_id: int, sender: str, text_: str, chat: str = GROUP):
    r = client.post("/webhooks/telegram",
                    content=_update(update_id, sender_id=sender, chat_id=chat, text_=text_),
                    headers={SECRET_HEADER: SECRET})
    assert r.status_code == 200
    return r


def _bound_to(_pg_engine, chat: str = GROUP):
    with _pg_engine.connect() as conn:
        return conn.execute(text("SELECT account_id FROM telegram_chat_bindings WHERE chat_id = :c"),
                            {"c": int(chat)}).scalar_one_or_none()


@pytest.fixture
def unbind_all(_pg_engine):
    yield
    with _pg_engine.begin() as conn:
        conn.execute(text("DELETE FROM telegram_chat_bindings WHERE chat_id = :c"), {"c": int(GROUP)})


def test_linkchat_binds_and_an_unlinked_member_is_logged_without_a_prompt(
    web_client_factory, _pg_engine, app_role, account_a, wired, sent, unbind_all
):
    """The Belle case, on the production role: staff without logins report through the group."""
    from mihomes import db
    from mihomes.services.gateways import chat_binding as cb

    db._engine, db._SessionLocal = app_role, None  # restored by `_pin_engine_to_test_db`
    client = web_client_factory()

    _say(client, 9901, ADMIN_TG, "/linkchat")
    assert sent[-1] == (GROUP, cb.LINKED)
    assert _bound_to(_pg_engine) == account_a

    before = _counts(_pg_engine, account_a)
    sent.clear()
    _say(client, 9902, "123123123", "The pool gate latch is broken")  # no link, no login
    assert _counts(_pg_engine, account_a)["issues"] == before["issues"] + 1
    assert not any("recognise" in t for _c, t in sent), "a bound group must never prompt"

    # Conversations page, on the production role: the staff message and the bot's reply are stored.
    with _pg_engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT direction, text FROM chat_messages WHERE account_id = :a AND chat_id = :c "
            "ORDER BY sent_at"), {"a": account_a, "c": GROUP}).all()
    assert ("in", "The pool gate latch is broken") in [tuple(r) for r in rows]
    assert any(r.direction == "out" for r in rows)


def test_an_unlinked_member_is_untrusted(
    web_client_factory, _pg_engine, account_a, wired, sent, unbind_all, monkeypatch
):
    """Binding routes the message; it does not make the sender trusted (M27)."""
    client = web_client_factory()
    _say(client, 9911, ADMIN_TG, "/linkchat")

    expense = {"items": [{"category": "expense_log", "title": "Pool chemicals", "amount": 250}]}
    monkeypatch.setattr("mihomes.services.gateways.telegram.responder.analyze_messages",
                        lambda *a, **k: expense)
    with _pg_engine.connect() as conn:
        before = conn.execute(text("SELECT count(*) FROM transactions WHERE account_id = :a"),
                              {"a": account_a}).scalar_one()
    _say(client, 9912, "123123123", "Paid 250 for pool chemicals")
    with _pg_engine.connect() as conn:
        after = conn.execute(text("SELECT count(*) FROM transactions WHERE account_id = :a"),
                             {"a": account_a}).scalar_one()
    assert after == before


def test_unbound_group_still_prompts_and_writes_nothing(
    web_client_factory, _pg_engine, account_a, wired, sent
):
    client = web_client_factory()
    before = _counts(_pg_engine, account_a)
    _say(client, 9921, "123123123", "The pool gate latch is broken")
    assert any("recognise" in t for _c, t in sent)
    assert _counts(_pg_engine, account_a) == before


def test_only_a_linked_owner_or_admin_may_bind(
    web_client_factory, _pg_engine, account_a, wired, sent, unbind_all
):
    from mihomes.services.gateways import chat_binding as cb

    _commit_link(_pg_engine, account_a, 515151515, "staff")
    client = web_client_factory()

    _say(client, 9931, "123123123", "/linkchat")          # no link at all
    assert sent[-1] == (GROUP, cb.LINK_YOURSELF_FIRST)
    _say(client, 9932, "515151515", "/linkchat@MiHomesBot")  # linked, but staff
    assert sent[-1] == (GROUP, cb.MANAGERS_ONLY)
    dm = json.loads(_update(9933, sender_id=ADMIN_TG, chat_id="777000", text_="/linkchat"))
    dm["message"]["chat"]["type"] = "private"  # `_update` always builds a group
    r = client.post("/webhooks/telegram", content=json.dumps(dm).encode(),
                    headers={SECRET_HEADER: SECRET})
    assert r.status_code == 200
    assert sent[-1] == ("777000", cb.NOT_A_GROUP)
    assert _bound_to(_pg_engine) is None


def test_a_group_bound_elsewhere_is_refused_without_naming_it(
    web_client_factory, _pg_engine, account_a, account_b, wired, sent, unbind_all
):
    from mihomes.services.gateways import chat_binding as cb

    _commit_link(_pg_engine, account_b, 626262626, "owner")
    client = web_client_factory()
    try:
        _say(client, 9941, ADMIN_TG, "/linkchat")
        _say(client, 9942, "626262626", "/linkchat")
        assert sent[-1] == (GROUP, cb.LINKED_ELSEWHERE)
        assert _bound_to(_pg_engine) == account_a
        _say(client, 9943, ADMIN_TG, "/linkchat")
        assert sent[-1] == (GROUP, cb.ALREADY_LINKED)
    finally:
        with _pg_engine.begin() as conn:
            for t in ("telegram_links", "memberships"):
                conn.execute(text(f"DELETE FROM {t} WHERE account_id = :a"), {"a": account_b})  # noqa: S608


def test_unlinkchat_restores_per_sender_resolution(
    web_client_factory, _pg_engine, account_a, wired, sent, unbind_all
):
    from mihomes.services.gateways import chat_binding as cb

    _commit_link(_pg_engine, account_a, 515151516, "staff")
    client = web_client_factory()
    _say(client, 9951, ADMIN_TG, "/linkchat")
    _say(client, 9952, "515151516", "/unlinkchat")
    assert sent[-1] == (GROUP, cb.MANAGERS_ONLY)
    _say(client, 9953, ADMIN_TG, "/unlinkchat")
    assert sent[-1] == (GROUP, cb.UNLINKED)
    assert _bound_to(_pg_engine) is None

    sent.clear()
    _say(client, 9954, "123123123", "Anyone there?")
    assert any("recognise" in t for _c, t in sent)


@pytest.mark.parametrize(
    ("text_", "expected"),
    [("/linkchat", "linkchat"), ("/LinkChat@MiHomesBot", "linkchat"), ("  /unlinkchat ", "unlinkchat"),
     ("/linkchat now", None), ("/link ABC123", None), ("please /linkchat", None), (None, None)],
)
def test_parse_chat_command(text_, expected):
    from mihomes.services.gateways.chat_binding import parse_chat_command

    assert parse_chat_command(text_) == expected


# ------------------------------------------------------------------------------------- #
# Fast acknowledgement — Telegram gets its 200 before the slow AI work runs
# ------------------------------------------------------------------------------------- #
def test_dispatch_runs_after_the_response_off_the_event_loop(
    web_client_factory, _pg_engine, account_a, wired, monkeypatch
):
    """Processing ran inline in this `async def`, so Telegram waited for the AI (~70s on
    evo-dev, past its ~60s timeout → redelivery) and the event loop was blocked meanwhile."""
    import threading

    from mihomes.web.routes import gateways as route

    seen = {}
    real = route._dispatch

    def _spy(account_id, message):
        seen["thread"] = threading.current_thread().name
        return real(account_id, message)

    monkeypatch.setattr(route, "_dispatch", _spy)
    client = web_client_factory()
    before = _counts(_pg_engine, account_a)
    r = client.post("/webhooks/telegram",
                    content=_update(9701, sender_id="424242424", chat_id="-100777", text_="Ack test"),
                    headers={SECRET_HEADER: SECRET})
    assert r.status_code == 200
    assert "thread" in seen, "dispatch never ran"
    assert seen["thread"] != "MainThread" and "AnyIO worker" in seen["thread"], seen
    assert _counts(_pg_engine, account_a)["issues"] == before["issues"] + 1


def test_a_retry_during_processing_is_not_processed_twice(
    web_client_factory, _pg_engine, account_a, wired, monkeypatch
):
    """The id is claimed BEFORE processing: a redelivery that arrives while (or after) the first
    attempt is working — or failed — is skipped, never processed into a duplicate."""
    calls = []

    def _boom(session, messages, property_slug=None):
        calls.append(messages[0]["id"])
        raise RuntimeError("AI provider exploded mid-processing")

    monkeypatch.setattr("mihomes.services.gateways.telegram.responder.process_and_respond", _boom)
    client = web_client_factory()
    body = _update(9702, sender_id="424242424", chat_id="-100777", text_="Retry test")
    for _ in range(2):
        try:
            client.post("/webhooks/telegram", content=body, headers={SECRET_HEADER: SECRET})
        except RuntimeError:
            pass  # a TestClient may re-raise the background task's error; Telegram never sees it
    assert calls == ["-100777:9702"], calls
