"""Authentication and tenant scoping.

Two dependencies:

  get_current_user  resolves the bearer token to a user, or 401.
  get_tenant_scope  checks the tenant in the URL is one that user has been
                    granted, or 404.

Every route that touches tenant data depends on get_tenant_scope, so a handler
cannot run without a validated tenant. See DESIGN.md decision 2.
"""

from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Path

from backend.app.db import get_connection


@dataclass
class TenantScope:
    """Who is asking, and which tenant they are allowed to act on.

    Resolved once from a single query so routes never re-derive it. The folder
    names are included because the Git path is built from them.
    """

    user_id: UUID
    user_email: str
    tenant_id: UUID
    customer_folder: str
    tenant_folder: str


def get_current_user(
    authorization: str | None = Header(default=None),
    conn=Depends(get_connection),
) -> dict:
    """Resolve the bearer token to a user row, or raise 401."""
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, email, customer_id FROM users WHERE api_token = %s",
            (token,),
        )
        user = cur.fetchone()

    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    return user


def get_tenant_scope(
    tenant_id: UUID = Path(...),
    user: dict = Depends(get_current_user),
    conn=Depends(get_connection),
) -> TenantScope:
    """Confirm the user holds a grant on this tenant, and gather what routes need.

    The join runs through user_tenants, so a tenant that exists but was not
    granted produces no row -- identical to a tenant that does not exist. The
    response is 404 in both cases: a 403 would confirm the tenant exists. See
    DESIGN.md decision 3.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.id           AS tenant_id,
                   t.folder_name  AS tenant_folder,
                   c.folder_name  AS customer_folder
            FROM user_tenants ut
            JOIN tenants   t ON t.id = ut.tenant_id
            JOIN customers c ON c.id = t.customer_id
            WHERE ut.user_id = %s AND ut.tenant_id = %s
            """,
            (user["id"], tenant_id),
        )
        row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Not Found")

    return TenantScope(
        user_id=user["id"],
        user_email=user["email"],
        tenant_id=row["tenant_id"],
        customer_folder=row["customer_folder"],
        tenant_folder=row["tenant_folder"],
    )