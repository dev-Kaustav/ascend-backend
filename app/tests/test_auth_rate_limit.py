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
