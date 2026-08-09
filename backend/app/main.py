"""FastAPI application entry point.

Run with:  uvicorn backend.app.main:app --reload
"""

from uuid import UUID

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.app.auth import get_current_user
from backend.app.db import get_connection
from backend.app.policies import router as policies_router

# The Vite dev server. Listed explicitly rather than using "*": the API is
# called with an Authorization header, and naming the origins that may do so
# keeps an arbitrary page from scripting the API on a user's behalf.
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

app = FastAPI(title="SmartVerify Policy Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    # Without this the browser hides Content-Disposition from JavaScript, so
    # the frontend cannot read the filename off a download response.
    expose_headers=["Content-Disposition"],
)

app.include_router(policies_router)


class TenantOut(BaseModel):
    id: UUID
    name: str


class MeOut(BaseModel):
    """Identity and reachable tenants for the caller.

    The tenant list is read from the database on every request rather than
    baked into the token, so revoking a grant takes effect immediately.
    """

    email: str
    customer: str
    tenants: list[TenantOut]


@app.get("/api/health")
def health(conn=Depends(get_connection)):
    """Liveness check. Queries the database so a failure here means the API
    cannot reach Postgres, not just that the process is running."""
    with conn.cursor() as cur:
        cur.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/api/me", response_model=MeOut)
def me(
    user: dict = Depends(get_current_user),
    conn=Depends(get_connection),
) -> dict:
    """Who the caller is, and which tenants they may act on.

    This is what the interface's tenant switcher is built from -- it must list
    only granted tenants, never every tenant belonging to the customer.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT name FROM customers WHERE id = %s",
            (user["customer_id"],),
        )
        customer = cur.fetchone()["name"]

        cur.execute(
            """
            SELECT t.id, t.name
            FROM user_tenants ut
            JOIN tenants t ON t.id = ut.tenant_id
            WHERE ut.user_id = %s
            ORDER BY t.name
            """,
            (user["id"],),
        )
        tenants = cur.fetchall()

    return {"email": user["email"], "customer": customer, "tenants": tenants}
