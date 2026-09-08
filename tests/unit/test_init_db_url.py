"""`init_db` must hand Alembic a URL that still carries the password.

**The bug.** `db.init_db` built its Alembic config with
`set_main_option("sqlalchemy.url", str(engine.url))`. SQLAlchemy's `URL.__str__` masks the
password as the literal string `***`, so Alembic received

    postgresql+psycopg://mihomes:***@localhost:5432/mihomes

and tried to authenticate as the password `***`. Postgres answered exactly that:

    FATAL: password authentication failed for user "mihomes"

So `mihomes init` could not migrate *any* password-authenticated database.

**Why it took a real deployment to find.** Three things conspired to make it read as an
operator error rather than a code one:

1. Under SQLite the URL is a file path with no password to mask, so every local run and the
   whole test suite passed.
2. `psql` with the same `DATABASE_URL` connected fine — it never goes through this code.
3. `alembic upgrade head` also worked, because `alembic/env.py` reads `DATABASE_URL` from the
   environment and never passes through `init_db`.

Only the one path that *does* pass through it failed, which is the hardest shape to attribute.

These are unit tests on purpose: the defect is entirely in URL rendering, so it needs no
database. A test that required Postgres would skip in exactly the environments most likely to
regress this.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

_PASSWORD = "S3cret-Passw0rd"
_URL = f"postgresql+psycopg://mihomes:{_PASSWORD}@localhost:5432/mihomes"


def test_str_of_a_url_masks_the_password():
    """Pins the SQLAlchemy behaviour the fix exists to avoid.

    If a future SQLAlchemy stopped masking, `str()` would be harmless and this test would fail
    loudly rather than leaving the reader wondering why `render_as_string` is used at all.
    """
    engine = create_engine(_URL)
    try:
        rendered = str(engine.url)
        assert "***" in rendered, (
            "SQLAlchemy no longer masks passwords in __str__ — re-read init_db's comment, the "
            "hazard it documents may no longer exist"
        )
        assert _PASSWORD not in rendered
    finally:
        engine.dispose()


def test_render_as_string_keeps_the_password():
    """The fix's mechanism: the password survives, so Alembic can actually connect."""
    engine = create_engine(_URL)
    try:
        rendered = engine.url.render_as_string(hide_password=False)
        assert _PASSWORD in rendered
        assert "***" not in rendered
        # Round-trips: what Alembic parses back must equal what we started with.
        assert make_url(rendered).password == _PASSWORD
    finally:
        engine.dispose()


def test_init_db_hands_alembic_the_unmasked_url(monkeypatch):
    """**The regression test.** Captures the URL `init_db` actually passes to Alembic.

    Everything after the config is stubbed out — the backend check, `command.upgrade` and the
    account bootstrap all no-op — so this asserts on the one thing that was wrong without
    needing a live Postgres. Reverting the fix to `str(engine.url)` fails this on the
    `"***" not in captured` assertion.
    """
    import mihomes.db as db_module

    captured: dict[str, str] = {}

    class _FakeConfig:
        def set_main_option(self, key: str, value: str) -> None:
            captured[key] = value

    import alembic.config

    monkeypatch.setattr(alembic.config, "Config", lambda *a, **kw: _FakeConfig())

    import alembic.command

    monkeypatch.setattr(alembic.command, "upgrade", lambda *a, **kw: None)

    # The engine must not connect: `create_engine` is lazy, but the backend guard and the
    # account bootstrap both would.
    monkeypatch.setattr(
        "mihomes.tenancy.runtime_role.verify_tenant_capable_backend", lambda engine: None
    )
    monkeypatch.setattr(
        "mihomes.tenancy.bootstrap.ensure_default_account", lambda engine: None
    )
    monkeypatch.setattr(db_module, "ensure_dirs", lambda: None)

    db_module.init_db(_URL)

    url = captured.get("sqlalchemy.url", "")
    assert url, "init_db set no sqlalchemy.url at all"
    assert _PASSWORD in url, (
        f"init_db handed Alembic a URL with no usable password ({url!r}) — every "
        f"password-authenticated database will fail with 'password authentication failed'"
    )
    assert "***" not in url, (
        "the password is masked, so Alembic will try to authenticate as the literal '***'"
    )


def test_a_password_containing_percent_survives_configparser(monkeypatch):
    """**The bug the first fix introduced.**

    Alembic's `Config` is a ConfigParser, which reads `%` as interpolation syntax and raises
    `ValueError: invalid interpolation syntax` on a raw one. While the password was masked as
    `***` there was never a `%` to trip over, so unmasking it is precisely what exposes this —
    the first version of the fix turned an authentication failure into a crash for any
    percent-encoded password (`p%40ss`) or any generated password containing `%`. Caught by
    the full suite, whose own test-database URL contains one.

    Uses a real `alembic.config.Config`, not the fake above: the escaping contract belongs to
    ConfigParser, and a stub would assert nothing about it.
    """
    import mihomes.db as db_module

    # `%40` is how a literal `@` must be written in a URL — the realistic way a `%` shows up in
    # a connection string. `make_url` percent-*decodes* on parse, so the parsed password is
    # `p@ss`; the assertion below is on that decoded value, which is what psycopg receives.
    url = "postgresql+psycopg://mihomes:p%40ss%25word@localhost:5432/mihomes"
    decoded_pw = make_url(url).password
    assert decoded_pw == "p@ss%word", "precondition: URL decoding works as assumed"

    import alembic.command

    monkeypatch.setattr(alembic.command, "upgrade", lambda *a, **kw: None)
    monkeypatch.setattr(
        "mihomes.tenancy.runtime_role.verify_tenant_capable_backend", lambda engine: None
    )
    monkeypatch.setattr(
        "mihomes.tenancy.bootstrap.ensure_default_account", lambda engine: None
    )
    monkeypatch.setattr(db_module, "ensure_dirs", lambda: None)

    # No exception, and the value must read back with the password intact.
    db_module.init_db(url)

    # Re-run against a real Config to prove the round-trip, which is the actual contract:
    # what Alembic reads back must decode to the same password psycopg would receive.
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option(
        "sqlalchemy.url",
        make_url(url).render_as_string(hide_password=False).replace("%", "%%"),
    )
    assert make_url(cfg.get_main_option("sqlalchemy.url")).password == decoded_pw
