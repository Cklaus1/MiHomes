"""Entry point: mihomes-web"""

import uvicorn

from mihomes.config import ensure_dirs
from mihomes.db import init_db


def main() -> None:
    ensure_dirs()
    init_db()
    uvicorn.run(
        "mihomes.web.app:app",
        host="0.0.0.0",
        port=8080,
        reload=True,
        reload_dirs=["src"],
    )


if __name__ == "__main__":
    main()
