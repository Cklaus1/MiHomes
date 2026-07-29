"""WhatsApp conversation review — AI-powered passive issue detection.

Thin re-export of the shared gateway review core. The Telegram and WhatsApp
review paths were near-verbatim copies that drifted (this WhatsApp schema had
lost 8 categories the dispatcher still handled); both now use the single
superset implementation in `services.gateways.review_common`.
"""

from mihomes.services.gateways.review_common import (  # noqa: F401
    REVIEW_SCHEMA,
    SYSTEM_PROMPT,
    analyze_messages,
    build_estate_context as _build_estate_context,
)

__all__ = ["REVIEW_SCHEMA", "SYSTEM_PROMPT", "analyze_messages"]
