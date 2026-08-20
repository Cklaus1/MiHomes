"""G15 · §6 Step 15 — the per-tenant config UI (A27), carrying O1.

F7 is why this exists at all: **SPEC-002 D1 drops local SQLite mode and makes the CLI an operator
tool**, so with no config UI *"a tenant cannot configure anything at all."* Today
`web/routes/ai.py:48` still answers "Run `mihomes ai setup` in the CLI" *to the browser*.

**Masking is a display concern, and the test that matters most is the negative one:**
`get_config` must keep returning the real value, because it is how the AI provider gets its key.
A mask applied there would look like better security and would break the feature.
"""

from __future__ import annotations

import pytest

from mihomes.services import config_service


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


class TestO1SecretWriteRefused:
    """N11 — *"Do not write secret config values to a plaintext column from a new web form
    until O1 is answered."*

    The refusal **is** the Phase 2 behaviour, not a placeholder for it: the same shape as
    SPEC-002's `UnsupportedBackendError` on SQLite, closing a hole by declining to open it.
    """

    def test_secret_write_is_refused_with_a_reason(self, web_client_as):
        client = web_client_as("owner")
        response = client.post(
            "/settings", data={"key": "ai.anthropic_api_key", "value": "sk-ant-NEW"}
        )

        assert response.status_code == 400
        assert "O1" in response.text or "encryption" in response.text, (
            "the refusal must say why, or it reads as a bug rather than a decision"
        )

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

    def test_the_refusal_does_not_write_the_value(self, web_client_as):
        """Belt and braces: a 400 that had already written the row would be the worst outcome —
        the credential stored *and* the user told it was not."""
        from sqlalchemy import text

        client = web_client_as("owner")
        client.post("/settings", data={"key": "smtp.password", "value": "hunter2"})

        stored = web_client_as.connection.execute(
            text("SELECT count(*) FROM configurations WHERE key = 'smtp.password'")
        ).scalar_one()
        assert stored == 0
