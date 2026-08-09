"""T-12 to T-15 -- end-to-end behaviour, store consistency, data invariants.

See TEST_PLAN.md.
"""

from backend.app import git_repo
from backend.tests.conftest import (
    ALICE_TOKEN,
    CAROL_TOKEN,
    GLOBEX_PRODUCTION,
    INITECH_PRODUCTION,
    auth,
    git_log,
    policies_url,
    read_fixture,
    upload,
)


def test_t12_full_lifecycle(client, policy_repo):
    """T-12: upload, list, download, delete.

    The download assertion is byte-for-byte: content is stored and read back as
    bytes with no decode step, so a round trip cannot silently alter it.
    """
    content = read_fixture("valid/read-only.cedar")

    created = upload(client, GLOBEX_PRODUCTION, ALICE_TOKEN, "admin.cedar", content)
    assert created.status_code == 201
    file_id = created.json()["id"]

    listing = client.get(policies_url(GLOBEX_PRODUCTION), headers=auth(ALICE_TOKEN))
    assert [f["filename"] for f in listing.json()] == ["admin.cedar"]

    downloaded = client.get(
        f"{policies_url(GLOBEX_PRODUCTION)}/{file_id}/content",
        headers=auth(ALICE_TOKEN),
    )
    assert downloaded.status_code == 200
    assert downloaded.content == content
    assert 'filename="admin.cedar"' in downloaded.headers["content-disposition"]

    deleted = client.delete(
        f"{policies_url(GLOBEX_PRODUCTION)}/{file_id}", headers=auth(ALICE_TOKEN)
    )
    assert deleted.status_code == 204

    after = client.get(policies_url(GLOBEX_PRODUCTION), headers=auth(ALICE_TOKEN))
    assert after.json() == []


def test_t12_deleted_file_is_unreachable(client, policy_repo):
    """T-12: a deleted file cannot be read or deleted again."""
    created = upload(client, GLOBEX_PRODUCTION, ALICE_TOKEN, "admin.cedar")
    file_id = created.json()["id"]
    url = f"{policies_url(GLOBEX_PRODUCTION)}/{file_id}"

    assert client.delete(url, headers=auth(ALICE_TOKEN)).status_code == 204
    assert client.delete(url, headers=auth(ALICE_TOKEN)).status_code == 404
    assert client.get(url, headers=auth(ALICE_TOKEN)).status_code == 404
    assert client.get(f"{url}/content", headers=auth(ALICE_TOKEN)).status_code == 404


def test_t12_delete_is_soft_and_content_survives(client, policy_repo, db):
    """T-12: the row is retained and the content stays recoverable from Git.

    Reads go through the recorded commit, not the working tree, so `git rm`
    removes the file from the checkout without destroying it.
    """
    created = upload(client, GLOBEX_PRODUCTION, ALICE_TOKEN, "admin.cedar")
    file_id = created.json()["id"]

    with db.cursor() as cur:
        cur.execute(
            "SELECT git_path, commit_sha FROM policy_files WHERE id = %s", (file_id,)
        )
        row = cur.fetchone()

    client.delete(
        f"{policies_url(GLOBEX_PRODUCTION)}/{file_id}", headers=auth(ALICE_TOKEN)
    )

    with db.cursor() as cur:
        cur.execute("SELECT deleted_at FROM policy_files WHERE id = %s", (file_id,))
        assert cur.fetchone()["deleted_at"] is not None

    assert not (policy_repo / row["git_path"]).exists()
    assert git_repo.read_file(row["commit_sha"], row["git_path"]) is not None


def test_t13_same_filename_in_two_tenants_stays_independent(client, policy_repo):
    """T-13: `admin.cedar` in two tenants is two unrelated files.

    Both customers own a tenant called `production`, so this also proves the
    Git path is built from customer *and* tenant rather than tenant alone.
    """
    globex_content = b"permit(principal, action, resource);\n"
    initech_content = b"forbid(principal, action, resource);\n"

    globex = upload(
        client, GLOBEX_PRODUCTION, ALICE_TOKEN, "admin.cedar", globex_content
    )
    initech = upload(
        client, INITECH_PRODUCTION, CAROL_TOKEN, "admin.cedar", initech_content
    )
    assert globex.status_code == initech.status_code == 201

    globex_download = client.get(
        f"{policies_url(GLOBEX_PRODUCTION)}/{globex.json()['id']}/content",
        headers=auth(ALICE_TOKEN),
    )
    initech_download = client.get(
        f"{policies_url(INITECH_PRODUCTION)}/{initech.json()['id']}/content",
        headers=auth(CAROL_TOKEN),
    )

    assert globex_download.content == globex_content
    assert initech_download.content == initech_content

    assert (policy_repo / "globex/production/admin.cedar").exists()
    assert (policy_repo / "initech/production/admin.cedar").exists()


def test_t14_git_and_database_agree(client, policy_repo, db):
    """T-14: the metadata row resolves to exactly the bytes that were uploaded.

    The only test that inspects both stores. Everything else could pass with a
    broken Git layer.
    """
    content = read_fixture("valid/multiple-statements.cedar")
    created = upload(client, GLOBEX_PRODUCTION, ALICE_TOKEN, "admin.cedar", content)
    file_id = created.json()["id"]

    with db.cursor() as cur:
        cur.execute(
            "SELECT git_path, commit_sha, size_bytes FROM policy_files WHERE id = %s",
            (file_id,),
        )
        row = cur.fetchone()

    assert row["git_path"] == "globex/production/admin.cedar"
    assert row["size_bytes"] == len(content)
    assert git_repo.read_file(row["commit_sha"], row["git_path"]) == content


def test_t14_one_commit_per_upload(client, policy_repo):
    """T-14: each accepted upload produces exactly one commit, naming the user."""
    before = len(git_log(policy_repo))

    upload(client, GLOBEX_PRODUCTION, ALICE_TOKEN, "admin.cedar")

    commits = git_log(policy_repo)
    assert len(commits) == before + 1
    assert "admin.cedar" in commits[0]
    assert "alice@globex.example" in commits[0]


def test_t14_rejected_upload_leaves_both_stores_clean(client, policy_repo, db):
    """T-14: a rejection must not half-succeed in either store."""
    before = len(git_log(policy_repo))

    rejected = upload(
        client,
        GLOBEX_PRODUCTION,
        ALICE_TOKEN,
        "broken.cedar",
        read_fixture("invalid/missing-semicolon.cedar"),
    )
    assert rejected.status_code == 400

    assert len(git_log(policy_repo)) == before
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM policy_files")
        assert cur.fetchone()["n"] == 0


def test_t15_no_cross_customer_grants(db):
    """T-15: no user holds a grant on a tenant belonging to another customer.

    The schema cannot express this without a composite foreign key, which was
    considered and rejected (DESIGN.md decision 4). This test and the guard in
    seed.sql are the compensating control, so it must not be skipped: a single
    bad row here would let the API serve another customer's files while every
    authorisation check behaved correctly.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT u.email, t.name
            FROM user_tenants ut
            JOIN users u ON u.id = ut.user_id
            JOIN tenants t ON t.id = ut.tenant_id
            WHERE u.customer_id <> t.customer_id
            """
        )
        assert cur.fetchall() == []
