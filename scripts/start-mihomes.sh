#!/bin/sh
set -e

export MIHOMES_DEMO=1
export MIHOMES_DIR=/data/mihomes

echo "Initializing database..."
python3 -c "
from mihomes.config import ensure_dirs
from mihomes.db import init_db
ensure_dirs()
init_db()
"

echo "Loading demo data if needed..."
python3 -c "
import os
os.environ['MIHOMES_DEMO'] = '1'
from mihomes.db import get_session
from sqlalchemy import text
with get_session() as s:
    count = s.execute(text('SELECT COUNT(*) FROM properties')).scalar()
    if count == 0:
        from mihomes.services.demo import load_demo_data
        load_demo_data(s)
        print('Demo data loaded.')
    else:
        print(f'Demo data already present ({count} properties).')
"

echo "Starting MiHomes API on port 8080..."
exec uvicorn mihomes.api.app:app --host 0.0.0.0 --port 8080
