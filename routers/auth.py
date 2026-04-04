from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from auth.utils import create_access_token, verify_password, hash_password, decode_token, security
from core.database import get_db
from models.user import User

router = APIRouter(prefix="", tags=["auth"])


@router.get("/me")
def get_me(credentials=Depends(security), db: Session = Depends(get_db)):
    user_id = decode_token(credentials.credentials)
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user_id": str(user.user_id), "email": user.email}


@router.post("/signup")
def signup(email: str, password: str, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(email=email, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    return {"message": "User created"}


@router.post("/login")
def login(email: str, password: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": str(user.user_id)})
    return {"access_token": token, "token_type": "bearer"}
