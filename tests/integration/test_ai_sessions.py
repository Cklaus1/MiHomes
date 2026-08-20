"""The AI page's session sidebar — broken for every role since the UUID conversion.

`_list_sessions` grouped conversations by `session_id` and took `func.min(AIConversation.id)` to
find each session's first message, so it could show that message as the session's title. That
worked while `id` was a sequential integer: the smallest id in a group *was* the earliest row.

SPEC-002 G6.1 converted every primary key to UUIDv7, and **Postgres has no `min(uuid)`** —
`UndefinedFunction: function min(uuid) does not exist`. So `/ai/` and `/ai/sessions-panel` have
returned 500 to *everyone*, owners included, since that conversion. Nothing caught it: the AI tests
mock `get_ai_api_key` and never render the page, and the smoke suite does not assert on it.

**The fix is `min(created_at)`, and that is not merely a substitution.** `created_at` is the column
that carries the ordering intent all along; `min(id)` only expressed it *incidentally*, as a
side-effect of the id being sequential. UUIDv7 is time-ordered too, so a v7-aware database could
still answer the question — but relying on that would re-encode the same accident.

Regression coverage for the page, not just the query: the bug was invisible because nothing ever
asked for a 200.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from mihomes.models.ai_conversation import AIConversation


@pytest.fixture
def conversations(web_client_as):
    """Two sessions, one of them with two messages.

    The two-message session is the load-bearing fixture: the whole reason `_list_sessions` groups
    at all is to collapse a multi-turn conversation into one sidebar entry, so a fixture with one
    message per session would pass against a version that never grouped.
    """
    base = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)

    def _seed(session):
        session.add(
            AIConversation(
                id=uuid.uuid4(), session_id="s-multi", session_name="Budget review",
                role="financial", user_message="FIRSTMESSAGE about the roof",
                ai_response="An answer.", created_at=base,
            )
        )
        session.add(
            AIConversation(
                id=uuid.uuid4(), session_id="s-multi", role="financial",
                user_message="SECONDMESSAGE following up",
                ai_response="Another answer.", created_at=base + timedelta(minutes=5),
            )
        )
        session.add(
            AIConversation(
                id=uuid.uuid4(), session_id="s-solo", role="estate_manager",
                user_message="SOLOMESSAGE about the gate",
                ai_response="An answer.", created_at=base + timedelta(hours=1),
            )
        )

    web_client_as.seed(_seed)


class TestThePageLoads:
    """The assertion whose absence let this live: somebody asking for a 200."""

    def test_ai_index_is_200_for_an_owner(self, web_client_as, conversations):
        client = web_client_as("owner")
        response = client.get("/ai/")
        assert response.status_code == 200, (
            f"/ai/ returned {response.status_code}. If this is 500, the session-list aggregate is "
            "broken again — Postgres has no min(uuid)."
        )

    def test_sessions_panel_is_200_for_an_owner(self, web_client_as, conversations):
        """The partial is a separate route with its own call to `_list_sessions`.

        Asserting only on `/ai/` would leave the htmx-refreshed panel untested, and it is the one
        that reloads on every new message.
        """
        client = web_client_as("owner")
        response = client.get("/ai/sessions-panel")
        assert response.status_code == 200


class TestGroupingIsCorrect:
    """A 200 proves the query runs. These prove it answers the right question."""

    def test_a_multi_message_session_appears_once(self, web_client_as, conversations):
        client = web_client_as("owner")
        body = client.get("/ai/sessions-panel").text

        assert body.count("Budget review") == 1, (
            "a session with two messages must collapse to one sidebar entry — that is what the "
            "GROUP BY is for"
        )

    def test_the_title_comes_from_the_earliest_message(self, web_client_as, conversations):
        """`min(created_at)` must select the *first* message, not an arbitrary one.

        The bug's shape makes this the test that matters: any aggregate returns *a* row, so a
        version that grouped correctly but picked the wrong row would still render a plausible
        sidebar — with the wrong titles.
        """
        client = web_client_as("owner")
        body = client.get("/ai/sessions-panel").text

        # `s-solo` has no custom name, so its title is its first message — which is also its only
        # one, giving a clean check that untitled sessions fall back to the message text.
        assert "SOLOMESSAGE" in body

        # `s-multi` has a custom name, so the name wins over either message.
        assert "Budget review" in body
        assert "SECONDMESSAGE" not in body, (
            "the later message leaked into the sidebar — the aggregate is picking the wrong row "
            "within the group"
        )

    def test_both_sessions_are_listed(self, web_client_as, conversations):
        client = web_client_as("owner")
        body = client.get("/ai/sessions-panel").text
        assert "Budget review" in body
        assert "SOLOMESSAGE" in body


class TestStaffStillCannotReadTranscripts:
    """G17's fix must survive this one.

    `/ai/sessions-panel` was moved to `audit.view` (denied to staff) when G17 found the transcript
    leak. Repairing the query is exactly the kind of change that could quietly restore the old
    declaration, so the denial is re-asserted here rather than trusted to stay put.
    """

    def test_sessions_panel_is_denied_to_staff(self, web_client_as, conversations):
        client = web_client_as("staff", scoped_to=[])
        assert client.get("/ai/sessions-panel").status_code == 403
