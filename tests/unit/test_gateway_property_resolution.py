"""L2: gateway inventory routing must not hardcode `belle-estate`.

When the property is unknown, resolve to the sole property if exactly one
exists, else None — never a made-up slug that happens to match one estate.
"""


from mihomes.services.gateways.review_common import resolve_default_property
from mihomes.services.property import create_property


class TestResolveDefaultProperty:
    def test_sole_property_returned(self, session):
        p = create_property(session, "Only Estate")
        assert resolve_default_property(session) == p.slug

    def test_none_when_no_properties(self, session):
        assert resolve_default_property(session) is None

    def test_none_when_multiple_properties(self, session):
        create_property(session, "Estate A")
        create_property(session, "Estate B")
        assert resolve_default_property(session) is None


# --------------------------------------------------------------------------- #
# SPEC-006 A13 (D13/N5) — property routing is unchanged by tenancy
#
# **Written at module level on purpose**, breaking this file's own convention of nesting
# everything inside `TestResolveDefaultProperty`. Harness C10: §8 declares the bare node id
# `test_unchanged_under_tenancy`, a nested name would not resolve under `--collect`, and the
# pending-set expiry test asserts non-resolution — so it could not catch the mistake either.
# The file's local convention loses here; the node id §8 declares is what the gate enforces.
# --------------------------------------------------------------------------- #
def test_unchanged_under_tenancy(session, account_a, account_b):
    """**A13** — `property_slug` behaviour is unchanged and orthogonal to `account`.

    N5 forbids collapsing the two: *"different axes. Collapsing breaks multi-property estates
    or leaks across accounts, depending which way it is done."* An account holds several
    properties, and the chat→property map still decides which house a staff group means.

    Two things are asserted, and the second is the one tenancy could have broken:

    1. The sole-property rule still holds **within** an account — unchanged from L2.
    2. A second property in **another** account does not make it ambiguous. Before tenancy,
       "exactly one property exists" was a global question; the scoped session makes it a
       per-account one. If `resolve_default_property` could see across accounts, adding a
       property to B would silently stop A's unlabelled messages from routing — a
       cross-account coupling with no error anywhere.
    """
    from mihomes.tenancy.context import account_context

    # Account A holds exactly one property: it resolves, exactly as L2 specified.
    only = create_property(session, "A's Only Estate")
    session.flush()
    assert resolve_default_property(session) == only.slug

    # Account B gains one of its own. A's answer must not change.
    with account_context(account_b):
        create_property(session, "B's Estate")
        session.flush()

    assert resolve_default_property(session) == only.slug, (
        "a property created in another account changed this account's routing — "
        "`property_slug` and `account` are different axes and must stay orthogonal (D13/N5)"
    )

    # And the sole-property rule still fires within the account: a SECOND property in A
    # makes it ambiguous again, which is the pre-tenancy behaviour preserved.
    create_property(session, "A's Second Estate")
    session.flush()
    assert resolve_default_property(session) is None
