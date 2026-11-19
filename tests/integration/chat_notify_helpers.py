"""Shared helpers for the SPEC-014 notification tests — app role, injected `now`, fake push."""

from __future__ import annotations

import base64
import os
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from mihomes.models.chat_native import ChatConversation
from mihomes.models.membership import Membership
from mihomes.services.chat import conversations as conv
from mihomes.services.chat import posts
from mihomes.services.chat.notify import run_chat_notify
from tests.integration.chat_helpers import app_session

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def keys() -> tuple[str, str]:
    b64 = lambda n: base64.urlsafe_b64encode(os.urandom(n)).rstrip(b"=").decode()  # noqa: E731
    return b64(65), b64(16)


class FakePushSender:
    def __init__(self, status: int = 201):
        self.status = status
        self.sent: list[tuple[str, dict]] = []

    def send(self, sub, payload):
        self.sent.append((sub.endpoint, payload))
        return self.status


def make_factory(app_engine):
    @contextmanager
    def factory():
        with Session(app_engine) as s:
            yield s
    return factory


def email_factory(s):
    from mihomes.services.email.console_provider import ConsoleProvider
    from mihomes.services.email.service import EmailService

    return EmailService(ConsoleProvider("test@mihomes.test"), session=s)


def run(app_engine, *, now=NOW, sender=None):
    return run_chat_notify(make_factory(app_engine), push_sender=sender,
                           email_factory=email_factory, now=now)


def rooms(app_engine, e) -> dict[str, uuid.UUID]:
    with app_session(app_engine, e.account_id) as s:
        ids = {"general": conv.get_or_create_estate(s).id,
               "belle": conv.get_or_create_property(s, e.belle).id,
               "blue": conv.get_or_create_property(s, e.blue).id,
               "dm": conv.get_or_create_dm(s, s.get(Membership, e.owner), e.staff_belle).id}
        s.commit()
        return ids


def post(app_engine, _pg_engine, e, conversation_id, author_id, body, *, age: timedelta,
         now=NOW) -> uuid.UUID:
    """Create a post and backdate it to `now - age`."""
    with app_session(app_engine, e.account_id) as s:
        p = posts.create_post(s, s.get(ChatConversation, conversation_id),
                              author=s.get(Membership, author_id), body=body)
        s.commit()
        pid = p.id
    with _pg_engine.begin() as c:
        c.execute(text("UPDATE chat_posts SET created_at = :t WHERE id = :p"),
                  {"t": now - age, "p": pid})
    return pid


def mention(_pg_engine, e, post_id, membership_id) -> None:
    """Insert a mention row directly — as if written while the member could still see the room."""
    with _pg_engine.begin() as c:
        c.execute(text("INSERT INTO chat_mentions (id, account_id, post_id, membership_id) "
                       "VALUES (:i, :a, :p, :m)"),
                  {"i": uuid.uuid4(), "a": e.account_id, "p": post_id, "m": membership_id})


def subscribe(app_engine, e, membership_id, endpoint=None) -> str:
    from mihomes.services.chat import push

    endpoint = endpoint or f"https://fcm.googleapis.com/fcm/send/{uuid.uuid4().hex}"
    p256dh, auth = keys()
    with app_session(app_engine, e.account_id) as s:
        push.subscribe(s, account_id=e.account_id, membership_id=membership_id,
                       endpoint=endpoint, p256dh=p256dh, auth=auth, user_agent="test")
        s.commit()
    return endpoint


def digests(_pg_engine, e) -> list[tuple[str, str]]:
    with _pg_engine.connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT to_address, context FROM email_outbox WHERE account_id = :a "
            "AND template = 'chat_digest' ORDER BY next_attempt_at, id"), {"a": e.account_id})]


def email_of(_pg_engine, membership_id) -> str:
    with _pg_engine.connect() as c:
        return c.execute(text("SELECT u.email FROM users u JOIN memberships m ON m.user_id = u.id "
                              "WHERE m.id = :m"), {"m": membership_id}).scalar_one()
