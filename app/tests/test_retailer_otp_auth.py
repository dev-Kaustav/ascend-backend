"""Retailer sign-in / sign-up with a Firebase phone-auth ID token (08-03, STORE-09/STORE-10).

Endpoint tests monkeypatch the one seam, `app.services.firebase_auth.verify_phone_id_token`.
Seam unit tests monkeypatch `firebase_admin.auth.verify_id_token`, `_app` and `_now`.
Nothing here touches the network or a real Firebase project.
"""

import importlib.util
import json
import logging
import os
import time
from datetime import datetime

import pytest
from jose import jwt

from app.core import security
from app.core.security import create_access_token, create_refresh_token, decode_token, get_password_hash
from app.models import Retailer, User
from app.models.enums import EmployeeRole
from app.services import firebase_auth
from app.services.auth import create_tokens
from app.services.firebase_auth import InvalidOtpToken, OtpVerifierUnavailable

PHONE = "+919876543210"
MOBILE = 9876543210
URL = "/auth/retailer/firebase"


@pytest.fixture(autouse=True)
def _no_default_firebase_app():
    yield
    import firebase_admin

    for app in list(firebase_admin._apps.values()):
        firebase_admin.delete_app(app)


def _seam(monkeypatch, phone=PHONE, exc=None):
    def fake(id_token):
        if exc is not None:
            raise exc
        return phone

    monkeypatch.setattr("app.services.firebase_auth.verify_phone_id_token", fake)


def _login(client, monkeypatch, phone=PHONE):
    _seam(monkeypatch, phone)
    return client.post(URL, json={"id_token": "t"})


def _retailer(db, mobile=MOBILE, **kw):
    r = Retailer(name=kw.pop("name", "Shop"), mobile_number=mobile, **kw)
    db.add(r)
    db.commit()
    return r


# --- endpoint: sign up / sign in -------------------------------------------------


def test_new_number_signs_up_as_retailer(db, client, monkeypatch):
    res = _login(client, monkeypatch)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["onboarding"] == "new"
    assert body["prefill"] is None
    user = db.query(User).filter(User.phone_number == MOBILE).one()
    assert user.role == EmployeeRole.RETAILER
    assert user.email == "9876543210@store.invalid"
    assert user.is_active
    assert user.retailer_id is None


def test_otp_user_can_never_use_password_login(db, client, monkeypatch):
    _login(client, monkeypatch)
    res = client.post("/auth/login", json={"email": "9876543210@store.invalid", "password": "anything"})
    assert res.status_code == 400  # Invalid credentials, not a 500 from a non-bcrypt hash


def test_same_number_returns_same_user(db, client, monkeypatch):
    _login(client, monkeypatch)
    first = db.query(User).filter(User.phone_number == MOBILE).one().id
    res = _login(client, monkeypatch)
    assert res.status_code == 200
    assert db.query(User).filter(User.phone_number == MOBILE).count() == 1
    assert db.query(User).filter(User.phone_number == MOBILE).one().id == first


def test_invalid_token_is_401(client, monkeypatch):
    _seam(monkeypatch, exc=InvalidOtpToken("bad"))
    res = client.post(URL, json={"id_token": "t"})
    assert res.status_code == 401
    assert res.json()["detail"] == "Could not verify the OTP. Please try again."


def test_verifier_unavailable_is_503(client, monkeypatch):
    _seam(monkeypatch, exc=OtpVerifierUnavailable("down"))
    res = client.post(URL, json={"id_token": "t"})
    assert res.status_code == 503
    assert res.json()["detail"] == "Login is temporarily unavailable."


def test_non_indian_number_is_400(client, monkeypatch):
    res = _login(client, monkeypatch, phone="+14155550100")
    assert res.status_code == 400
    assert res.json()["detail"] == "Only Indian (+91) mobile numbers can sign in."


def test_inactive_and_deleted_users_are_refused(db, client, monkeypatch):
    user = User(
        email="9876543210@store.invalid",
        password_hash=get_password_hash("x"),
        role=EmployeeRole.RETAILER,
        phone_number=MOBILE,
        is_active=False,
    )
    db.add(user)
    db.commit()
    res = _login(client, monkeypatch)
    assert res.status_code == 401
    assert res.json()["detail"] == "Account disabled"

    user.is_active = True
    user.deleted_at = datetime.utcnow()
    db.commit()
    assert _login(client, monkeypatch).status_code == 401


def test_body_carries_only_the_token(client, monkeypatch):
    _seam(monkeypatch)
    assert client.post(URL, json={}).status_code == 422
    assert client.post(URL, json={"id_token": ""}).status_code == 422
    assert client.post(URL, json={"id_token": "x" * 4097}).status_code == 422


def test_concurrent_first_login_race_ends_with_one_user(db, monkeypatch):
    """The loser of a race on users.phone_number catches the IntegrityError and re-reads."""
    from app.services import retailer_onboarding as ro

    winner = User(
        email="9876543210@store.invalid",
        password_hash=get_password_hash("x"),
        role=EmployeeRole.RETAILER,
        phone_number=MOBILE,
    )
    real_query = db.query
    calls = {"n": 0}

    class _Empty:
        def filter(self, *a, **k):
            return self

        def first(self):
            return None

    def racing_query(*args, **kwargs):
        # The first lookup (before the insert) sees nothing; the winner commits meanwhile.
        if args and args[0] is User and calls["n"] == 0:
            calls["n"] += 1
            db.add(winner)
            db.commit()
            return _Empty()
        return real_query(*args, **kwargs)

    monkeypatch.setattr(db, "query", racing_query)
    user = ro.get_or_create_otp_user(db, MOBILE)
    assert user.id == winner.id
    assert real_query(User).filter(User.phone_number == MOBILE).count() == 1


# --- token lifetime ---------------------------------------------------------------


def test_retailer_refresh_is_30_days_access_is_60_minutes(db, client, monkeypatch):
    now = time.time()
    body = _login(client, monkeypatch).json()
    refresh = decode_token(body["refresh_token"])
    access = decode_token(body["access_token"])
    assert abs(refresh["exp"] - (now + 30 * 86400)) <= 60
    assert abs(access["exp"] - (now + 60 * 60)) <= 60
    assert refresh["role"] == "RETAILER"


def test_staff_refresh_stays_7_days(db):
    staff = User(email="s@example.com", password_hash=get_password_hash("p"), role=EmployeeRole.SALESMAN)
    db.add(staff)
    db.commit()
    now = time.time()
    access, refresh = create_tokens(staff)
    assert abs(decode_token(refresh)["exp"] - (now + 7 * 86400)) <= 60
    assert abs(decode_token(access)["exp"] - (now + 60 * 60)) <= 60


def test_refresh_slides_the_retailer_window(db, client, monkeypatch):
    body = _login(client, monkeypatch).json()
    res = client.post("/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert res.status_code == 200
    assert abs(decode_token(res.json()["refresh_token"])["exp"] - (time.time() + 30 * 86400)) <= 60


def test_bumping_token_version_ends_the_session(db, client, monkeypatch):
    body = _login(client, monkeypatch).json()
    user = db.query(User).filter(User.phone_number == MOBILE).one()
    user.token_version = (user.token_version or 0) + 1
    db.commit()
    res = client.post("/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert res.status_code == 401


# --- Task 2 decision: typ-claim ----------------------------------------------------


def test_token_types_are_stamped(db, client, monkeypatch):
    body = _login(client, monkeypatch).json()
    assert decode_token(body["access_token"])["typ"] == "access"
    assert decode_token(body["refresh_token"])["typ"] == "refresh"


def test_access_token_cannot_refresh(db, client, monkeypatch):
    body = _login(client, monkeypatch).json()
    res = client.post("/auth/refresh", json={"refresh_token": body["access_token"]})
    assert res.status_code == 401


def test_refresh_token_cannot_be_a_bearer(db, client, monkeypatch):
    body = _login(client, monkeypatch).json()
    ok = client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert ok.status_code == 200
    bad = client.get("/auth/me", headers={"Authorization": f"Bearer {body['refresh_token']}"})
    assert bad.status_code == 401


def test_tokens_without_typ_keep_working(db, client, monkeypatch):
    _login(client, monkeypatch)
    user = db.query(User).filter(User.phone_number == MOBILE).one()
    claims = {"user_id": user.id, "role": "RETAILER", "tv": 0, "exp": int(time.time()) + 600}
    legacy = jwt.encode(claims, security.SECRET_KEY, algorithm="HS256")
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {legacy}"}).status_code == 200
    assert client.post("/auth/refresh", json={"refresh_token": legacy}).status_code == 200


# --- Firebase seam (unit) ----------------------------------------------------------


def _claims(provider="phone", phone=PHONE, auth_time=1_000_000):
    claims = {"firebase": {"sign_in_provider": provider}, "auth_time": auth_time}
    if phone is not None:
        claims["phone_number"] = phone
    return claims


@pytest.fixture
def seam(monkeypatch):
    """Patch the SDK call, the app lookup and the clock; return a setter for the claims."""
    import firebase_admin.auth as fa

    state = {"claims": _claims(), "exc": None}

    def fake_verify(token, app=None, check_revoked=False, **kw):
        if state["exc"] is not None:
            raise state["exc"]
        return state["claims"]

    monkeypatch.setattr(fa, "verify_id_token", fake_verify)
    monkeypatch.setattr(firebase_auth, "_app", lambda: object())
    monkeypatch.setattr(firebase_auth, "_now", lambda: 1_000_000 + 10)
    return state


def test_seam_returns_phone_for_fresh_phone_token(seam):
    assert firebase_auth.verify_phone_id_token("t") == PHONE


def test_seam_auth_time_boundary(seam, monkeypatch):
    assert firebase_auth.OTP_TOKEN_MAX_AGE_SECONDS == 300
    monkeypatch.setattr(firebase_auth, "_now", lambda: 1_000_000 + 300)
    assert firebase_auth.verify_phone_id_token("t") == PHONE
    monkeypatch.setattr(firebase_auth, "_now", lambda: 1_000_000 + 301)
    with pytest.raises(InvalidOtpToken):
        firebase_auth.verify_phone_id_token("t")


def test_seam_rejects_non_phone_provider(seam):
    seam["claims"] = _claims(provider="password")
    with pytest.raises(InvalidOtpToken):
        firebase_auth.verify_phone_id_token("t")


def test_seam_rejects_missing_phone(seam):
    seam["claims"] = _claims(phone=None)
    with pytest.raises(InvalidOtpToken):
        firebase_auth.verify_phone_id_token("t")


def test_seam_rejects_missing_auth_time(seam):
    seam["claims"] = {"firebase": {"sign_in_provider": "phone"}, "phone_number": PHONE}
    with pytest.raises(InvalidOtpToken):
        firebase_auth.verify_phone_id_token("t")


def test_seam_maps_invalid_and_expired_to_invalid(seam):
    import firebase_admin.auth as fa

    seam["exc"] = fa.InvalidIdTokenError("bad")
    with pytest.raises(InvalidOtpToken):
        firebase_auth.verify_phone_id_token("t")
    seam["exc"] = ValueError("bad")
    with pytest.raises(InvalidOtpToken):
        firebase_auth.verify_phone_id_token("t")


def test_seam_maps_cert_fetch_to_unavailable(seam):
    import firebase_admin.auth as fa

    seam["exc"] = fa.CertificateFetchError("no certs", cause=None)
    with pytest.raises(OtpVerifierUnavailable):
        firebase_auth.verify_phone_id_token("t")


@pytest.mark.parametrize("value", [None, "", "   ", "not json", '{"type": "service_account"}'])
def test_seam_unavailable_without_valid_service_account(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT_JSON", raising=False)
    else:
        monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", value)
    with pytest.raises(OtpVerifierUnavailable):
        firebase_auth.verify_phone_id_token("t")


def test_real_sdk_rejects_garbage_offline(monkeypatch):
    """A throwaway service account is enough for the real SDK to reject a malformed token
    without any network access."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    account = {
        "type": "service_account",
        "project_id": "throwaway-project",
        "private_key_id": "abc123",
        "private_key": pem,
        "client_email": "x@throwaway-project.iam.gserviceaccount.com",
        "client_id": "1",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", json.dumps(account))
    with pytest.raises(InvalidOtpToken):
        firebase_auth.verify_phone_id_token("not-a-jwt")


# --- linking an existing shop (D-20) ------------------------------------------------


def test_single_match_links_and_prefills(db, client, monkeypatch):
    r = _retailer(
        db,
        name="Sharma General Store",
        address_line1="12 Main Rd",
        address_line2="Near Temple",
        city="Gurgaon",
        state="Haryana",
        pincode=122001,
        gst_number="06ABCDE1234F1Z5",
        latitude=28.45,
        longitude=77.02,
    )
    body = _login(client, monkeypatch).json()
    assert body["onboarding"] == "confirm"
    assert body["prefill"] == {
        "shop_name": "Sharma General Store",
        "address_line1": "12 Main Rd",
        "address_line2": "Near Temple",
        "city": "Gurgaon",
        "state": "Haryana",
        "pincode": 122001,
        "gst_number": "06ABCDE1234F1Z5",
        "latitude": 28.45,
        "longitude": 77.02,
    }
    assert db.query(User).filter(User.phone_number == MOBILE).one().retailer_id == r.id


def test_ambiguous_match_is_new_and_logged_masked(db, client, monkeypatch, caplog):
    _retailer(db, name="A", gst_number="GST-A")
    _retailer(db, name="B", gst_number="GST-B")
    with caplog.at_level(logging.DEBUG):
        body = _login(client, monkeypatch).json()
    assert body["onboarding"] == "new"
    assert body["prefill"] is None
    assert db.query(User).filter(User.phone_number == MOBILE).one().retailer_id is None
    warnings = [r for r in caplog.records if r.name == "ascend.auth" and r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "ambiguous_mobile" in warnings[0].getMessage()
    assert "3210" in warnings[0].getMessage()
    assert all("9876543210" not in r.getMessage() for r in caplog.records)


def test_zero_match_is_new(db, client, monkeypatch):
    _retailer(db, mobile=9123456789)
    body = _login(client, monkeypatch).json()
    assert body["onboarding"] == "new"
    assert db.query(User).filter(User.phone_number == MOBILE).one().retailer_id is None


def test_legacy_12_digit_row_does_not_match(db, client, monkeypatch):
    _retailer(db, mobile=919876543210)
    body = _login(client, monkeypatch).json()
    assert body["onboarding"] == "new"
    assert body["prefill"] is None


def test_confirmed_shop_is_ready_without_prefill(db, client, monkeypatch):
    r = _retailer(db, name="Mine")
    _login(client, monkeypatch)
    user = db.query(User).filter(User.phone_number == MOBILE).one()
    user.shop_confirmed_at = datetime.utcnow()
    db.commit()
    body = _login(client, monkeypatch).json()
    assert body["onboarding"] == "ready"
    assert body["prefill"] is None
    assert user.retailer_id == r.id


def test_user_with_retailer_is_never_relinked(db, client, monkeypatch):
    first = _retailer(db, name="First")
    _login(client, monkeypatch)
    assert db.query(User).filter(User.phone_number == MOBILE).one().retailer_id == first.id
    _retailer(db, name="Second")  # now two match, but the user is already linked
    body = _login(client, monkeypatch).json()
    assert body["onboarding"] == "confirm"
    assert body["prefill"]["shop_name"] == "First"
    assert db.query(User).filter(User.phone_number == MOBILE).one().retailer_id == first.id


# --- audit script ---------------------------------------------------------------------


def _load_audit():
    path = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "audit_retailer_mobiles.py")
    spec = importlib.util.spec_from_file_location("audit_retailer_mobiles", os.path.abspath(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _audit_fixture(db):
    _retailer(db, mobile=None, name="null")
    _retailer(db, mobile=9876543210, name="v1")
    _retailer(db, mobile=9876543210, name="v1-dup")
    _retailer(db, mobile=9123456789, name="v2")
    _retailer(db, mobile=919876543211, name="p91")
    _retailer(db, mobile=12345, name="short")
    _retailer(db, mobile=5876543210, name="bad-start")
    db.add(
        User(
            email="u@store.invalid",
            password_hash=get_password_hash("x"),
            role=EmployeeRole.RETAILER,
            phone_number=9123456789,
            retailer_id=db.query(Retailer).filter(Retailer.name == "v2").one().id,
        )
    )
    db.commit()


def test_audit_counts(db):
    _audit_fixture(db)
    result = _load_audit().audit(db)
    assert result["total"] == 7
    assert result["null_mobile"] == 1
    assert result["valid_10_digit"] == 3
    assert result["prefixed_91_12_digit"] == 1
    assert result["other_invalid"] == 2
    assert result["shared_numbers"] == 1
    assert result["retailers_on_shared_numbers"] == 2
    assert result["already_linked_users"] == 1


def test_audit_is_read_only_and_masked(db, monkeypatch):
    _audit_fixture(db)
    module = _load_audit()

    def boom():
        raise AssertionError("audit must never commit")

    monkeypatch.setattr(db, "commit", boom)
    result = module.audit(db)
    report = module.format_report(db)
    assert result["total"] == 7
    assert not db.new and not db.dirty and not db.deleted
    for number in ("9876543210", "9123456789", "919876543211", "5876543210"):
        assert number not in report
    assert "3211" in report  # last 4 digits only
