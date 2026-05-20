"""Entry point: mihomes-web"""

import os
import sys

from mihomes.config import ensure_dirs, DB_DIR
from mihomes.db import init_db


def _demo_flag() -> bool:
    return "--demo" in sys.argv


def _seed_demo_db() -> None:
    """Create and seed demo.db if it hasn't been seeded yet."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from mihomes.models import Base
    from mihomes.models.property import Property

    url = f"sqlite:///{DB_DIR / 'demo.db'}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        if session.query(Property).count() == 0:
            from mihomes.services.demo import load_demo_data
            load_demo_data(session)
            session.commit()


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
    print(f"MiHomes{mode} running on http://localhost:5000")
    serve(wsgi_app, host="0.0.0.0", port=5000)


def dev() -> None:
    """Development server — no-reload by default, stable on Windows."""
    ensure_dirs()
    demo = _demo_flag()
    if demo:
        _seed_demo_db()
        os.environ["MIHOMES_DEMO"] = "1"

    init_db()

    import uvicorn
    from pathlib import Path
    import mihomes.web.app as _app_module
    app_file = Path(_app_module.__file__).resolve()
    want_reload = "--reload" in sys.argv
    mode = " [DEMO]" if demo else ""
    reload_note = " (auto-reload on)" if want_reload else " (restart to pick up changes)"
    print(f"MiHomes{mode} dev server running on http://localhost:5000{reload_note}")
    print(f"  Loading from: {app_file}")
    uvicorn.run(
        "mihomes.web.app:app",
        host="0.0.0.0",
        port=5000,
        reload=want_reload,
        **({"reload_dirs": [str(app_file.parents[3] / "src")]} if want_reload else {}),
    )


if __name__ == "__main__":
    main()
