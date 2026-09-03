from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.services.auth import authenticate_user, create_tokens
from app.schemas.auth import LoginRequest, TokenResponse, RefreshRequest, PasswordChangeRequest
from app.core.deps import get_current_active_user
from app.core.security import verify_password, get_password_hash
from app.models import User

router = APIRouter()

@router.post("/login", response_model=TokenResponse)
def login(request: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate_user(db, request.email, request.password)
    if not user:
        raise HTTPException(status_code=400, detail="Invalid credentials")
    access_token, refresh_token = create_tokens(user)
    return {"access_token": access_token, "refresh_token": refresh_token}

@router.post("/refresh", response_model=TokenResponse)
def refresh(request: RefreshRequest, db: Session = Depends(get_db)):
    from app.core.security import decode_token
    payload = decode_token(request.refresh_token)
    if not payload:
        raise HTTPException(status_code=400, detail="Invalid refresh token")
    user_id = payload.get("user_id")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=400, detail="User not found")
    # Every other endpoint goes through get_current_active_user, which rejects deleted and
    # deactivated accounts on each request. This one checked only existence, so a
    # deactivated user could still mint fresh tokens — they could not use them, since real
    # endpoints recheck, but the inconsistency is worth closing now that the frontend
    # actually calls this endpoint.
    if user.deleted_at or not user.is_active:
        raise HTTPException(status_code=401, detail="Inactive user")
    access_token, refresh_token = create_tokens(user)
    return {"access_token": access_token, "refresh_token": refresh_token}

@router.get("/me")
def get_me(
    current_user: User = Depends(get_current_active_user),
):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "role": current_user.role.value,
        "is_active": current_user.is_active,
    }

@router.patch("/me/password")
def change_password(
    payload: PasswordChangeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    current_user.password_hash = get_password_hash(payload.new_password)
    db.commit()
    return {"status": "ok"}
