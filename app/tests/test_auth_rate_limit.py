"""Rate limiting and logging of failed logins (08-07, PROD-04, D-22).

The limiter is DB-backed (`auth_attempts`). Tests freeze time by monkeypatching the
`rate_limit._utcnow` seam, which returns naive UTC, the same convention the service writes.
"""

import logging
from datetime import datetime, timedelta

import pytest

from app.core.security import get_password_hash
from app.models import User
from app.models.enums import EmployeeRole
from app.services import rate_limit

EMAIL = "staff@example.com"
PASSWORD = "correct-horse"
WRONG = "wrong-password-xyz"
T0 = datetime(2026, 1, 1, 12, 0, 0)


class Clock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now = self.now + timedelta(seconds=seconds)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(rate_limit, "_utcnow", c)
    return c


@pytest.fixture
def staff(db):
    user = User(email=EMAIL, password_hash=get_password_hash(PASSWORD), role=EmployeeRole.ADMIN)
    db.add(user)
    db.commit()
    return user


def _login(client, email=EMAIL, password=PASSWORD):
    return client.post("/auth/login", json={"email": email, "password": password})


def test_sixth_attempt_for_one_email_is_429_even_with_the_correct_password(client, staff, clock):
    for _ in range(5):
        assert _login(client, password=WRONG).status_code == 400
    res = _login(client)
    assert res.status_code == 429
    assert res.headers["Retry-After"] == "900"


def test_retry_after_is_whole_seconds_rounded_up_and_never_below_one(client, staff, clock):
    for _ in range(5):
        _login(client, password=WRONG)
    clock.advance(899.5)
    res = _login(client)
    assert res.status_code == 429
    assert res.headers["Retry-After"] == "1"
    clock.advance(0.5)  # exactly 900 s after the failures: they no longer count
    res = _login(client)
    assert res.status_code == 200, res.text


def test_retry_after_counts_down_from_the_oldest_failure(client, staff, clock):
    _login(client, password=WRONG)
    clock.advance(100)
    for _ in range(4):
        _login(client, password=WRONG)
    clock.advance(50.2)
    res = _login(client)
    assert res.status_code == 429
    # oldest failure is 150.2 s old -> 749.8 s left -> 750
    assert res.headers["Retry-After"] == "750"


def test_another_email_is_not_blocked(client, db, staff, clock):
    other = User(email="other@example.com", password_hash=get_password_hash("pw-other"), role=EmployeeRole.ADMIN)
    db.add(other)
    db.commit()
    for _ in range(5):
        _login(client, password=WRONG)
    assert _login(client).status_code == 429
    assert _login(client, email="other@example.com", password="pw-other").status_code == 200


def test_email_is_normalised_so_case_and_whitespace_cannot_dodge_the_cap(client, staff, clock):
    for variant in ["staff@example.com", "STAFF@example.com", " staff@example.com ", "Staff@Example.com", "staff@EXAMPLE.com"]:
        assert _login(client, email=variant, password=WRONG).status_code == 400
    assert _login(client).status_code == 429


def test_successful_login_records_nothing(client, db, staff, clock):
    for _ in range(3):
        assert _login(client).status_code == 200
    from app.models.auth_attempt import AuthAttempt

    assert db.query(AuthAttempt).count() == 0


def test_failures_are_logged_without_the_password(client, staff, clock, caplog):
    with caplog.at_level(logging.WARNING, logger="ascend.auth"):
        for _ in range(5):
            _login(client, password=WRONG)
    records = [r for r in caplog.records if r.name == "ascend.auth" and r.levelno == logging.WARNING]
    assert len(records) == 5
    for r in records:
        msg = r.getMessage()
        assert "login_failed" in msg
        assert EMAIL in msg
        assert "testclient" in msg
        assert WRONG not in msg
        assert PASSWORD not in msg


# --- per-IP ceilings, OTP exchange, purge, logging hygiene (Task 2) -------------------------

OTP_URL = "/auth/retailer/firebase"
ID_TOKEN = "eyJhbGciOiJSUzI1NiJ9.super-secret-id-token.sig"


def _otp_seam(monkeypatch, phone="+919876543210", exc=None):
    def fake(id_token):
        if exc is not None:
            raise exc
        return phone

    monkeypatch.setattr("app.services.firebase_auth.verify_phone_id_token", fake)


def _otp(client):
    return client.post(OTP_URL, json={"id_token": ID_TOKEN})


def test_eleventh_otp_exchange_from_one_ip_is_429_even_with_a_valid_token(client, monkeypatch, clock):
    from app.services.firebase_auth import InvalidOtpToken

    _otp_seam(monkeypatch, exc=InvalidOtpToken("bad"))
    for _ in range(10):
        assert _otp(client).status_code == 401
    _otp_seam(monkeypatch)  # now a valid +91 number
    res = _otp(client)
    assert res.status_code == 429
    assert res.headers["Retry-After"] == "900"
    clock.advance(900)
    assert _otp(client).status_code == 200


def test_otp_limit_is_per_ip(client, monkeypatch, clock):
    from app.services.firebase_auth import InvalidOtpToken

    _otp_seam(monkeypatch, exc=InvalidOtpToken("bad"))
    for _ in range(10):
        _otp(client)
    _otp_seam(monkeypatch)
    monkeypatch.setattr(rate_limit, "client_ip", lambda request: "203.0.113.9")
    assert _otp(client).status_code == 200


@pytest.mark.parametrize("kind", ["unavailable", "unsupported"])
def test_503_and_400_are_not_counted_as_failures(client, db, monkeypatch, clock, kind):
    from app.models.auth_attempt import AuthAttempt
    from app.services.firebase_auth import OtpVerifierUnavailable

    if kind == "unavailable":
        _otp_seam(monkeypatch, exc=OtpVerifierUnavailable("down"))
        expected = 503
    else:
        _otp_seam(monkeypatch, phone="+14155550123")
        expected = 400
    for _ in range(15):
        assert _otp(client).status_code == expected
    assert db.query(AuthAttempt).count() == 0
    _otp_seam(monkeypatch)
    assert _otp(client).status_code == 200


def test_thirty_failed_logins_from_one_ip_block_the_next_attempt(client, staff, clock):
    for i in range(30):
        assert _login(client, email=f"nobody{i}@example.com", password=WRONG).status_code == 400
    res = _login(client)  # the correct password for a real, unlocked email
    assert res.status_code == 429
    assert res.headers["Retry-After"] == "900"
    assert _login(client, email="another@example.com", password=WRONG).status_code == 429


def test_another_ip_can_still_log_in_while_one_email_is_locked(client, staff, clock, monkeypatch):
    for _ in range(5):
        _login(client, password=WRONG)
    assert _login(client).status_code == 429
    # The per-email cap is not IP-dependent, so the same email stays locked from anywhere...
    monkeypatch.setattr(rate_limit, "client_ip", lambda request: "198.51.100.7")
    assert _login(client).status_code == 429


def test_a_locked_email_does_not_block_a_different_email_from_another_ip(client, db, staff, clock, monkeypatch):
    other = User(email="other@example.com", password_hash=get_password_hash("pw-other"), role=EmployeeRole.ADMIN)
    db.add(other)
    db.commit()
    for _ in range(5):
        _login(client, password=WRONG)
    monkeypatch.setattr(rate_limit, "client_ip", lambda request: "198.51.100.7")
    assert _login(client, email="other@example.com", password="pw-other").status_code == 200


def test_otp_failure_log_has_ip_but_no_token_or_mobile(client, monkeypatch, clock, caplog):
    from app.services.firebase_auth import InvalidOtpToken

    _otp_seam(monkeypatch, phone="+919876543210", exc=InvalidOtpToken("bad token for +919876543210"))
    with caplog.at_level(logging.WARNING, logger="ascend.auth"):
        _otp(client)
    records = [r for r in caplog.records if r.name == "ascend.auth" and r.levelno == logging.WARNING]
    assert len(records) == 1
    msg = records[0].getMessage()
    assert "otp_failed" in msg
    assert "testclient" in msg
    assert ID_TOKEN not in msg
    assert "super-secret-id-token" not in msg
    assert "9876543210" not in msg


def test_mask_mobile_keeps_only_the_last_four_digits():
    assert rate_limit.mask_mobile(9876543210) == "******3210"
    assert rate_limit.mask_mobile("9876543210") == "******3210"


def test_rows_older_than_24_hours_are_purged_on_the_next_failure(db, clock):
    from app.models.auth_attempt import AuthAttempt

    clock.advance(-25 * 3600)
    rate_limit.record_failure(db, "login_ip", "old")
    clock.advance(2 * 3600)  # 23 h before the real "now"
    rate_limit.record_failure(db, "login_ip", "recent")
    # The 25 h row was already 2 h old when the 23 h-old row was written; it is not yet past 24 h.
    assert db.query(AuthAttempt).count() == 2
    clock.advance(23 * 3600)  # back to T0: old row is 25 h old, recent is 23 h old
    rate_limit.record_failure(db, "login_ip", "now")
    keys = sorted(r.key for r in db.query(AuthAttempt).all())
    assert keys == ["now", "recent"]


def test_long_email_does_not_overflow_the_key_column(client, staff, clock):
    long_email = "a" * 400 + "@example.com"
    assert _login(client, email=long_email, password=WRONG).status_code == 400


def test_log_line_cannot_be_forged_with_newlines_in_the_email(client, staff, clock, caplog):
    with caplog.at_level(logging.WARNING, logger="ascend.auth"):
        _login(client, email="x@example.com\nFAKE login_ok", password=WRONG)
    msg = [r for r in caplog.records if r.name == "ascend.auth"][0].getMessage()
    assert "\n" not in msg
