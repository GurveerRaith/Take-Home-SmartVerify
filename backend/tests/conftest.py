"""Shared pytest fixtures.

Tests run against a separate database (`smartverify_test`) and a throwaway Git
repository, so running the suite never touches development data.

Create the test database once:

    docker exec smartverify-db createdb -U policy smartverify_test

Then run the suite from the repository root:

    pytest
"""

import os
import subprocess
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
SCHEMA_FILE = BACKEND_DIR / "db" / "schema.sql"
SEED_FILE = BACKEND_DIR / "db" / "seed.sql"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

TEST_DSN = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://policy:policy@localhost:5433/smartverify_test",
)

# Point the application at the test database. This must happen before the app
# is imported, because backend/app/db.py reads DATABASE_URL at import time.
os.environ["DATABASE_URL"] = TEST_DSN

from fastapi.testclient import TestClient  # noqa: E402
from backend.app.main import app  # noqa: E402

# --------------------------------------------------------------------------
# Seeded values. Hardcoded in seed.sql precisely so tests can refer to them.
# --------------------------------------------------------------------------

ALICE_TOKEN = "alice_token"
BOB_TOKEN = "bob_token"
CAROL_TOKEN = "carol_token"

GLOBEX_PRODUCTION = "aaaaaaaa-0000-0000-0000-000000000001"
GLOBEX_STAGING = "aaaaaaaa-0000-0000-0000-000000000002"
INITECH_PRODUCTION = "bbbbbbbb-0000-0000-0000-000000000001"

NONEXISTENT_UUID = "99999999-9999-9999-9999-999999999999"

VALID_POLICY = b"permit(principal, action, resource);\n"

# Kept in step with MAX_FILE_BYTES in backend/app/policies.py.
MAX_UPLOAD_BYTES = 1024 * 1024


def auth(token: str) -> dict:
    """Authorization header for a seeded token."""
    return {"Authorization": f"Bearer {token}"}


def policies_url(tenant_id: str) -> str:
    """Collection URL for one tenant's policy files."""
    return f"/api/tenants/{tenant_id}/policies"


def upload(client, tenant_id, token, filename, content=VALID_POLICY):
    """Upload a policy file as the given user."""
    return client.post(
        policies_url(tenant_id),
        headers=auth(token),
        files={"file": (filename, content, "text/plain")},
    )


def read_fixture(name: str) -> bytes:
    """Read a .cedar fixture, e.g. read_fixture("valid/simple-permit.cedar")."""
    return (FIXTURES_DIR / name).read_bytes()


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def require_test_database():
    """Refuse to run against anything but a test database, and fail early.

    Two protections, both session-wide:

    1. The target database name must end in `_test`. Every test drops and
       recreates all tables, so pointing TEST_DATABASE_URL at the development
       database -- by habit, a stale shell variable, or a typo -- would destroy
       real data. Refusing outright is cheaper than trusting care.

    2. If the database is unreachable, exit once with instructions. Otherwise
       every test fails with the same connection error and the real cause is
       buried.
    """
    dbname = conninfo_to_dict(TEST_DSN).get("dbname", "")
    if not dbname.endswith("_test"):
        pytest.exit(
            f"\nRefusing to run: '{dbname}' is not a test database.\n"
            "The suite drops and recreates every table before each test.\n"
            "Point TEST_DATABASE_URL at a database whose name ends in '_test'.\n",
            returncode=1,
        )

    try:
        psycopg.connect(TEST_DSN, connect_timeout=3).close()
    except psycopg.OperationalError as exc:
        pytest.exit(
            f"\nCannot reach the test database at {TEST_DSN.rsplit('@', 1)[-1]}\n"
            f"  {str(exc).splitlines()[0]}\n\n"
            "Start Postgres and create the test database:\n"
            "  docker compose up -d --wait\n"
            "  docker exec smartverify-db createdb -U policy smartverify_test\n",
            returncode=1,
        )


@pytest.fixture(scope="session", autouse=True)
def never_touch_the_development_repo(tmp_path_factory):
    """Point POLICY_REPO_PATH away from data/policy-repo for the whole session.

    `policy_repo` overrides this per test for anything that needs a working
    repository. This is the floor beneath it: without it, a test that reached
    git_repo without requesting that fixture would fall back to the real
    development repository and write to it.

    The fallback directory is deliberately *not* a Git repository, so such a
    test fails loudly with "not a git repository" instead of quietly succeeding
    somewhere harmless -- the missing fixture is the actual bug.
    """
    fallback = tmp_path_factory.mktemp("no-policy-repo-requested")
    os.environ["POLICY_REPO_PATH"] = str(fallback)
    yield
    os.environ.pop("POLICY_REPO_PATH", None)


@pytest.fixture(autouse=True)
def reset_database():
    """Rebuild the test database before every test.

    Applied per test rather than per session so tests cannot leak state into
    each other and start passing or failing depending on the order they run in.
    """
    with psycopg.connect(TEST_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_FILE.read_text())
            cur.execute(SEED_FILE.read_text())


@pytest.fixture
def db():
    """A connection for asserting directly against the database.

    Used by tests that check what the API wrote, rather than what it returned.
    """
    with psycopg.connect(TEST_DSN, row_factory=dict_row) as conn:
        yield conn


@pytest.fixture
def policy_repo(tmp_path, monkeypatch):
    """A throwaway Git repository for one test.

    `git_repo.py` reads POLICY_REPO_PATH so tests can redirect it here instead
    of writing to the development repository under data/.
    """
    repo = tmp_path / "policy-repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    git("init", "--initial-branch=main")
    git("config", "user.name", "SmartVerify Test")
    git("config", "user.email", "test@smartverify.local")
    git("commit", "--allow-empty", "-m", "Initialise test policy repository")

    monkeypatch.setenv("POLICY_REPO_PATH", str(repo))
    return repo


@pytest.fixture
def client(policy_repo):
    """FastAPI test client, wired to the test database and a temp Git repo."""
    with TestClient(app) as test_client:
        yield test_client


def git_log(repo: Path) -> list[str]:
    """Commit subjects in a policy repo, newest first. For consistency tests."""
    result = subprocess.run(
        ["git", "log", "--format=%s"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip().splitlines()


def git_show(repo: Path, commit_sha: str, path: str) -> bytes:
    """Read a file's content at a specific commit, as the API does."""
    result = subprocess.run(
        ["git", "show", f"{commit_sha}:{path}"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    return result.stdout
