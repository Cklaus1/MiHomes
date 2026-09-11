"""G15 · §6 Step 15 — the per-tenant config UI (A27). O1/U1 closed 2026-08-20.

F7 is why this exists at all: **SPEC-002 D1 drops local SQLite mode and makes the CLI an operator
tool**, so with no config UI *"a tenant cannot configure anything at all."*

**Masking is a display concern, and the test that matters most is the negative one:**
`get_config` must keep returning the real value, because it is how the AI provider gets its key.
A mask applied there would look like better security and would break the feature.

**Encryption is a storage concern, and it is a different test.** U1 answered O1: secret values are
Fernet-encrypted in the column. Masking and encryption address different threats and neither
substitutes for the other — masking stops a shoulder-surfer and a pasted terminal buffer,
encryption stops a `pg_dump`. So the suite asserts both, and `TestValuesAreCiphertextAtRest` is
the one that proves U1 rather than merely exercising it: **every other test here would still pass
against a no-op cipher.**
"""

from __future__ import annotations

import uuid

import pytest

from mihomes.services import config_service


@pytest.fixture
def secret_key(monkeypatch):
    """A real Fernet key for the duration of one test.

    Generated per test rather than hard-coded: a fixed key in a test file is a key that eventually
    gets copied into a deployment, and the tests do not care which key it is.
    """
    from mihomes import crypto

    monkeypatch.setenv(crypto.SECRET_KEY_ENV, crypto.generate_key())
    return None


@pytest.fixture
def no_secret_key(monkeypatch):
    """No key configured — the state of an install that has not been given one yet."""
    from mihomes import crypto

    monkeypatch.delenv(crypto.SECRET_KEY_ENV, raising=False)
    return None


class TestSecretDetection:
    @pytest.mark.parametrize(
        "key",
        [
            "ai.anthropic_api_key",
            "ai.openai_api_key",
            "telegram.bot_token",
            "some.client_secret",
            "smtp.password",
            "vendor.credential",
            "AI.ANTHROPIC_API_KEY",
        ],
    )
    def test_credentials_are_recognised(self, key):
        """Substring markers, not an allow-list of known key names.

        `configurations` is a free-form KV store, so an allow-list would silently fail to mask
        `ai.anthropic_api_key_backup` the day someone adds it. Over-masking a harmless key is
        cosmetic; under-masking a credential is the bug.
        """
        assert config_service.is_secret(key) is True

    @pytest.mark.parametrize(
        "key",
        ["currency.default", "ai.provider", "ai.model", "weather.default_location"],
    )
    def test_ordinary_settings_are_not_masked(self, key):
        """The negative control — masking everything would make the page useless."""
        assert config_service.is_secret(key) is False

    def test_mask_shows_the_last_four(self):
        """Enough to answer "is this the key I think it is?" and nothing usable.

        The convention every payment form uses, and the reason this beats a fixed row of dots.
        """
        masked = config_service.mask_value("ai.anthropic_api_key", "sk-ant-SECRETVALUE9876")
        assert masked.endswith("9876")
        assert "SECRETVALUE" not in masked

    def test_short_values_reveal_nothing(self):
        """A 4-character "key" is a placeholder or already compromised; showing half helps no one."""
        assert config_service.mask_value("ai.api_key", "abcd") == "••••"

    def test_none_passes_through(self):
        assert config_service.mask_value("ai.api_key", None) is None


class TestMaskingIsDisplayOnly:
    @pytest.fixture(autouse=True)
    def _key(self, monkeypatch):
        """These tests write secrets to exercise **masking**, which post-U1 needs a key present.

        Autouse on the class rather than added to each signature: the key is incidental to what
        this class asserts. Switching these to a non-secret key would have been the other way to
        make them pass, and it would have quietly stopped them testing masking at all.
        """
        from mihomes import crypto

        monkeypatch.setenv(crypto.SECRET_KEY_ENV, crypto.generate_key())

    def test_get_config_still_returns_the_real_value(self, session, account_a):
        """**The most important test in this module.**

        `get_config` is how the AI provider gets its API key. Masking there would look like
        better security and would break every AI call — and it would fail *at runtime, in
        production*, as an authentication error from the provider rather than as a test failure.
        """
        config_service.set_config(session, "ai.anthropic_api_key", "sk-ant-REALVALUE1234")

        assert config_service.get_config(session, "ai.anthropic_api_key") == (
            "sk-ant-REALVALUE1234"
        )

    def test_secrets_masked(self, session, account_a):
        """A27 — masked in the display listing, which is what the page and the CLI render."""
        config_service.set_config(session, "ai.anthropic_api_key", "sk-ant-REALVALUE1234")

        rows = {r["key"]: r for r in config_service.list_config_for_display(session)}
        assert "REALVALUE" not in rows["ai.anthropic_api_key"]["value"]
        assert rows["ai.anthropic_api_key"]["value"].endswith("1234")
        assert rows["ai.anthropic_api_key"]["secret"] is True

    def test_unmasked_listing_still_exists_for_app_paths(self, session, account_a):
        """`list_config` is deliberately left unmasked, and that is not an oversight.

        A boolean flag defaulting to "unmasked" is precisely how `mihomes config list` ended up
        printing raw API keys. Two named functions mean a caller has to *choose*.
        """
        config_service.set_config(session, "ai.anthropic_api_key", "sk-ant-REALVALUE1234")
        raw = {r["key"]: r["value"] for r in config_service.list_config(session)}
        assert raw["ai.anthropic_api_key"] == "sk-ant-REALVALUE1234"


class TestSettingsPage:
    def test_staff_denied(self, web_client_as, session, account_a):
        """A27 — staff get 403 from the enforcement dependency, before the route body runs."""
        client = web_client_as("staff", scoped_to=[])
        assert client.get("/settings").status_code == 403

    @pytest.mark.parametrize("role", ["owner", "admin"])
    def test_privileged_can_read_settings(self, web_client_as, role):
        """The positive control: row 2's grant pattern is owner ✓, admin ✓, staff ✗."""
        client = web_client_as(role)
        response = client.get("/settings")
        assert response.status_code == 200
        assert "Settings" in response.text

    def test_the_page_never_renders_a_raw_secret(self, web_client_as):
        """End to end, on the rendered HTML.

        The unit tests prove `mask_value` works; this proves the *page* calls it. G8 taught the
        difference — `redact_for_role` passed its unit tests for two whole groups while nothing
        called it.
        """
        from sqlalchemy import text

        client = web_client_as("owner")
        account_id = web_client_as.connection.execute(
            text("SELECT account_id FROM memberships LIMIT 1")
        ).scalar_one()
        web_client_as.connection.execute(
            text(
                "INSERT INTO configurations (account_id, key, value) VALUES "
                "(:a, 'ai.anthropic_api_key', 'sk-ant-LEAKME99887766')"
            ),
            {"a": account_id},
        )

        body = client.get("/settings").text
        assert "LEAKME" not in body, "the settings page rendered a raw credential"
        assert "sk-ant" not in body


class TestSecretWriteNowSucceeds:
    """N11's blanket refusal is lifted — narrowed, not deleted.

    Phase 2 refused *every* secret write from this form because O1 was unanswered. U1 answered it,
    so the refusal now applies only where the answer does not reach: no configured key.
    """

    def test_secret_write_succeeds_when_a_key_is_configured(self, web_client_as, secret_key):
        client = web_client_as("owner")
        response = client.post(
            "/settings", data={"key": "ai.anthropic_api_key", "value": "sk-ant-NEW1234"},
            follow_redirects=False,
        )
        assert response.status_code == 303, (
            f"expected the write to succeed now that O1 is closed, got {response.status_code}"
        )

    def test_the_written_secret_reads_back_intact(self, web_client_as, secret_key):
        """Encryption is worthless if it corrupts the value — the AI provider needs the real key."""
        client = web_client_as("owner")
        client.post(
            "/settings", data={"key": "ai.anthropic_api_key", "value": "sk-ant-ROUNDTRIP99"},
            follow_redirects=False,
        )
        session = web_client_as.session_for_scope()
        assert config_service.get_config(session, "ai.anthropic_api_key") == "sk-ant-ROUNDTRIP99"

    def test_non_secret_write_succeeds(self, web_client_as):
        """The other half: refusing *everything* would make the page decorative.

        Without this, "no config UI" and "a config UI that refuses all writes" are the same
        product, and F7's complaint — a tenant cannot configure anything — would still hold.
        """
        client = web_client_as("owner")
        response = client.post(
            "/settings", data={"key": "currency.default", "value": "GBP"},
            follow_redirects=False,
        )
        assert response.status_code == 303


class TestSecretWriteStillRefusedWithoutAKey:
    """The surviving arm of N11, and the reason it survives.

    With no key the only way to honour the write is plaintext — through a form whose user believes
    the value is protected. Refusing is the same decision Phase 2 made, for a narrower reason.
    """

    def test_refusal_names_the_environment_variable(self, web_client_as, no_secret_key):
        client = web_client_as("owner")
        response = client.post(
            "/settings", data={"key": "ai.anthropic_api_key", "value": "sk-ant-NEW"}
        )

        assert response.status_code == 400
        assert "MIHOMES_SECRET_KEY" in response.text, (
            "the refusal must name the variable, or it reads as a bug rather than as a fixable "
            "configuration problem"
        )

    def test_the_refusal_does_not_write_the_value(self, web_client_as, no_secret_key):
        """Belt and braces: a 400 that had already written the row would be the worst outcome —
        the credential stored *and* the user told it was not."""
        from sqlalchemy import text

        client = web_client_as("owner")
        client.post("/settings", data={"key": "smtp.password", "value": "hunter2"})

        stored = web_client_as.connection.execute(
            text("SELECT count(*) FROM configurations WHERE key = 'smtp.password'")
        ).scalar_one()
        assert stored == 0


class TestValuesAreCiphertextAtRest:
    """**The test that proves U1.** Everything else here passes against a no-op cipher.

    Reads the column with raw SQL rather than through the ORM, deliberately: the service layer is
    the thing under test, so asking it whether it encrypted is circular. This is the assertion that
    corresponds to the actual threat — someone reading the database, not someone using the app.
    """

    def test_the_stored_column_holds_no_plaintext(self, web_client_as, secret_key):
        from sqlalchemy import text

        session = web_client_as.session_for_scope()
        config_service.set_config(session, "ai.anthropic_api_key", "sk-ant-PLAINTEXTCANARY")
        session.commit()

        raw = web_client_as.connection.execute(
            text("SELECT value FROM configurations WHERE key = 'ai.anthropic_api_key'")
        ).scalar_one()

        assert "PLAINTEXTCANARY" not in raw, (
            "the credential is readable in the database — this is precisely what U1 exists to "
            "prevent, and a pg_dump would disclose it"
        )
        assert raw.startswith("enc:v1:"), (
            f"expected a versioned ciphertext marker, got {raw[:20]!r}. The prefix is what lets a "
            "later version distinguish formats without guessing."
        )

    def test_non_secrets_are_left_readable(self, web_client_as, secret_key):
        """Operational state stays plaintext, on purpose.

        `telegram.last_update_id` is written once per poll and the dedup id list on every message;
        encrypting them would put a Fernet round-trip on the bot's hot path to protect values that
        are not secrets. The marker list is the boundary and this pins that it is being applied.
        """
        from sqlalchemy import text

        session = web_client_as.session_for_scope()
        config_service.set_config(session, "currency.default", "GBP")
        session.commit()

        raw = web_client_as.connection.execute(
            text("SELECT value FROM configurations WHERE key = 'currency.default'")
        ).scalar_one()
        assert raw == "GBP"


class TestLegacyPlaintextStillReadable:
    """The compatibility path that makes a phased rollout possible.

    Rows written before encryption existed — or imported from a pre-Postgres SQLite database, which
    `services/importer.py` Core-inserts directly — carry no prefix. Reading them is how the system
    keeps working before `mihomes config encrypt-secrets` is run. Refusing them instead would take
    the AI and the bot down on upgrade, which is a worse failure than the one it prevents.
    """

    @staticmethod
    def _plant_legacy(web_client_as, account_a, key, value):
        """A pre-encryption row, written with raw SQL so no service-layer code touches it.

        `account_id` is the `account_a` fixture, taken as an argument rather than queried. Two
        wrong versions came before this one, and both failed for reasons unrelated to what the
        tests assert: `(SELECT id FROM accounts LIMIT 1)` can pick an account the client is not
        bound to, and `SELECT account_id FROM memberships` returns nothing until
        `web_client_as(role)` has been *called* — the factory is what inserts the membership. The
        fixture is the account the factory binds to, so it is the one source that is right before
        any client exists.
        """
        from sqlalchemy import text

        web_client_as.connection.execute(
            text("INSERT INTO configurations (account_id, key, value) VALUES (:a, :k, :v)"),
            {"a": account_a, "k": key, "v": value},
        )

    def test_unprefixed_value_reads_back_unchanged(
        self, web_client_as, account_a, secret_key
    ):
        self._plant_legacy(web_client_as, account_a, "ai.openai_api_key", "sk-LEGACYPLAIN")
        session = web_client_as.session_for_scope()
        assert config_service.get_config(session, "ai.openai_api_key") == "sk-LEGACYPLAIN"

    def test_legacy_value_is_still_masked_on_the_page(
        self, web_client_as, account_a, secret_key
    ):
        """A legacy row must not be *more* exposed than an encrypted one on the display path."""
        self._plant_legacy(web_client_as, account_a, "ai.openai_api_key", "sk-LEGACYPLAIN")
        client = web_client_as("owner")
        body = client.get("/settings").text
        assert "LEGACYPLAIN" not in body
        assert "••••LAIN" in body, (
            "the masked form should be present — asserting only the absence of the plaintext "
            "passes vacuously if the page 500s or renders nothing"
        )


class TestEncryptExistingSecrets:
    """The conversion command. Deliberately not a migration — see the service docstring."""

    @staticmethod
    def _plant_legacy(web_client_as, account_a, key, value):
        """A pre-encryption row, written with raw SQL so no service-layer code touches it.

        `account_id` is the `account_a` fixture, taken as an argument rather than queried. Two
        wrong versions came before this one, and both failed for reasons unrelated to what the
        tests assert: `(SELECT id FROM accounts LIMIT 1)` can pick an account the client is not
        bound to, and `SELECT account_id FROM memberships` returns nothing until
        `web_client_as(role)` has been *called* — the factory is what inserts the membership. The
        fixture is the account the factory binds to, so it is the one source that is right before
        any client exists.
        """
        from sqlalchemy import text

        web_client_as.connection.execute(
            text("INSERT INTO configurations (account_id, key, value) VALUES (:a, :k, :v)"),
            {"a": account_a, "k": key, "v": value},
        )

    def test_it_converts_a_legacy_row(self, web_client_as, account_a, secret_key):
        from sqlalchemy import text

        self._plant_legacy(web_client_as, account_a, "telegram.bot_token", "123:LEGACYTOKEN")
        session = web_client_as.session_for_scope()

        converted = config_service.encrypt_existing_secrets(session)
        session.commit()

        assert "telegram.bot_token" in converted
        raw = web_client_as.connection.execute(
            text("SELECT value FROM configurations WHERE key = 'telegram.bot_token'")
        ).scalar_one()
        assert raw.startswith("enc:v1:")
        assert "LEGACYTOKEN" not in raw
        # And it is still usable afterwards — a conversion that broke the value would be worse
        # than leaving it plaintext.
        assert config_service.get_config(session, "telegram.bot_token") == "123:LEGACYTOKEN"

    def test_it_is_idempotent(self, web_client_as, account_a, secret_key):
        """Safe in a deploy script, which is the only way it will actually get run."""
        self._plant_legacy(web_client_as, account_a, "telegram.bot_token", "123:LEGACYTOKEN")
        session = web_client_as.session_for_scope()

        first = config_service.encrypt_existing_secrets(session)
        second = config_service.encrypt_existing_secrets(session)

        assert first == ["telegram.bot_token"]
        assert second == [], "a second run must convert nothing — not double-wrap"

    def test_it_leaves_non_secrets_alone(self, web_client_as, account_a, secret_key):
        self._plant_legacy(web_client_as, account_a, "weather.default_location", "Ibiza")
        session = web_client_as.session_for_scope()
        assert config_service.encrypt_existing_secrets(session) == []


class TestMissingKeyFailsLoudly:
    """The failure mode this module exists to avoid is the *quiet* one.

    Returning the ciphertext, or `None`, when no key is configured turns a missing environment
    variable into an authentication error three layers away — at the Anthropic client, complaining
    about an invalid key. Loud and local beats quiet and distant.
    """

    def test_reading_an_encrypted_value_without_a_key_raises(
        self, web_client_as, secret_key, monkeypatch
    ):
        from mihomes import crypto

        session = web_client_as.session_for_scope()
        config_service.set_config(session, "ai.anthropic_api_key", "sk-ant-WILLBELOCKED")
        session.commit()

        monkeypatch.delenv(crypto.SECRET_KEY_ENV, raising=False)
        with pytest.raises(crypto.UndecryptableValue):
            config_service.get_config(session, "ai.anthropic_api_key")

    def test_the_wrong_key_raises_rather_than_returning_garbage(
        self, web_client_as, secret_key, monkeypatch
    ):
        from mihomes import crypto

        session = web_client_as.session_for_scope()
        config_service.set_config(session, "ai.anthropic_api_key", "sk-ant-WILLBELOCKED")
        session.commit()

        monkeypatch.setenv(crypto.SECRET_KEY_ENV, crypto.generate_key())
        with pytest.raises(crypto.UndecryptableValue):
            config_service.get_config(session, "ai.anthropic_api_key")

    def test_writing_a_secret_without_a_key_raises(self, web_client_as, no_secret_key):
        from mihomes import crypto

        session = web_client_as.session_for_scope()
        with pytest.raises(crypto.EncryptionUnavailable):
            config_service.set_config(session, "smtp.password", "hunter2")


class TestRenamingTheEstate:
    """The rename onboarding promised and the app could not do.

    Step 2's household-name field carries the caption *"You can change this later."* Nothing in
    the app wrote `accounts.name` after creation — `/settings` offered only a raw config
    key/value form — so the only way to rename an estate was `UPDATE accounts SET name = …` by
    hand. Reported from a live install: the user went looking for the rename, landed back in the
    onboarding wizard, and got a 500.
    """

    def test_owner_can_rename_and_the_page_shows_it(self, web_client_as):
        client = web_client_as("owner")

        response = client.post(
            "/settings/account", data={"name": "Belle Estate"}, follow_redirects=False
        )
        assert response.status_code == 303, response.text

        page = client.get("/settings")
        assert "Belle Estate" in page.text

    def test_the_slug_does_not_change(self, web_client_as):
        """**The slug is the stable identifier and must survive a rename.**

        It is what `--account <slug>` names on every CLI invocation and what `resolve_account`
        looks installs up by, so rebuilding it from the new name would break whatever the
        operator had scripted — the kind of breakage that surfaces later and elsewhere. It is
        also UNIQUE, so a rebuild could collide and fail a rename that has nothing wrong with it.
        """
        from sqlalchemy import text

        client = web_client_as("owner")
        conn = web_client_as.connection
        before = conn.execute(text("select slug from accounts limit 1")).scalar()

        client.post("/settings/account", data={"name": "Something Else Entirely"})

        after = conn.execute(text("select slug from accounts limit 1")).scalar()
        assert after == before, (
            f"the slug changed from {before!r} to {after!r} — every `--account {before}` "
            f"invocation and any script using it now fails"
        )

    def test_staff_cannot_rename(self, web_client_as):
        """Same gate as the config form: owner ✓, admin ✓, staff ✗, from the dependency."""
        client = web_client_as("staff", scoped_to=[])
        response = client.post(
            "/settings/account", data={"name": "Staff Renamed This"}, follow_redirects=False
        )
        assert response.status_code == 403

    def test_an_empty_name_is_refused_not_written(self, web_client_as):
        """Whitespace only is empty. The refusal re-renders the page rather than 500ing."""
        from sqlalchemy import text

        client = web_client_as("owner")
        conn = web_client_as.connection
        before = conn.execute(text("select name from accounts limit 1")).scalar()

        response = client.post(
            "/settings/account", data={"name": "   "}, follow_redirects=False
        )
        assert response.status_code == 400
        assert "cannot be empty" in response.text

        after = conn.execute(text("select name from accounts limit 1")).scalar()
        assert after == before, "the name was cleared despite the refusal"

    def test_an_overlong_name_is_refused_readably(self, web_client_as):
        """Enforced in the service, so it is a readable message rather than a driver error.

        Left to the column, this is `StringDataRightTruncation` raised three frames inside a
        flush — a 500 the user cannot act on.
        """
        client = web_client_as("owner")
        response = client.post(
            "/settings/account", data={"name": "x" * 201}, follow_redirects=False
        )
        assert response.status_code == 400
        assert "too long" in response.text

    def test_the_rename_is_audited(self, web_client_as):
        """Who renamed the estate, and what it was called before — otherwise unrecoverable."""
        from sqlalchemy import text

        client = web_client_as("owner")
        client.post("/settings/account", data={"name": "Audited Estate"})

        row = web_client_as.connection.execute(
            text(
                "select action, changes from audit_log "
                "where entity_type = 'account' and action = 'rename' "
                "order by timestamp desc limit 1"
            )
        ).first()
        assert row is not None, "the rename left no audit entry"
        assert row.changes["name"]["to"] == "Audited Estate"

    def test_resubmitting_the_same_name_writes_no_audit_row(self, web_client_as):
        """A form re-submit is not a change, and should not fill the trail with no-ops."""
        from sqlalchemy import text

        client = web_client_as("owner")
        client.post("/settings/account", data={"name": "Stable Name"})

        def rename_rows() -> int:
            return web_client_as.connection.execute(
                text(
                    "select count(*) from audit_log "
                    "where entity_type = 'account' and action = 'rename'"
                )
            ).scalar()

        before = rename_rows()
        client.post("/settings/account", data={"name": "Stable Name"})
        assert rename_rows() == before, "an unchanged re-submit recorded an audit event"


class TestSettingsIsReachable:
    """**The page existed and nothing linked to it**, which is the same as not existing.

    `/settings` shipped with SPEC-003 G15 and the estate rename was added to it, but no
    template carried an `href="/settings"` — so it was reachable only by typing the URL. The
    feature was reported missing by someone looking at the dashboard, which is the only
    evidence that matters for a nav link.
    """

    def test_the_sidebar_links_to_settings(self, web_client_as):
        client = web_client_as("owner")
        page = client.get("/")
        assert 'href="/settings"' in page.text, (
            "no link to /settings anywhere in the chrome — the page is reachable only by "
            "typing the URL, which from the outside is the feature not existing"
        )


class TestEditingYourOwnProfile:
    """Name and sign-in email, for the signed-in user only."""

    @staticmethod
    def _email_of(conn):
        """The fixture mints a random `owner-<hex>@example.com`, so it must be read, not
        assumed — posting a different address reads as an email *change* and takes the
        password path."""
        from sqlalchemy import text

        return conn.execute(text("select email from users limit 1")).scalar()

    def test_a_name_change_needs_no_password(self, web_client_as):
        """Renaming yourself is a display tweak. Demanding a password for it trains people to
        type their password for trivial things, which is its own hazard."""
        client = web_client_as("owner")
        response = client.post(
            "/settings/profile",
            data={
                "name": "Renamed Person",
                "email": self._email_of(web_client_as.connection),
            },
            follow_redirects=False,
        )
        assert response.status_code == 303, response.text
        assert "Renamed Person" in client.get("/settings").text

    def test_changing_the_email_requires_the_current_password(self, web_client_as):
        """**The guard that matters.** The email is the sign-in credential, so a borrowed
        session must not be able to move the account to an attacker's address.

        The fixture's user signs in with Google, so it is given a password here first — a
        Google identity has no password to verify and is refused earlier, by
        `test_a_google_identity_cannot_change_its_email`.
        """
        from sqlalchemy import text

        from mihomes.auth.passwords import hash_password

        client = web_client_as("owner")
        conn = web_client_as.connection
        conn.execute(
            text("update users set password_hash = :h, password_set_at = now()"),
            {"h": hash_password("correct horse battery staple")},
        )
        before = conn.execute(text("select email from users limit 1")).scalar()

        response = client.post(
            "/settings/profile",
            data={
                "name": "Someone",
                "email": "attacker@example.com",
                "current_password": "not-the-password",
            },
            follow_redirects=False,
        )
        assert response.status_code == 400
        assert "password is not correct" in response.text

        after = conn.execute(text("select email from users limit 1")).scalar()
        assert after == before, "the email changed despite the wrong password"

    def test_a_google_identity_cannot_change_its_email(self, web_client_as):
        """A Google account has no password to verify and its address is managed upstream, so
        the change is refused with a reason rather than silently failing verification."""
        client = web_client_as("owner")
        response = client.post(
            "/settings/profile",
            data={"name": "Someone", "email": "elsewhere@example.com"},
            follow_redirects=False,
        )
        assert response.status_code == 400
        assert "managed by Google" in response.text or "Google" in response.text

    def test_a_malformed_email_is_refused(self, web_client_as):
        client = web_client_as("owner")
        response = client.post(
            "/settings/profile",
            data={"name": "Someone", "email": "not-an-email"},
            follow_redirects=False,
        )
        assert response.status_code == 400
        assert "does not look like an email" in response.text

    def test_an_empty_name_is_refused(self, web_client_as):
        client = web_client_as("owner")
        response = client.post(
            "/settings/profile",
            data={"name": "   ", "email": self._email_of(web_client_as.connection)},
            follow_redirects=False,
        )
        assert response.status_code == 400
        assert "cannot be empty" in response.text

    def test_the_route_edits_only_the_signed_in_user(self, web_client_as):
        """**Why a permission open to every role is safe here.**

        The handler reads `principal.user_id` and accepts no user id from the request, so there
        is no version of this call that edits somebody else. A stray `user_id` field must be
        ignored rather than honoured.
        """
        from sqlalchemy import text

        client = web_client_as("owner")
        conn = web_client_as.connection
        victim = conn.execute(
            text(
                "insert into users (id, email, name, created_at) values "
                "(gen_random_uuid(), 'victim@example.com', 'Victim', now()) returning id"
            )
        ).scalar()

        client.post(
            "/settings/profile",
            data={
                "name": "Hijacked",
                "email": self._email_of(conn),
                "user_id": str(victim),
            },
            follow_redirects=False,
        )

        still = conn.execute(
            text("select name from users where id = :i"), {"i": victim}
        ).scalar()
        assert still == "Victim", "the route honoured a user id from the request body"


class TestMembersSection:
    """The rendered page and the five routes behind it.

    Separate from the service tests in `test_settings_members.py` for the reason G8 taught:
    `redact_for_role` passed its unit tests for two whole groups while nothing called it. These
    go through HTTP, so they fail if the wiring is wrong even when every service is correct.
    """

    def test_the_page_lists_members(self, web_client_as):
        client = web_client_as("owner")
        response = client.get("/settings")

        assert response.status_code == 200
        assert "Members" in response.text
        # The signed-in owner is a member, so their own address must be on the page.
        assert "owner-" in response.text

    def test_invite_renders_the_link_once(self, web_client_as):
        """**The defect this feature exists to fix**, asserted end to end.

        `create_invite` hands back a token that exists nowhere else — `team.py:114` drops it and
        no invite email template exists. If this route redirected like that one does, the
        invitation would consume a seat and produce a link nobody could retrieve. The response
        body is the only delivery mechanism, so the response body is what gets asserted.
        """
        client = web_client_as("owner")
        response = client.post(
            "/settings/members/invite",
            data={"email": "newcomer@example.com", "role": "admin"},
        )

        assert response.status_code == 200, "must render, not redirect — the token is in the body"
        assert "/invite/" in response.text, "the invitation link must be shown"
        assert "newcomer@example.com" in response.text

    def test_staff_invite_without_properties_is_refused_on_the_page(self, web_client_as):
        """A21/D3 surfaces as a form error rather than a 500."""
        client = web_client_as("owner")
        response = client.post(
            "/settings/members/invite",
            data={"email": "housekeeper@example.com", "role": "staff"},
        )

        assert response.status_code == 400
        assert "at least one property" in response.text

    def test_admin_cannot_revoke_themselves(self, web_client_as):
        """The lockout guard — one click, no confirmation, no way back in.

        R1 covers role changes but `offboard` has no self-check, so this is the only thing
        standing between an admin and locking themselves out of the estate. The template omits
        the button; this posts anyway, because the form is not the gate.
        """
        client = web_client_as("admin")
        response = client.post(f"/settings/members/{client.membership_id}/revoke")

        assert response.status_code == 400
        assert "your own access" in response.text

        # And they are still in.
        assert client.get("/settings").status_code == 200

    def test_a_membership_from_another_estate_is_not_found(self, web_client_as):
        """D9's reasoning: ids arrive from a form, so an unknown one must not be actionable.

        A random uuid stands in for another account's membership — the response must be
        identical either way, or the pair of responses enumerates which ids are real.
        """
        client = web_client_as("owner")
        stranger = uuid.uuid4()

        assert client.post(f"/settings/members/{stranger}/revoke").status_code == 404
        assert client.post(
            f"/settings/members/{stranger}/role", data={"role": "staff"}
        ).status_code == 404

    def test_staff_never_reach_any_member_route(self, web_client_as):
        """Rows 10-13 are all `DENY` for staff, applied by the dependency before the body runs."""
        client = web_client_as("staff", scoped_to=[])
        target = uuid.uuid4()

        assert client.get("/settings").status_code == 403
        assert client.post(
            "/settings/members/invite", data={"email": "x@example.com", "role": "admin"}
        ).status_code == 403
        assert client.post(
            f"/settings/members/{target}/role", data={"role": "admin"}
        ).status_code == 403
        assert client.post(f"/settings/members/{target}/revoke").status_code == 403

    def test_billing_link_is_owner_only(self, web_client_as):
        """`billing.manage` is owner-only (row 15) — an admin runs the estate, not the card.

        A link an admin cannot follow would be a 403 dressed as a feature, so the page omits it
        rather than rendering one that refuses.
        """
        assert "/billing" in web_client_as("owner").get("/settings").text
        assert "/billing" not in web_client_as("admin").get("/settings").text
