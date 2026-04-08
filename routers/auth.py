import json
import uuid
from datetime import datetime, timedelta
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
from models.email_otp import EmailOTP
from services.email_service import generate_otp, send_otp_email, EMAIL_DEV_MODE

AVATAR_DIR = Path("uploads/avatars")

router = APIRouter(prefix="", tags=["auth"])


# ── Request / response schemas ───────────────────────────────────────────────

class SignupRequest(BaseModel):
    email: str
    password: str = Field(min_length=6)
    name: str | None = None
    phone: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class ProfileUpdateRequest(BaseModel):
    name: str | None = None
    phone: str | None = None


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6)


class VerifyOtpRequest(BaseModel):
    email: str
    otp: str = Field(min_length=4, max_length=6)


class ResendOtpRequest(BaseModel):
    email: str
    purpose: str = "signup"


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    email: str
    otp: str = Field(min_length=4, max_length=6)
    new_password: str = Field(min_length=6)


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

    # Invalidate any existing unused signup OTPs for this email
    db.query(EmailOTP).filter(
        EmailOTP.email == body.email,
        EmailOTP.purpose == "signup",
        EmailOTP.is_used == False,  # noqa: E712
    ).update({"is_used": True})

    otp_code = generate_otp(4)
    payload = json.dumps({
        "password_hash": hash_password(body.password),
        "name": body.name,
        "phone": body.phone,
    })

    otp_record = EmailOTP(
        email=body.email,
        otp_code=otp_code,
        purpose="signup",
        signup_payload=payload,
        expires_at=datetime.utcnow() + timedelta(minutes=5),
    )
    db.add(otp_record)
    db.commit()

    try:
        send_otp_email(body.email, otp_code, purpose="signup")
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    resp = {"message": "OTP sent", "email": body.email}
    if EMAIL_DEV_MODE:
        resp["dev_otp"] = otp_code
    return resp


@router.post("/verify-otp")
def verify_otp(body: VerifyOtpRequest, db: Session = Depends(get_db)):
    otp_record = db.query(EmailOTP).filter(
        EmailOTP.email == body.email,
        EmailOTP.purpose == "signup",
        EmailOTP.is_used == False,  # noqa: E712
        EmailOTP.expires_at > datetime.utcnow(),
    ).order_by(EmailOTP.created_at.desc()).first()

    if not otp_record:
        raise HTTPException(status_code=400, detail="No valid OTP found. Please request a new one.")
    if otp_record.attempts >= 5:
        raise HTTPException(status_code=400, detail="Too many attempts. Please request a new OTP.")
    if body.otp != otp_record.otp_code:
        otp_record.attempts += 1
        db.commit()
        remaining = 5 - otp_record.attempts
        raise HTTPException(status_code=400, detail=f"Invalid OTP. {remaining} attempt(s) remaining.")

    otp_record.is_used = True
    db.flush()

    # Race-condition guard
    existing = db.query(User).filter(User.email == body.email).first()
    if existing:
        db.commit()
        raise HTTPException(status_code=400, detail="Email already registered")

    data = json.loads(otp_record.signup_payload)
    user = User(
        email=body.email,
        password_hash=data["password_hash"],
        name=data.get("name"),
        phone=data.get("phone"),
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


@router.post("/resend-otp")
def resend_otp(body: ResendOtpRequest, db: Session = Depends(get_db)):
    last_otp = db.query(EmailOTP).filter(
        EmailOTP.email == body.email,
        EmailOTP.purpose == body.purpose,
    ).order_by(EmailOTP.created_at.desc()).first()

    if not last_otp:
        raise HTTPException(status_code=400, detail="No pending verification found.")

    # Rate limit: 30s between requests
    if (
        not last_otp.is_used
        and last_otp.created_at
        and (datetime.utcnow() - last_otp.created_at).total_seconds() < 30
    ):
        raise HTTPException(status_code=429, detail="Please wait before requesting a new OTP.")

    # Invalidate old OTPs
    db.query(EmailOTP).filter(
        EmailOTP.email == body.email,
        EmailOTP.purpose == body.purpose,
        EmailOTP.is_used == False,  # noqa: E712
    ).update({"is_used": True})

    otp_length = 4 if body.purpose == "signup" else 6
    otp_code = generate_otp(otp_length)

    otp_record = EmailOTP(
        email=body.email,
        otp_code=otp_code,
        purpose=body.purpose,
        signup_payload=last_otp.signup_payload if body.purpose == "signup" else None,
        expires_at=datetime.utcnow() + timedelta(minutes=5),
    )
    db.add(otp_record)
    db.commit()

    try:
        send_otp_email(body.email, otp_code, purpose=body.purpose)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    resp = {"message": "New OTP sent", "email": body.email}
    if EMAIL_DEV_MODE:
        resp["dev_otp"] = otp_code
    return resp


@router.post("/forgot-password")
def forgot_password(body: ForgotPasswordRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()

    # Always return success to not leak whether email exists
    if user:
        # Invalidate old unused reset OTPs
        db.query(EmailOTP).filter(
            EmailOTP.email == body.email,
            EmailOTP.purpose == "reset_password",
            EmailOTP.is_used == False,  # noqa: E712
        ).update({"is_used": True})

        otp_code = generate_otp(6)
        otp_record = EmailOTP(
            email=body.email,
            otp_code=otp_code,
            purpose="reset_password",
            expires_at=datetime.utcnow() + timedelta(minutes=5),
        )
        db.add(otp_record)
        db.commit()
        try:
            send_otp_email(body.email, otp_code, purpose="reset_password")
        except RuntimeError:
            pass  # Don't leak email existence via error

    resp = {"message": "If the email exists, an OTP has been sent.", "email": body.email}
    if EMAIL_DEV_MODE and user:
        resp["dev_otp"] = otp_code
    return resp


@router.post("/reset-password")
def reset_password(body: ResetPasswordRequest, db: Session = Depends(get_db)):
    otp_record = db.query(EmailOTP).filter(
        EmailOTP.email == body.email,
        EmailOTP.purpose == "reset_password",
        EmailOTP.is_used == False,  # noqa: E712
        EmailOTP.expires_at > datetime.utcnow(),
    ).order_by(EmailOTP.created_at.desc()).first()

    if not otp_record:
        raise HTTPException(status_code=400, detail="No valid OTP found. Please request a new one.")
    if otp_record.attempts >= 5:
        raise HTTPException(status_code=400, detail="Too many attempts. Please request a new OTP.")
    if body.otp != otp_record.otp_code:
        otp_record.attempts += 1
        db.commit()
        remaining = 5 - otp_record.attempts
        raise HTTPException(status_code=400, detail=f"Invalid OTP. {remaining} attempt(s) remaining.")

    otp_record.is_used = True

    user = db.query(User).filter(User.email == body.email).first()
    if not user:
        db.commit()
        raise HTTPException(status_code=400, detail="User not found")

    user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"message": "Password reset successful"}


@router.post("/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
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
        "has_avatar": bool(current_user.avatar_path),
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
    return FileResponse(p)
