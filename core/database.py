import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.base import Base

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://poc_user:poc_password@localhost:5432/tender_poc")
# Railway provides postgres:// but SQLAlchemy requires postgresql://
DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL,
    pool_size=15,
    max_overflow=25,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_timeout=10,  # fail fast on pool exhaustion instead of hanging 30s
)

SessionLocal = sessionmaker(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
