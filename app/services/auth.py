from sqlalchemy.orm import Session
from app.core.security import verify_password, create_access_token, create_refresh_token
from app.models import User

def authenticate_user(db: Session, email: str, password: str):
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.password_hash):
        return None
    return user

def create_tokens(user: User):
    role_value = user.role.value if hasattr(user.role, "value") else user.role
    access_token = create_access_token({"user_id": user.id, "role": role_value})
    refresh_token = create_refresh_token({"user_id": user.id, "role": role_value})
    return access_token, refresh_token
