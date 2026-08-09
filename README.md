# SmartVerify — Cedar Policy File Service

A service for uploading, listing, downloading and deleting Cedar policy files.
File content is stored in a Git repository; metadata lives in PostgreSQL and
serves as the query index. Access is scoped per tenant.

- [DESIGN.md](DESIGN.md) — architecture and design decisions
- [TEST_PLAN.md](TEST_PLAN.md) — test cases, execution reports, bugs and fixes

---

## Status

| Component               | State                              |
| ----------------------- | ---------------------------------- |
| Database schema         | Done                               |
| Seed data               | Done                               |
| Database init script    | Done                               |
| Backend API             | Not started                        |
| Cedar validation        | Not started                        |
| Git storage layer       | Not started                        |
| Frontend                | Not started                        |

Everything below describes what currently runs.

---

## Prerequisites

- **Docker Desktop** — runs PostgreSQL, so no local Postgres install is needed
- **Python 3.11+** — developed against 3.14

You do not need `psql` installed. The container ships with it, and the
instructions below run it inside the container.

---

## Quick start

From the repository root.

**1. Create and activate a virtual environment.**

Homebrew's Python is externally managed (PEP 668) and refuses direct installs,
so a virtualenv is required rather than optional.

```bash
python3 -m venv .venv
```

```bash
source .venv/bin/activate
```

**2. Install backend dependencies.**

```bash
pip install -r backend/requirements.txt
```

**3. Start PostgreSQL.**

`--wait` blocks until the container's healthcheck passes, so the next step
never races the database.

```bash
docker compose up -d --wait
```

**4. Create the schema and load the seed data.**

```bash
python backend/scripts/init_db.py
```

Expected output:

```
Resetting database at localhost:5433/smartverify
This DROPS ALL TABLES in that database.

  applied schema.sql
  applied seed.sql

Seeded users:
  alice@globex.example: 2 tenant(s)
  bob@globex.example: 1 tenant(s)
  carol@initech.example: 1 tenant(s)

Done.
```

`init_db.py` is destructive by design — it drops every table and rebuilds from
`schema.sql`, then loads `seed.sql`. Re-run it after any change to either file;
that is the normal edit-test loop.

---

## Seeded data

Two customers, three tenants, three users. The fixture is shaped so that every
isolation scenario can be tested — in particular Bob belongs to Globex but has
no grant on `globex/staging`, which is what makes "same customer, wrong tenant"
distinguishable from "different customer".

| User                    | Customer | Tenants                               | Token         |
| ----------------------- | -------- | ------------------------------------- | ------------- |
| `alice@globex.example`  | Globex   | `globex/production`, `globex/staging` | `alice_token` |
| `bob@globex.example`    | Globex   | `globex/production`                   | `bob_token`   |
| `carol@initech.example` | Initech  | `initech/production`                  | `carol_token` |

Both customers have a tenant named `production`, deliberately — it proves Git
paths are built from customer *and* tenant rather than tenant alone.

---

## Inspecting the database

Interactive session:

```bash
docker exec -it smartverify-db psql -U policy -d smartverify
```

Useful commands inside: `\dt` lists tables, `\d policy_files` shows one table's
full definition including constraints and indexes, `\q` quits.

Single query without an interactive session:

```bash
docker exec smartverify-db psql -U policy -d smartverify -c "SELECT email FROM users ORDER BY email"
```

Confirm which tenants each user can reach:

```bash
docker exec smartverify-db psql -U policy -d smartverify -c "SELECT u.email, c.folder_name AS customer, string_agg(t.folder_name, ', ' ORDER BY t.folder_name) AS tenants FROM users u JOIN customers c ON c.id=u.customer_id LEFT JOIN user_tenants ut ON ut.user_id=u.id LEFT JOIN tenants t ON t.id=ut.tenant_id GROUP BY u.email, c.folder_name ORDER BY u.email"
```

Postgres's own logs, if the container misbehaves:

```bash
docker compose logs db
```

---

## Stopping

Stop the container, keeping the data volume:

```bash
docker compose down
```

Stop and destroy the data volume, for a genuinely cold start:

```bash
docker compose down -v
```

Running from cold is worth doing periodically — it is what a reviewer will do.

---

## Configuration

Settings live in `.env` at the repository root. The credentials are local
development values only.

`.env` is gitignored, following the usual practice of keeping configuration out
of version control. It is included in the distributed archive, so no setup is
needed. Every value below also has a fallback — `docker-compose.yml` uses
`${VAR:-default}` substitution and `init_db.py` keeps a `DEFAULT_DSN` — so the
stack still starts correctly if `.env` is absent.

| Variable            | Purpose                       | Default                |
| ------------------- | ----------------------------- | ---------------------- |
| `POSTGRES_USER`     | Database user                 | `policy`               |
| `POSTGRES_PASSWORD` | Database password             | `policy`               |
| `POSTGRES_DB`       | Database name                 | `smartverify`          |
| `POSTGRES_PORT`     | Host port for Postgres        | `5433`                 |
| `DATABASE_URL`      | Connection string for Python  | see `.env`             |

Port 5433 rather than 5432 so the container never collides with a Postgres
already running on the host.

**Note:** Docker Compose reads `.env` automatically. Python does not — reading
`DATABASE_URL` from it needs `python-dotenv` or an exported shell variable.
`init_db.py` falls back to the same value when `DATABASE_URL` is unset, so the
two agree either way.

To point the init script at a different database:

```bash
DATABASE_URL=postgresql://policy:policy@localhost:5433/smartverify_test python backend/scripts/init_db.py
```

---

## Project layout

```
backend/
  db/
    schema.sql        tables, constraints, indexes
    seed.sql          customers, tenants, users, grants
  scripts/
    init_db.py        drops, recreates and seeds the database
  app/                API (not yet implemented)
  tests/              test suite (not yet implemented)
  requirements.txt
frontend/             React UI (not yet implemented)
data/
  policy-repo/        Git repository holding policy file content
                      (created at runtime, not committed)
docker-compose.yml    PostgreSQL service
.env                  local development configuration
```
