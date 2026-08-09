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

**Why:** Soft delete and uniqueness conflict directly. A plain unique constraint
counts deleted rows, so once a filename has been used it can never be used again
in that tenant — surprising behaviour for a user who deletes a file and re-uploads
it. Doing the check only in application code leaves a race between two concurrent
uploads. The partial index resolves both: it applies only to live rows, so names
free up on deletion, and it is still enforced by the database so a race produces
a constraint violation rather than a duplicate.

**Cost:** Slightly less obvious than a plain constraint, and the predicate must
match the one used in queries for the index to be used.

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

**Chose:** Parse validation using the `cedarpy` bindings, rejecting failures with
the parser's own error message.

**Why:** The requirement is that uploaded files are valid Cedar and that failures
produce a clear, actionable message. Parsing satisfies both, and the parser's
error already includes position information, which is more useful than anything I
would write by hand. Full schema validation would additionally check that entity
types and actions exist — but that requires a Cedar schema per tenant, which the
requirements never mention and which there is no mechanism to supply. Validating
against a schema that does not exist is not possible, so schema validation is out
of scope rather than skipped.

**Cost:** A policy that parses but references entity types that do not exist is
accepted. This is documented as CED-09 so the behaviour is recorded as intended
rather than discovered as a defect.

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
`init_db.py`.

**Why:** Migrations exist to evolve a schema without losing data that cannot be
regenerated. Every row in this database is either seeded or re-uploadable, so
there is nothing to preserve and no migration history worth maintaining.
`CREATE TABLE IF NOT EXISTS` was rejected outright because it silently does
nothing when a table already exists — a schema change appears to apply and does
not, which is worse than an error. Drop-and-recreate guarantees the database
matches the file exactly, and gives tests a known clean starting state.

**Cost:** Running the script destroys all data, so it is kept in a script a
person runs deliberately and never wired into application startup. Alembic would
be the first thing added if this held data worth keeping.

### 13. Primary keys

**Considered:** `BIGSERIAL` integers; UUIDs generated by the database.

**Chose:** UUIDs via `gen_random_uuid()` on every table.

**Why:** File ids appear in URLs, and sequential integers invite enumeration — a
caller can walk `/policies/1`, `/policies/2` and learn how many files exist.
UUIDs make that pointless. It is worth being precise about what this does and
does not buy: unguessable ids are defence in depth, not the control. The actual
control is that every query filters by the tenant from the validated auth scope,
so a correctly guessed id still returns nothing.

**Cost:** Less convenient to type while debugging. Mitigated by hardcoding
readable UUIDs in the seed data so tests and README examples can reference fixed
values.

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
