"""G9 · §6 Step 9 — no factory bypass (A10). Step 10 extends this file with A11–A13.

**A10 is a static assertion, and static is the point.** *"No direct SDK client construction
outside `services/ai/*_provider.py`."* A behavioural test would have to invoke every AI path with
real credentials to notice a bypass; a source scan notices it in the diff that introduces it.

This is the check `agent.py:78` would have failed. It constructed `anthropic.Anthropic(...)`
directly and never touched `get_provider()` — the one path in the tree outside the factory, and
the **highest-token path in the app**, since an agentic loop makes up to six API calls per
question. Step 10 wraps the factory's return value; a path that never calls the factory is
therefore a path the meter cannot see, so Step 9 exists to close it *first*.

> A meter that binds at every entry point but one does not cap spend. It caps the paths that
> were already cheap.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "mihomes"

#: The modules allowed to construct a vendor SDK client.
#:
#: These *are* the provider implementations — constructing the client is their entire job, and
#: they are where `get_provider()` dispatches to. Everything else must come through the factory,
#: which is what makes wrapping the factory sufficient to meter the whole app.
ALLOWED_CONSTRUCTION_SITES = {
    "services/ai/claude_provider.py",
    "services/ai/openai_provider.py",
    "services/ai/nim_provider.py",
    "services/ai/ollama_provider.py",
}

#: Constructor calls that create a vendor SDK client.
_SDK_CONSTRUCTORS = {
    ("anthropic", "Anthropic"),
    ("anthropic", "AsyncAnthropic"),
    ("openai", "OpenAI"),
    ("openai", "AsyncOpenAI"),
}


def _construction_sites() -> list[str]:
    """Walk `src/` and report every SDK client construction, with its location.

    AST rather than grep: a comment or a docstring mentioning `anthropic.Anthropic(` is not a
    construction, and this module's own docstring says the phrase. A text scan would flag the
    documentation explaining the rule — which trains the next author to delete the explanation.
    """
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel in ALLOWED_CONSTRUCTION_SITES:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if (func.value.id, func.attr) in _SDK_CONSTRUCTORS:
                    offenders.append(f"{rel}:{node.lineno} — {func.value.id}.{func.attr}(...)")
    return offenders


class TestNoFactoryBypass:
    def test_no_factory_bypass(self):
        """**A10** — every AI client comes from `get_provider()`.

        The failure message names file and line, because the fix is always the same shape:
        replace the construction with a factory call, borrowing `.client` if the caller genuinely
        needs the raw SDK surface (as the agentic tool loop does).
        """
        offenders = _construction_sites()
        assert not offenders, (
            "SDK clients must be constructed only inside services/ai/*_provider.py, or the "
            "Step 10 meter cannot see the path (D17/F8). Found:\n  " + "\n  ".join(offenders)
        )

    def test_the_scan_has_teeth(self):
        """**The guard on the guard**, and A10 is worthless without it.

        A10 passing proves nothing on its own — a scanner with a typo in the constructor names,
        or one that silently skipped every file, would look identical. So: parse a snippet that
        *does* construct a client and require the same detection logic to catch it.
        """
        tree = ast.parse("import anthropic\nc = anthropic.Anthropic(api_key='x')\n")
        found = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and (node.func.value.id, node.func.attr) in _SDK_CONSTRUCTORS
        ]
        assert found, "the construction scan failed to notice an obvious SDK client"

    def test_the_allowlist_names_only_real_provider_modules(self):
        """A stale entry would exempt a file that no longer exists — and pre-authorise the next
        module to reuse the path."""
        for rel in ALLOWED_CONSTRUCTION_SITES:
            assert (SRC / rel).exists(), f"{rel} is allowlisted but does not exist"

    def test_agent_stream_uses_the_factory(self):
        """The specific regression Step 9 fixes, pinned by name.

        A10 is a whole-tree rule; this asserts that *this* module — the one that had the bypass —
        obtains its client from the factory. Named separately so a future reader sees why
        `agent.py` borrows `.client` instead of calling `provider.stream()`: the agentic loop
        needs `messages.create` with tools, which the Protocol deliberately does not expose.
        """
        source = (SRC / "services" / "ai" / "agent.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "get_provider"
        ]
        assert len(calls) >= 2, (
            "both provider_stream and agent_stream must obtain their client from get_provider()"
        )


#: Modules that call `get_provider()` **without** an `entry_point`, each with its reason.
#:
#: **A declared exemption, not an omission**, and the distinction is the whole of A11. A module
#: missing from the metered set could mean "system-initiated, correctly exempt" or "somebody
#: forgot" — and those are byte-identical in code. Naming them here forces the question to be
#: answered once, in a place a reviewer reads, and `test_every_exemption_is_a_system_path` keeps
#: the list honest.
#:
#: Same construction as SPEC-003's `UNFILTERED_CLASSES` and this phase's
#: `GLOBAL_TABLES_WITH_ACCOUNT_ID`.
UNMETERED_DISPATCH = {
    "services/weather_tasks.py": (
        "system-initiated — the nightly automation, not a user action. D11/N10: a limit that "
        "trips a scheduled job is a bug, because the user cannot upgrade their way out of "
        "something they did not do."
    ),
}


def _dispatch_modules() -> dict[str, bool]:
    """Every module calling `get_provider()`, mapped to whether it passes an `entry_point`.

    **Derived from the tree at test time** — A11's own requirement: *"A hand-maintained list in a
    test file rots the first time someone adds a dispatch path, and it rots silently."*

    AST rather than grep, for the reason A10 gives: the docstrings in this area quote the call
    they describe, and a text scan would count the documentation.
    """
    found: dict[str, bool] = {}
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel == "services/ai/provider.py":
            continue  # the factory itself
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "get_provider"
            ):
                metered = any(kw.arg == "entry_point" for kw in node.keywords)
                # A module with several call sites is metered only if *every* one is.
                found[rel] = found.get(rel, True) and metered
    return found


class TestEveryEntryPointIsMetered:
    def test_all_entry_points_metered(self):
        """**A11 — the phase's definition of done.**

        > The AI meter binds at every entry point, or it bounds nothing. A meter on the web route
        > but not the Telegram bot, the CLI, or `agent_stream` does not cap Claude spend — the
        > limit is only as strong as its leakiest dispatch path.

        Enumerated from the tree, so a **nineteenth** dispatch path added next year fails this
        test rather than quietly joining the unmetered set. That is the difference between a gate
        and a decoration, and it is why the criterion is written as an enumeration rather than as
        "does the meter increment when called" — which passes trivially.
        """
        unmetered = sorted(
            rel for rel, metered in _dispatch_modules().items()
            if not metered and rel not in UNMETERED_DISPATCH
        )
        assert not unmetered, (
            "every user-facing AI dispatch must pass an entry_point to get_provider(), or the "
            "call is free (A11). Unmetered:\n  " + "\n  ".join(unmetered)
            + "\n\nIf a path is system-initiated (D11/N10), add it to UNMETERED_DISPATCH with "
            "its reason instead of leaving it silent."
        )

    def test_the_census_is_not_empty(self):
        """**A guard on A11**, and without it the criterion is worthless.

        If the AST walk stopped finding call sites — a rename, a refactor to a different factory
        function — `unmetered` would be empty and A11 would pass over a tree with no metering at
        all. The exact shape of false green §0.4 exists to close.
        """
        modules = _dispatch_modules()
        assert len(modules) >= 7, (
            f"the dispatch census found only {len(modules)} modules — the scan is probably "
            "broken, and A11 would pass vacuously"
        )

    def test_every_exemption_is_a_system_path(self):
        """The exemption list cannot outlive what it excuses, or absorb a user-facing path.

        Two directions: a stale entry (the module no longer dispatches) would pre-authorise the
        next module to reuse the path, and an entry that *does* pass an `entry_point` is an
        exemption excusing nothing — which reads as precedent for the next one.
        """
        census = _dispatch_modules()
        for rel, reason in UNMETERED_DISPATCH.items():
            assert rel in census, (
                f"{rel} is exempted from metering but no longer calls get_provider() — delete "
                "the exemption rather than leaving one that excuses nothing"
            )
            assert not census[rel], (
                f"{rel} is exempted but now passes an entry_point — it is metered, so remove it "
                "from UNMETERED_DISPATCH"
            )
            assert reason.strip(), f"{rel}'s exemption must carry a reason"


class TestNoModuleLevelCache:
    def test_no_module_level_cache(self):
        """**A12** — no AI provider instance is cached at module level.

        F10 measured this as true today, and A12 asserts it rather than trusting it to stay
        true. The reason it matters is specific: `MeteredProvider` counts per *invocation*, which
        is equivalent to counting per *construction* only while every call site builds a fresh
        provider. A module-level cache would make the two diverge — and the divergence would
        under-count silently, since a cached provider still works perfectly.

        The four `_get_provider` functions in `calendar_sync`, `staff_pto` and `routes/calendar`
        are **Google Calendar**, unrelated, and are excluded by name (F10 names them so a later
        reader does not re-derive it).
        """
        offenders = []
        for path in sorted(SRC.rglob("*.py")):
            rel = path.relative_to(SRC).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover
                continue
            for node in tree.body:  # module level only — not inside a function
                if not isinstance(node, ast.Assign):
                    continue
                value = node.value
                if (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name)
                    and value.func.id == "get_provider"
                ):
                    offenders.append(f"{rel}:{node.lineno}")

        assert not offenders, (
            "an AI provider cached at module level makes per-construction and per-invocation "
            f"metering diverge, silently under-counting (A12). Found: {offenders}"
        )


class _RecordingProvider:
    """A provider that records what it was asked, with no network and no database."""

    supports_images = True

    def __init__(self) -> None:
        self.model = "default-model"
        self.client = object()
        self.calls: list[str] = []

    def complete(self, *a, **kw) -> str:
        self.calls.append("complete")
        return "answer"

    def structured_output(self, *a, **kw) -> dict:
        self.calls.append("structured_output")
        return {}

    def stream(self, *a, **kw):
        self.calls.append("stream")
        return iter(["tok"])


def _wrapped(provider):
    """A `MeteredProvider` with metering disabled (`account=None`).

    These tests are about **proxying**, not counting: A11 and A13 cover the counting. Passing
    `account=None` takes the database out of the picture entirely, so a proxying bug cannot hide
    behind a session error.
    """
    from mihomes.services.metering.ai_wrapper import MeteredProvider

    return MeteredProvider(
        provider, session_factory=None, account=None, entry_point="test",
    )


class TestTheWrapperProxiesTheFullSurface:
    def test_wrapper_proxies_undeclared_surface(self):
        """**F8's first hazard** — `agent.py` reads `provider.client` for the agentic tool loop.

        The Protocol deliberately does not declare `client` (it is Anthropic-specific), so a
        wrapper implementing only the declared methods would raise `AttributeError` the first
        time a user asked the assistant a question that needed a tool.
        """
        inner = _RecordingProvider()
        wrapper = _wrapped(inner)

        assert wrapper.client is inner.client
        assert wrapper.supports_images is True

    def test_attribute_writes_reach_the_provider(self):
        """**F8's second hazard, and the subtlest bug this file could contain.**

        `agent.py:41` does `provider.model = model`. If `__setattr__` stored that on the wrapper
        instead of passing it through, the assignment would succeed, nothing would raise, and the
        provider would quietly use its default model for every subsequent call — a caller asking
        for Opus silently getting Sonnet, visible only in the bill and the answer quality.
        """
        inner = _RecordingProvider()
        wrapper = _wrapped(inner)

        wrapper.model = "claude-opus-5"

        assert inner.model == "claude-opus-5", (
            "attribute writes must reach the wrapped provider — storing them on the wrapper "
            "silently ignores the caller's choice of model"
        )
        assert wrapper.model == "claude-opus-5"

    @pytest.mark.parametrize("method", ["complete", "structured_output", "stream"])
    def test_every_declared_method_dispatches(self, method):
        """All three reach the provider and return its result.

        Parameterised over the declared surface rather than testing `complete` alone: `stream` is
        the one that would break most quietly, since a wrapper returning `None` instead of an
        iterator looks like an empty answer rather than an error.
        """
        inner = _RecordingProvider()
        wrapper = _wrapped(inner)

        result = getattr(wrapper, method)("system", "user")

        assert inner.calls == [method]
        assert result is not None

    def test_stream_returns_a_real_iterator(self):
        """The specific shape a broken proxy would produce: an empty answer, not an error."""
        wrapper = _wrapped(_RecordingProvider())
        assert list(wrapper.stream("system", "user")) == ["tok"]

    def test_a_metering_failure_does_not_break_the_call(self):
        """Both the counter and the ceiling **fail open on an infrastructure error**.

        For `_record` the reasoning is direct: the answer has already been produced and the
        vendor has already billed for it, so refusing to return it because the *counter* failed
        would cost money and deliver nothing.

        For `_check` it took a measurement. This file is otherwise built on the asymmetry that an
        under-count costs a little revenue while an unenforced ceiling costs unbounded inference
        — but "the database is unreachable" is neither: it is **no information**.
        `web/routes/ai.py:460` reads the provider API key from the database *before* a provider
        exists, so a dead database already fails the request. The choice is between failing with a
        confusing billing error and failing with a database error, not between capped and
        uncapped. Recorded as a bounded residual in the harness §0.8.

        **This test caught the behaviour change.** Step 11 added `_check` before `_record`, and
        the first version raised into the caller — a dead metering database would have blocked AI
        entirely.
        """
        from mihomes.services.metering.ai_wrapper import MeteredProvider

        def _explode():
            raise RuntimeError("database is down")

        inner = _RecordingProvider()
        wrapper = MeteredProvider(
            inner,
            session_factory=_explode,
            account=type("A", (), {"id": "x"})(),
            entry_point="test",
        )

        assert wrapper.complete("system", "user") == "answer"

    def test_failing_open_on_an_outage_does_not_disarm_the_ceiling(self):
        """**The other half**, and without it the test above only proves the wrapper is inert.

        A `try` around the whole of `_check` — rather than around the lookup alone — would
        swallow `EntitlementError` and silently remove A14's teeth while every outage test still
        passed. So: a *working* session that returns `Denied` must still raise.
        """
        from contextlib import contextmanager

        from mihomes.entitlements.service import Denied
        from mihomes.services.metering.ai_wrapper import MeteredProvider
        from mihomes.services.property import EntitlementError

        @contextmanager
        def _working_session():
            yield object()

        import mihomes.services.metering.meter as meter_mod

        original = meter_mod.check_and_reserve
        meter_mod.check_and_reserve = lambda *a, **k: Denied(
            reason="AI paused", upgrade_target="pro", limit=200,
        )
        try:
            wrapper = MeteredProvider(
                _RecordingProvider(),
                session_factory=_working_session,
                account=type("A", (), {"id": "x"})(),
                entry_point="test",
            )
            with pytest.raises(EntitlementError):
                wrapper.complete("system", "user")
        finally:
            meter_mod.check_and_reserve = original


class TestProtocolDeclaresWhatIsCalled:
    @pytest.mark.parametrize("method", ["complete", "structured_output", "stream"])
    def test_protocol_declares(self, method):
        """F8 — `stream` was called at `agent.py:44` but not declared on `AIProvider`.

        An undeclared method is one a wrapper has no reason to proxy, which is exactly how
        streaming would have escaped Step 10's meter. Declaring it makes the surface the meter
        must cover explicit rather than discovered at runtime.
        """
        from mihomes.services.ai.provider import AIProvider

        assert hasattr(AIProvider, method), f"AIProvider must declare {method}()"

    @pytest.mark.parametrize(
        "module,cls",
        [
            ("claude_provider", "ClaudeProvider"),
            ("openai_provider", "OpenAIProvider"),
            ("nim_provider", "NIMProvider"),
            ("ollama_provider", "OllamaProvider"),
        ],
    )
    def test_every_provider_implements_the_declared_surface(self, module, cls):
        """Declaring `stream` is only honest if every implementation has it.

        Checked across all four rather than on Claude alone: a Protocol method missing from one
        implementation fails at runtime on whichever provider a deployment happens to configure,
        which is the worst possible place to discover it.
        """
        import importlib

        impl = getattr(importlib.import_module(f"mihomes.services.ai.{module}"), cls)
        for method in ("complete", "structured_output", "stream"):
            assert callable(getattr(impl, method, None)), (
                f"{cls} does not implement {method}() — it cannot satisfy AIProvider"
            )
