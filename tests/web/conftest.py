"""Shared fixtures for web-layer tests.

Was an isolated in-memory SQLite engine. SPEC-002 Step 15 makes the suite
Postgres-only, and G2 made `account_id` NOT NULL on 40 tables — so this conftest
needed the same treatment as the root one. The spec's Fixtures paragraph does not
mention this second conftest at all; it surfaced as 123 errors, every one
`LookupError: <ContextVar name='current_account'>`, which is the fail-closed
behaviour working exactly as intended.

The client construction itself now lives in the root conftest's
`web_client_factory`, because three other files had hand-rolled the same body and
the fix had to reach all of them.
"""

import pytest


@pytest.fixture
def client(web_client_factory, seed_estate):
    return web_client_factory(seed_estate)
