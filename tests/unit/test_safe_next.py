"""`safe_next` — the open-redirect guard behind A14's `?next=` (SPEC-010 §6 Step 6).

**A login page that reflects an arbitrary destination is a phishing primitive**, and a
particularly effective one: the victim signs in at the genuine domain, sees it in the address
bar, and is then sent to a copy that asks them to "confirm" the password they just typed.
Nothing looks wrong at any step.

So the guard is allow-list shaped — a path beginning with a single `/`, and nothing else. This
file is mostly a table of the shapes a deny-list would have to remember, each of which the
allow-list excludes by construction.
"""

from __future__ import annotations

import pytest

from mihomes.auth.session_flow import safe_next

# Every one of these has been used in a real open-redirect exploit.
HOSTILE = [
    "https://evil.example/steal",       # absolute
    "http://evil.example",              # absolute, no path
    "//evil.example/steal",             # protocol-relative — the browser reads a host
    "///evil.example",                  # three slashes; some parsers collapse to two
    r"/\evil.example",                  # backslash, which several browsers normalise to `/`
    "\\\\evil.example",                 # UNC-style, a literal `\\evil.example`
    "javascript:alert(1)",              # scheme, no leading slash
    "data:text/html,<script>1</script>",
    "mailto:a@b.c",
    "evil.example/path",                # bare host, relative-looking
    "",                                 # nothing to go back to
    "/path\r\nSet-Cookie: a=b",         # header injection through a reflected value
    "/path\nLocation: https://evil",
]

SAFE = [
    "/",
    "/invite/abc123",
    "/invite/abc123/accept",
    "/properties/villa/edit",
    "/password/reset",
]


@pytest.mark.parametrize("target", HOSTILE)
def test_hostile_destinations_are_refused(target):
    assert safe_next(target) is None, (
        f"{target!r} was accepted as a post-sign-in destination. The login page is an open "
        "redirector — a phishing page reached from the real domain, after a real sign-in"
    )


@pytest.mark.parametrize("target", SAFE)
def test_same_site_paths_are_kept(target):
    """The positive twin. A guard that refused everything would pass every test above and
    silently break A14 — an invitee would land on the dashboard with the invitation lost."""
    assert safe_next(target) == target


def test_the_query_string_travels():
    """`/invite/x?from=email` must keep its query, or a link with parameters loses them."""
    assert safe_next("/invite/x", "from=email") == "/invite/x?from=email"
    assert safe_next("/invite/x", "") == "/invite/x"


def test_the_login_pages_are_not_destinations():
    """Otherwise sign-in can redirect to itself, which loops."""
    assert safe_next("/login") is None
    assert safe_next("/signup") is None
    assert safe_next("/login/") is None


def test_a_deny_list_would_have_missed_these():
    """The shapes that motivate the allow-list, called out so the reasoning survives a rewrite.

    Each is a path that *looks* relative and is not. Anyone tempted to replace this with
    `if target.startswith("http")` should read this test first.
    """
    for sneaky in ("//evil.example", r"/\evil.example", "\\\\evil.example", "///evil.example"):
        assert safe_next(sneaky) is None, (
            f"{sneaky!r} passes a naive 'does it start with http' check and still leaves the site"
        )
