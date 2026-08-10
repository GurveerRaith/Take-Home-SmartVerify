# SmartVerify — Cedar Policy File Service

Upload, list, download and delete Cedar policy files. Content lives in a Git
repository; metadata lives in PostgreSQL and serves as the query index. Access
is scoped per tenant.

- [DESIGN.md](DESIGN.md) — architecture and design decisions
- [TEST_PLAN.md](TEST_PLAN.md) — test cases, results, bugs and fixes

## Status

| Component         | State       |
| ----------------- | ----------- |
| Database schema   | Done        |
| Seed data         | Done        |
| Dev init script   | Done        |
| Cedar validation  | Done        |
| Git storage layer | Done        |
| Backend API       | Done        |
| Tests             | Done        |
| Frontend          | In progress — scaffold and API client done |

---

## Quick start

**Prerequisites:** Docker Desktop, Python 3.11+ (developed against 3.14), and
Node 20+ (developed against 23). `psql` is not needed — the container provides
it.

**Run every command from the repository root unless stated otherwise.**

Steps 6 and 7 each occupy a terminal, so you will need three in total.

**1. Create and activate a virtual environment.**

Homebrew's Python is externally managed (PEP 668) and refuses direct installs,
so a virtualenv is required rather than optional.

```bash
python3 -m venv .venv && source .venv/bin/activate
```

**2. Install the backend dependencies.**

```bash
pip install -r backend/requirements.txt
```

**3. Start PostgreSQL.**

```bash
docker compose up -d --wait
```

**4. Create the schema, load the seed data, and initialise the policy repo.**

```bash
python backend/scripts/init_dev.py
```

**5. Point your editor at the virtualenv.**

Only needed if you are opening the project in an editor, and it cannot be done
from a config file — every editor requires the interpreter to be selected once
per project.

- **VS Code / Cursor:** `Cmd+Shift+P` → "Python: Select Interpreter" →
  `./.venv/bin/python`, then reload the window.
- **PyCharm:** Settings → Project → Python Interpreter → add the existing
  interpreter at `.venv/bin/python`.

Skip this and imports such as `fastapi` and `psycopg` will show as unresolved
even though the code runs correctly.

**6. Start the API.**

```bash
uvicorn backend.app.main:app --reload
```

```bash
curl localhost:8000/api/health
```

Should return `{"status":"ok"}`. Interactive API docs are at
<http://localhost:8000/docs>.

Leave this running and open a new terminal for the next step.

**7. Start the frontend.**

```bash
cd frontend && npm install
```

```bash
npm run dev
```

Open <http://localhost:5173>. Sign in with any seeded token — `alice_token`,
`bob_token` or `carol_token` (see [Seeded data](#seeded-data)).

The frontend calls the API directly at `http://localhost:8000`, which the API
permits by naming the Vite origin in its CORS configuration. Override the API
location with `VITE_API_URL` if you run it elsewhere.

Both servers hot reload: `--reload` restarts the API when Python changes, and
Vite swaps modules on save.

### Stopping

`Ctrl+C` in each terminal. If either is left running in the background:

```bash
pkill -f uvicorn && pkill -f vite
```

PostgreSQL keeps running until stopped separately — see [Stopping](#stopping-1).

---

Step 3's `--wait` blocks until the database healthcheck passes, so step 4 never
races it. Expected output from step 4:

```
Resetting database at localhost:5433/smartverify
This DROPS ALL TABLES in that database, and rebuilds the
policy repository from scratch.

  applied schema.sql
  applied seed.sql

Seeded users:
  alice@globex.example: 2 tenant(s)
  bob@globex.example: 1 tenant(s)
  carol@initech.example: 1 tenant(s)
  initialised .../data/policy-repo

Done.
```

`init_dev.py` is destructive by design: it drops every table, reloads the seed
data, and rebuilds the policy Git repository from scratch. Re-run it after
editing `schema.sql` or `seed.sql` — that is the normal edit-test loop. Nothing
runs it automatically, so starting the API will never wipe your data.

The `/api/health` endpoint queries the database rather than just returning a
constant, so a failure there means the API cannot reach Postgres — not merely
that the process started.

All commands are run from the repository root. The application is imported as
`backend.app.main`, so no `PYTHONPATH` or `--app-dir` setting is needed —
editors, `pytest` and `uvicorn` all resolve it the same way.

**Rebuilding the virtualenv?** Stop the server first (`Ctrl+C`, or
`pkill -f uvicorn`). Deleting `.venv` while `uvicorn` is running leaves the
process alive but broken — see BUG-06 in [TEST_PLAN.md](TEST_PLAN.md).

### Optional: test database

Created once, then seeded by the same script:

```bash
docker exec smartverify-db createdb -U policy smartverify_test
```

```bash
DATABASE_URL=postgresql://policy:policy@localhost:5433/smartverify_test python backend/scripts/init_dev.py
```

Keeping tests off the main database means running the suite never destroys data
you are demonstrating with.

### Stopping

```bash
docker compose down      # stops the container, keeps the data volume
```

---

## Resetting

There are two levels, depending on how much you want to throw away.

**Reset the data** — drops and recreates every table, reloads the seed data, and
rebuilds the policy Git repository. The Postgres container and its volume are
left alone. This is the normal edit-test loop after changing `schema.sql` or
`seed.sql`:

```bash
python backend/scripts/init_dev.py
```

**Full cold rebuild** — additionally destroys the Postgres data volume, so the
database is recreated from nothing. This is what a reviewer starting from the
archive effectively does, and it is worth running occasionally to prove the
project still comes up from a clean machine:

```bash
docker compose down -v
```

```bash
docker compose up -d --wait
```

```bash
python backend/scripts/init_dev.py
```

The Git repository at `data/policy-repo/` does **not** need deleting by hand —
`init_dev.py` removes and re-initialises it every run. Deleting the `data/`
directory manually is harmless but unnecessary.

After a cold rebuild the `smartverify_test` database is gone too, since it lived
in the destroyed volume. Recreate it with the `createdb` command above if you
need it.

---

## Seeded data

| User                    | Customer | Tenants                               | Token         |
| ----------------------- | -------- | ------------------------------------- | ------------- |
| `alice@globex.example`  | Globex   | `globex/production`, `globex/staging` | `alice_token` |
| `bob@globex.example`    | Globex   | `globex/production`                   | `bob_token`   |
| `carol@initech.example` | Initech  | `initech/production`                  | `carol_token` |

The fixture is shaped so every isolation scenario is testable:

- **Bob** belongs to Globex but has no grant on `globex/staging` — this is what
  separates "same customer, wrong tenant" from "different customer".
- **Both customers have a tenant named `production`** — proves Git paths are
  built from customer _and_ tenant, not tenant alone.

---

## Inspecting the database

```bash
docker exec -it smartverify-db psql -U policy -d smartverify
```

Inside: `\dt` lists tables, `\d policy_files` shows one table's full definition,
`\q` quits.

Confirm which tenants each user can reach:

```bash
docker exec smartverify-db psql -U policy -d smartverify -c "SELECT u.email, string_agg(t.folder_name, ', ' ORDER BY t.folder_name) AS tenants FROM users u LEFT JOIN user_tenants ut ON ut.user_id=u.id LEFT JOIN tenants t ON t.id=ut.tenant_id GROUP BY u.email ORDER BY u.email"
```

Postgres logs, if the container misbehaves:

```bash
docker compose logs db
```

---

## Configuration

Local development values live in `.env` at the repository root.

| Variable            | Purpose                      | Default       |
| ------------------- | ---------------------------- | ------------- |
| `POSTGRES_USER`     | Database user                | `policy`      |
| `POSTGRES_PASSWORD` | Database password            | `policy`      |
| `POSTGRES_DB`       | Database name                | `smartverify` |
| `POSTGRES_PORT`     | Host port for Postgres       | `5433`        |
| `DATABASE_URL`      | Connection string for Python | see `.env`    |

Port 5433 rather than 5432 avoids colliding with a Postgres already running on
the host.

`.env` is gitignored but ships in the distributed archive. Every value has a
fallback — `docker-compose.yml` uses `${VAR:-default}` and `init_dev.py` keeps a
`DEFAULT_DSN` — so the stack starts correctly even without it.

**Note:** Docker Compose reads `.env` automatically; Python does not. `init_dev.py`
loads it explicitly via `python-dotenv`.

---

## Troubleshooting

**Imports show as unresolved in the editor.** Step 5 of the quick start was
skipped, or the editor needs a reload. The editor is analysing with a different
Python than `.venv`.

No path configuration is needed beyond that — imports are rooted at `backend.`,
so they resolve from the repository root without `PYTHONPATH`, `--app-dir` or
editor `extraPaths` settings.

**`ModuleNotFoundError: No module named 'backend'`.** The command was run from
somewhere other than the repository root.

**`connection refused` from `init_dev.py`.** PostgreSQL is not running. Run
`docker compose up -d --wait` first.

---

## Project layout

```
backend/
  app/
    main.py           app, CORS, /api/health, /api/me
    auth.py           token -> user, and the tenant scope guard
    policies.py       upload, list, download, delete
    cedar.py          Cedar policy validation
    git_repo.py       reads and writes the policy Git repository
    db.py             database connection dependency
  db/
    schema.sql        tables, constraints, indexes
    seed.sql          customers, tenants, users, grants
  scripts/
    init_dev.py       resets the database and the policy repo
  tests/
    conftest.py       fixtures, seeded constants, helpers
    test_*.py         57 cases across 5 files
    fixtures/         valid and invalid .cedar files
  requirements.txt
frontend/
  src/
    api.js            every backend call, in one place
    App.jsx           application shell
    main.jsx          entry point
    index.css         styles
  package.json
data/
  policy-repo/        Git repo holding policy content (runtime, not committed)
docker-compose.yml    PostgreSQL service
.env                  local development configuration
```
