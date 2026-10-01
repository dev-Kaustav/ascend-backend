from sqlalchemy import Column, DateTime, Index, Integer, String

from app.db.base import Base


class AuthAttempt(Base):
    """One failed login or failed OTP exchange, kept only long enough to rate-limit it.

    `created_at` is naive UTC written by `app.services.rate_limit`, not a server default, so
    SQLite and PostgreSQL compare the 15-minute window identically (the RPT-06 naive/aware
    lesson). `key` is "<client ip>|<normalised email>" (scope "login_email") or the client IP.
    """

    __tablename__ = "auth_attempts"
    __table_args__ = (
        Index("ix_auth_attempts_scope_key_created", "scope", "key", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    scope = Column(String(16), nullable=False)
    key = Column(String(255), nullable=False)
    created_at = Column(DateTime, nullable=False)
