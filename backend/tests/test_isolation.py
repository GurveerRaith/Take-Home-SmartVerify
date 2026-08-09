"""T-03 to T-07 -- tenant isolation.

See TEST_PLAN.md.
"""

import pytest

from backend.tests.conftest import (
    ALICE_TOKEN,
    BOB_TOKEN,
    CAROL_TOKEN,
    GLOBEX_PRODUCTION,
    GLOBEX_STAGING,
    NONEXISTENT_UUID,
    auth,
    git_log,
    policies_url,
    upload,
)


def test_t03_cross_customer_list_is_denied(client):
    """T-03: Carol (Initech) cannot list a Globex tenant."""
    response = client.get(policies_url(GLOBEX_PRODUCTION), headers=auth(CAROL_TOKEN))
    assert response.status_code == 404


def test_t04_same_customer_ungranted_tenant_is_denied(client):
    """T-04: Bob belongs to Globex but holds no grant on globex/staging.

    This is the only test that fails if authorisation is done by *customer*
    rather than by *tenant grant*. Every other isolation test would still pass
    with that defect present.
    """
    response = client.get(policies_url(GLOBEX_STAGING), headers=auth(BOB_TOKEN))
    assert response.status_code == 404


def test_granted_tenants_remain_reachable(client):
    """Isolation must deny, not break everything: the allowed paths still work."""
    for token, tenant in [
        (ALICE_TOKEN, GLOBEX_PRODUCTION),
        (ALICE_TOKEN, GLOBEX_STAGING),
        (BOB_TOKEN, GLOBEX_PRODUCTION),
    ]:
        assert client.get(policies_url(tenant), headers=auth(token)).status_code == 200


@pytest.mark.parametrize(
    "method,suffix",
    [("get", ""), ("get", "/content"), ("delete", "")],
    ids=["metadata", "download", "delete"],
)
def test_t05_cross_tenant_access_by_exact_id_is_denied(
    client, policy_repo, method, suffix
):
    """T-05: knowing a real file id from another tenant gains nothing.

    Also asserts the file survives, so a rejected delete really did nothing.
    """
    created = upload(client, GLOBEX_PRODUCTION, ALICE_TOKEN, "admin.cedar")
    file_id = created.json()["id"]

    response = getattr(client, method)(
        f"{policies_url(GLOBEX_PRODUCTION)}/{file_id}{suffix}",
        headers=auth(CAROL_TOKEN),
    )
    assert response.status_code == 404

    surviving = client.get(policies_url(GLOBEX_PRODUCTION), headers=auth(ALICE_TOKEN))
    assert len(surviving.json()) == 1


def test_t05_denied_and_nonexistent_are_identical(client, policy_repo):
    """T-05: a real file and an imaginary one must be indistinguishable.

    Any difference lets a caller enumerate which ids exist in other tenants.
    """
    created = upload(client, GLOBEX_PRODUCTION, ALICE_TOKEN, "admin.cedar")
    real_id = created.json()["id"]

    denied = client.get(
        f"{policies_url(GLOBEX_PRODUCTION)}/{real_id}", headers=auth(CAROL_TOKEN)
    )
    imaginary = client.get(
        f"{policies_url(GLOBEX_PRODUCTION)}/{NONEXISTENT_UUID}",
        headers=auth(CAROL_TOKEN),
    )
    assert denied.status_code == imaginary.status_code == 404
    assert denied.json() == imaginary.json()


def test_t06_valid_file_id_under_the_wrong_tenant_path(client, policy_repo):
    """T-06: a real id belonging to another of the caller's own tenants.

    Alice may read both tenants, so this is not an authorisation failure -- it
    checks that the lookup is scoped by tenant. A query of the form
    `WHERE id = ?` instead of `WHERE id = ? AND tenant_id = ?` fails here and
    nowhere else.
    """
    created = upload(client, GLOBEX_STAGING, ALICE_TOKEN, "admin.cedar")
    staging_file_id = created.json()["id"]

    response = client.get(
        f"{policies_url(GLOBEX_PRODUCTION)}/{staging_file_id}",
        headers=auth(ALICE_TOKEN),
    )
    assert response.status_code == 404


def test_t07_cross_customer_upload_writes_nothing(client, policy_repo, db):
    """T-07: a denied upload must not reach either store."""
    commits_before = len(git_log(policy_repo))

    response = upload(client, GLOBEX_PRODUCTION, CAROL_TOKEN, "evil.cedar")
    assert response.status_code == 404

    assert len(git_log(policy_repo)) == commits_before
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM policy_files")
        assert cur.fetchone()["n"] == 0


def test_t07_same_customer_ungranted_upload_writes_nothing(client, policy_repo, db):
    """T-07: the same, for a user inside the owning customer."""
    commits_before = len(git_log(policy_repo))

    response = upload(client, GLOBEX_STAGING, BOB_TOKEN, "evil.cedar")
    assert response.status_code == 404

    assert len(git_log(policy_repo)) == commits_before
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM policy_files")
        assert cur.fetchone()["n"] == 0


def test_malformed_tenant_id_is_rejected_cleanly(client):
    """A non-UUID tenant must be a 422, never a 500."""
    response = client.get(policies_url("not-a-uuid"), headers=auth(ALICE_TOKEN))
    assert response.status_code == 422
