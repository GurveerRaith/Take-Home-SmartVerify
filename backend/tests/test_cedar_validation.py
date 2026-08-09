"""T-08, T-09 -- Cedar validation.

See TEST_PLAN.md.
"""

import pytest

from backend.tests.conftest import (
    ALICE_TOKEN,
    GLOBEX_PRODUCTION,
    auth,
    git_log,
    policies_url,
    read_fixture,
    upload,
)

VALID_FIXTURES = [
    "simple-permit.cedar",
    "admin-access.cedar",
    "read-only.cedar",
    "multiple-statements.cedar",
]

INVALID_FIXTURES = [
    "missing-semicolon.cedar",
    "misspelled-effect.cedar",
    "unbalanced-brace.cedar",
    "not-cedar.cedar",
    "not-utf8.cedar",
    "empty.cedar",
    "comments-only.cedar",
]


@pytest.mark.parametrize("fixture", VALID_FIXTURES)
def test_t08_valid_policies_are_accepted(client, policy_repo, fixture):
    """T-08: every valid fixture uploads and is retrievable.

    Covers a single statement, a conditional policy with a `when` clause, and
    a file containing several statements.
    """
    content = read_fixture(f"valid/{fixture}")
    response = upload(client, GLOBEX_PRODUCTION, ALICE_TOKEN, fixture, content)
    assert response.status_code == 201, response.text

    body = response.json()
    assert body["filename"] == fixture
    assert body["size_bytes"] == len(content)
    assert body["uploaded_by"] == "alice@globex.example"

    listing = client.get(policies_url(GLOBEX_PRODUCTION), headers=auth(ALICE_TOKEN))
    assert fixture in [f["filename"] for f in listing.json()]


@pytest.mark.parametrize("fixture", INVALID_FIXTURES)
def test_t09_invalid_policies_are_rejected(client, policy_repo, db, fixture):
    """T-09: every invalid fixture is a 400 with an actionable message.

    Also asserts the rejection leaves *both* stores untouched -- no commit and
    no metadata row -- so a bad upload cannot half-succeed.

    `empty.cedar` and `comments-only.cedar` are the interesting cases: both are
    syntactically valid Cedar that parses to an empty policy set, so the parser
    alone would accept them. See BUG-04.
    """
    commits_before = len(git_log(policy_repo))

    response = upload(
        client,
        GLOBEX_PRODUCTION,
        ALICE_TOKEN,
        "policy.cedar",
        read_fixture(f"invalid/{fixture}"),
    )

    assert response.status_code == 400, f"{fixture} -> {response.status_code}"
    assert len(response.json()["detail"]) > 10, "message must say something useful"

    assert len(git_log(policy_repo)) == commits_before
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM policy_files")
        assert cur.fetchone()["n"] == 0


def test_t09_messages_distinguish_the_failure(client, policy_repo):
    """T-09: different problems produce different messages.

    A single generic "invalid file" would satisfy the status code but not the
    requirement for an actionable error.
    """
    messages = {}
    for fixture in ("missing-semicolon.cedar", "not-utf8.cedar", "empty.cedar"):
        response = upload(
            client,
            GLOBEX_PRODUCTION,
            ALICE_TOKEN,
            "policy.cedar",
            read_fixture(f"invalid/{fixture}"),
        )
        messages[fixture] = response.json()["detail"]

    assert len(set(messages.values())) == 3, messages
    assert "UTF-8" in messages["not-utf8.cedar"]
    assert "no Cedar policy statements" in messages["empty.cedar"]
