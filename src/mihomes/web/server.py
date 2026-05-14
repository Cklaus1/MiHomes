"""Entry point: mihomes-web"""

from mihomes.config import ensure_dirs
from mihomes.db import init_db


def main() -> None:
    ensure_dirs()
    init_db()

    from a2wsgi import ASGIMiddleware
    from waitress import serve

    from mihomes.web.app import app

    wsgi_app = ASGIMiddleware(app)
    print("MiHomes running on http://localhost:5000")
    serve(wsgi_app, host="0.0.0.0", port=5000)


def dev() -> None:
    """Development server with hot reload — restarts automatically on file changes."""
    ensure_dirs()
    init_db()

    import uvicorn
    print("MiHomes dev server running on http://localhost:5000  (auto-reload on)")
    uvicorn.run(
        "mihomes.web.app:app",
        host="0.0.0.0",
        port=5000,
        reload=True,
        reload_dirs=["src"],
    )


if __name__ == "__main__":
    main()
