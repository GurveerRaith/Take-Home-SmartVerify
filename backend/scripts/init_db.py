"""Reset the development database.

Drops and recreates every table from schema.sql, then loads seed.sql.
Destructive by design: all data in the target database is lost.

Usage:  python backend/scripts/init_db.py
"""

import os
import sys
import time
from pathlib import Path

import psycopg
from dotenv import load_dotenv

# __file__ is backend/scripts/init_db.py, so two levels up is backend/.
# Resolved from the script's own location so the script works no matter
# which directory it is run from.
BACKEND_DIR = Path(__file__).resolve().parent.parent
SCHEMA_FILE = BACKEND_DIR / "db" / "schema.sql"
SEED_FILE = BACKEND_DIR / "db" / "seed.sql"
ENV_FILE = BACKEND_DIR.parent / ".env"

# Docker Compose reads .env automatically; Python does not. Loading it here
# means both sides take their configuration from the same file. The path is
# explicit rather than searched, so it does not depend on the working
# directory. Real environment variables already set are not overwritten,
# so DATABASE_URL=... on the command line still wins.
load_dotenv(ENV_FILE)

# Fallback if .env is missing entirely. Matches the defaults in
# docker-compose.yml.
DEFAULT_DSN = "postgresql://policy:policy@localhost:5433/smartverify"


def wait_for_database(dsn: str, attempts: int = 30) -> None:
    """Block until Postgres accepts connections.

    `docker compose up -d` returns before Postgres is ready to serve, so
    connecting immediately fails intermittently. Retrying removes the race.
    """
    for attempt in range(1, attempts + 1):
        try:
            with psycopg.connect(dsn, connect_timeout=3):
                return
        except psycopg.OperationalError as exc:
            if attempt == attempts:
                raise
            if attempt == 1:
                # Report the real reason once, up front. A permanent
                # misconfiguration (wrong database name, wrong password) is
                # indistinguishable from "not ready yet" until you read the
                # message, and retrying silently hides it for 30 seconds.
                reason = str(exc).strip().splitlines()[0]
                print(f"  not reachable yet: {reason}")
            print(f"  waiting for database ({attempt}/{attempts})")
            time.sleep(1)


def run_sql_file(cur, path: Path) -> None:
    """Execute every statement in a .sql file.

    psycopg sends the file as a single command when no parameters are
    passed, so multi-statement files work as-is. Splitting on ';' would
    break on semicolons inside string literals.
    """
    if not path.exists():
        raise FileNotFoundError(f"missing SQL file: {path}")
    cur.execute(path.read_text())
    print(f"  applied {path.name}")


def summarise(cur) -> None:
    """Print who can see what, so a seeding mistake is visible immediately."""
    cur.execute(
        """
        SELECT u.email, count(ut.tenant_id)
        FROM users u
        LEFT JOIN user_tenants ut ON ut.user_id = u.id
        GROUP BY u.email
        ORDER BY u.email
        """
    )
    print("\nSeeded users:")
    for email, tenant_count in cur.fetchall():
        print(f"  {email}: {tenant_count} tenant(s)")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", DEFAULT_DSN)

    # Show the target without the credentials.
    print(f"Resetting database at {dsn.rsplit('@', 1)[-1]}")
    print("This DROPS ALL TABLES in that database.\n")

    try:
        wait_for_database(dsn)
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                run_sql_file(cur, SCHEMA_FILE)
                run_sql_file(cur, SEED_FILE)
                summarise(cur)
    except Exception as exc:
        print(f"\nFailed: {exc}", file=sys.stderr)
        return 1

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())