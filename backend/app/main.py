"""FastAPI application entry point.

Run with:  uvicorn backend.app.main:app --reload
"""

from fastapi import Depends, FastAPI

from backend.app.db import get_connection

app = FastAPI(title="SmartVerify Policy Service")


@app.get("/api/health")
def health(conn=Depends(get_connection)):
    """Liveness check. Queries the database so a failure here means the API
    cannot reach Postgres, not just that the process is running."""
    with conn.cursor() as cur:
        cur.execute("SELECT 1")
    return {"status": "ok"}