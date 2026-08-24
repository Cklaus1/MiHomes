"""G3 · §6 Step 3 — the RLS carve-out (A6).

**A6 asserts an absence, which is the only shape that can catch this defect.** Every other
migration in the tree emits `policy_statements()` and `trigger_ddl_statements()` for the table it
creates; `0010` deliberately does not. If a later migration "completes" the pattern here, nothing
breaks visibly: the insert still succeeds, the dedup lookup returns zero rows on the webhook
route's account-less session, and **every Stripe event silently reprocesses** — a customer charged
twice or downgraded twice, with no error anywhere.

That is why the spec calls this out twice (B7, and again inline in §4.3's migration): *"If a later
migration adds a policy here, every Stripe event silently reprocesses. A6 is the test that catches
that regression."*

**Static, not a live-database test.** The assertion is over the migration source and the model
metadata, so it runs without Postgres and cannot be skipped into vacuity — conventions §0: *"a
skipped test is a red gate."* The live half (that the table really has no policy after
`upgrade head`) is covered by `tests/integration/test_pg_baseline.py`'s existing round-trip.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mihomes.models import Base
from mihomes.models.processed_webhook_event import ProcessedWebhookEvent
from mihomes.tenancy.registry import GLOBAL_TABLES, TENANT_TABLES

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "0010_processed_webhook_events.py"
)


def _executable_lines(path: Path) -> str:
    """A migration's source with comments and the module docstring removed.

    `ast` rather than a line-prefix heuristic: a docstring is not a comment, and this file's
    docstring necessarily *names* the thing it forbids. Unparsing the AST drops both, so the
    assertion is about what the migration **does**, which is the only thing that can reprocess a
    webhook.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        tree.body = tree.body[1:]
    return ast.unparse(tree)


class TestLedgerIsNotTenantScoped:
    def test_ledger_not_rls(self):
        """**A6** — the migration creating the ledger emits no RLS policy for it.

        Reads the migration source rather than trusting a comment: the two helpers every other
        migration calls must be absent from this one. A future migration adding a policy in a
        *different* file is caught by `test_no_later_migration_adds_a_policy` below.

        **Comments and the docstring are stripped before matching**, and the first draft of this
        test did not do that — it failed immediately on `0010`'s own comment warning a reader not
        to add `policy_statements` here. A source scan cannot tell a warning from a call, and the
        fix is not to soften the wording: a migration that explains its carve-out is doing the
        right thing, and a test that punishes the explanation would train the next author to
        delete it.
        """
        code = _executable_lines(MIGRATION)
        assert "policy_statements" not in code, (
            "0010 must not create an RLS policy for processed_webhook_events (B7) — a tenant "
            "policy makes every dedup lookup return zero rows on the webhook route's "
            "account-less session, and every Stripe event then silently reprocesses"
        )
        assert "trigger_ddl_statements" not in code
        assert "ENABLE ROW LEVEL SECURITY" not in code.upper()

    def test_no_later_migration_adds_a_policy(self):
        """The regression this criterion actually guards against.

        A6 as written names one migration; the danger is a *later* one. So sweep every revision
        file for a policy naming this table — the same derive-from-the-tree principle A11 is
        built on, rather than a check that rots the moment the next migration is written.
        """
        offenders = []
        for path in sorted(MIGRATION.parent.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue  # a comment explaining the carve-out is not a policy
                if "processed_webhook_events" in stripped and (
                    "POLICY" in stripped.upper() or "ROW LEVEL SECURITY" in stripped.upper()
                ):
                    offenders.append(f"{path.name}: {stripped}")

        assert not offenders, (
            "processed_webhook_events must never get an RLS policy (B7). Found:\n  "
            + "\n  ".join(offenders)
        )

    def test_ledger_is_not_registered_as_a_tenant_table(self):
        """The registry half of the same decision.

        `TENANT_TABLES` drives RLS generation *and* the isolation test, so a ledger listed there
        would acquire a policy from the generator even if no migration wrote one by hand.
        """
        assert "processed_webhook_events" in GLOBAL_TABLES
        assert "processed_webhook_events" not in TENANT_TABLES

    def test_ledger_is_not_tenant_owned(self):
        """D2/N5 at the model layer.

        `TenantOwned` would add the mixin's `account_id` *and* enrol the model in the scoped
        session's `with_loader_criteria`, which filters by the bound account — the same zero-rows
        failure as RLS, reached through the ORM instead of the database.
        """
        from mihomes.models import TenantOwned

        assert not issubclass(ProcessedWebhookEvent, TenantOwned)

    def test_account_id_has_no_foreign_key(self):
        """The ledger must outlive the account it describes.

        A `CASCADE` would delete processing history when an account is deleted, and `RESTRICT`
        would block the deletion. Either way a replayed webhook for a deleted account would be
        processed as if new — which is the exact failure this table exists to prevent.
        """
        table = Base.metadata.tables["processed_webhook_events"]
        assert not table.c.account_id.foreign_keys, (
            "processed_webhook_events.account_id must carry no FK — the ledger outlives the "
            "account it describes"
        )
        assert table.c.account_id.nullable, (
            "an event that resolved to no account is still recorded, so it is not retried "
            "forever — NULL is a legitimate state here"
        )


class TestIdempotencyConstraint:
    def test_unique_constraint_is_the_dedup_mechanism(self):
        """N4 — insert-first relies on the violation itself, so the constraint is load-bearing.

        A bare index would leave every test green and the guarantee gone: `SELECT`-then-`INSERT`
        races, and Stripe delivers concurrently under load.
        """
        table = Base.metadata.tables["processed_webhook_events"]
        uniques = {
            tuple(c.name for c in con.columns)
            for con in table.constraints
            if con.__class__.__name__ == "UniqueConstraint"
        }
        assert ("provider", "provider_event_id") in uniques, (
            "the (provider, provider_event_id) UNIQUE constraint IS the idempotency guarantee "
            "(N4) — insert-first treats its violation as the dedup signal"
        )

    @pytest.mark.parametrize(
        "column,nullable",
        [
            ("provider", False),
            ("provider_event_id", False),
            ("event_type", False),
            ("occurred_at", False),
            ("processed_at", False),
            ("account_id", True),
            ("error", True),
        ],
    )
    def test_column_nullability(self, column, nullable):
        table = Base.metadata.tables["processed_webhook_events"]
        assert table.c[column].nullable is nullable

    def test_timestamps_are_timezone_aware(self):
        """Both are compared against `NormalizedEvent.occurred_at`, built from a Unix timestamp
        in UTC. A naive column makes that comparison raise at runtime — in the webhook handler,
        which Stripe then retries.
        """
        table = Base.metadata.tables["processed_webhook_events"]
        assert table.c.occurred_at.type.timezone is True
        assert table.c.processed_at.type.timezone is True
