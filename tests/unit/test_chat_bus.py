"""SPEC-012 A28 — the bus delivers across event loops and degrades to `resync` on overflow."""

from __future__ import annotations

import asyncio
import threading
import uuid

from mihomes.services.chat import bus as bus_mod
from mihomes.services.chat.bus import ChatBus


def _run_subscriber(bus: ChatBus, account_id, ready: threading.Event, got: list, n: int):
    async def main():
        sub = bus.subscribe(account_id)
        ready.set()
        try:
            for _ in range(n):
                got.append(await asyncio.wait_for(sub.queue.get(), timeout=5))
        finally:
            bus.unsubscribe(sub)

    asyncio.run(main())


def test_cross_loop_publish_and_overflow(monkeypatch):
    bus = ChatBus()
    acct, other = uuid.uuid4(), uuid.uuid4()

    # Two subscribers, each on its own loop in its own thread; publish from the main thread.
    results: list[list] = [[], []]
    readies = [threading.Event(), threading.Event()]
    threads = [threading.Thread(target=_run_subscriber, args=(bus, acct, readies[i], results[i], 2))
               for i in range(2)]
    for t in threads:
        t.start()
    for r in readies:
        assert r.wait(5)
    bus.publish(other, {"type": "post.created", "id": 99})   # another account: never delivered
    bus.publish(acct, {"type": "post.created", "id": 1})
    bus.publish(str(acct), {"type": "post.created", "id": 2})  # str ids normalise (NOTIFY path)
    for t in threads:
        t.join(5)
    assert [[e["id"] for e in r] for r in results] == [[1, 2], [1, 2]]
    assert bus.subscriber_count() == 0
    bus.publish(acct, {"type": "post.created", "id": 3})   # no subscribers: a no-op, no loop needed

    # Overflow: a slow subscriber gets one resync, then nothing until drained.
    monkeypatch.setattr(bus_mod, "QUEUE_MAX", 3)

    async def overflow():
        sub = bus.subscribe(acct)
        for i in range(10):
            bus.publish(acct, {"type": "post.created", "id": i})
        await asyncio.sleep(0.05)   # let call_soon_threadsafe callbacks run
        drained = []
        while not sub.queue.empty():
            drained.append(sub.queue.get_nowait())
        bus.unsubscribe(sub)
        return drained

    drained = asyncio.run(overflow())
    assert drained[-1] == {"type": "resync"}
    assert [e for e in drained if e["type"] == "resync"] == [{"type": "resync"}]
    assert len(drained) <= 3
