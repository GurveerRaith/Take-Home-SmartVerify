# Test Plan

Test cases for the SmartVerify policy file service.

Status legend: `—` not yet run · `PASS` · `FAIL` · `N/A`
Priority: **P0** must pass to submit · **P1** important · **P2** stretch

---

## Assumptions

These are the decisions the plan is written against. If any change, the affected
cases change with them.

- Authentication is a bearer token seeded per user. There is no login flow and no
  password. `Authorization: Bearer <token>`.
- Tenant is addressed in the URL path: `/api/tenants/{tenant_id}/policies`.
- A request for a tenant the user has no grant for returns **404**, not 403, so
  that the API never confirms the existence of another tenant's resources.
- Deletes are soft in Postgres (`deleted_at`) and remove the file from the Git
  working tree, leaving the content in Git history.
- Uploads write to Git first, then insert the metadata row.

---

## Fixture data

All cases below refer to this seed. It is designed so that each isolation
scenario is expressible; in particular Bob exists so that "same customer, wrong
tenant" can be tested separately from "different customer".

| Customer    | slug      | Tenants                 |
| ----------- | --------- | ----------------------- |
| Globex Inc  | `globex`  | `production`, `staging` |
| Initech LLC | `initech` | `production`            |

| User                  | Customer | Granted tenants                       | Token         |
| --------------------- | -------- | ------------------------------------- | ------------- |
| alice@globex.example  | Globex   | `globex/production`, `globex/staging` | `alice_token` |
| bob@globex.example    | Globex   | `globex/production` only              | `bob_token`   |
| carol@initech.example | Initech  | `initech/production`                  | `carol_token` |

Notes on why the fixture looks like this:

- **Initech also has a tenant named `production`.** Two tenants sharing a folder
  name across different customers proves the Git path is built from customer and
  tenant together, and exercises the `UNIQUE (customer_id, folder_name)` constraint.
- **Bob is deliberately under-privileged** relative to his own customer. Without
  him, ISO-03 cannot be written, and a bug that authorises by customer instead of
  by tenant grant would pass every other test.
- **No `policy_files` rows are seeded.** Metadata rows carry a `commit_sha`; seeding
  them without matching Git commits would make the two stores inconsistent from
  initialisation. Files enter only through the upload path.

---

## 1. Authentication

| ID      | Pri | Scenario                                                        | Expected                              | Result |
| ------- | --- | --------------------------------------------------------------- | ------------------------------------- | ------ |
| AUTH-01 | P0  | Request with no `Authorization` header                          | 401                                   | —      |
| AUTH-02 | P0  | Malformed header (`Bearer`, no token; wrong scheme)             | 401                                   | —      |
| AUTH-03 | P0  | Well-formed but unknown token                                   | 401                                   | —      |
| AUTH-04 | P0  | Valid token on `GET /api/me`                                    | 200, correct user identity            | —      |
| AUTH-05 | P0  | `GET /api/me` as Alice                                          | lists exactly 2 tenants               | —      |
| AUTH-06 | P0  | `GET /api/me` as Bob                                            | lists exactly 1 tenant, not `staging` | —      |
| AUTH-07 | P1  | Token of a different user does not return the first user's data | correct identity per token            | —      |
| AUTH-08 | P2  | Auth failure body contains no user or tenant details            | generic message only                  | —      |

---

## 2. Tenant isolation

The core requirement. Every case here is an attempt to reach another tenant's
data with a hand-crafted request rather than through the UI.

### 2.1 Read isolation

| ID     | Pri | Scenario                                                                                                    | Expected                                              | Result |
| ------ | --- | ----------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- | ------ |
| ISO-01 | P0  | Alice lists `globex/production`                                                                             | 200, only that tenant's files                         | —      |
| ISO-02 | P0  | Alice lists `globex/staging`                                                                                | 200, only that tenant's files, no overlap with ISO-01 | —      |
| ISO-03 | P0  | **Bob lists `globex/staging`** — own customer, no grant                                                     | 404                                                   | —      |
| ISO-04 | P0  | **Carol lists `globex/production`** — different customer                                                    | 404                                                   | —      |
| ISO-05 | P0  | Carol requests metadata for a Globex file by its exact UUID                                                 | 404                                                   | —      |
| ISO-06 | P0  | Carol downloads a Globex file by its exact UUID                                                             | 404, no bytes returned                                | —      |
| ISO-07 | P1  | Request for a tenant UUID that does not exist                                                               | 404                                                   | —      |
| ISO-08 | P1  | Request with a malformed (non-UUID) tenant id                                                               | 422 or 400, not 500                                   | —      |
| ISO-09 | P1  | **Valid file ID under the wrong tenant path**: Alice requests a `staging` file ID via the `production` path | 404                                                   | —      |
| ISO-10 | P1  | `admin.cedar` exists in both `globex/production` and `initech/production`; each user downloads theirs       | distinct content, no cross-contamination              | —      |

ISO-09 is the case that catches a lookup done by file ID alone. If the query is
`WHERE id = ?` rather than `WHERE id = ? AND tenant_id = ?`, this is the test that
fails.

### 2.2 Write isolation

| ID     | Pri | Scenario                                                        | Expected                                    | Result |
| ------ | --- | --------------------------------------------------------------- | ------------------------------------------- | ------ |
| ISO-11 | P0  | Carol uploads to `globex/production`                            | 404; nothing written to Git; no DB row      | —      |
| ISO-12 | P0  | Carol deletes a Globex file by its exact UUID                   | 404; file still listed for Alice afterwards | —      |
| ISO-13 | P0  | Bob uploads to `globex/staging` — own customer, no grant        | 404; nothing written                        | —      |
| ISO-14 | P1  | After every rejected write above, `git log` shows no new commit | history unchanged                           | —      |

### 2.3 Information leakage

| ID     | Pri | Scenario                                                                            | Expected                  | Result |
| ------ | --- | ----------------------------------------------------------------------------------- | ------------------------- | ------ |
| ISO-15 | P1  | Compare response for a _nonexistent_ file ID vs another tenant's _real_ file ID     | identical status and body | —      |
| ISO-16 | P2  | Error bodies never contain filenames, tenant names, or Git paths from other tenants | no leakage                | —      |

ISO-15 is the reason for choosing 404 over 403. If the two responses differ in
any way, an attacker can enumerate which file IDs exist.

---

## 3. Cedar validation

| ID     | Pri | Scenario                                                            | Expected                                                             | Result |
| ------ | --- | ------------------------------------------------------------------- | -------------------------------------------------------------------- | ------ |
| CED-01 | P0  | Upload a syntactically valid Cedar policy                           | 201, file stored                                                     | —      |
| CED-02 | P0  | Upload a file with a syntax error (missing semicolon)               | 400, rejected                                                        | —      |
| CED-03 | P0  | The 400 body names the offending token (no line/column — see BUG-05) | actionable message                                                   | —      |
| CED-04 | P0  | A rejected file leaves no Git commit and no DB row                  | both stores unchanged                                                | —      |
| CED-05 | P1  | Upload an empty file                                                | 400 with a clear message, not a 500                                  | —      |
| CED-06 | P1  | Upload a file of invalid UTF-8 bytes                                | 400, not a 500 or an unhandled decode error                          | —      |
| CED-07 | P1  | Upload a file containing several policy statements                  | 201, accepted                                                        | —      |
| CED-08 | P1  | Upload plain English prose with a `.cedar` extension                | 400                                                                  | —      |
| CED-09 | P2  | Upload a policy with valid syntax but an unknown entity type        | documented behaviour (accepted — syntax-only validation is in scope) | —      |

CED-09 records a deliberate scope decision rather than a defect: validation is
parse-level, because schema validation would require a per-tenant Cedar schema
that the requirements do not describe.

---

## 4. Upload rules

| ID     | Pri | Scenario                                                  | Expected                                          | Result |
| ------ | --- | --------------------------------------------------------- | ------------------------------------------------- | ------ |
| UPL-01 | P0  | Filename `notes.txt`                                      | 400, extension rejected                           | —      |
| UPL-02 | P0  | Filename `../../etc/passwd.cedar`                         | 400, nothing written outside the tenant directory | —      |
| UPL-03 | P0  | Filename containing `/`                                   | 400                                               | —      |
| UPL-04 | P0  | Duplicate filename, same tenant, file still live          | 409                                               | —      |
| UPL-05 | P0  | Same filename in a _different_ tenant                     | 201, allowed                                      | —      |
| UPL-06 | P1  | Re-upload a filename after the original was deleted       | 201, allowed                                      | —      |
| UPL-07 | P1  | Filename beginning with `.` (e.g. `.hidden.cedar`)        | 400                                               | —      |
| UPL-08 | P1  | Filename longer than 255 characters                       | 400                                               | —      |
| UPL-09 | P1  | File larger than the configured size limit                | 413                                               | —      |
| UPL-10 | P1  | Request with no file attached                             | 422                                               | —      |
| UPL-11 | P2  | Filename with unicode or spaces                           | 400, consistent with the documented pattern       | —      |
| UPL-12 | P2  | Two concurrent uploads of the same filename to one tenant | exactly one 201, one 409; no duplicate rows       | —      |

UPL-12 is the case the partial unique index exists to win. The application's
duplicate check can be raced; the database constraint cannot.

---

## 5. CRUD behaviour

| ID      | Pri | Scenario                                                                        | Expected                            | Result |
| ------- | --- | ------------------------------------------------------------------------------- | ----------------------------------- | ------ |
| CRUD-01 | P0  | Full lifecycle: upload → appears in list → download → delete → absent from list | each step succeeds                  | —      |
| CRUD-02 | P0  | Downloaded bytes are identical to uploaded bytes                                | byte-for-byte match                 | —      |
| CRUD-03 | P0  | Soft-deleted files are excluded from list responses                             | not present                         | —      |
| CRUD-04 | P0  | Download a soft-deleted file by ID                                              | 404                                 | —      |
| CRUD-05 | P1  | Delete the same file twice                                                      | second call 404                     | —      |
| CRUD-06 | P1  | List response contains the expected metadata fields                             | filename, size, uploader, timestamp | —      |
| CRUD-07 | P1  | List is served without reading Git                                              | Postgres alone answers the query    | —      |
| CRUD-08 | P1  | List ordering is newest first and stable                                        | consistent order                    | —      |
| CRUD-09 | P1  | Download response sets a filename in `Content-Disposition`                      | correct filename                    | —      |
| CRUD-10 | P2  | List for a tenant with no files                                                 | 200 with an empty array, not 404    | —      |

CRUD-07 verifies the stated architecture, not just the output: if listing touches
Git, the claim that Postgres is the query index is not actually true.

---

## 6. Git / Postgres consistency

| ID     | Pri | Scenario                                                                      | Expected                                                               | Result |
| ------ | --- | ----------------------------------------------------------------------------- | ---------------------------------------------------------------------- | ------ |
| CON-01 | P0  | After upload, `git show <commit_sha>:<git_path>` returns the uploaded content | exact match                                                            | —      |
| CON-02 | P0  | `git_path` equals `{customer_slug}/{tenant_slug}/{filename}`                  | correct path                                                           | —      |
| CON-03 | P1  | After delete, the file is gone from the working tree but present in history   | recoverable from Git                                                   | —      |
| CON-04 | P1  | Each successful upload produces exactly one commit                            | no empty or duplicate commits                                          | —      |
| CON-05 | P1  | Commit message records the acting user                                        | audit trail usable                                                     | —      |
| CON-06 | P2  | **Fault injection:** force the metadata insert to fail after the Git commit   | no metadata row; file invisible to the API; orphaned blob is tolerated | —      |
| CON-07 | P2  | **Fault injection:** force the Git write to fail                              | no metadata row; no partial state                                      | —      |
| CON-08 | P2  | Two concurrent uploads to different tenants                                   | both succeed; Git index not corrupted; both rows present               | —      |

CON-06 is the test that justifies the write ordering. Git-first means a partial
failure leaves invisible content rather than a broken download.

---

## 7. Database invariants

Tested directly against Postgres, independent of the API. These verify the
guarantees the schema is supposed to provide on its own.

| ID     | Pri | Scenario                                                                                                      | Expected             | Result |
| ------ | --- | ------------------------------------------------------------------------------------------------------------- | -------------------- | ------ |
| INV-01 | P0  | **No cross-customer grants:** no row in `user_tenants` joins a user and a tenant with different `customer_id` | zero rows            | —      |
| INV-02 | P0  | `schema.sql` applies cleanly to an empty database                                                             | no errors            | —      |
| INV-03 | P0  | `schema.sql` is re-runnable                                                                                   | second run succeeds  | —      |
| INV-04 | P1  | Direct insert of a traversal filename is rejected by the CHECK constraint                                     | rejected at DB level | —      |
| INV-05 | P1  | Direct insert of a path-unsafe customer `folder_name` is rejected                                             | rejected at DB level | —      |
| INV-06 | P1  | Two live rows with the same `(tenant_id, filename)` are rejected                                              | unique violation     | —      |
| INV-07 | P1  | The same pair is permitted once the first is soft-deleted                                                     | insert succeeds      | —      |
| INV-08 | P1  | Deleting a user removes their `user_tenants` rows                                                             | cascade              | —      |
| INV-09 | P1  | Deleting a user who has uploaded files is refused                                                             | FK violation         | —      |
| INV-10 | P1  | Deleting a tenant that still has files is refused                                                             | FK violation         | —      |

INV-01 is the compensating control for the invariant the schema does not enforce
structurally. A composite foreign key on `user_tenants` was considered and
rejected as disproportionate; this test is what replaces it, so it must not be
skipped.

INV-04 and INV-05 matter because they hold even if the application layer is
bypassed entirely.

---

## 8. User interface

Manual cases, verified during the demo recording.

| ID    | Pri | Scenario                                       | Expected                                                 | Result |
| ----- | --- | ---------------------------------------------- | -------------------------------------------------------- | ------ |
| UI-01 | P0  | Sign in with a seeded token                    | reaches the file list                                    | —      |
| UI-02 | P0  | Tenant switcher lists only granted tenants     | Bob sees one, Alice sees two                             | —      |
| UI-03 | P0  | Switching tenant reloads the correct file list | list changes                                             | —      |
| UI-04 | P0  | Upload an invalid Cedar file                   | the validation error is displayed, not a generic failure | —      |
| UI-05 | P0  | Upload a valid file                            | appears in the list without a manual refresh             | —      |
| UI-06 | P0  | Download a file from the list                  | file downloads with the right name and content           | —      |
| UI-07 | P0  | Delete a file                                  | removed from the list                                    | —      |
| UI-08 | P1  | Sign in with an invalid token                  | clear error, no access                                   | —      |
| UI-09 | P1  | Sign out clears the stored token               | returns to sign-in                                       | —      |
| UI-10 | P2  | Duplicate filename upload                      | the 409 is surfaced meaningfully                         | —      |

---

## Requirements coverage

Maps each stated requirement to the cases that demonstrate it.

| Requirement                                       | Covered by                             |
| ------------------------------------------------- | -------------------------------------- |
| Git is the system of record for content           | CON-01, CON-02, CON-03, CRUD-02        |
| Postgres holds metadata and serves listing        | CRUD-06, CRUD-07, CRUD-08              |
| Tenant isolation enforced end to end              | all of §2, INV-01                      |
| Isolation holds against hand-crafted API requests | ISO-05, ISO-06, ISO-09, ISO-11, ISO-12 |
| Cedar validation before acceptance                | CED-01 – CED-08                        |
| Invalid files rejected with an actionable message | CED-03, UI-04                          |
| Authentication                                    | §1                                     |
| Upload, list, download, delete                    | §5                                     |
| Simple, fully functional interface                | §8                                     |

---

## Execution report

To be completed once the suite runs. Paste real terminal output rather than a
summary.

NOTE: Below is the code to instantiate the test db

Go to .env and set DATABASE_URL to this:
DATABASE_URL=postgresql://policy:policy@localhost:5433/smartverify_test

```
docker exec smartverify-db createdb -U policy smartverify_test

python backend/scripts/init_dev.py
```

**Environment:**
**Command:**
**Date:**

```
<pytest output>
```

| Run | Date | Passed | Failed | Skipped | Notes |
| --- | ---- | ------ | ------ | ------- | ----- |
|     |      |        |        |         |       |

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
