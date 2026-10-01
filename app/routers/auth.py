from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.services import firebase_auth
from app.services.auth import authenticate_user, create_tokens
from app.services.firebase_auth import InvalidOtpToken, OtpVerifierUnavailable
from app.services.retailer_onboarding import (
    UnsupportedMobile,
    get_or_create_otp_user,
    link_existing_retailer,
    normalize_indian_mobile,
    onboarding_state,
    shop_prefill,
)
from app.schemas.auth import (
    LoginRequest,
    TokenResponse,
    RefreshRequest,
    PasswordChangeRequest,
    RetailerFirebaseLoginRequest,
    RetailerLoginResponse,
)
from app.core.deps import get_current_active_user
from app.core.security import verify_password, get_password_hash
from app.models import Retailer, User

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
    # An access token must not mint new sessions. Tokens minted before "typ" existed carry none.
    if payload.get("typ") == "access":
        raise HTTPException(status_code=401, detail="Invalid refresh token")
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
    # This endpoint does not go through get_current_user, so it has to make the same
    # token_version check itself — otherwise a refresh token from before a password change
    # would mint a valid new pair and undo the sign-out entirely.
    if payload.get("tv", 0) != (user.token_version or 0):
        raise HTTPException(status_code=401, detail="Session expired, sign in again")
    access_token, refresh_token = create_tokens(user)
    return {"access_token": access_token, "refresh_token": refresh_token}

@router.post("/retailer/firebase", response_model=RetailerLoginResponse)
def retailer_firebase_login(payload: RetailerFirebaseLoginRequest, db: Session = Depends(get_db)):
    """Exchange a Firebase phone-auth ID token for our own session (STORE-09, D-17..D-20).

    One flow signs a retailer in or up. The number comes only from the verified token, so it
    cannot be claimed without owning it.
    """
    try:
        e164 = firebase_auth.verify_phone_id_token(payload.id_token)
    except InvalidOtpToken:
        raise HTTPException(status_code=401, detail="Could not verify the OTP. Please try again.")
    except OtpVerifierUnavailable:
        raise HTTPException(status_code=503, detail="Login is temporarily unavailable.")
    try:
        mobile = normalize_indian_mobile(e164)
    except UnsupportedMobile:
        raise HTTPException(status_code=400, detail="Only Indian (+91) mobile numbers can sign in.")

    user = get_or_create_otp_user(db, mobile)
    if user.deleted_at or not user.is_active:
        raise HTTPException(status_code=401, detail="Account disabled")
    retailer = link_existing_retailer(db, user)
    state = onboarding_state(user)
    if state == "confirm" and retailer is None:
        # Linked on an earlier login but never confirmed: offer the same pre-fill again.
        retailer = db.query(Retailer).filter(Retailer.id == user.retailer_id).first()
    access_token, refresh_token = create_tokens(user)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "onboarding": state,
        "prefill": shop_prefill(retailer) if state == "confirm" and retailer else None,
    }

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
    # Signs out every session this user has, this one included: the caller's own token was
    # minted under the previous version and stops working on its next request. The client
    # is expected to drop its tokens and send the user back to the login screen.
    current_user.token_version = (current_user.token_version or 0) + 1
    db.commit()
    return {"status": "ok", "reauthentication_required": True}
