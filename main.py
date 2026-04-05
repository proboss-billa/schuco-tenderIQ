# main.py
import asyncio
import logging
import os
import uuid

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

from auth.utils import hash_password
from core.database import SessionLocal
from core.clients import validate_clients, get_client_status
from migrations.initial_schema import create_tables, run_migrations
from models.user import User
from models.project import Project

# Import core.logging to attach timing handlers at module load
import core.logging  # noqa: F401
from core.middleware import RequestIDMiddleware

# Import routers
from routers.auth import router as auth_router
from routers.projects import router as projects_router
from routers.documents import router as documents_router
from routers.parameters import router as parameters_router
from routers.query import router as query_router
from routers.timings import router as timings_router

logger = logging.getLogger("tenderiq.main")

app = FastAPI(title="Tender Analysis POC API", debug=True)

# Default dev origins: CRA (3000), Vite (5173), Vite preview (4173). Override
# via ALLOWED_ORIGINS env var for staging/prod.
ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:3000,http://localhost:5173,http://localhost:4173,http://127.0.0.1:5173",
).split(",")

# ── Timeout middleware ───────────────────────────────────────────────────────
# Prevents any request from hanging indefinitely (e.g., slow external API).
class TimeoutMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        try:
            return await asyncio.wait_for(call_next(request), timeout=120.0)
        except asyncio.TimeoutError:
            return JSONResponse(
                status_code=504,
                content={"detail": "Request timed out after 120 seconds"},
            )


app.add_middleware(TimeoutMiddleware)
app.add_middleware(RequestIDMiddleware)

# CORSMiddleware MUST be added last so it ends up outermost in the stack.
# Starlette's add_middleware prepends, and the stack wraps inside-out, so the
# last-added is the first to see the response on the way out. Without this,
# 5xx responses from inner middleware (timeouts, exceptions) escape without
# CORS headers, and the browser reports a confusing CORS error instead of
# the real 500.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health check with real dependency checks ─────────────────────────────────
@app.get("/health")
def health():
    checks = get_client_status()

    # Database connectivity check
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        content={"status": "healthy" if all_ok else "degraded", "checks": checks},
        status_code=200 if all_ok else 503,
    )


# ── Include all routers ──────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(projects_router)
app.include_router(documents_router)
app.include_router(parameters_router)
app.include_router(query_router)
app.include_router(timings_router)


# ── Startup events ───────────────────────────────────────────────────────────
@app.on_event("startup")
def on_startup():
    create_tables()
    run_migrations()

    # Log any client initialization failures (non-fatal — app still starts)
    errors = validate_clients()
    if errors:
        logger.warning(f"[STARTUP] Client initialization issues: {errors}")


@app.on_event("startup")
def recover_stale_processing():
    """Mark projects stuck in 'processing' from a previous crash as 'failed'.

    Without this, a server restart mid-pipeline leaves projects permanently
    stuck — the frontend polls 200 times (10 min) then shows 'load failed'.
    """
    db = SessionLocal()
    try:
        stale = db.query(Project).filter(
            Project.processing_status == "processing"
        ).all()
        for p in stale:
            p.processing_status = "failed"
            p.error_message = (
                "Server restarted during processing. "
                "Click re-extract to retry."
            )
            p.pipeline_step = None
        if stale:
            db.commit()
            logger.info(
                f"[STARTUP] Recovered {len(stale)} stale processing project(s)"
            )
    except Exception as e:
        logger.warning(f"[STARTUP] Could not recover stale projects: {e}")
        db.rollback()
    finally:
        db.close()


@app.on_event("startup")
def seed_default_user():
    """Create default user abc@sooru.ai on startup if it doesn't exist."""
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == "abc@sooru.ai").first()
        if not existing:
            user = User(user_id=uuid.uuid4(), email="abc@sooru.ai", password_hash=hash_password("12345678"))
            db.add(user)
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
