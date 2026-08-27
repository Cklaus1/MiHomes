"""G6.2 · §6 Step 2/6 — the suppression RLS carve-out (A21).

**A21 asserts an absence**, the same shape as SPEC-004's A6 and for the same reason: every other
migration that creates a table emits `policy_statements()` and `trigger_ddl_statements()`, and a
later migration "completing" the pattern here breaks nothing visibly. The insert still succeeds,
`is_suppressed` still returns a bool — it just returns `False` for an address suppressed under a
different account, and the mail goes out. Nothing raises. A complainer gets re-mailed, and the
sending domain's reputation is what pays.

The reason the carve-out exists is not `0010`'s. That ledger is global because it is written
*before* account context exists. **This table is global because suppression is a property of an
address, not of an account** — someone who unsubscribed must stay suppressed when they later
appear under a second account, as invited staff, a second signup, or a vendor contact.

**Static, not a live-database test**, so it runs without Postgres and cannot skip into vacuity
(conventions §0: *"a skipped test is a red gate"*). The live half — that the table really has no
policy after `upgrade head` — is covered by `tests/integration/test_pg_baseline.py`'s round-trip.
"""

from __future__ import annotations

import ast
from pathlib import Path

from mihomes.models import Base, TenantOwned
from mihomes.models.email_suppression import EmailSuppression
from mihomes.tenancy.registry import GLOBAL_TABLES, TENANT_TABLES

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
MIGRATION = VERSIONS / "0012_email_suppressions.py"


def _executable_lines(path: Path) -> str:
    """A migration's source with comments and the module docstring removed.

    `ast` rather than a line-prefix heuristic: a docstring is not a comment, and this
    migration's docstring necessarily *names* the thing it forbids. SPEC-004's A6 test learned
    this the hard way — its first draft failed on `0010`'s own warning comment.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        tree.body = tree.body[1:]
    return ast.unparse(tree)


def test_suppression_not_rls():
    """**A21** — the migration creating the suppression list emits no RLS policy for it."""
    code = _executable_lines(MIGRATION)

    assert "policy_statements" not in code, (
        "0012 must not create an RLS policy for email_suppressions (D13) — a tenant policy "
        "makes is_suppressed return False for an address suppressed under another account, "
        "and the mail goes out with nothing raising"
    )
    assert "trigger_ddl_statements" not in code
    assert "ENABLE ROW LEVEL SECURITY" not in code.upper()


def test_no_later_migration_adds_a_policy():
    """The regression this criterion actually guards against.

    A21 as written names one migration; the danger is a *later* one. Sweeping every revision
    file is the derive-from-the-tree principle rather than a check that rots the moment the
    next migration is written.
    """
    offenders = []
    for path in sorted(VERSIONS.glob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue  # a comment explaining the carve-out is not a policy
            if "email_suppressions" in stripped and (
                "POLICY" in stripped.upper() or "ROW LEVEL SECURITY" in stripped.upper()
            ):
                offenders.append(f"{path.name}: {stripped}")

    assert not offenders, (
        "email_suppressions must never get an RLS policy (D13). Found:\n  "
        + "\n  ".join(offenders)
    )


def test_suppressions_are_not_registered_as_a_tenant_table():
    """The registry half of the same decision.

    `TENANT_TABLES` drives RLS generation *and* the isolation test, so a table listed there
    would acquire a policy from the generator even if no migration wrote one by hand.
    """
    assert "email_suppressions" in GLOBAL_TABLES
    assert "email_suppressions" not in TENANT_TABLES


def test_suppressions_are_not_tenant_owned():
    """`TenantOwned` would add the mixin's `account_id` *and* enrol the model in the scoped
    session's `with_loader_criteria` — the same per-account scoping as RLS, reached through the
    ORM instead of the database."""
    assert not issubclass(EmailSuppression, TenantOwned)


def test_the_table_has_no_account_column_at_all():
    """Not "nullable and not tenancy", as the webhook ledger has to qualify — absent.

    This is what makes it the clean `GLOBAL_TABLES` entry: there is nothing here to scope on,
    so no future reader can mistake a column for a tenancy hook and "fix" it.
    """
    columns = set(Base.metadata.tables["email_suppressions"].columns.keys())
    assert columns == {
        "id", "address", "reason", "suppressed_at", "provider_event_id"
    }, columns


def test_the_address_is_unique():
    """The constraint is the idempotency mechanism, not a hint.

    `suppress()` inserts first and treats the violation as the signal, because bounce and
    complaint webhooks for one address arrive concurrently. Drop the constraint and every
    behavioural test stays green while A22's guarantee is gone.
    """
    table = Base.metadata.tables["email_suppressions"]
    uniques = {
        tuple(c.name for c in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("address",) in uniques, uniques
