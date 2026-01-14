from app.services.auth import authenticate_user, create_tokens
from app.models import User
from app.models.enums import EmployeeRole
from app.core.security import get_password_hash, decode_token

def test_login(db):
    user = User(email="test@example.com", password_hash=get_password_hash("password"), role=EmployeeRole.ADMIN)
    db.add(user)
    db.commit()
    authenticated = authenticate_user(db, "test@example.com", "password")
    assert authenticated is not None
    assert authenticated.email == "test@example.com"

def test_login_invalid(db):
    authenticated = authenticate_user(db, "invalid@example.com", "password")
    assert authenticated is None

def test_token_validation(db):
    user = User(email="token@example.com", password_hash=get_password_hash("password"), role=EmployeeRole.ADMIN)
    db.add(user)
    db.commit()
    access_token, _ = create_tokens(user)
    payload = decode_token(access_token)
    assert payload["user_id"] == user.id
    assert payload["role"] == EmployeeRole.ADMIN.value
