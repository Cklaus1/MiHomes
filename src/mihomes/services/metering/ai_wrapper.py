"""`MeteredProvider` — wraps a concrete `AIProvider` and counts every invocation (D17).

**It must proxy the provider's full surface, not merely the declared Protocol** (F8), and the two
reasons are measured rather than defensive:

- `agent.py:41` sets `provider.model = model` — an attribute **write** onto the wrapped instance.
  Without `__setattr__` passing through, that would land on the wrapper and the real provider
  would keep its default model, so a caller asking for Opus would silently get Sonnet.
- `agent.py` reaches for `provider.client` to drive the agentic tool loop, which needs the raw
  `messages.create(..., tools=...)` surface the Protocol deliberately does not expose.

Implementing only `complete`/`structured_output` would therefore either break streaming and the
tool loop, or let them escape the meter — and streaming is the second-highest-token path in the
app. So: explicit metered methods for the three declared ones, `__getattr__` for everything else,
`__setattr__` writing through.

## Construction-time wrapping is equivalent to per-call metering *today*

Metering happens per method invocation, not at construction — but the two would be
indistinguishable if every call site constructed a fresh provider, which is true right now: F10
measured that **no AI provider instance is cached module-level** anywhere in the tree. `A12`
asserts that rather than trusting it to stay true, because the day someone adds a module-level
cache, construction-time counting would silently under-count and per-invocation counting would
keep working.

## Why the session is passed in rather than opened here

A metering write that opened its own session would commit outside the caller's transaction, which
is precisely the race `PRICING` §3.2 rule 5 forbids. The caller supplies the session it is
already inside.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["MeteredProvider"]

#: Methods that dispatch to the vendor API and therefore cost money.
#:
#: Derived-from-the-Protocol would be tempting, but this set is *narrower* than the Protocol on
#: purpose: `supports_images` is a declared attribute that costs nothing, and a future
#: non-dispatching method would be metered by accident. Naming the three is the honest version,
#: and A11 catches the case where a fourth dispatch method is added and forgotten — it enumerates
#: call sites from the tree, not from this list.
METERED_METHODS = ("complete", "structured_output", "stream")

#: Rough tokens an image costs a vision model. Only used when the vendor reports no usage.
_IMAGE_TOKENS_ESTIMATE = 1_600


def _estimate_tokens(args, kwargs, result) -> tuple[int, int]:
    """About 4 characters a token, over every text argument sent and the text that came back.

    The fallback for a call whose usage the vendor did not report. Deliberately rough: its only
    job is to make sure such a call still counts against the allowance.
    """
    import json

    sent = 0
    for value in list(args) + list(kwargs.values()):
        if isinstance(value, str):
            sent += len(value)
        elif isinstance(value, dict):
            sent += len(json.dumps(value, default=str))
        elif isinstance(value, list):
            for att in value:
                if getattr(att, "is_image", False):
                    sent += _IMAGE_TOKENS_ESTIMATE * 4
                else:
                    sent += len(getattr(att, "text_content", "") or "")
    received = len(result if isinstance(result, str) else json.dumps(result, default=str))
    return max(1, sent // 4), max(1, received // 4)


class MeteredProvider:
    """Proxies an `AIProvider`, recording one usage event per dispatch.

    Not a subclass of anything: the Protocol is structural, so the wrapper satisfies it by having
    the methods — the same reason `FakeBillingProvider` needs no base class.
    """

    # Declared at class level so `__setattr__` can tell its own attributes from the wrapped
    # provider's. Without this the first `self._provider = ...` in `__init__` would recurse
    # into `__setattr__`, find no `_provider` yet, and fail.
    _OWN_ATTRS = frozenset({"_provider", "_session_factory", "_account", "_entry_point"})

    def __init__(self, provider, *, session_factory, account, entry_point: str) -> None:
        object.__setattr__(self, "_provider", provider)
        object.__setattr__(self, "_session_factory", session_factory)
        object.__setattr__(self, "_account", account)
        object.__setattr__(self, "_entry_point", entry_point)

    # -- the three metered methods ------------------------------------------------------

    def complete(self, *args, **kwargs):
        self._check()
        result = self._provider.complete(*args, **kwargs)
        self._record("complete", self._tokens(args, kwargs, result))
        return result

    def structured_output(self, *args, **kwargs):
        self._check()
        result = self._provider.structured_output(*args, **kwargs)
        self._record("structured_output", self._tokens(args, kwargs, result))
        return result

    def stream(self, *args, **kwargs):
        """Checked now, recorded **once, after the stream ends** — that is when its tokens are known.

        The ceiling check stays eager (here, not inside the generator) so a denied call raises
        before any streaming starts, as before. The record moves to the generator's `finally`:
        a caller that abandons the stream halfway is still counted (the provider billed for it),
        with whatever usage is known, or an estimate of what was sent and received. A stream that
        fails before producing anything is not counted, matching `complete`.
        """
        self._check()
        return self._metered_stream(args, kwargs)

    def _metered_stream(self, args, kwargs):
        pieces: list[str] = []
        failed = False
        try:
            for chunk in self._provider.stream(*args, **kwargs):
                pieces.append(chunk)
                yield chunk
        except Exception:
            failed = True
            raise
        finally:
            # GeneratorExit (an abandoned stream) is a BaseException, so `failed` stays False.
            if not failed or pieces:
                self._record("stream", self._tokens(args, kwargs, "".join(pieces)))

    def _tokens(self, args, kwargs, result) -> tuple[int, int]:
        """`(tokens_in, tokens_out)`: the vendor's count, or an estimate — **never zero**.

        A provider that returned no usage (Ollama, an abandoned stream, a vendor that dropped the
        field) must still use up allowance, or a missing usage block would mean unlimited AI.
        """
        usage = getattr(self._provider, "last_usage", None)
        if usage is not None:
            return usage
        return _estimate_tokens(args, kwargs, result)

    # -- full-surface proxying (F8) -----------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        """Undeclared attributes and methods pass through to the wrapped provider.

        Reached only when normal lookup fails, so the metered methods above win. This is what
        carries `client` (the agentic loop's raw SDK handle) and `supports_images` (H13's vision
        capability flag) without either being enumerated here.
        """
        return getattr(object.__getattribute__(self, "_provider"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        """Writes go **through** to the wrapped provider — `agent.py:41`'s `provider.model = …`.

        Storing them on the wrapper instead would be the subtlest possible bug in this file: the
        assignment succeeds, nothing raises, and the provider quietly uses the wrong model for
        every subsequent call.
        """
        if name in self._OWN_ATTRS:
            object.__setattr__(self, name, value)
        else:
            setattr(object.__getattribute__(self, "_provider"), name, value)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<MeteredProvider {self._provider!r} entry_point={self._entry_point}>"

    # -- recording ----------------------------------------------------------------------

    def _check(self) -> None:
        """Enforce the hard ceiling **before** the provider is invoked (§5.3, A14).

        Raises `EntitlementError` carrying the `Denied`, so the caller can render §5.3's message
        — *"AI paused until &lt;resets_at&gt; — upgrade to continue"* — with the usage meter and
        reset date, rather than a bare failure. The same exception type `property.add` raises, so
        one handler covers both kinds of plan denial.

        **A ceiling check that failed open would be the single most expensive bug in this phase**,
        so unlike `_record` this does not swallow. `_record` swallowing is right — the answer
        already exists and the vendor already billed — and the asymmetry is deliberate: an
        under-count costs a little revenue, an unenforced ceiling costs unbounded inference.
        """
        if self._account is None:
            return  # system-initiated (N10) — never metered, never denied
        from mihomes.entitlements.service import Denied
        from mihomes.services.metering.meter import check_and_reserve

        # **Only the lookup is wrapped, and that boundary is the whole design.**
        #
        # A `Denied` must still raise — putting the `isinstance` check inside this `try` would
        # swallow `EntitlementError` and silently remove A14's teeth, which is the specific way
        # this fix could go wrong.
        #
        # Failing *open* on an infrastructure error rather than closed, because "the database is
        # unreachable" is not the asymmetry this file is otherwise built around: it is **no
        # information**. Measured before choosing — `web/routes/ai.py:460` reads the provider API
        # key from the database *before* a provider exists, so a dead database already fails the
        # request. The real choice is between failing with a confusing billing error and failing
        # with a database error, not between capped and uncapped.
        #
        # The residual is bounded and recorded in the harness §0.8: a metering-infrastructure
        # outage lifts the AI ceiling for its duration.
        try:
            with self._session_factory() as session:
                decision = check_and_reserve(
                    session, self._account, entry_point=self._entry_point
                )
        except Exception:
            logger.exception(
                "ceiling check failed for entry_point=%s — allowing the call; see the AI "
                "ceiling residual in the SPEC-004 harness §0.8",
                self._entry_point,
            )
            return

        if isinstance(decision, Denied):
            from mihomes.services.property import EntitlementError

            raise EntitlementError(decision)

    def _record(self, method: str, tokens: tuple[int, int] | None = None) -> None:
        """Write the usage event. **Never raises into the caller.**

        A metering failure must not break the feature the user is paying for: the answer has
        already been produced and the vendor has already billed for it, so refusing to return it
        would cost money *and* deliver nothing. Logged at exception level so an under-count is
        visible in the logs rather than only in the invoice.
        """
        if self._account is None:
            # No bound account — a CLI operator invocation or a background job. N10: system
            # activity is not metered, and there is no household to bill.
            return
        try:
            from mihomes.services.metering.meter import record_usage

            with self._session_factory() as session:
                record_usage(
                    session,
                    self._account,
                    entry_point=self._entry_point,
                    provider=type(self._provider).__name__,
                    method=method,
                    tokens_in=tokens[0] if tokens else None,
                    tokens_out=tokens[1] if tokens else None,
                )
        except Exception:
            logger.exception(
                "AI usage metering failed for entry_point=%s method=%s — the call itself "
                "succeeded, so this is an under-count, not a failed request",
                self._entry_point, method,
            )
