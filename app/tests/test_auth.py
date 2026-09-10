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


def test_password_change_signs_out_every_session(db, client):
    """A password change ends every session opened under the old password, including the
    one that made the change and any refresh token still in hand."""
    user = User(
        email="rotate@example.com",
        password_hash=get_password_hash("old-password"),
        role=EmployeeRole.ADMIN,
        is_active=True,
    )
    db.add(user)
    db.commit()
    # Two devices, each holding its own pair minted before the change.
    phone_access, phone_refresh = create_tokens(user)
    laptop_access, _ = create_tokens(user)
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {phone_access}"}).status_code == 200
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {laptop_access}"}).status_code == 200

    changed = client.patch(
        "/auth/me/password",
        json={"current_password": "old-password", "new_password": "new-password"},
        headers={"Authorization": f"Bearer {laptop_access}"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["reauthentication_required"] is True

    # The other device, the device that made the change, and the refresh token that could
    # have minted a fresh pair are all dead.
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {phone_access}"}).status_code == 401
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {laptop_access}"}).status_code == 401
    denied = client.post("/auth/refresh", json={"refresh_token": phone_refresh})
    assert denied.status_code == 401

    # Logging in again with the new password works, and that session is usable.
    logged_in = client.post("/auth/login", json={"email": "rotate@example.com", "password": "new-password"})
    assert logged_in.status_code == 200, logged_in.text
    fresh = logged_in.json()["access_token"]
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {fresh}"}).status_code == 200


def test_admin_reset_signs_out_the_target_user(db, client):
    """The case that matters most: an admin resetting a password must end the sessions the
    account already has, not wait for them to expire."""
    from app.services.admin import set_user_password

    admin = User(
        email="reset-admin@example.com",
        password_hash=get_password_hash("password"),
        role=EmployeeRole.ADMIN,
        is_active=True,
    )
    target = User(
        email="reset-target@example.com",
        password_hash=get_password_hash("password"),
        role=EmployeeRole.SALESMAN,
        is_active=True,
    )
    db.add_all([admin, target])
    db.commit()
    target_access, _ = create_tokens(target)
    admin_access, _ = create_tokens(admin)
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {target_access}"}).status_code == 200

    set_user_password(db, target.id, "admin-issued-password", admin)

    assert client.get("/auth/me", headers={"Authorization": f"Bearer {target_access}"}).status_code == 401
    # The admin's own session is untouched — only the target's tokens were versioned out.
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {admin_access}"}).status_code == 200


def test_tokens_minted_before_this_feature_still_authenticate(db):
    """A token with no "tv" claim reads as version 0, matching the column default, so
    deploying this does not sign the whole company out — only a password change does."""
    from app.core.security import create_access_token
    from app.core.deps import get_current_user
    from fastapi.security import HTTPAuthorizationCredentials

    user = User(
        email="legacy-token@example.com",
        password_hash=get_password_hash("password"),
        role=EmployeeRole.ADMIN,
        is_active=True,
    )
    db.add(user)
    db.commit()
    legacy = create_access_token({"user_id": user.id, "role": EmployeeRole.ADMIN.value})

    resolved = get_current_user(
        credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=legacy),
        db=db,
    )
    assert resolved.id == user.id
