# main.py
import os
import uuid

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from auth.utils import hash_password
from core.database import SessionLocal
from migrations.initial_schema import create_tables, run_migrations
from models.user import User

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

app = FastAPI(title="Tender Analysis POC API", debug=True)

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestIDMiddleware)


@app.get("/health")
def health():
    return {"status": "ok"}


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
