"""SPEC-014 A21, A22 (+ D6 allowlist, VAPID keygen) — push payloads and the sender."""

from __future__ import annotations

import base64
import json
import uuid
from types import SimpleNamespace

import pytest

from mihomes.services.chat import push


def _conv(kind):
    return SimpleNamespace(id=uuid.uuid4(), kind=kind)


def test_payload_has_no_body_text():
    """A21 — never a body or sender; a DM is titled 'Direct message' whatever its real title."""
    dm = push.build_payload(_conv("dm"), "dm", title="SENTINEL Maria Lopez")
    assert dm["title"] == "Direct message" and dm["body"] == "New message"
    assert "SENTINEL" not in json.dumps(dm)
    group = push.build_payload(_conv("property"), "mention", title="Belle Estate")
    assert group["title"] == "Belle Estate" and group["body"] == "You were mentioned"
    assert set(group) == {"title", "body", "url", "tag"}
    assert group["url"].startswith("/chat/c/") and group["tag"].startswith("chat-")


@pytest.mark.parametrize("endpoint,ok", [
    ("https://fcm.googleapis.com/fcm/send/abc", True),
    ("https://updates.push.services.mozilla.com/wpush/v2/x", True),
    ("https://web.push.apple.com/QG", True),
    ("https://db5.notify.windows.com/w/?token=x", True),
    ("http://fcm.googleapis.com/fcm/send/abc", False),           # not https
    ("https://fcm.googleapis.com:8443/fcm/send/abc", False),     # explicit port
    ("https://u:p@fcm.googleapis.com/x", False),                 # userinfo
    ("https://169.254.169.254/latest/meta-data", False),         # SSRF target
    ("https://evo-dev.tail6765ef.ts.net/x", False),
    ("https://notify.windows.com.evil.example/x", False),
    ("https://notify.windows.com/x", False),                     # bare suffix, no subdomain
    ("not a url", False),
])
def test_endpoint_allowlist(endpoint, ok):
    assert push.endpoint_allowed(endpoint) is ok


def test_fresh_vapid_claims_per_send(monkeypatch):
    """A22 — pywebpush writes `aud` into the dict it gets; each call must get a new dict."""
    seen: list[dict] = []

    def fake_webpush(**kw):
        claims = kw["vapid_claims"]
        seen.append(claims)
        claims.setdefault("aud", kw["subscription_info"]["endpoint"].split("/")[2])

    import pywebpush

    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)
    public, private = push.generate_vapid_keys()
    sender = push.WebPushSender(private_key=private, subject="mailto:ops@example.com")
    subs = [SimpleNamespace(endpoint="https://fcm.googleapis.com/a", p256dh="k", auth="a"),
            SimpleNamespace(endpoint="https://updates.push.services.mozilla.com/b", p256dh="k",
                            auth="a")]
    for s in subs:
        assert sender.send(s, {"title": "t"}) == 201
    assert seen[0] is not seen[1]
    assert seen[0]["aud"] == "fcm.googleapis.com"
    assert seen[1]["aud"] == "updates.push.services.mozilla.com"


def test_generate_vapid_keys_shapes():
    public, private = push.generate_vapid_keys()

    def raw(v):
        return base64.urlsafe_b64decode(v + "=" * (-len(v) % 4))

    assert len(raw(public)) == 65 and raw(public)[0] == 4
    assert len(raw(private)) == 32
