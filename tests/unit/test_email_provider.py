"""Email provider Protocol, factory, and ConsoleProvider (SPEC-001 A9, §5.1).

The Protocol is deliberately narrow — a provider transports an already-rendered
message. Template selection and rendering live in EmailService so they happen
once, identically, regardless of vendor. A provider that rendered its own
templates would break failover (BILLING §2.1), so these tests assert the seam as
much as the behaviour.
"""

import dataclasses

import pytest

from mihomes.services.email.provider import (
    EmailAuthError,
    EmailProvider,
    EmailProviderError,
    EmailResult,
    EmailSendError,
    get_email_provider,
)


def test_unknown_provider_raises():
    """A9 — the factory must reject an unknown name, not fall back silently.

    A silent default would send real mail through the wrong vendor, or worse,
    swallow the misconfiguration until launch day.
    """
    with pytest.raises(EmailProviderError, match="Unknown email provider"):
        get_email_provider("sendgrid")


def test_exception_hierarchy():
    """Auth and send failures must be distinguishable but share a base.

    EmailService catches EmailSendError specifically (§5.3) and must not
    accidentally swallow an auth misconfiguration, so the two cannot be the
    same type.
    """
    assert issubclass(EmailAuthError, EmailProviderError)
    assert issubclass(EmailSendError, EmailProviderError)
    assert not issubclass(EmailAuthError, EmailSendError)
    assert not issubclass(EmailSendError, EmailAuthError)


def test_console_provider_satisfies_the_protocol():
    provider = get_email_provider("console")
    assert isinstance(provider, EmailProvider)


def test_console_provider_sends_nothing_and_returns_a_result(capsys):
    """The dev/CI provider logs and returns an id; it must not attempt network I/O."""
    provider = get_email_provider("console")

    result = provider.send(
        "someone@example.com",
        "Subject line",
        "<p>hello</p>",
        text="hello",
    )

    assert isinstance(result, EmailResult)
    assert result.provider == "console"
    assert result.provider_message_id

    out = capsys.readouterr().out
    assert "someone@example.com" in out
    assert "Subject line" in out


def test_console_provider_prints_both_parts(capsys):
    """Both parts must be visible in dev — that is how the confirm loop is tested.

    Step 7's verification is "submit → token in console → GET confirm", which only
    works if the console provider actually shows the rendered body.
    """
    provider = get_email_provider("console")
    provider.send("a@b.com", "S", "<p>HTMLBODY</p>", text="TEXTBODY")

    out = capsys.readouterr().out
    assert "HTMLBODY" in out
    assert "TEXTBODY" in out


def test_email_result_is_frozen():
    """A provider result is a record of what happened; mutating it is a bug."""
    result = EmailResult(provider_message_id="abc", provider="console")
    # FrozenInstanceError specifically, not a blind Exception: the latter would
    # also pass on a misspelled attribute name and prove nothing about frozenness.
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.provider_message_id = "changed"  # type: ignore[misc]


def test_default_provider_comes_from_the_environment(monkeypatch):
    """EMAIL_PROVIDER selects the provider; 'resend' is the default (§5.1)."""
    monkeypatch.setenv("EMAIL_PROVIDER", "console")
    assert get_email_provider().provider_name == "console"


def test_resend_provider_requires_an_api_key(monkeypatch):
    """A missing key is an auth error at construction, not a silent no-op send.

    Failing late would mean a launch where signups succeed and no mail arrives.
    """
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    with pytest.raises(EmailAuthError):
        get_email_provider("resend", from_address="MiHomes <no-reply@send.mihomes.ai>")


def test_provider_accepts_a_list_of_recipients():
    """The Protocol's `to` is `str | list[str]` — both must work."""
    provider = get_email_provider("console")
    result = provider.send(["a@b.com", "c@d.com"], "S", "<p>h</p>", text="h")
    assert result.provider == "console"
