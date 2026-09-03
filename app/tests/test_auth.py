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


def test_refresh_rejects_deactivated_user(db, client):
    """/auth/refresh used to check only that the user existed, unlike every other endpoint,
    which goes through get_current_active_user. A deactivated account could still mint
    fresh tokens."""
    user = User(
        email="refresh-inactive@example.com",
        password_hash=get_password_hash("password"),
        role=EmployeeRole.ADMIN,
        is_active=True,
    )
    db.add(user)
    db.commit()
    _, refresh_token = create_tokens(user)

    ok = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert ok.status_code == 200, ok.text
    assert ok.json()["access_token"]

    user.is_active = False
    db.commit()

    denied = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert denied.status_code == 401
    assert "Inactive" in denied.json()["detail"]


def test_refresh_returns_a_usable_access_token(db, client):
    """The frontend now replays a 401'd request with the refreshed token, so the token this
    endpoint returns has to be accepted by a real endpoint."""
    user = User(
        email="refresh-usable@example.com",
        password_hash=get_password_hash("password"),
        role=EmployeeRole.ADMIN,
        is_active=True,
    )
    db.add(user)
    db.commit()
    _, refresh_token = create_tokens(user)

    refreshed = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert refreshed.status_code == 200
    new_access = refreshed.json()["access_token"]

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {new_access}"})
    assert me.status_code == 200, me.text
    assert me.json()["email"] == "refresh-usable@example.com"
