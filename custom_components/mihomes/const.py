"""Constants for the MiHomes integration."""

DOMAIN = "mihomes"
MANUFACTURER = "MiHomes"

CONF_API_URL = "api_url"
DEFAULT_API_URL = "http://localhost:8080"

SCAN_INTERVAL_MINUTES = 5

# API endpoints (relative to api_url) — all under /api/v1/
API_PROPERTIES = "/api/v1/properties"
API_TASKS = "/api/v1/tasks"
API_ISSUES = "/api/v1/issues"
API_ALERTS = "/api/v1/alerts"
API_DASHBOARD = "/api/v1/dashboard"
