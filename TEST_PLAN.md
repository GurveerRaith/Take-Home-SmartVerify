# Test Plan

## Running the tests

The suite runs against a separate database so it never touches development
data. Create it once:

```bash
docker exec smartverify-db createdb -U policy smartverify_test
```

Then, from the repository root:

```bash
pytest
```

`backend/tests/conftest.py` rebuilds the test database from `schema.sql` and
`seed.sql` before **every** test, and gives each test a throwaway Git
repository, so no test can depend on another having run first.

---

## Approach

The scenarios worth covering outnumber the tests worth writing. Rather than one
test per scenario, related cases are grouped into parametrized tests: 15 test
functions cover roughly 40 scenarios, and a failure still names the exact case.

Weighting is deliberate. Tenant isolation is the hardest requirement, so it gets
5 of the 15. Two of those (T-04 and T-06) exist to catch specific defects that
every other test would pass through.

Status legend: `—` not run · `PASS` · `FAIL`
Priority: **P0** must pass to submit · **P1** important

---

## Test suite

### Authentication

| ID   | Pri | Test                                                                     | Asserts                                          | Result | Last run |
| ---- | --- | ------------------------------------------------------------------------ | ------------------------------------------------ | ------ | -------- |
| T-01 | P0  | Bad credentials rejected<br>*param:* no header, malformed header, unknown token | 401, and an identical body in all three cases | PASS   | 2026-08-09 |
| T-02 | P0  | `/api/me` returns exactly the granted tenants<br>*param:* alice=2, bob=1, carol=1 | correct identity and tenant list per token  | PASS   | 2026-08-09 |

T-01 uses one message for every failure mode on purpose. Distinguishing "no
token" from "unknown token" would tell a caller whether a token exists.

### Tenant isolation

| ID   | Pri | Test                                                                | Asserts                                       | Result | Last run |
| ---- | --- | ------------------------------------------------------------------- | --------------------------------------------- | ------ | -------- |
| T-03 | P0  | Carol lists `globex/production` — different customer                | 404                                           | PASS   | 2026-08-09 |
| T-04 | P0  | **Bob lists `globex/staging`** — own customer, no grant             | 404                                           | PASS   | 2026-08-09 |
| T-05 | P0  | Carol targets a real Globex file by exact UUID<br>*param:* GET, download, DELETE | 404 each; file still present afterwards | PASS   | 2026-08-09 |
| T-06 | P0  | Valid file ID requested under the wrong tenant path                 | 404                                           | PASS   | 2026-08-09 |
| T-07 | P0  | Upload to a tenant the caller was not granted                       | 404; no Git commit; no metadata row           | PASS   | 2026-08-09 |

Why these two matter most:

- **T-04** is the only test that fails if authorisation is done by *customer*
  rather than by *tenant grant*. Bob exists in the fixture solely to make it
  expressible.
- **T-06** is the only test that fails if a file is looked up by `id` alone
  instead of `WHERE id = ? AND tenant_id = ?`.

T-05 also asserts that the response for another tenant's real file is identical
to the response for a file that does not exist — a difference would let a caller
enumerate which IDs are real.

### Cedar validation

| ID   | Pri | Test                                                          | Asserts                                             | Result | Last run |
| ---- | --- | ------------------------------------------------------------- | --------------------------------------------------- | ------ | -------- |
| T-08 | P0  | Valid policies accepted<br>*param:* all 4 `fixtures/valid/`    | 201, file retrievable                               | PASS   | 2026-08-09 |
| T-09 | P0  | Invalid policies rejected<br>*param:* all 7 `fixtures/invalid/` | 400 (never 500), message names the actual problem | PASS   | 2026-08-09 |

The invalid fixtures cover syntax errors, non-Cedar prose, non-UTF-8 bytes, and
the two cases that *parse successfully* but define nothing (empty and
comments-only — see BUG-04).

### Upload rules

| ID   | Pri | Test                                                                 | Asserts                                   | Result | Last run |
| ---- | --- | -------------------------------------------------------------------- | ----------------------------------------- | ------ | -------- |
| T-10 | P0  | Bad filenames rejected<br>*param:* `../`, `/`, `.txt`, leading dot, >255 chars | 400; nothing written outside the tenant directory | PASS | 2026-08-09 |
| T-11 | P0  | Duplicate live filename → 409; the same name after deletion → 201    | uniqueness scoped to live rows only       | PASS   | 2026-08-09 |

T-11 is what the partial unique index exists for: uniqueness that applies to
live files without permanently reserving a deleted name.

### Behaviour and consistency

| ID   | Pri | Test                                                                          | Asserts                                             | Result | Last run |
| ---- | --- | ----------------------------------------------------------------------------- | --------------------------------------------------- | ------ | -------- |
| T-12 | P0  | Full lifecycle: upload → appears in list → download → delete → gone from list  | download is byte-identical to upload                | PASS   | 2026-08-09 |
| T-13 | P0  | The same filename in two tenants stays independent                            | each tenant gets its own content                    | PASS   | 2026-08-09 |
| T-14 | P1  | Git content at the stored `commit_sha` matches the upload; a rejected upload leaves no commit and no row | both stores agree            | PASS   | 2026-08-09 |
| T-15 | P0  | No cross-customer grants exist in `user_tenants`                              | zero rows                                           | PASS   | 2026-08-09 |

T-14 is the only test that inspects **both** stores. Everything else could pass
with a broken Git layer.

T-15 is the compensating control for the invariant the schema does not enforce
structurally — see DESIGN.md decision 4. It must not be skipped.

---

## Coverage mapping

Every scenario originally enumerated, and the test that now covers it.

| Test | Scenarios covered                                              |
| ---- | -------------------------------------------------------------- |
| T-01 | no header, malformed header, unknown token, no detail leaked in error body |
| T-02 | valid token identity, Alice's 2 tenants, Bob's 1 tenant, per-token isolation |
| T-03 | cross-customer list denied                                     |
| T-04 | same-customer ungranted tenant denied                          |
| T-05 | cross-tenant metadata read, download, delete; identical responses for real vs nonexistent |
| T-06 | valid file ID under the wrong tenant path                      |
| T-07 | cross-tenant upload denied; Git history unchanged; no metadata row |
| T-08 | single policy, multi-statement policy, conditional policy       |
| T-09 | missing semicolon, misspelled effect, unbalanced brace, non-Cedar prose, non-UTF-8, empty file, comments-only |
| T-10 | traversal filename, `/` in filename, wrong extension, leading dot, over-length |
| T-11 | duplicate filename 409, name reuse after delete, DB-level uniqueness |
| T-12 | upload, list, download fidelity, delete, exclusion of deleted rows |
| T-13 | same filename across tenants, per-tenant content separation     |
| T-14 | commit SHA resolves to the uploaded bytes, git_path correctness, one commit per upload, rejected upload leaves both stores clean |
| T-15 | cross-customer grant invariant                                 |

---

## Not automated

Identified, considered, and deliberately left out — with reasons.

| Scenario                        | Why not                                                              |
| ------------------------------- | -------------------------------------------------------------------- |
| Fault injection between the two stores | Requires patching mid-write to simulate a crash. The write ordering is argued in DESIGN.md decision 1; proving it needs machinery out of proportion to a take-home. |
| Concurrent uploads of the same filename | The partial unique index makes the database the arbiter, so correctness does not depend on application timing. Reliable concurrency tests need process coordination. |
| Frontend component tests        | Would add a JavaScript test toolchain for little marginal confidence. Covered by the manual checklist below. |
| Malformed UUID in the path      | FastAPI rejects it with 422 before any application code runs; testing it would test the framework. |

---

## Manual UI checklist

Verified by hand rather than automated, and used as the script for the demo
recording. The steps below run all seven checks in a single pass.

| #   | Check                                                        | Result | Date |
| --- | ------------------------------------------------------------ | ------ | ---- |
| M-1 | Sign in with a seeded token reaches the file list            | —      | —    |
| M-2 | Tenant switcher shows only granted tenants (Bob sees one)    | —      | —    |
| M-3 | Switching tenant reloads the correct file list               | —      | —    |
| M-4 | Uploading an invalid Cedar file shows the validation message | —      | —    |
| M-5 | Uploading a valid file updates the list without a refresh    | —      | —    |
| M-6 | Download returns the right filename and content              | —      | —    |
| M-7 | Delete removes the file from the list                        | —      | —    |

### Setup

Start from a freshly seeded environment so the recording is reproducible:

```bash
docker compose up -d --wait && python backend/scripts/init_dev.py
```

Then, in two more terminals:

```bash
uvicorn backend.app.main:app --reload
```

```bash
cd frontend && npm run dev
```

Open <http://localhost:5173> in a **real browser window**, not an embedded
preview — M-6 requires a genuine download to disk.

### Walkthrough

**1. Sign in as `bob_token`** — covers **M-1**, and half of **M-2**.

Expect `bob@globex.example · Globex`, with the tenant dropdown containing only
**Production**. Open the dropdown so the single entry is visible: Bob's customer
owns two tenants and he holds a grant on one. This is the isolation model made
visible, and it is the same scenario as T-04.

**2. Sign out, sign in as `alice_token`** — completes **M-2**.

Expect two tenants: Production and Staging.

**3. Switch between Production and Staging** — **M-3**.

Expect Production to list `admin-access.cedar` and `read-only.cedar`, and
Staging to list `admin-access.cedar` only. The same filename in two tenants is
deliberate seeded content.

**4. Upload an invalid policy** — **M-4**.

Choose `backend/tests/fixtures/invalid/missing-semicolon.cedar`.

Expect `Invalid Cedar syntax: unexpected end of input`, and the list unchanged.
Repeat with `invalid/empty.cedar`, which gives a different message
(`File contains no Cedar policy statements...`) — showing the errors come from
the parser rather than a single canned string. See BUG-04.

**5. Upload a valid policy** — **M-5**.

Choose `backend/tests/fixtures/valid/simple-permit.cedar`.

Expect a success message and the file at the top of the list with no manual
refresh. Uploading `read-only.cedar` into Production instead would return 409,
which is correct but makes a worse demonstration.

**6. Download `admin-access.cedar`** — **M-6**.

Expect it to save under that exact name. Confirm the content matches:

```bash
diff ~/Downloads/admin-access.cedar data/policy-repo/globex/production/admin-access.cedar
```

No output means byte-identical.

**7. Delete a file** — **M-7**.

Expect a confirmation prompt, then the row disappears from the list.

### Isolation, demonstrated directly

The requirement is that isolation holds "even with a hand-crafted API request
that bypasses the UI", so it is worth showing outside the interface:

```bash
curl -i -H "Authorization: Bearer carol_token" \
  "http://localhost:8000/api/tenants/aaaaaaaa-0000-0000-0000-000000000001/policies"
```

Expect **404**. Carol is authenticated and the tenant is real; she still gets
nothing, and the response is indistinguishable from a tenant that does not
exist.

Then sign in as `carol_token` in the interface and download **her**
`admin-access.cedar`. Same filename as Alice's, entirely different content —
one name, two tenants, no leakage.

---

## Execution report

Captured with:

```bash
pytest -v | tee test-output.txt
```

**Environment:** macOS · Python 3.14.3 · pytest 9.1.1 · PostgreSQL 16 (Docker)
**Suite:** 15 planned tests, expanded to **57 cases** by parametrization.

| Run | Date       | Passed | Failed | Notes                                  |
| --- | ---------- | ------ | ------ | -------------------------------------- |
| 1   | 2026-08-09 | 57     | 0      | First full run after the API completed |

Cases per test file:

| File                       | Cases | Covers      |
| -------------------------- | ----- | ----------- |
| `test_auth.py`             | 13    | T-01, T-02  |
| `test_isolation.py`        | 11    | T-03 – T-07 |
| `test_cedar_validation.py` | 12    | T-08, T-09  |
| `test_upload_rules.py`     | 13    | T-10, T-11  |
| `test_behaviour.py`        | 8     | T-12 – T-15 |

Full output is in [test-output.txt](test-output.txt). Tail:

```
backend/tests/test_isolation.py::test_t03_cross_customer_list_is_denied PASSED
backend/tests/test_isolation.py::test_t04_same_customer_ungranted_tenant_is_denied PASSED
backend/tests/test_isolation.py::test_t05_cross_tenant_access_by_exact_id_is_denied[metadata] PASSED
backend/tests/test_isolation.py::test_t05_cross_tenant_access_by_exact_id_is_denied[download] PASSED
backend/tests/test_isolation.py::test_t05_cross_tenant_access_by_exact_id_is_denied[delete] PASSED
backend/tests/test_isolation.py::test_t05_denied_and_nonexistent_are_identical PASSED
backend/tests/test_isolation.py::test_t06_valid_file_id_under_the_wrong_tenant_path PASSED
backend/tests/test_isolation.py::test_t07_cross_customer_upload_writes_nothing PASSED
backend/tests/test_isolation.py::test_t07_same_customer_ungranted_upload_writes_nothing PASSED

============================== 57 passed in 6.82s ==============================
```

---

## Bugs and fixes

One entry per defect, including those found during development rather than only
at the end.

| ID | Summary | Status |
| --- | --- | --- |
| BUG-01 | `seed.sql` aborted: column/value count mismatch | Fixed |
| BUG-02 | Alice's and Bob's tenant grants were inverted | Fixed |
| BUG-03 | Init script reported failure over a fully seeded database | Fixed |
| BUG-04 | Empty and comment-only files pass Cedar validation | Fixed |
| BUG-05 | Design doc claimed Cedar errors include position info | Fixed |
| BUG-06 | API unreachable after rebuilding the virtualenv | Fixed (environment) |
| BUG-07 | Init script ignored `POLICY_REPO_PATH` | Fixed |
| BUG-08 | Downloads failed intermittently — object URL revoked too early | Fixed |
| BUG-09 | Sample policy filenames did not match their stored names | Fixed |

### BUG-01 — `seed.sql` aborted: column/value count mismatch

**Found by:** applying `schema.sql` then `seed.sql` to a clean PostgreSQL 16
database, before any application code existed.

**Symptom:** `ERROR: INSERT has more target columns than expressions` on the
`tenants` insert. The file is transaction-wrapped, so the whole seed rolled
back, leaving a correctly built but empty database.

**Cause:** the column list named five columns while each `VALUES` tuple supplied
four — `created_at` had no matching expression.

**Fix:** removed `created_at` from the column list; the schema already defaults
it.

**Regression test:** INV-02 / INV-03, extended to apply `seed.sql` as well as
`schema.sql`.

### BUG-02 — Alice's and Bob's tenant grants were inverted

**Found by:** reviewing `seed.sql` against the fixture spec, then confirming with
a "who can see what" query after seeding.

**Symptom:** comments described Alice as having production *and* staging and Bob
production only. The rows gave Alice one grant and Bob two.

**Why it mattered:** Bob exists solely to be a Globex user *without* a
`globex/staging` grant — that is what makes ISO-03 testable. With the grants
inverted, ISO-03 could never fail, and a defect authorising by customer instead
of by tenant grant would have passed every isolation test.

**Fix:** moved the `staging` grant from Bob to Alice.

**Regression test:** AUTH-05, AUTH-06, ISO-03.

**Follow-up (done):** `seed.sql` now ends with a `DO` block that raises if any
grant crosses customers. It sits *above* the `COMMIT` deliberately — verified
both ways, below the `COMMIT` it raises the same error but the bad grant is
already committed. An assertion only prevents damage if it shares a transaction
with the write it checks.

### BUG-03 — Init script reported failure over a fully seeded database

**Found by:** running the init script after refactoring, while it still called a
helper that had moved into `seed.sql`.

**Symptom:** printed `Failed: name 'check_no_cross_customer_grants' is not
defined` and exited 1, implying nothing had been applied. The database was in
fact fully populated.

**Cause:** the stale call was trivial. The misleading report was not:
`schema.sql` and `seed.sql` each carry their own `BEGIN`/`COMMIT`, so both had
already committed before Python raised. The `with psycopg.connect(...)` context
manager had nothing left to roll back.

**Fix:** removed the stale call, and corrected the assumption that the
connection context manager makes the two files atomic as a unit. It does not.

**Lesson:** the script's exit code describes the script, not the database.

### BUG-04 — Empty and comment-only files pass Cedar validation

**Found by:** spiking `cedarpy` against sample files before writing the upload
path.

**Symptom:** empty, whitespace-only and comments-only files all parse
successfully — `PolicySet.from_str("")` returns without error.

**Cause:** an empty policy set is valid Cedar. The parser is correct; the
assumption that "parses" implies "is usable" was not.

**Impact if unfixed:** CED-05 would fail, and the service would accept policy
files that grant and forbid nothing.

**Fix:** `app/cedar.py` counts policies after parsing. The parser exposes no
count, so the set is serialised with `policies_to_json_str` and its
`staticPolicies` counted; zero is rejected with a distinct message.

**Regression test:** CED-05, plus fixtures `invalid/empty.cedar` and
`invalid/comments-only.cedar`.

### BUG-05 — Design doc claimed Cedar errors include position information

**Found by:** the same `cedarpy` spike.

**Symptom:** DESIGN.md decision 10 justified parse-level validation partly on the
grounds that the parser's errors include position information. They do not:

```
missing semicolon  ->  unexpected end of input
typo in keyword    ->  invalid policy effect: permitt
unbalanced brace   ->  unexpected token `;`
```

**Cause:** an assumption about a library recorded as fact before it was verified.

**Impact:** a documentation defect, not a code one. The decision still holds —
the messages name the offending token — but the reasoning was wrong, and CED-03
expected the body to identify *where*.

**Fix:** reworded decision 10 and CED-03 to match observed behaviour. Reporting a
line number would mean locating the token in the source ourselves; recorded as a
possible improvement, not a requirement.

### BUG-06 — API unreachable after rebuilding the virtualenv

**Found by:** deleting `.venv`, reinstalling the dependencies, and then finding
`curl localhost:8000/api/health` hung with no response.

**Symptom:** the health endpoint timed out rather than refusing the connection.
`lsof` showed a Python process holding port 8000 in the `CLOSED` state, and a new
`uvicorn` refused to start with `[Errno 48] address already in use`. Both
Postgres and the rebuilt virtualenv were healthy.

**Cause:** the `uvicorn` server was still running from the *deleted* virtualenv.
Removing `.venv` deletes the interpreter and packages out from under a live
process; it does not stop it. The process survived in a broken state, still
holding its socket, so the port was neither serving nor free. Reinstalling the
dependencies had no effect, because the running process was not using them.

A second, unrelated contributor: three orphaned `uvicorn` processes from earlier
manual verification were still bound to other ports. They had been started in the
background and the `kill %1` used to stop them referred to a job in a shell that
had already exited, so it silently did nothing.

**Fix:** stopped the stale process, then started the server with the documented
command. `{"status":"ok"}` returned immediately. No application code was
involved — the same command also worked before the change, confirming the import
restructure was not the cause.

**Prevention:** the README now says to stop the server before rebuilding the
virtualenv, and documents `pkill -f uvicorn` for a background process.

**Lesson:** "the API does not respond" has at least three distinct causes that
look alike — nothing listening, something listening but broken, and something
listening on a different port. `lsof -nP -iTCP:8000` distinguishes them
immediately, and a *timeout* rather than *connection refused* is the signal that
a process is holding the socket without serving.

### BUG-07 — Init script honoured `DATABASE_URL` but ignored `POLICY_REPO_PATH`

**Found by:** asking how to point the application at the test database and test
policy repository, and reading which module consumed which variable.

**Symptom:** `init_dev.py` read `DATABASE_URL`, so it could be aimed at any
database, but its policy repository path was a hardcoded constant. Running

```bash
DATABASE_URL=...smartverify_test POLICY_REPO_PATH=/tmp/scratch/policy-repo \
    python backend/scripts/init_dev.py
```

would seed the test database as asked while **deleting and recreating the
development policy repository**, which the caller had explicitly redirected away
from.

**Cause:** the configuration convention was only half implemented.
`backend/app/git_repo.py` reads `POLICY_REPO_PATH` on every call so tests can
redirect writes; `init_dev.py` was written earlier and never updated to match,
so the application and the script could disagree about which repository was in
play.

**Impact:** no test caught this, because the test suite never invokes
`init_dev.py` — `conftest.py` applies the SQL files itself. The failure mode was
data loss in the one situation the variable exists to prevent.

**Fix:** `init_dev.py` now has a `repo_path()` helper reading `POLICY_REPO_PATH`
with the same default, mirroring `git_repo.py`. The existing guard, which
refuses any path not ending in `policy-repo`, still applies and now also limits
where the environment variable can point.

**Verified:** with both variables set, the test database is seeded and a scratch
repository is created, while the development database (3 live files) and
repository (6 commits) are untouched.

**Lesson:** a configuration convention honoured in some places and not others is
worse than none at all, because it looks like it works. When a variable exists
to redirect writes, every writer has to read it.

### BUG-08 — Downloads failed intermittently: object URL revoked too early

**Found by:** manual testing of the download button in the browser. The
reported symptom was that the downloaded file did not seem to use the filename
shown in the interface.

**Symptom:** intermittent. A download would sometimes not produce the expected
file. Because it did not fail every time, it initially looked like a naming
problem.

**Investigation:** the naming path was traced end to end and found correct —

| Step | Value |
| --- | --- |
| List row | `read-only.cedar` |
| `Content-Disposition` (readable cross-origin) | `attachment; filename="read-only.cedar"` |
| Parsed by `filenameFromResponse` | `read-only.cedar` |
| `link.download` set to | `read-only.cedar` |

Instrumenting the click showed the real problem: `URL.revokeObjectURL` ran
**45 ms after** `link.click()`, in the same task.

**Cause:** `saveBlob` released the object URL synchronously after clicking the
synthetic link. That races the browser reading the blob; if the release wins,
the download fails or saves under the wrong name. The intermittency is inherent
to a race.

**Fix:** the revoke is deferred to a later task rather than firing in the same
tick. Memory is still released; the download gets a chance to start first.
`filenameFromResponse` was also hardened to accept an unquoted `filename=`.

**Verified:** every row's Download button now sets the matching filename, with
nothing revoked during the download window. The same filename in two tenants
still downloads different content.

**Lesson:** the reported symptom and the actual cause were in different places.
Tracing the naming path first — and proving it correct — is what made the
timing problem visible.

### BUG-09 — Sample policy filenames did not match their stored names

**Found by:** noticing that `backend/db/sample-policies/globex-readonly.cedar`
appears in the interface as `read-only.cedar`.

**Symptom:** the filename on disk and the filename stored by `init_dev.py`
were different, with nothing in the folder to indicate the mapping. Reading the
samples directory gave a misleading impression of what the seeded environment
would contain.

**Cause:** all sample files sat in one flat directory and therefore needed
unique names, so a five-column table in `init_dev.py` mapped each source file
to a different stored name:

```python
("globex", "production", "read-only.cedar", "globex-readonly.cedar", "alice@globex.example")
```

The same fact — which file belongs where, under what name — was recorded in two
places: the directory contents and that table. Nothing kept them in agreement.

**Fix:** `sample-policies/` now mirrors the policy repository layout, so a file
at `globex/production/admin-access.cedar` is stored under exactly that path with
exactly that name. The mapping table is gone; seeding walks the directory and
derives customer, tenant and filename from the path. Only a two-entry
customer-to-uploader map remains.

**Consequence:** adding a sample file is now dropping it in the right folder,
with no code change and no second place to update.

**Lesson:** the third instance of the same root cause in this project, after
BUG-02 (fixture comments disagreeing with fixture rows) and BUG-07 (a
configuration variable honoured in one place and not another). One fact
recorded twice will eventually disagree; deriving it from a single source
removes the possibility rather than reducing the likelihood.
