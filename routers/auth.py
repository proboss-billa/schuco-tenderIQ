import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from auth.utils import (
    create_access_token, verify_password, hash_password,
    validate_email_domain, get_current_user, ALLOWED_DOMAINS,
)
from core.database import get_db
from models.user import User

AVATAR_DIR = Path("uploads/avatars")

router = APIRouter(prefix="", tags=["auth"])


# ── Request / response schemas ───────────────────────────────────────────────

class SignupRequest(BaseModel):
    email: str
    password: str = Field(min_length=8)
    name: str = Field(min_length=1)
    phone: str = Field(min_length=1)


class LoginRequest(BaseModel):
    email: str
    password: str


class ProfileUpdateRequest(BaseModel):
    name: str | None = None
    phone: str | None = None


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/signup")
def signup(body: SignupRequest, db: Session = Depends(get_db)):
    if not validate_email_domain(body.email):
        allowed = ", ".join(f"@{d}" for d in sorted(ALLOWED_DOMAINS))
        raise HTTPException(
            status_code=400,
            detail=(
                f"Registration is restricted to {allowed} email domains. "
                "For more, contact mike@sooru.ai or brijesh@sooru.ai for assistance."
            ),
        )

    existing = db.query(User).filter(User.email == body.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        name=body.name,
        phone=body.phone,
    )
    db.add(user)
    db.commit()

    token = create_access_token({"sub": str(user.user_id)})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "user_id": str(user.user_id),
            "email": user.email,
            "name": user.name,
        },
    }


@router.post("/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found. Please register.")
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect password")
    token = create_access_token({"sub": str(user.user_id)})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "user_id": str(user.user_id),
            "email": user.email,
            "name": user.name,
        },
    }


@router.get("/me")
def get_me(current_user: User = Depends(get_current_user)):
    return {
        "user_id": str(current_user.user_id),
        "email": current_user.email,
        "name": current_user.name,
        "phone": current_user.phone,
        "has_avatar": bool(current_user.avatar_path) and Path(current_user.avatar_path).exists(),
        "token_limit": current_user.token_limit,
        "tokens_used": current_user.tokens_used,
        "tokens_remaining": max(0, current_user.token_limit - current_user.tokens_used),
    }


@router.put("/me")
def update_profile(
    body: ProfileUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.name is not None:
        current_user.name = body.name
    if body.phone is not None:
        current_user.phone = body.phone
    db.commit()
    return {
        "user_id": str(current_user.user_id),
        "email": current_user.email,
        "name": current_user.name,
        "phone": current_user.phone,
    }


@router.put("/me/password")
def change_password(
    body: PasswordChangeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    current_user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"message": "Password updated"}


@router.delete("/me")
def delete_account(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Remove avatar file if exists
    if current_user.avatar_path:
        p = Path(current_user.avatar_path)
        if p.exists():
            p.unlink()
    db.delete(current_user)
    db.commit()
    return {"message": "Account deleted"}


@router.put("/me/avatar")
def upload_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if file.content_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(status_code=400, detail="Only JPEG, PNG, and WebP images are allowed")
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    # Remove old avatar if exists
    if current_user.avatar_path:
        old = Path(current_user.avatar_path)
        if old.exists():
            old.unlink()
    ext = file.filename.rsplit(".", 1)[-1] if "." in file.filename else "jpg"
    filename = f"{current_user.user_id}_{uuid.uuid4().hex[:8]}.{ext}"
    dest = AVATAR_DIR / filename
    with open(dest, "wb") as f:
        f.write(file.file.read())
    current_user.avatar_path = str(dest)
    db.commit()
    return {"message": "Avatar updated", "has_avatar": True}


@router.delete("/me/avatar")
def delete_avatar(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.avatar_path:
        old = Path(current_user.avatar_path)
        if old.exists():
            old.unlink()
        current_user.avatar_path = None
        db.commit()
    return {"message": "Avatar removed", "has_avatar": False}


@router.get("/me/avatar")
def get_avatar(token: str, db: Session = Depends(get_db)):
    from auth.utils import decode_token
    user_id = decode_token(token)
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user or not user.avatar_path:
        raise HTTPException(status_code=404, detail="No avatar")
    p = Path(user.avatar_path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="No avatar")
    _AVATAR_MIMES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
    return FileResponse(p, media_type=_AVATAR_MIMES.get(p.suffix.lower(), "image/jpeg"))
