"""Phase 0 landing app — public marketing page plus waitlist capture.

A standalone FastAPI app (D1), deliberately sharing the stack and nothing else
with `mihomes.web`. See app.py for why that separation is a security boundary
rather than a style choice.
"""

from mihomes.landing.app import create_landing_app, main

__all__ = ["create_landing_app", "main"]
