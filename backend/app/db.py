"""Database connection handling.

One connection per request, opened by a FastAPI dependency and closed when the
request finishes. A connection pool would be the production choice; at this
scale the extra concept is not worth it. See DESIGN.md.
"""

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

# backend/app/db.py -> parents[2] is the repository root.
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(ENV_FILE)

# Same fallback as init_dev.py, for when .env is absent.
DEFAULT_DSN = "postgresql://policy:policy@localhost:5433/smartverify"
DSN = os.environ.get("DATABASE_URL", DEFAULT_DSN)


def get_connection():
    """FastAPI dependency yielding a database connection for one request.

    The `with` block commits when the request succeeds and rolls back if it
    raises, so no route needs to manage transactions itself.
    """
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        yield conn