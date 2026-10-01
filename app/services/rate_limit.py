"""DB-backed failed-attempt limiter for /auth/login and the retailer OTP exchange (PROD-04, D-22).

Only failures are recorded. `check` runs before the credential is verified, so a locked-out
caller gets 429 even with the right password. The count-then-insert is not atomic: concurrent
failures can overshoot a cap by the number of in-flight requests (single uvicorn worker today;
accepted, revisit if Phase 7 adds workers).
"""

import logging
import math
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.auth_attempt import AuthAttempt

WINDOW_SECONDS = 900
# Research assumption A7 (flagged): starting values, not measured ones.
LIMITS = {"login_email": 5, "login_ip": 30, "otp_ip": 10}
RETENTION_HOURS = 24
_KEY_MAX = 255

auth_logger = logging.getLogger("ascend.auth")
if not auth_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    auth_logger.addHandler(_handler)
    auth_logger.setLevel(logging.INFO)


class RateLimited(Exception):
    def __init__(self, retry_after: int):
        super().__init__(f"rate limited, retry after {retry_after}s")
        self.retry_after = retry_after


def _utcnow() -> datetime:
    """Naive UTC. A seam so tests can freeze the clock."""
    return datetime.utcnow()


def _key(key: str) -> str:
    return key[:_KEY_MAX]


def check(db: Session, scope: str, key: str) -> None:
    """Raise RateLimited when this scope/key already has LIMITS[scope] failures in the window."""
    key = _key(key)
    now = _utcnow()
    cutoff = now - timedelta(seconds=WINDOW_SECONDS)
    limit = LIMITS[scope]
    in_window = (
        db.query(AuthAttempt.created_at)
        .filter(AuthAttempt.scope == scope, AuthAttempt.key == key, AuthAttempt.created_at > cutoff)
        .order_by(AuthAttempt.created_at.asc())
        .all()
    )
    if len(in_window) < limit:
        return
    # The caller is released when enough failures have aged out to get back under the cap. At
    # exactly the cap that is the oldest one; if concurrency overshot, it is a later one.
    releasing = in_window[len(in_window) - limit][0]
    remaining = WINDOW_SECONDS - (now - releasing).total_seconds()
    raise RateLimited(max(1, math.ceil(remaining)))


def record_failure(db: Session, scope: str, key: str) -> None:
    """Write one failure row, purge rows past retention, commit."""
    now = _utcnow()
    db.add(AuthAttempt(scope=scope, key=_key(key), created_at=now))
    db.query(AuthAttempt).filter(
        AuthAttempt.created_at < now - timedelta(hours=RETENTION_HOURS)
    ).delete(synchronize_session=False)
    db.commit()


def client_ip(request) -> str:
    return request.client.host if request.client else "unknown"


def mask_mobile(n) -> str:
    return "******" + str(n)[-4:]


def printable(value: str) -> str:
    """Strip control characters so a hostile email cannot forge log lines."""
    return "".join(c for c in value[:_KEY_MAX] if c.isprintable())
