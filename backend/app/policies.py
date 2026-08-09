"""Policy file routes.

Every route here depends on get_tenant_scope, so a handler cannot run without
a tenant the caller has actually been granted. Queries filter on
scope.tenant_id as well as the row id, so another tenant's file is unreachable
even when its id is known. See DESIGN.md decision 2.
"""

import re
from datetime import datetime
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, status
from pydantic import BaseModel

from backend.app import git_repo
from backend.app.auth import TenantScope, get_tenant_scope
from backend.app.cedar import CedarValidationError, validate_policy_bytes
from backend.app.db import get_connection

router = APIRouter(prefix="/api/tenants/{tenant_id}/policies", tags=["policies"])

# Must stay identical to the policy_files_filename_format CHECK constraint in
# schema.sql. The application check produces a helpful message; the constraint
# is the backstop that holds even if something writes to the database directly.
# Excluding '/' is what makes path traversal unrepresentable.
FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.cedar$")

MAX_FILE_BYTES = 1024 * 1024  # 1 MB; policy files are small by nature.


class PolicyFileOut(BaseModel):
    """The public shape of a policy file.

    A projection of the row, not the row itself: git_path and commit_sha are
    internal storage details and are deliberately absent. FastAPI strips any
    field not listed here, so they cannot leak by accident even if a query
    starts selecting them.
    """

    id: UUID
    filename: str
    size_bytes: int
    uploaded_by: str
    uploaded_at: datetime


@router.get("", response_model=list[PolicyFileOut])
def list_policies(
    scope: TenantScope = Depends(get_tenant_scope),
    conn=Depends(get_connection),
) -> list[dict]:
    """List the live policy files in this tenant, newest first.

    Answered entirely from PostgreSQL; Git is never read. Serving the list
    without touching the content store is the reason metadata is held
    separately at all.

    `uploaded_by` is joined to an email address rather than returned as a UUID,
    which is what the interface actually needs to display.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT pf.id,
                   pf.filename,
                   pf.size_bytes,
                   u.email AS uploaded_by,
                   pf.uploaded_at
            FROM policy_files pf
            JOIN users u ON u.id = pf.uploaded_by
            WHERE pf.tenant_id = %s
              AND pf.deleted_at IS NULL
            ORDER BY pf.uploaded_at DESC
            """,
            (scope.tenant_id,),
        )
        return cur.fetchall()


def _get_live_file(conn, scope: TenantScope, file_id: UUID) -> dict:
    """Fetch one live policy file belonging to this tenant, or raise 404.

    The `tenant_id` in the WHERE clause is the isolation control for every
    per-file route. A real file id belonging to a different tenant matches no
    row here, so it is indistinguishable from an id that does not exist -- the
    caller cannot tell whether the file is real.

    Looking a file up by id alone would defeat that, which is what T-06 tests.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT pf.id,
                   pf.filename,
                   pf.size_bytes,
                   pf.git_path,
                   pf.commit_sha,
                   pf.uploaded_at,
                   u.email AS uploaded_by
            FROM policy_files pf
            JOIN users u ON u.id = pf.uploaded_by
            WHERE pf.id = %s
              AND pf.tenant_id = %s
              AND pf.deleted_at IS NULL
            """,
            (file_id, scope.tenant_id),
        )
        row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return row


@router.get("/{file_id}", response_model=PolicyFileOut)
def get_policy(
    file_id: UUID,
    scope: TenantScope = Depends(get_tenant_scope),
    conn=Depends(get_connection),
) -> dict:
    """Metadata for a single policy file."""
    return _get_live_file(conn, scope, file_id)


@router.get("/{file_id}/content")
def download_policy(
    file_id: UUID,
    scope: TenantScope = Depends(get_tenant_scope),
    conn=Depends(get_connection),
) -> Response:
    """Download a policy file's content.

    Content is read from Git at the commit recorded on the metadata row, not
    from the working tree, so the bytes returned are exactly the bytes that
    were uploaded with this row.

    Returned as raw bytes with no decode step, which is what keeps the download
    byte-identical to the upload.
    """
    row = _get_live_file(conn, scope, file_id)
    content = git_repo.read_file(row["commit_sha"], row["git_path"])

    # The filename is safe to interpolate here: the CHECK constraint and the
    # upload validation both restrict it to [A-Za-z0-9._-], so it cannot
    # contain a quote that would break out of the header.
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{row["filename"]}"'},
    )


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_policy(
    file_id: UUID,
    scope: TenantScope = Depends(get_tenant_scope),
    conn=Depends(get_connection),
) -> None:
    """Delete a policy file: soft-delete the row, then remove it from Git.

    PostgreSQL first, Git second -- the reverse of upload. The metadata row is
    what makes a file visible, so removing visibility first means a failure
    between the two steps leaves content in Git that nothing points at:
    invisible and harmless. The opposite order could leave a file listed whose
    content had already gone. See DESIGN.md decision 1.

    The commit is explicit rather than left to the request teardown, so the
    database change is durable *before* Git is touched. Without it both writes
    would land in the opposite order to the one documented.
    """
    row = _get_live_file(conn, scope, file_id)

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE policy_files
            SET deleted_at = now()
            WHERE id = %s AND tenant_id = %s AND deleted_at IS NULL
            """,
            (file_id, scope.tenant_id),
        )
    conn.commit()

    git_repo.delete_file(
        row["git_path"],
        f"Delete {row['filename']} in {scope.customer_folder}/{scope.tenant_folder} "
        f"(by {scope.user_email})",
    )


@router.post("", response_model=PolicyFileOut, status_code=status.HTTP_201_CREATED)
def upload_policy(
    file: UploadFile,
    scope: TenantScope = Depends(get_tenant_scope),
    conn=Depends(get_connection),
) -> dict:
    """Validate and store a Cedar policy file.

    Checks run cheapest-first, so a bad request is rejected before anything is
    written anywhere:

      1. filename pattern      400
      2. size limit            413
      3. Cedar validity        400
      4. name already in use   409
      5. write to Git          -> commit SHA
      6. insert metadata row

    Git is written before PostgreSQL. If step 6 fails, the commit is discarded
    and nothing is left visible; had the order been reversed, a failure would
    leave a listed file whose content was never stored. See DESIGN.md decision 1.
    """
    filename = file.filename or ""
    if not FILENAME_PATTERN.match(filename) or len(filename) > 255:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid filename {filename!r}. Names must end in .cedar and "
                "may contain only letters, digits, dots, dashes and underscores."
            ),
        )

    content = file.file.read()
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File is larger than the {MAX_FILE_BYTES // 1024} KB limit.",
        )

    try:
        validate_policy_bytes(content)
    except CedarValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Checked before writing to Git so a duplicate upload does not create a
    # commit that has to be discarded. The unique index below is still the
    # authority: two concurrent uploads can both pass this check.
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM policy_files
            WHERE tenant_id = %s AND filename = %s AND deleted_at IS NULL
            """,
            (scope.tenant_id, filename),
        )
        if cur.fetchone() is not None:
            raise HTTPException(
                status_code=409,
                detail=f"A file named {filename} already exists in this tenant.",
            )

    # Built from the validated tenant scope, never from anything the caller
    # supplied, so a file cannot land outside its own tenant's directory.
    git_path = git_repo.build_path(
        scope.customer_folder, scope.tenant_folder, filename
    )
    commit_sha = git_repo.write_file(
        git_path,
        content,
        f"Add {filename} to {scope.customer_folder}/{scope.tenant_folder} "
        f"(by {scope.user_email})",
    )

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO policy_files
                    (tenant_id, filename, git_path, commit_sha, size_bytes, uploaded_by)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, filename, size_bytes, uploaded_at
                """,
                (
                    scope.tenant_id,
                    filename,
                    git_path,
                    commit_sha,
                    len(content),
                    scope.user_id,
                ),
            )
            row = cur.fetchone()
        conn.commit()
    except psycopg.errors.UniqueViolation as exc:
        # Lost a race against a concurrent upload of the same name. The partial
        # unique index caught what the check above could not.
        conn.rollback()
        git_repo.discard_last_commit()
        raise HTTPException(
            status_code=409,
            detail=f"A file named {filename} already exists in this tenant.",
        ) from exc
    except Exception:
        # Metadata write failed after the content was committed. Discard the
        # commit so no orphaned content is left behind.
        conn.rollback()
        git_repo.discard_last_commit()
        raise

    # The row returns uploaded_by as a UUID; the response contract is an email.
    row["uploaded_by"] = scope.user_email
    return row
