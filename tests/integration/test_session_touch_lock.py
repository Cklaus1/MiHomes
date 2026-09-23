"""A request must never wait on another request's lock on its own session row.

**The hang.** `lookup_session` set `row.last_seen_at`, autoflushed as `UPDATE sessions`, and held
that row lock until commit. A page load fires several requests on one session at once; the
second request's UPDATE waited on the first's lock *on the event loop* (the enforcing dependency
is async), which is the loop that would have run the first request's commit. Both stuck, then
every request after them — pages "kept loading" until the process was restarted.

Reproduced deterministically: an outside transaction holds the session row `FOR UPDATE` — the
role the first request played — while a request with that cookie runs. Before the fix it blocks
until the lock is released; after, it answers at once.

Run as the non-superuser app role (`rls_app`), the shape of every real install.
"""

from __future__ import annotations

import concurrent.futures

from sqlalchemy import text

from tests.integration.test_rls_request_path import _HTML, _sign_in, rls_app  # noqa: F401


def test_a_request_does_not_wait_on_a_locked_session_row(rls_app):  # noqa: F811
    import mihomes.db as db_module

    client, _ = rls_app
    _sign_in(client)
    assert client.get("/", headers=_HTML).status_code == 200

    # Stale enough that the request will want to write `last_seen_at`.
    with db_module._engine.begin() as conn:
        conn.execute(text("update sessions set last_seen_at = now() - interval '1 hour'"))

    holder = db_module._engine.connect()
    tx = holder.begin()
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        holder.execute(text("select id from sessions for update"))

        future = pool.submit(client.get, "/", headers=_HTML)
        try:
            response = future.result(timeout=10)
        except concurrent.futures.TimeoutError:
            raise AssertionError(
                "the request blocked on another transaction's lock on its session row — "
                "the same wait that hung every page when a page load's requests collided"
            ) from None
        assert response.status_code == 200
    finally:
        tx.rollback()
        holder.close()
        pool.shutdown(wait=True)


def test_last_seen_is_still_recorded(rls_app):  # noqa: F811
    """Not waiting must not mean not writing: an uncontended request still records activity."""
    import mihomes.db as db_module

    client, _ = rls_app
    _sign_in(client)

    with db_module._engine.begin() as conn:
        conn.execute(text("update sessions set last_seen_at = now() - interval '1 hour'"))

    assert client.get("/", headers=_HTML).status_code == 200

    with db_module._engine.begin() as conn:
        stale = conn.execute(
            text("select count(*) from sessions where last_seen_at < now() - interval '30 minutes'")
        ).scalar_one()
    assert stale == 0, "an uncontended request should have refreshed last_seen_at"
