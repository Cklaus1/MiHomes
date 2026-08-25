"""AI usage metering — the cost-control half of Phase 3.

Two modules:

- `meter.py` — `record_usage` / `current_usage` / the billing-period boundary.
- `ai_wrapper.py` — `MeteredProvider`, which wraps a concrete `AIProvider` so every dispatch is
  counted at the one place all of them pass through.

**The phase's definition of done lives here** (§8, A11): *"The AI meter binds at every entry
point, or it bounds nothing."* A meter on the web route but not the CLI, the Telegram gateway or
the agentic loop does not cap Claude spend — the limit is only as strong as its leakiest dispatch
path. That is why Step 9 closed `agent.py`'s factory bypass before this package existed.
"""

from mihomes.services.metering.meter import (
    billing_period,
    check_and_reserve,
    current_usage,
    hard_ceiling,
    record_usage,
)

__all__ = [
    "billing_period",
    "check_and_reserve",
    "current_usage",
    "hard_ceiling",
    "record_usage",
]
