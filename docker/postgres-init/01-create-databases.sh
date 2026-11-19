#!/bin/sh
# Runs once, only when the postgres data volume is first initialized (the official
# image's /docker-entrypoint-initdb.d convention) — not on every `docker compose up`.
#
# SPEC-002 keeps three databases separate on purpose and this mirrors that split
# for local dev, the same way CI's postgres service does for itself:
#   mihomes         the app's own database (docker-compose.yml's `mihomes` service,
#                    once it is pointed at DATABASE_URL instead of MIHOMES_DEMO — see
#                    that service's comment; not done by this compose file yet, S7)
#   mihomes_test    this suite (TEST_DATABASE_URL) — see tests/conftest.py
#   mihomes_phase0  the alembic_landing/ tree (SPEC-001's waitlist) — LANDING_TEST_DATABASE_URL
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
    CREATE DATABASE mihomes_test;
    CREATE DATABASE mihomes_phase0;
EOSQL
