from sqlalchemy.orm import Session
from app.core.security import (
    RETAILER_REFRESH_TOKEN_EXPIRE_DAYS,
    create_access_token,
    create_refresh_token,
    verify_password,
)
from app.models import User

def authenticate_user(db: Session, email: str, password: str):
    normalized_email = email.strip().lower()
    user = db.query(User).filter(User.email == normalized_email).first()
    if not user or user.deleted_at or not user.is_active or not verify_password(password, user.password_hash):
        return None
    return user

def create_tokens(user: User):
    role_value = user.role.value if hasattr(user.role, "value") else user.role
    # "tv" pins the token to the password it was issued under. A password change bumps
    # User.token_version, and get_current_user then refuses every token carrying the older
    # value — which is every token minted before the change, on every device.
    claims = {"user_id": user.id, "role": role_value, "tv": user.token_version or 0}
    access_token = create_access_token(claims)
    refresh_token = create_refresh_token(
        claims,
        days=RETAILER_REFRESH_TOKEN_EXPIRE_DAYS if role_value == "RETAILER" else None,
    )
    return access_token, refresh_token
