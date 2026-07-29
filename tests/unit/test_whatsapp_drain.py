"""M30 · WhatsApp burst drain — a burst larger than one page must not lose messages.

The bridge's `/messages` returns at most `limit` rows. The monitor used to make a
single `get_messages(since=last_check, limit=50)` call and then jump
`last_check = now` — so if 120 messages arrived in one interval, only 50 came back
and the other 70 were skipped forever.

`drain_messages` pages forward from `since` (oldest-first) until a short page,
de-duplicating by id, so the whole backlog is returned regardless of page size.
"""

from datetime import datetime, timezone

from mihomes.services.gateways.whatsapp.client import drain_messages


def _msg(i: int, ts: datetime) -> dict:
    return {"id": f"m{i}", "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"}


def _make_backlog(n: int) -> list[dict]:
    base = datetime(2026, 7, 29, 12, 0, 0, tzinfo=timezone.utc)
    # one message per second so timestamps are strictly increasing
    return [_msg(i, base.replace(second=i % 60, minute=i // 60)) for i in range(n)]


def _pager(backlog: list[dict]):
    """Fake bridge: oldest-first page of `limit` rows with timestamp >= since."""

    def fetch(since, limit):
        rows = backlog
        if since is not None:
            rows = [m for m in rows if datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00")) >= since]
        return rows[:limit]

    return fetch


def test_single_page_returns_all():
    backlog = _make_backlog(10)
    got = drain_messages(_pager(backlog), since=None, limit=50)
    assert [m["id"] for m in got] == [m["id"] for m in backlog]


def test_burst_larger_than_page_is_fully_drained():
    backlog = _make_backlog(120)  # 120 msgs, page size 50 -> would drop 70 without draining
    got = drain_messages(_pager(backlog), since=None, limit=50)
    assert len(got) == 120
    assert [m["id"] for m in got] == [m["id"] for m in backlog]


def test_no_duplicates_across_page_boundary():
    backlog = _make_backlog(100)
    got = drain_messages(_pager(backlog), since=None, limit=50)
    ids = [m["id"] for m in got]
    assert len(ids) == len(set(ids)), "boundary message must not be double-counted"


def test_empty_backlog_returns_empty():
    assert drain_messages(_pager([]), since=None, limit=50) == []


def test_terminates_when_page_all_same_timestamp():
    # Pathological: more same-second messages than a page. Must not infinite-loop.
    base = datetime(2026, 7, 29, 12, 0, 0, tzinfo=timezone.utc)
    backlog = [{"id": f"s{i}", "timestamp": base.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"} for i in range(75)]
    got = drain_messages(_pager(backlog), since=None, limit=50)
    # It returns at least the first page and terminates (no hang).
    assert len(got) >= 50
