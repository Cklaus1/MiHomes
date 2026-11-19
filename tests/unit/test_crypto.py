"""U1 — `mihomes.crypto`, the module that closes SPEC-003 O1.

Unit-level, so it exercises the cases the integration tests cannot reach cheaply: a malformed key,
a foreign ciphertext, the empty string, and the difference between the two error types.

**The tests are organised around the failure modes, not the happy path.** A crypto helper's happy
path is one line and self-evidently correct if it round-trips; what makes it safe or unsafe is what
it does when something is missing or wrong. Every one of those cases here has a named test, because
the tempting implementation of each — return the input, return None, swallow the exception — turns
a local configuration error into a distant authentication failure.
"""

from __future__ import annotations

import pytest

from mihomes import crypto


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv(crypto.SECRET_KEY_ENV, crypto.generate_key())


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv(crypto.SECRET_KEY_ENV, raising=False)


class TestRoundTrip:
    def test_encrypt_then_decrypt_is_identity(self, key):
        assert crypto.decrypt(crypto.encrypt("sk-ant-abc123")) == "sk-ant-abc123"

    def test_ciphertext_carries_the_version_prefix(self, key):
        assert crypto.encrypt("x").startswith("enc:v1:")

    def test_ciphertext_does_not_contain_the_plaintext(self, key):
        """The whole point, asserted directly rather than inferred from the round-trip."""
        assert "CANARY" not in crypto.encrypt("CANARY")

    def test_encryption_is_non_deterministic(self, key):
        """Fernet includes a random IV, so the same input yields different ciphertext.

        Worth pinning because it rules out a whole class of "optimisation": no caller may ever
        compare stored ciphertexts for equality, index on the column, or use it in a WHERE clause.
        `config_service` only ever filters on `key`, and this test is why that must stay true.
        """
        assert crypto.encrypt("same") != crypto.encrypt("same")

    def test_unicode_survives(self, key):
        assert crypto.decrypt(crypto.encrypt("clé-café-🔑")) == "clé-café-🔑"


class TestLegacyPlaintextPassesThrough:
    """The compatibility path. Without it, encryption is a breaking change on upgrade."""

    def test_unprefixed_value_is_returned_unchanged(self, key):
        assert crypto.decrypt("sk-ant-legacyplaintext") == "sk-ant-legacyplaintext"

    def test_unprefixed_value_needs_no_key_at_all(self, no_key):
        """A pre-encryption install must keep working before the key is ever set.

        This is the case that lets `mihomes config encrypt-secrets` be a deliberate operator step
        rather than a hard upgrade gate.
        """
        assert crypto.decrypt("sk-ant-legacyplaintext") == "sk-ant-legacyplaintext"

    def test_none_passes_through(self, key):
        assert crypto.decrypt(None) is None

    def test_a_value_that_merely_mentions_the_prefix_is_not_treated_as_encrypted(self, key):
        """`is_encrypted` is a *prefix* test, not a substring one.

        A config value containing "enc:v1:" in the middle — a URL, a JSON blob — must not be sent
        to the decryptor, where it would raise and take a working setting down with it.
        """
        assert not crypto.is_encrypted("https://example.com/?x=enc:v1:abc")
        assert crypto.decrypt("https://example.com/?x=enc:v1:abc") == (
            "https://example.com/?x=enc:v1:abc"
        )


class TestFailuresAreLoud:
    """Each of these has a quiet alternative, and each quiet alternative is a bug."""

    def test_encrypt_without_a_key_raises(self, no_key):
        """The alternative — returning the plaintext — silently reopens the hole U1 closes."""
        with pytest.raises(crypto.EncryptionUnavailable):
            crypto.encrypt("secret")

    def test_the_error_names_the_variable(self, no_key):
        with pytest.raises(crypto.EncryptionUnavailable, match="MIHOMES_SECRET_KEY"):
            crypto.encrypt("secret")

    def test_decrypt_without_a_key_raises(self, monkeypatch):
        """Never the ciphertext, never `None`.

        Returning either hands an unusable string to the Anthropic client, which reports an
        invalid API key — a message that sends the reader to the wrong problem entirely.
        """
        monkeypatch.setenv(crypto.SECRET_KEY_ENV, crypto.generate_key())
        token = crypto.encrypt("secret")
        monkeypatch.delenv(crypto.SECRET_KEY_ENV)

        with pytest.raises(crypto.UndecryptableValue):
            crypto.decrypt(token)

    def test_the_wrong_key_raises_rather_than_returning_garbage(self, monkeypatch):
        monkeypatch.setenv(crypto.SECRET_KEY_ENV, crypto.generate_key())
        token = crypto.encrypt("secret")
        monkeypatch.setenv(crypto.SECRET_KEY_ENV, crypto.generate_key())

        with pytest.raises(crypto.UndecryptableValue):
            crypto.decrypt(token)

    def test_a_malformed_key_is_reported_as_such(self, monkeypatch):
        """A truncated or hand-edited key must not read as "no key configured".

        Two different fixes — "set the variable" versus "you set it wrong" — so the message has to
        distinguish them or the operator debugs the wrong thing.
        """
        monkeypatch.setenv(crypto.SECRET_KEY_ENV, "not-a-fernet-key")
        with pytest.raises(crypto.EncryptionUnavailable, match="not a valid Fernet key"):
            crypto.encrypt("secret")

    def test_the_two_error_types_are_distinguishable(self, monkeypatch):
        """`EncryptionUnavailable` means "no usable key"; `UndecryptableValue` means "wrong key".

        Callers act differently on them: `config_service.set_config` propagates the first as a
        refusal, while the second can only ever be an operator error on read.
        """
        assert not issubclass(crypto.UndecryptableValue, crypto.EncryptionUnavailable)
        assert not issubclass(crypto.EncryptionUnavailable, crypto.UndecryptableValue)


class TestKeyResolution:
    def test_absent_variable_is_none(self, no_key):
        assert crypto.secret_key() is None

    @pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
    def test_blank_variable_is_treated_as_absent(self, monkeypatch, blank):
        """An exported-but-empty variable is a deployment mistake, not a request to use "" as a key.

        Without this, `Fernet("")` raises a base64 error from deep inside the library instead of
        the actionable "not set" message, and in a container an unset variable very often arrives
        as an empty one.
        """
        monkeypatch.setenv(crypto.SECRET_KEY_ENV, blank)
        assert crypto.secret_key() is None

    def test_generate_key_produces_a_usable_key(self, monkeypatch):
        monkeypatch.setenv(crypto.SECRET_KEY_ENV, crypto.generate_key())
        assert crypto.decrypt(crypto.encrypt("ok")) == "ok"

    def test_generated_keys_are_unique(self):
        assert crypto.generate_key() != crypto.generate_key()
