# SmartVerify Design Document

## High Level Architecture Diagram

![Architecture Diagram](architecture.png)

## Sequence Diagram (uploading a file)

User -> API: POST /api/tenants/{tenant_id}/policies (+ token, file)

API -> Postgres: resolve user, check tenant_id is in user's tenants -- fails -> 404
(404 rather than 403: a 403 would confirm that the tenant
exists, letting a caller enumerate other tenants' resources)

API -> API: validate filename pattern -- fails -> 400

API -> Cedar: parse policy -- fails -> 400 + parser message

API -> Postgres: check (tenant_id, filename) not taken -- taken -> 409

API -> Git: write to {customer}/{tenant}/{file}, commit -> SHA

API -> Postgres: INSERT metadata row (incl. commit SHA)

API -> User: 201 + metadata

## Design Choices

Each entry states what was considered, what was chosen, why, and what it costs.

| #   | Decision              | Chose                                 | Over                            |
| --- | --------------------- | ------------------------------------- | ------------------------------- |
| 1   | Write ordering        | Git first, Postgres second            | Metadata first; 2PC             |
| 2   | Isolation enforcement | Shared auth dependency + query filter | Per-endpoint checks; RLS        |
| 3   | Denying access        | 404                                   | 403                             |
| 4   | Cross-customer grants | Guard inside the seed transaction     | Composite foreign key           |
| 5   | Reading content       | By `commit_sha`                       | From the working tree           |
| 6   | Delete                | Soft delete + `git rm`                | Hard delete                     |
| 7   | Filename uniqueness   | Partial unique index                  | Plain unique constraint         |
| 8   | Path safety           | `CHECK` constraints + app validation  | App validation only             |
| 9   | FK delete behaviour   | Chosen per relationship               | Uniform `CASCADE`; all defaults |
| 10  | Cedar validation      | Parse-level + non-empty check         | Schema validation               |
| 11  | Authentication        | Seeded plaintext bearer tokens        | JWT + login; hashed tokens      |
| 12  | Schema management     | Re-runnable init script               | Alembic migrations              |
| 13  | Primary keys          | UUID                                  | `BIGSERIAL`                     |
| 14  | Database connections  | One per request                       | Connection pool                 |
| 15  | API response shape    | A projection, enforced by a model     | Returning the row               |
| 16  | Git concurrency       | One process-wide write lock           | No lock; advisory lock          |
| 17  | Configuration         | Environment variables with fallbacks  | Hardcoded paths and DSNs        |
| 18  | Browser access        | CORS restricted to named origins      | `allow_origins=["*"]`           |
| 19  | Test strategy         | 15 parametrized tests, per-test reset | One test per scenario           |

### 1. Write ordering: Git before Postgres

**Considered:** Writing the metadata row first then Git commit; Git commit first then
metadata row; a distributed transaction across both stores.

**Chose:** Write to Git first, then PostgreSQL. For deletion, delete from PostgreSQL first then from Git.

**Why:** There is no shared transaction across the two stores, so a partial
failure is always possible and the only real choice is which direction it
fails in. Writing Git first means a crash leaves content in the repository
that no metadata row points at: invisible to the API and harmless, since Git
is append-only. The reverse order leaves a metadata row pointing at content
that does not exist, which surfaces as a broken download. A distributed transaction would better suit a production scenario, but since this is a take home assignment I opted against it.

**Cost:** Orphaned files can accumulate in Git after failed uploads. They are
invisible and cheap, and a reconciliation job could clean them up if it ever
mattered.

### 2. Where tenant isolation is enforced

**Considered:** Checking the tenant grant inside each endpoint; a single shared
dependency that resolves and validates the tenant before any handler runs;
PostgreSQL row-level security.

**Chose:** A single auth dependency that resolves the caller and validates the
tenant from the URL path, combined with a rule that every query filters on
`tenant_id` as well as the row's own id.

**Why:** Per-endpoint checks fail open — one endpoint written without the check
is a breach, and nothing structural prevents that from happening. A shared
dependency means a handler cannot run at all without a validated tenant scope,
so the check cannot be forgotten. The query filter is a second, independent
layer: even if a caller obtains a real file id belonging to another tenant, a
query of the form `WHERE id = ? AND tenant_id = ?` returns no rows and the
request 404s naturally. Row-level security would add a genuine third layer, but
it requires session-variable plumbing on every connection and is
disproportionate for a single-service application of this size.

**Cost:** The `tenant_id` filter is a convention rather than something the
database enforces, so it relies on discipline in every query. ISO-09 exists
specifically to catch a lookup written by file id alone.

### 3. Denying access with 404 rather than 403

**Considered:** Returning 403 Forbidden for a tenant or file the caller may not
access; returning 404 Not Found.

**Chose:** 404 in all cases, with an identical response body regardless of
whether the resource exists.

**Why:** A 403 is an admission that the resource exists. A caller iterating over
file ids could distinguish "this id is real but belongs to someone else" from
"this id is not real", which leaks the existence, count and identifiers of
another tenant's files without ever reading one. Returning 404 for both cases
means an unauthorised caller cannot tell the two apart. This matters more here
than in a typical application because tenants are mutually distrusting.

**Cost:** Slightly less helpful for legitimate users who genuinely mistype an
id, since they cannot tell a missing resource from one they lack access to. For
an authorization service that trade is worth making.

### 4. Enforcing the cross-customer grant invariant

**Considered:** A composite foreign key on `user_tenants` referencing both
`users (id, customer_id)` and `tenants (id, customer_id)`, which would make a
cross-customer grant structurally impossible; enforcing the rule only in
application code; asserting it in the seed transaction.

**Chose:** An assertion inside `seed.sql`, positioned above the `COMMIT`, plus a
standing test (INV-01).

**Why:** Nothing in the schema currently prevents a row granting a user access to
a tenant belonging to a different customer. If such a row existed, the tenant
check would legitimately pass and the API would serve another customer's files —
the isolation logic would be correct and the data would be wrong. The composite
foreign key does prevent it structurally, but it requires denormalising
`customer_id` into the join table and adding two unique constraints that exist
only to satisfy the foreign key. Given a fixed seeded user set with no runtime
grant management, a guard in the seed transaction covers the realistic risk at a
fraction of the complexity.

The position of the guard matters as much as its existence. Above the `COMMIT`,
a violation rolls the entire seed back and nothing persists. Below it, the same
error is raised but the bad grant is already committed — detection instead of
prevention. This was verified both ways.

**Cost:** The invariant is enforced by convention rather than by the database, so
it would need revisiting if grants ever became editable at runtime.

### 5. Reading file content by commit SHA

**Considered:** Reading the file from the Git working tree by path; storing the
commit SHA on each metadata row and reading with `git show <sha>:<path>`.

**Chose:** Store `commit_sha` and `git_path` on every row, and read content at
that specific commit.

**Why:** Reading from the working tree returns whatever the file happens to
contain now, which is not necessarily what the metadata row describes. Pinning
the commit means a row always resolves to an immutable snapshot, so metadata and
content cannot drift apart. It also means version history is already addressable
if file versioning is added later, without a schema change.

`git_path` is stored rather than derived from the customer and tenant folder
names because it is a historical fact: it records where the content was actually
written. If a folder name were ever renamed, a derived path would break while the
stored one still resolves.

**Cost:** One extra column, and reads go through Git plumbing rather than a plain
file read.

### 6. Soft delete rather than hard delete

**Considered:** Deleting the metadata row outright; marking it with a
`deleted_at` timestamp and removing the file from the Git working tree.

**Chose:** Soft delete — set `deleted_at`, and remove the file from Git in a
commit that leaves the content in history.

**Why:** A hard delete destroys the record that the file ever existed, including
who uploaded it and when. Because policy files control authorization, the history
of what was in place at a given time has real value. Soft deleting keeps that
record queryable, and removing the file in a Git commit rather than rewriting
history means the content itself stays recoverable.

**Cost:** Every query that lists files must remember `WHERE deleted_at IS NULL`,
and deleted rows accumulate. Both indexes are partial on the same predicate so
the cost stays off the common path.

### 7. Allowing filename reuse after deletion

**Considered:** A plain unique constraint on `(tenant_id, filename)`; no
constraint with the check done in application code; a partial unique index.

**Chose:** `CREATE UNIQUE INDEX ... ON policy_files (tenant_id, filename) WHERE
deleted_at IS NULL`.

**Why:** Soft delete and uniqueness conflict. A plain constraint counts deleted
rows, so a filename could never be reused after deletion. An application-only
check leaves a race between concurrent uploads. The partial index solves both:
it applies only to live rows, and the database still enforces it, so a race
produces a constraint violation rather than a duplicate.

**Cost:** Less obvious than a plain constraint, and the predicate must match the
one used in queries for the index to be used.

### 8. Enforcing path safety in the database

**Considered:** Validating filenames in the API only; adding `CHECK` constraints
on filename and folder-name columns as well.

**Chose:** Both — an API-level check for a clear error message, and `CHECK`
constraints as a backstop.

**Why:** The filename becomes part of a filesystem path inside the Git
repository, so a value containing `/` or `..` could write outside its tenant's
directory. The API validation is the primary control and produces the useful
error, but it only protects paths that go through the API. A `CHECK` constraint
holds for any writer — a future import script, a manual `INSERT`, a fixture. The
character set deliberately excludes `/`, which is what makes traversal
unrepresentable rather than merely rejected.

**Cost:** The rule is expressed in two places and they must be kept in agreement.
INV-04 and INV-05 test the database layer directly, independently of the API.

### 9. Foreign key delete behaviour

**Considered:** Leaving every foreign key at the default; applying `CASCADE`
uniformly; choosing per relationship.

**Chose:** Per relationship. `CASCADE` on `user_tenants`; `RESTRICT` on the
ownership chain and on `uploaded_by`.

**Why:** The question for each foreign key is whether the child row still means
anything once the parent is gone. An access grant for a deleted user is a
dangling pointer that nobody would want to keep, so it cascades. A policy file's
upload record is evidence, and deleting a user should not silently destroy the
history of what they uploaded — so that restricts. `RESTRICT` there also encodes
a real position: user deletion is not a supported operation, and users should be
deactivated instead. Applying `CASCADE` uniformly would have made deleting one
user quietly delete policy file metadata.

**Cost:** Removing a user who has uploaded files requires deliberate cleanup
rather than a single delete. That friction is intentional.

### 10. Scope of Cedar validation

**Considered:** Parse/syntax validation only; full schema validation against a
Cedar schema; no validation beyond a file extension check.

**Chose:** Parse validation using the `cedarpy` bindings, plus an explicit check
that the file defines at least one policy.

**Why:** Schema validation would check that the entity types and actions a policy
references actually exist — but that needs a Cedar schema per tenant, which the
requirements never mention and provide no way to supply. Validating against a
schema that does not exist is not possible, so this is out of scope rather than
skipped.

The second check exists because an empty, whitespace-only or comments-only file
is _valid_ Cedar: it parses to an empty policy set. Accepting one would store a
policy file that grants and forbids nothing. Verified and logged as BUG-04.

**Cost:** Two limitations, both known. A policy that parses but references
non-existent entity types is accepted (CED-09). And the parser's errors name the
offending token — `unexpected token \`;\`` — but carry no line or column, so
error messages say what is wrong but not where. Reporting a position would mean
locating the token in the source myself; recorded as a possible improvement.

### 11. Authentication

**Considered:** Email and password with a login endpoint issuing a JWT; seeded
bearer tokens stored as hashes; seeded bearer tokens stored in plaintext.

**Chose:** Seeded bearer tokens, stored in plaintext, presented as
`Authorization: Bearer <token>`.

**Why:** The requirements ask for minimal authentication and explicitly exclude
OAuth, session management and signup. A login flow issuing JWTs would add token
expiry, refresh and signing-key handling without testing anything the assignment
is actually about. Tokens are stored in plaintext because these are seeded
demonstration credentials that ship with the project and are printed in the
README — hashing them would protect nothing while making the seed data harder to
read. In a real deployment I would store a SHA-256 of the token and compare
hashes on lookup; a fast hash is sufficient there because a random token has far
more entropy than a password.

**Cost:** No token expiry or revocation. Acceptable for fixed demonstration
users, and not extendable to real ones without the change described above.

### 12. Schema management

**Considered:** A migration framework such as Alembic; `CREATE TABLE IF NOT
EXISTS`; a re-runnable script that drops and recreates everything.

**Chose:** `schema.sql` with explicit drops at the top, applied by
`init_dev.py`.

**Why:** Migrations exist to evolve a schema without losing data that cannot be
regenerated. Every row here is seeded or re-uploadable, so there is nothing to
preserve. `CREATE TABLE IF NOT EXISTS` was rejected outright: it silently does
nothing when a table exists, so a schema change appears to apply and does not —
worse than an error. Drop-and-recreate guarantees the database matches the file,
and gives tests a known clean starting state.

**Cost:** The script destroys all data, so it is run deliberately by a person and
never wired into application startup. Alembic would be the first addition if this
held data worth keeping.

### 13. Primary keys

**Considered:** `BIGSERIAL` integers; UUIDs generated by the database.

**Chose:** UUIDs via `gen_random_uuid()` on every table.

**Why:** File ids appear in URLs, and sequential integers invite enumeration — a
caller can walk `/policies/1`, `/policies/2` and learn how many files exist.

To be precise about what this buys: unguessable ids are defence in depth, **not**
the control. The control is that every query filters by the tenant from the
validated auth scope, so a correctly guessed id still returns nothing.

**Cost:** Less convenient while debugging. Mitigated by hardcoding readable UUIDs
in the seed data so tests and examples reference fixed values.

### 14. Database connections

**Considered:** A connection pool (`psycopg_pool`); a new connection per
request.

**Chose:** One connection opened per request by a FastAPI dependency, closed
when the request finishes.

**Why:** A pool is the right answer for a service under real load, and is only a
few lines more. It was rejected here because it adds a lifecycle to explain and
manage for a project whose traffic is a demonstration. The per-request approach
also gives transaction handling for free: the `with psycopg.connect(...)` block
commits when a request succeeds and rolls back when it raises, so no route
manages transactions itself.

FastAPI caches dependency results within a request, so a route depending on both
`get_tenant_scope` and `get_connection` receives the _same_ connection, not two.

**Cost:** A connection is established per request, which would matter under
load. Moving to a pool is a change to one function.

### 15. Shape of API responses

**Considered:** Returning database rows directly; defining an explicit response
model.

**Chose:** A Pydantic `PolicyFileOut` model listing exactly the five fields the
interface needs, set as `response_model` on each route.

**Why:** The row carries `git_path` and `commit_sha`, which are internal storage
details. Publishing them would leak the repository layout — telling a caller
that files live at `{customer}/{tenant}/{file}` is free information for anyone
probing for traversal — and would tie the API contract to the schema, so a
column rename would break clients.

The mechanism matters as much as the intent: FastAPI _strips_ any field absent
from the model, so a query that starts selecting `git_path` cannot leak it by
accident. The rule is enforced rather than remembered.

`uploaded_by` is resolved to an email address rather than returned as a UUID,
because that is what the interface displays.

**Cost:** One model to keep in step with the interface's needs.

### 16. Serialising Git writes

**Considered:** No locking; a process-wide lock; a PostgreSQL advisory lock.

**Chose:** A single `threading.Lock` around every Git write.

**Why:** Git's index is one file with no concurrency protection, and FastAPI
runs synchronous endpoints in a thread pool — so two uploads genuinely can
interleave and corrupt it. A process-wide lock is three lines and removes the
problem entirely for a single-process deployment.

**Cost:** It only holds within one process. Running multiple API workers would
need a PostgreSQL advisory lock, or a single writer that owns the repository.
This is the first thing I would change before scaling out.

### 17. Configuration and testability

**Considered:** Hardcoding the database DSN and repository path; reading both
from the environment.

**Chose:** `DATABASE_URL` and `POLICY_REPO_PATH`, each with a working fallback,
and `.env` loaded explicitly via `python-dotenv`.

**Why:** These two variables are what make the test suite safe. Tests point
`DATABASE_URL` at `smartverify_test` and `POLICY_REPO_PATH` at a temporary
repository, so running the suite cannot touch development data or the real
policy repository.

`repo_path()` reads the environment on every call rather than at import time.
Captured at import, a test's `monkeypatch.setenv` would arrive too late and
every test would write to the real repository.

Fallbacks mean the project still runs with no `.env` present at all, which is
why the same commands work from a clone or from the distributed archive.

**Cost:** The default DSN is duplicated between `docker-compose.yml` and the
application. They are asserted to match by the fact that the stack starts.

### 18. Browser access

**Considered:** `allow_origins=["*"]`; naming the permitted origins.

**Chose:** An explicit list containing only the Vite development server.

**Why:** The API is called with an `Authorization` header. Allowing any origin
would let an arbitrary page script the API using a token it had obtained. Naming
the origins costs nothing and keeps the browser enforcing the boundary.

`Content-Disposition` is added to `expose_headers`, without which the browser
hides it from JavaScript and the frontend cannot read a download's filename —
a failure that appears only in the browser and not in `curl`.

**Cost:** A deployed frontend would need its origin added.

### 19. Test strategy

**Considered:** One test per scenario (about 75); a smaller set of parametrized
tests; testing only the happy paths.

**Chose:** 15 tests, parametrized into 57 cases, weighted towards isolation.

**Why:** Enumerating every scenario separately produces a suite that is
expensive to write and read while testing nothing extra. Parametrization keeps
the coverage and names each case in the output, so a failure still identifies
the exact input.

Isolation gets five of the fifteen because it is the hardest requirement. Two of
those exist to catch specific defects nothing else would: T-04 fails if
authorisation is done by customer rather than by tenant grant, and T-06 fails if
a file is looked up by id without its tenant.

The database is rebuilt before _every_ test rather than once per session. It is
slower, but it is what makes the suite order-independent — without it a test
that deletes rows changes the result of whatever runs next.

**Cost:** About 50 ms per test for the reset, and four scenario groups left
unautomated (fault injection, concurrency, frontend components, framework-level
validation), each recorded with its reasoning in TEST_PLAN.md.

## Considered and deliberately excluded

Features that were evaluated and left out, with the reasoning:

- **A `policy_file_versions` table.** Git already stores every version. A second
  system modelling the same history creates the possibility of the two
  disagreeing. If versioning were in scope I would add a `superseded_by` column
  rather than a parallel table.
- **A separate audit table.** The Git log already records every content change
  with an author and timestamp. A dedicated audit table would duplicate it.
- **PostgreSQL row-level security.** A genuine third isolation layer, but the
  session-variable plumbing is disproportionate for one service. It is the first
  thing I would add if multiple services shared this database.
- **Cedar schema validation.** Requires a per-tenant schema the requirements do
  not describe. See decision 10.
- **Roles and permissions.** Authentication was scoped to minimal. An unused
  `role` column would imply an authorization model that was not built.
- **`updated_at` on `policy_files`.** Rows are inserted and soft-deleted, never
  updated. A column that never changes is misleading.

## Additional features to extend this project by

These are features that I did not implement, but they are potential next steps if this project were to be developed further

### 1. Policy File Rollbacks from Previous Versions

Being able to rollback to previous versions of policy files would be really good because it acts as an instant safety net, allowing teams to undo bad permission changes, stop accidental security lockouts, or fix syntax logic errors by reverting to a known working version without downtime

Having a history shown of file content would also make sense, something similar to Github where the user could see how the file looked like at certain commits in time.

### 2. Caching Policy File Content

Every download currently shells out to `git show <commit_sha>:<path>`, which
spawns a process. Measured on this machine, that is a median of **8.8 ms** per
read, against **0.4 ms** for the tenant authorisation query and effectively zero
for an in-memory lookup. Reading content is by far the most expensive thing the
service does, and it is the one thing that is trivially cacheable.

What makes it trivial is a property that falls out of decision 5. Content is
addressed by commit SHA, and a commit is immutable — the bytes at
`(commit_sha, git_path)` can never change. Cache invalidation, normally the hard
part of caching, simply does not arise: there is no event that makes an entry
wrong. Entries are evicted for space, never for staleness. A cache keyed on a
mutable identifier such as the file id would need invalidating on every upload
and delete; keyed on the commit SHA, it needs nothing.

I would add it in two layers:

**In-process cache.** An LRU keyed on `(commit_sha, git_path)`, bounded by total
bytes rather than entry count, since policy files vary in size. Roughly ten lines
around `git_repo.read_file`. Policy files are small and re-read far more often
than they are written, so a small cache would serve most reads.

**HTTP caching.** Because the content is immutable, the download response can
carry `ETag: <commit_sha>` and `Cache-Control: private, max-age=31536000,
immutable`. A browser that has already downloaded a file would not request it
again, removing the round trip entirely rather than just making it faster.

`private` is essential rather than cosmetic. These responses are authorised per
tenant, so a shared cache — a corporate proxy, a CDN — must never be allowed to
store one and serve it to a different tenant. That would defeat the isolation
model outside the application entirely, which is exactly the kind of failure that
does not show up in tests of the application itself.

**What I would not cache** is the authorisation lookup in `get_tenant_scope`,
even though it runs on every single request and is the obvious candidate. It
costs 0.4 ms, so there is little to win — and caching it would mean a revoked
tenant grant continued to work until the entry expired. Decision 11 deliberately
reads grants from the database per request so that revocation takes effect
immediately; caching them would trade a security property for a fifth of a
millisecond. The file list is similar: Postgres already serves it from an index,
and a cache would need invalidating on every upload and delete, which is real
complexity for no measurable gain.

That contrast is the interesting part of this feature. The naive instinct is to
cache the thing that runs most often; the right answer here is to cache the thing
that is most expensive and immutable, and to leave the cheap, security-
sensitive, mutable thing alone.

### 3. Cedar Schema Validation Per Tenant

Validation today is parse-level only, which decision 10 records as a deliberate
limitation rather than an oversight: checking that a policy refers to entity
types and actions that actually exist requires a Cedar schema, and the
requirements never describe one or provide a way to supply it.

The gap this leaves is real. A policy containing a typo parses perfectly and is
accepted, but does nothing:

```
permit(principal == User::"alice", action == Action::"veiw", resource == Folder::"reports");
```

`Action::"veiw"` is valid Cedar syntax. It is also a permission that will never
match anything, and the current service will happily store it and show it in the
list as a healthy file. Silently ineffective authorisation rules are arguably
worse than rules that are obviously broken.

Adding a schema closes it. `cedarpy` already exposes
`validate_policies(policies, schema)`, which returns a result carrying specific
errors — for the policy above it reports
`unrecognized action `Action::"veiw"`` — so the message shown to the user would
be as actionable as the parse errors already are.

**Where the schema would live.** A tenant's schema is itself a file with content
and history, so Git is the natural home, stored beside the policies it governs
at `{customer}/{tenant}/schema.cedarschema`. That is consistent with the existing
storage model and gives schema changes the same audit trail as policy changes.

It does collide with an existing constraint, which is worth stating rather than
glossing over: `policy_files_filename_format` requires names ending in `.cedar`,
so a schema file could not be stored through the current upload path. It would
need either a separate endpoint and table, or a widened constraint plus a column
distinguishing a schema from a policy. I would prefer the separate endpoint —
a schema is a different kind of object with a different lifecycle, and one per
tenant rather than many.

**Validation would have to be optional.** A tenant with no schema must keep
working exactly as it does now, falling back to parse-only validation. Making it
mandatory would break every existing tenant, and would force customers to model
their entire entity hierarchy before uploading a single policy.

**The hard part is not the validation, it is the drift.** Policies are validated
on upload against the schema as it stood at that moment. If the schema is later
changed — an action renamed, an entity type removed — policies that were valid
when stored silently become invalid, and nothing would notice. Options I would
weigh: re-validate every policy in a tenant whenever its schema is replaced and
refuse the change if any would break; or accept the change and surface affected
files as warnings in the list. The first is safer, the second is more usable, and
the right answer probably depends on whether a customer is expected to fix
policies before or after a schema migration.

That question — what happens to stored data when the rules that validated it
change — is the genuinely interesting design problem here, and it is the reason
this is a larger piece of work than "call one more library function on upload".
