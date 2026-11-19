"""G10 · §6 Step 10 (A13) — no f-string interpolation into `text()`.

**Why this is an AST walk and not the spec's grep.** Step 10's verify clause is
*"`grep -rn 'text(f"' src/` returns nothing"*, which can never go green: the pattern matches
any function whose name merely *ends* in `text`, and this codebase has two —
`SESSION_FILE.write_text(f"…")` and `save_document_text(f"…")`. Both are file writes with no SQL
anywhere near them. A grep-based gate is therefore permanently red for reasons unrelated to SQL,
which is the same failure mode as any check that matches on spelling instead of structure (see
the `"waitlist" not in baseline_source` mistake in G6.3).

Matching a `Call` whose callee is literally named `text` and whose first argument is a
`JoinedStr` (an f-string) is exact: it cannot collide with `write_text`, and it catches
`sa.text(f"…")` and `sqlalchemy.text(f"…")` as well as the bare form.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"


def _callee_name(func: ast.expr) -> str | None:
    """The final attribute/name of a call target: `text`, `sa.text` and `x.y.text` all -> 'text'."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _fstring_text_calls(tree: ast.AST) -> list[int]:
    """Line numbers of `text(f"...")` calls — the exact shape, not the spelling."""
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Exactly `text`, so `write_text` / `save_document_text` cannot match: `_callee_name`
        # returns the attribute or name itself, never a suffix of a longer identifier.
        if _callee_name(node.func) != "text":
            continue
        if node.args and isinstance(node.args[0], ast.JoinedStr):
            hits.append(node.lineno)
    return hits


def _python_files() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


def test_no_fstring_text_calls():
    """A13 — no SQL is built by string interpolation anywhere in `src/`.

    Bound parameters are not merely tidier: an interpolated identifier or value is the one place
    tenancy cannot be enforced by anything above the database, since the G8 filter never sees a
    raw `text()` statement at all.
    """
    offenders = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as e:  # a file that will not parse is its own problem
            offenders.append(f"{path.relative_to(SRC)}: unparseable ({e})")
            continue
        offenders += [
            f"{path.relative_to(SRC)}:{line}" for line in _fstring_text_calls(tree)
        ]
    assert not offenders, (
        "SQL built by f-string interpolation into text():\n  " + "\n  ".join(offenders)
    )


def test_the_guard_actually_detects_the_pattern():
    """The guard must be able to fail — otherwise it is decoration.

    Three variants that must be caught, and the two real call sites in this codebase that must
    **not** be, since those false positives are exactly why the spec's grep is unusable.
    """
    caught = ast.parse('text(f"SELECT * FROM {t}")')
    assert _fstring_text_calls(caught) == [1]

    qualified = ast.parse('sa.text(f"SELECT {c} FROM t")')
    assert _fstring_text_calls(qualified) == [1], "a qualified sa.text(f\"…\") must be caught"

    nested = ast.parse('session.execute(text(f"DELETE FROM {t}"))')
    assert _fstring_text_calls(nested) == [1], "a nested text(f\"…\") must be caught"

    for benign in (
        'SESSION_FILE.write_text(f"{data}")',
        'save_document_text(f"report-{slug}", body)',
        'text("SELECT 1")',
        'text("SELECT :x", {"x": 1})',
    ):
        assert _fstring_text_calls(ast.parse(benign)) == [], (
            f"false positive on {benign!r} — this is why grep 'text(f\"' cannot be the gate"
        )


def test_the_specs_grep_would_still_report_hits():
    """Documents *why* Step 10's clause is unsatisfiable, rather than just asserting it is.

    If this ever finds zero, the two `*_text(f"` call sites have been renamed or removed and the
    spec's grep would finally pass — at which point this test should go, and the note in
    `opportunities.md` with it.
    """
    grep_hits = [
        f"{p.relative_to(SRC)}:{i}"
        for p in _python_files()
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if 'text(f"' in line
    ]
    ast_hits = [
        f"{p.relative_to(SRC)}:{line}"
        for p in _python_files()
        for line in _fstring_text_calls(ast.parse(p.read_text(encoding="utf-8")))
    ]
    assert not ast_hits, f"the AST guard should be clean here; got {ast_hits}"
    assert grep_hits, (
        "the spec's grep now returns nothing — the *_text(f\"…\") collisions are gone, so "
        "Step 10's verify clause is satisfiable and this test plus its opportunities.md entry "
        "can be retired"
    )
