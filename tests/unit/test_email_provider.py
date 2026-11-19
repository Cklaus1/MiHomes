"""Email provider Protocol, factory, and ConsoleProvider (SPEC-001 A9, §5.1).

The Protocol is deliberately narrow — a provider transports an already-rendered
message. Template selection and rendering live in EmailService so they happen
once, identically, regardless of vendor. A provider that rendered its own
templates would break failover (BILLING §2.1), so these tests assert the seam as
much as the behaviour.
"""

import dataclasses
import inspect

import pytest

from mihomes.services.email.console_provider import ConsoleProvider
from mihomes.services.email.provider import (
    EmailAuthError,
    EmailProvider,
    EmailProviderError,
    EmailResult,
    EmailSendError,
    get_email_provider,
)
from mihomes.services.email.resend_provider import ResendProvider


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


# --- SPEC-005 Step 1 (D11/N1): the one widening the set makes ---------------------------


def _send_params(func) -> dict[str, inspect.Parameter]:
    """`send`'s parameters, minus `self`."""
    return {n: p for n, p in inspect.signature(func).parameters.items() if n != "self"}


def test_headers_is_the_only_widening():
    """N1 — D11 authorises exactly one additive keyword, and this is the gate on it.

    Asserted as **set equality** against the frozen SPEC-001 signature plus `headers`,
    not as `"headers" in params`. The containment form would pass just as happily after
    someone adds `attachments`, `tags`, `template` or `send_batch` — which is the exact
    drift N1 exists to prevent, because the moment a provider does more than transport a
    rendered message, failover breaks (BILLING §2.1, D1).

    Both concrete implementations are checked, not just the Protocol: an implementation
    that dropped the kwarg would still satisfy `isinstance`, because `runtime_checkable`
    compares attribute *names* and never signatures.
    """
    expected = {"to", "subject", "html", "text", "reply_to", "headers"}

    for owner in (EmailProvider, ConsoleProvider, ResendProvider):
        params = _send_params(owner.send)
        assert set(params) == expected, f"{owner.__name__}.send widened beyond D11"

        headers = params["headers"]
        # Additive means defaulted: every existing call site keeps working untouched.
        assert headers.default is None, f"{owner.__name__} made headers required"
        assert headers.kind is inspect.Parameter.KEYWORD_ONLY


def test_console_provider_prints_the_headers_it_is_given(capsys):
    """Step 1's own verification: `ConsoleProvider` emits a header dict when given one.

    Printed rather than summarised, for the same reason both body parts are printed —
    the dev loop is the only place these are observable before Step 9 wires them up.
    """
    provider = get_email_provider("console")
    provider.send(
        "a@b.com",
        "S",
        "<p>h</p>",
        text="h",
        headers={"List-Unsubscribe": "<https://mihomes.ai/u/tok>"},
    )

    out = capsys.readouterr().out
    assert "List-Unsubscribe: <https://mihomes.ai/u/tok>" in out


def test_a_send_without_headers_mentions_no_unsubscribe(capsys):
    """The default is absence, not an empty header — A18's transactional half in embryo.

    A provider that emitted `List-Unsubscribe:` with an empty value would satisfy "the
    kwarg is optional" while putting an unsubscribe header on every receipt.
    """
    provider = get_email_provider("console")
    provider.send("a@b.com", "S", "<p>h</p>", text="h")

    assert "List-Unsubscribe" not in capsys.readouterr().out
