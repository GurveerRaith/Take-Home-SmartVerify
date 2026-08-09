"""T-01, T-02 -- authentication.

See TEST_PLAN.md.
"""

import pytest

from backend.tests.conftest import (
    ALICE_TOKEN,
    BOB_TOKEN,
    CAROL_TOKEN,
    GLOBEX_PRODUCTION,
    auth,
    policies_url,
)


@pytest.mark.parametrize(
    "headers,case",
    [
        ({}, "no Authorization header"),
        ({"Authorization": "Bearer"}, "scheme with no token"),
        ({"Authorization": "Bearer   "}, "scheme with blank token"),
        ({"Authorization": "Basic alice_token"}, "wrong scheme"),
        ({"Authorization": "alice_token"}, "token with no scheme"),
        ({"Authorization": "Bearer not_a_real_token"}, "unknown token"),
    ],
)
def test_t01_bad_credentials_are_rejected(client, headers, case):
    """T-01: every malformed or unknown credential is a 401."""
    response = client.get(policies_url(GLOBEX_PRODUCTION), headers=headers)
    assert response.status_code == 401, case


def test_t01_auth_failures_are_indistinguishable(client):
    """T-01: an unknown token must look exactly like a missing one.

    Different messages would tell a caller whether a token exists.
    """
    missing = client.get(policies_url(GLOBEX_PRODUCTION), headers={})
    unknown = client.get(
        policies_url(GLOBEX_PRODUCTION),
        headers={"Authorization": "Bearer not_a_real_token"},
    )
    assert missing.status_code == unknown.status_code == 401
    assert missing.json() == unknown.json()


def test_t01_auth_failure_leaks_no_detail(client):
    """T-01: the error body must not name users, tenants or tokens."""
    body = str(client.get(policies_url(GLOBEX_PRODUCTION), headers={}).json()).lower()
    for leak in ("alice", "globex", "token", "user", "tenant"):
        assert leak not in body


@pytest.mark.parametrize(
    "token,email,customer,tenant_names",
    [
        (ALICE_TOKEN, "alice@globex.example", "Globex", ["Production", "Staging"]),
        (BOB_TOKEN, "bob@globex.example", "Globex", ["Production"]),
        (CAROL_TOKEN, "carol@initech.example", "Initech", ["Production"]),
    ],
)
def test_t02_me_returns_exactly_the_granted_tenants(
    client, token, email, customer, tenant_names
):
    """T-02: /api/me identifies the caller and lists only granted tenants.

    Bob belongs to Globex, which owns two tenants, but holds a grant on only
    one. Returning both would be an isolation failure at the first hop.
    """
    response = client.get("/api/me", headers=auth(token))
    assert response.status_code == 200

    body = response.json()
    assert body["email"] == email
    assert body["customer"] == customer
    assert [t["name"] for t in body["tenants"]] == tenant_names


def test_t02_me_requires_authentication(client):
    """T-02: /api/me is not readable without a valid token."""
    assert client.get("/api/me").status_code == 401


def test_t02_tenant_ids_from_me_are_usable(client):
    """T-02: every tenant /api/me reports is actually reachable.

    Guards against the switcher listing tenants the API then refuses.
    """
    tenants = client.get("/api/me", headers=auth(ALICE_TOKEN)).json()["tenants"]
    for tenant in tenants:
        response = client.get(policies_url(tenant["id"]), headers=auth(ALICE_TOKEN))
        assert response.status_code == 200
