from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.services.auth import authenticate_user, create_tokens
from app.schemas.auth import LoginRequest, TokenResponse, RefreshRequest, PasswordChangeRequest
from app.core.deps import get_current_active_user
from app.services.admin import get_user_permission_entries
from app.services.access_rules import list_access_rules
from app.core.security import verify_password, get_password_hash
from app.models import User
from app.schemas.access_rules import AccessRuleResponse

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
    access_token, refresh_token = create_tokens(user)
    return {"access_token": access_token, "refresh_token": refresh_token}

@router.get("/me")
def get_me(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    permissions = get_user_permission_entries(db, current_user)
    return {
        "id": current_user.id,
        "email": current_user.email,
        "role": current_user.role.value,
        "is_active": current_user.is_active,
        "permissions": permissions,
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

@router.get("/access-rules", response_model=list[AccessRuleResponse])
def get_access_rules(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return list_access_rules(db)
