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
