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
        result = self._provider.complete(*args, **kwargs)
        self._record("complete")
        return result

    def structured_output(self, *args, **kwargs):
        result = self._provider.structured_output(*args, **kwargs)
        self._record("structured_output")
        return result

    def stream(self, *args, **kwargs):
        """Metered **once per call**, not per token.

        `PRICING` §5.1 bills calls, and a stream is one call that happens to arrive in pieces —
        counting tokens here would charge a long answer as hundreds of calls. Recorded before
        the generator is consumed, so a caller that abandons the stream halfway is still counted:
        the request was made and the provider billed for it.
        """
        self._record("stream")
        return self._provider.stream(*args, **kwargs)

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

    def _record(self, method: str) -> None:
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
                )
        except Exception:
            logger.exception(
                "AI usage metering failed for entry_point=%s method=%s — the call itself "
                "succeeded, so this is an under-count, not a failed request",
                self._entry_point, method,
            )
