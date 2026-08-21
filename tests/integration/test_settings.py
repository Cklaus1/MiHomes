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
