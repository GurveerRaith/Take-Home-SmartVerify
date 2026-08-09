"""Cedar policy validation.

Wraps the `cedarpy` bindings so nothing else in the application imports
cedarpy directly, and so a bare ValueError never reaches a request handler.

Validation is parse-level only: the file must be syntactically valid Cedar and
contain at least one policy. It does not check that the entity types or actions
a policy refers to exist, which would need a Cedar schema per tenant. See
DESIGN.md decision 10.
"""

import json

from cedarpy import PolicySet, policies_to_json_str


class CedarValidationError(Exception):
    """Raised when uploaded content is not a usable Cedar policy file.

    The message goes back to the caller in a 400 response, so it has to say
    what is wrong in terms the uploader can act on.
    """


def validate_policy_bytes(raw: bytes) -> str:
    """Validate uploaded bytes as a Cedar policy file.

    Returns the decoded text, which is what gets written to Git. Raises
    CedarValidationError on any failure.

    Filename and file size are checked by the route, not here.
    """
    # Decoding here rather than in the route means a binary file uploaded with
    # a .cedar extension gives a 400, not an unhandled 500.
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CedarValidationError(
            "File is not valid UTF-8 text. Cedar policy files must be plain text."
        ) from exc

    # cedarpy raises ValueError with a short message naming the offending
    # token, e.g. "unexpected token `;`". It gives no line or column, so the
    # message is passed through as-is.
    try:
        PolicySet.from_str(text)
    except ValueError as exc:
        raise CedarValidationError(f"Invalid Cedar syntax: {exc}") from exc

    # An empty, whitespace-only or comments-only file is valid Cedar - it
    # parses to an empty policy set. Uploading one is almost certainly a
    # mistake, so reject it. The parser exposes no policy count, so serialise
    # to JSON and count the static policies.
    parsed = json.loads(policies_to_json_str(text))
    if not parsed.get("staticPolicies"):
        raise CedarValidationError(
            "File contains no Cedar policy statements. "
            "Expected at least one permit or forbid statement."
        )

    return text
