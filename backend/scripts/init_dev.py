"""Reset the development environment.

Drops and recreates every table from schema.sql, loads seed.sql, then
recreates the Git repository that holds policy file content.
Destructive by design: all data in the target database is lost, and the
policy repository is deleted and rebuilt.

Usage:  python backend/scripts/init_dev.py
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psycopg
from dotenv import load_dotenv

# __file__ is backend/scripts/init_dev.py, so two levels up is backend/.
# Resolved from the script's own location so the script works no matter
# which directory it is run from.
BACKEND_DIR = Path(__file__).resolve().parent.parent
SCHEMA_FILE = BACKEND_DIR / "db" / "schema.sql"
SEED_FILE = BACKEND_DIR / "db" / "seed.sql"
SAMPLES_DIR = BACKEND_DIR / "db" / "sample-policies"
ENV_FILE = BACKEND_DIR.parent / ".env"
DEFAULT_POLICY_REPO = BACKEND_DIR.parent / "data" / "policy-repo"

# Running a script puts its own directory on sys.path, not the repository
# root, so `backend.app` is not importable without this. Reusing the
# application's Git and validation code keeps sample files on exactly the same
# path a real upload takes, rather than reimplementing it here.
sys.path.insert(0, str(BACKEND_DIR.parent))

from backend.app import git_repo  # noqa: E402
from backend.app.cedar import validate_policy_bytes  # noqa: E402

# Docker Compose reads .env automatically; Python does not. Loading it here
# means both sides take their configuration from the same file. The path is
# explicit rather than searched, so it does not depend on the working
# directory. Real environment variables already set are not overwritten,
# so DATABASE_URL=... on the command line still wins.
load_dotenv(ENV_FILE)

# Fallback if .env is missing entirely. Matches the defaults in
# docker-compose.yml.
DEFAULT_DSN = "postgresql://policy:policy@localhost:5433/smartverify"

# Sample content so a freshly initialised environment is not empty.
#
# backend/db/sample-policies/ mirrors the layout of the policy repository:
# a file at globex/production/admin-access.cedar is stored under exactly that
# path, with exactly that name. Nothing is renamed on the way in.
#
# admin-access.cedar deliberately appears in three tenants with different
# content, demonstrating that files are scoped per tenant and that two
# customers can hold the same filename without collision.
UPLOADER_BY_CUSTOMER = {
    "globex": "alice@globex.example",
    "initech": "carol@initech.example",
}


def repo_path() -> Path:
    """Location of the policy repository to reset.

    Reads POLICY_REPO_PATH, the same variable backend/app/git_repo.py uses, so
    the script and the application always agree on which repository is in play.
    """
    return Path(os.environ.get("POLICY_REPO_PATH", DEFAULT_POLICY_REPO))


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


def run_git(*args: str) -> None:
    """Run a git command inside the policy repository.

    The return code is checked by hand rather than with check=True, because
    CalledProcessError does not include stderr and git failures would come
    out as an exit status with no reason attached.
    """
    result = subprocess.run(
        ["git", *args], cwd=repo_path(), capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")


def reset_policy_repo() -> None:
    """Delete and recreate the Git repository that holds policy file content."""
    repo = repo_path()

    # Guard against a wrong path turning this into a recursive delete of
    # something else. Also stops POLICY_REPO_PATH being pointed at, say, a
    # home directory by accident.
    if repo.name != "policy-repo":
        raise RuntimeError(f"refusing to delete unexpected path: {repo}")

    shutil.rmtree(repo, ignore_errors=True)
    repo.mkdir(parents=True)

    run_git("init", "--initial-branch=main")
    # Set locally, never with --global: this must not touch the user's own
    # git identity. Without an identity configured the first commit fails.
    run_git("config", "user.name", "SmartVerify Policy Service")
    run_git("config", "user.email", "policy-service@smartverify.local")
    # An empty first commit gives the repo a real HEAD. Without any commit
    # HEAD is "unborn" and some git commands behave differently.
    run_git("commit", "--allow-empty", "-m", "Initialise policy repository")

    print(f"  initialised {repo}")


def seed_policy_files(conn) -> None:
    """Write the sample policies to Git, then record them in the database.

    Deliberately done here rather than in seed.sql. Each metadata row carries
    the commit SHA its content was stored in, and SQL cannot create Git
    commits -- rows seeded there would reference commits that do not exist, so
    the two stores would be inconsistent from initialisation.

    The order matches the upload endpoint: validate, write to Git, then insert
    the row. Content is committed before it becomes visible, so a failure part
    way through leaves unreferenced content rather than a listed file with
    nothing behind it. See DESIGN.md decision 1.
    """
    samples = sorted(SAMPLES_DIR.rglob("*.cedar"))
    if not samples:
        raise RuntimeError(f"no sample policies found in {SAMPLES_DIR}")

    for sample in samples:
        # The path under sample-policies/ *is* the path in the repository.
        customer, tenant, filename = sample.relative_to(SAMPLES_DIR).parts
        email = UPLOADER_BY_CUSTOMER[customer]
        content = sample.read_bytes()

        # Same validation the API applies, so a broken sample fails here
        # rather than being served to the interface.
        validate_policy_bytes(content)

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT t.id AS tenant_id, u.id AS user_id
                FROM tenants t
                JOIN customers c ON c.id = t.customer_id
                JOIN users u ON u.email = %s
                WHERE c.folder_name = %s AND t.folder_name = %s
                """,
                (email, customer, tenant),
            )
            row = cur.fetchone()

        if row is None:
            raise RuntimeError(f"no such tenant or user: {customer}/{tenant}, {email}")

        tenant_id, user_id = row

        git_path = git_repo.build_path(customer, tenant, filename)
        commit_sha = git_repo.write_file(
            git_path, content, f"Add {filename} to {customer}/{tenant} (by {email})"
        )

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO policy_files
                    (tenant_id, filename, git_path, commit_sha, size_bytes, uploaded_by)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (tenant_id, filename, git_path, commit_sha, len(content), user_id),
            )
        conn.commit()

    print(f"  seeded {len(samples)} sample policy files")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", DEFAULT_DSN)

    # Show the target without the credentials.
    print(f"Resetting database at {dsn.rsplit('@', 1)[-1]}")
    print("This DROPS ALL TABLES in that database, and rebuilds the")
    print("policy repository from scratch.\n")

    try:
        wait_for_database(dsn)
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                run_sql_file(cur, SCHEMA_FILE)
                run_sql_file(cur, SEED_FILE)
                summarise(cur)

        # The repository must exist before any content can be committed to it.
        reset_policy_repo()

        with psycopg.connect(dsn) as conn:
            seed_policy_files(conn)
    except Exception as exc:
        print(f"\nFailed: {exc}", file=sys.stderr)
        return 1

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())