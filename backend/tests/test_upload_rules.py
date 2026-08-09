"""T-10, T-11 -- upload rules.

See TEST_PLAN.md.
"""

import psycopg
import pytest

from backend.tests.conftest import (
    ALICE_TOKEN,
    GLOBEX_PRODUCTION,
    MAX_UPLOAD_BYTES,
    auth,
    git_log,
    policies_url,
    upload,
)


@pytest.mark.parametrize(
    "filename,reason",
    [
        ("../../etc/passwd.cedar", "path traversal"),
        ("../admin.cedar", "parent directory"),
        ("nested/admin.cedar", "contains a separator"),
        ("notes.txt", "wrong extension"),
        ("admin", "no extension"),
        (".hidden.cedar", "leading dot"),
        ("-flag.cedar", "leading dash"),
        ("x" * 260 + ".cedar", "longer than 255 characters"),
    ],
)
def test_t10_bad_filenames_are_rejected(client, policy_repo, filename, reason):
    """T-10: filenames outside the allowed pattern never reach storage.

    Excluding '/' is what makes traversal unrepresentable rather than merely
    filtered -- there is no encoding of `../` that survives the check.
    """
    commits_before = len(git_log(policy_repo))

    response = upload(client, GLOBEX_PRODUCTION, ALICE_TOKEN, filename)
    assert response.status_code == 400, f"{reason}: {filename}"

    assert len(git_log(policy_repo)) == commits_before


def test_t10_nothing_is_written_outside_the_tenant_directory(client, policy_repo):
    """T-10: after a traversal attempt, the repository is unchanged."""
    before = sorted(p.name for p in policy_repo.iterdir())

    upload(client, GLOBEX_PRODUCTION, ALICE_TOKEN, "../../escaped.cedar")

    assert sorted(p.name for p in policy_repo.iterdir()) == before
    assert not (policy_repo.parent / "escaped.cedar").exists()


def test_t10_oversized_file_is_rejected(client, policy_repo):
    """T-10: the size limit is enforced, with 413 rather than 400."""
    oversized = b"x" * (MAX_UPLOAD_BYTES + 1)
    response = upload(client, GLOBEX_PRODUCTION, ALICE_TOKEN, "big.cedar", oversized)
    assert response.status_code == 413


def test_t11_duplicate_live_filename_is_rejected(client, policy_repo):
    """T-11: one live file per name per tenant."""
    assert upload(client, GLOBEX_PRODUCTION, ALICE_TOKEN, "admin.cedar").status_code == 201

    duplicate = upload(client, GLOBEX_PRODUCTION, ALICE_TOKEN, "admin.cedar")
    assert duplicate.status_code == 409

    listing = client.get(policies_url(GLOBEX_PRODUCTION), headers=auth(ALICE_TOKEN))
    assert len(listing.json()) == 1


def test_t11_name_can_be_reused_after_deletion(client, policy_repo):
    """T-11: deleting a file frees its name again.

    This is what the *partial* unique index buys. A plain unique constraint
    would count the soft-deleted row and reserve the name permanently.
    """
    first = upload(client, GLOBEX_PRODUCTION, ALICE_TOKEN, "admin.cedar")
    file_id = first.json()["id"]

    client.delete(
        f"{policies_url(GLOBEX_PRODUCTION)}/{file_id}", headers=auth(ALICE_TOKEN)
    )

    second = upload(client, GLOBEX_PRODUCTION, ALICE_TOKEN, "admin.cedar")
    assert second.status_code == 201
    assert second.json()["id"] != file_id


def test_t11_database_enforces_uniqueness_independently(client, policy_repo, db):
    """T-11: the constraint holds even when the API is bypassed.

    The application's duplicate check can be raced by two concurrent uploads;
    the index cannot. Inserting directly proves the database is the authority.
    """
    upload(client, GLOBEX_PRODUCTION, ALICE_TOKEN, "admin.cedar")

    with pytest.raises(psycopg.errors.UniqueViolation):
        with db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO policy_files
                    (tenant_id, filename, git_path, commit_sha, size_bytes, uploaded_by)
                VALUES (%s, 'admin.cedar', 'x', 'y', 1,
                        'cccccccc-0000-0000-0000-000000000001')
                """,
                (GLOBEX_PRODUCTION,),
            )
