"""SPEC-014 Step 9 / A34 — open N chat streams against a deployment and measure them.

    python scripts/chat_sse_load.py --base https://evo-dev.tail6765ef.ts.net \
        --cookie-file a.txt --cookie-file b.txt ... --clients 50 --minutes 10

Each cookie file holds one member's raw `mihomes_session` value. Clients are spread across the
files (the server allows 6 streams per member). Reports: connect times, fan-out latency of one
post to every stream (p95 / max), disconnects, and `GET /chat/` latency while streams are open.
Pool, pg_stat_activity and RSS (criteria 5–7) are read on the server, not here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time

import httpx

COOKIE = "mihomes_session"


async def stream(client: httpx.AsyncClient, base: str, idx: int, state: dict) -> None:
    t0 = time.monotonic()
    try:
        async with client.stream("GET", f"{base}/chat/stream", timeout=None) as r:
            state["status"][idx] = r.status_code
            state["connect"][idx] = time.monotonic() - t0
            state["http"][idx] = r.http_version
            async for line in r.aiter_lines():
                if line.startswith("event: post.created"):
                    state["seen"][idx] = time.monotonic()
    except Exception as exc:  # noqa: BLE001 - a load probe records every failure
        state["errors"][idx] = repr(exc)
    finally:
        state["closed"][idx] = time.monotonic()


async def main(args) -> None:
    cookies = [open(p, encoding="utf-8").read().strip() for p in args.cookie_file]
    clients = [httpx.AsyncClient(http2=True, cookies={COOKIE: cookies[i % len(cookies)]},
                                 headers={"Origin": args.base}) for i in range(args.clients)]
    state = {k: {} for k in ("status", "connect", "http", "seen", "errors", "closed")}
    tasks = [asyncio.create_task(stream(c, args.base, i, state)) for i, c in enumerate(clients)]
    await asyncio.sleep(10)
    ok = [i for i, s in state["status"].items() if s == 200]
    print(f"streams 200 within 10 s: {len(ok)}/{args.clients}  "
          f"(http: {sorted(set(state['http'].values()))})")

    poster = clients[0]
    page = await poster.get(f"{args.base}/chat/")
    general = page.text.split('href="/chat/c/')[1].split('"')[0] if 'href="/chat/c/' in page.text else None
    if general:
        sent_at = time.monotonic()
        await poster.post(f"{args.base}/chat/c/{general}/posts", data={"body": "load-check"})
        await asyncio.sleep(6)
        lat = sorted(state["seen"][i] - sent_at for i in ok if i in state["seen"])
        if lat:
            p95 = lat[int(len(lat) * 0.95) - 1] if len(lat) > 1 else lat[0]
            print(f"fan-out: {len(lat)}/{len(ok)} received; p95 {p95:.2f}s max {lat[-1]:.2f}s")

    pages = []
    for _ in range(20):
        t = time.monotonic()
        await poster.get(f"{args.base}/chat/")
        pages.append(time.monotonic() - t)
    pages.sort()
    print(f"GET /chat/ under load: p95 {pages[18]:.2f}s median {statistics.median(pages):.2f}s")

    await asyncio.sleep(max(args.minutes * 60 - 30, 0))
    dropped = [i for i in ok if i in state["closed"]]
    print(f"disconnects during idle: {len(dropped)}")
    print(json.dumps({"errors": list(state["errors"].values())[:5]}, indent=2))
    for t in tasks:
        t.cancel()
    for c in clients:
        await c.aclose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--cookie-file", action="append", required=True)
    ap.add_argument("--clients", type=int, default=50)
    ap.add_argument("--minutes", type=float, default=10)
    asyncio.run(main(ap.parse_args()))
