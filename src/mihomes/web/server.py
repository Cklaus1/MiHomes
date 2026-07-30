"""Entry point: mihomes-web"""

import os
import sys

import mihomes.config as config
from mihomes.config import ensure_dirs
from mihomes.db import init_db


def _demo_flag() -> bool:
    return "--demo" in sys.argv


def _seed_demo_db() -> None:
    """Create and seed demo.db if it hasn't been seeded yet."""
    from sqlalchemy import create_engine, inspect
    from sqlalchemy.orm import Session

    from mihomes.models import Base
    from mihomes.models.property import Property

    # Resolve DB_DIR live (see mihomes.db._active_url) so a prior config reload
    # can't leave this pointed at a stale directory.
    url = f"sqlite:///{config.DB_DIR / 'demo.db'}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    already_seeded = inspect(engine).has_table("alembic_version")
    Base.metadata.create_all(engine)
    if not already_seeded:
        # create_all() builds the schema directly, so the migrations must be
        # recorded as already-applied — otherwise init_db()'s `alembic upgrade
        # head` re-runs the first migration and dies on "table already exists".
        _stamp_head(url)
    with Session(engine) as session:
        if session.query(Property).count() == 0:
            from mihomes.services.demo import load_demo_data
            load_demo_data(session)
            session.commit()
    engine.dispose()


def _stamp_head(url: str) -> None:
    """Mark the DB at the latest migration without running any migrations."""
    from alembic.config import Config

    from alembic import command
    from mihomes.db import _get_alembic_dir

    cfg = Config()
    cfg.set_main_option("script_location", _get_alembic_dir())
    cfg.set_main_option("sqlalchemy.url", url)
    command.stamp(cfg, "head")


def main() -> None:
    ensure_dirs()
    demo = _demo_flag()
    if demo:
        _seed_demo_db()
        os.environ["MIHOMES_DEMO"] = "1"

    init_db()

    from a2wsgi import ASGIMiddleware
    from waitress import serve

    from mihomes.web.app import app

    wsgi_app = ASGIMiddleware(app)
    mode = " [DEMO]" if demo else ""
    # Bind to loopback by default — the app has no auth and holds sensitive
    # estate data. Set MIHOMES_HOST=0.0.0.0 to deliberately expose on a LAN.
    host = os.environ.get("MIHOMES_HOST", "127.0.0.1")
    print(f"MiHomes{mode} running on http://localhost:5000")
    serve(wsgi_app, host=host, port=5000)


def dev() -> None:
    """Development server — no-reload by default, stable on Windows."""
    ensure_dirs()
    demo = _demo_flag()
    if demo:
        _seed_demo_db()
        os.environ["MIHOMES_DEMO"] = "1"

    init_db()

    from pathlib import Path

    import uvicorn

    import mihomes.web.app as _app_module
    app_file = Path(_app_module.__file__).resolve()
    want_reload = "--reload" in sys.argv
    mode = " [DEMO]" if demo else ""
    reload_note = " (auto-reload on)" if want_reload else " (restart to pick up changes)"
    print(f"MiHomes{mode} dev server running on http://localhost:5000{reload_note}")
    print(f"  Loading from: {app_file}")
    host = os.environ.get("MIHOMES_HOST", "127.0.0.1")
    uvicorn.run(
        "mihomes.web.app:app",
        host=host,
        port=5000,
        reload=want_reload,
        **({"reload_dirs": [str(app_file.parents[3] / "src")]} if want_reload else {}),
    )


if __name__ == "__main__":
    main()
