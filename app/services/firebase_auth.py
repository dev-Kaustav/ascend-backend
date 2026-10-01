"""Server-side verification of Firebase phone-auth ID tokens (D-17, D-19).

This is the only module that touches firebase_admin, and it imports it lazily inside the
functions: a missing or broken SDK must never break unrelated imports or tests (research
Pitfall 11). Tests monkeypatch `verify_phone_id_token`.

The service-account credential is read from FIREBASE_SERVICE_ACCOUNT_JSON (a minified JSON
document in the server's .env, which git and docker already ignore). It is never logged.
"""

import json
import os
import time


class InvalidOtpToken(Exception):
    """The ID token is forged, expired, malformed, not a phone sign-in, or too old."""


class OtpVerifierUnavailable(Exception):
    """We cannot verify tokens right now: Google certs unreachable or credentials missing."""


# A token whose OTP was entered longer ago than this is refused, so a leaked one-hour ID
# token cannot mint sessions later (research assumption A8). Raise it if real users are refused.
OTP_TOKEN_MAX_AGE_SECONDS = 300


def _now() -> float:
    return time.time()


def _app():
    try:
        import firebase_admin
        from firebase_admin import credentials
    except ImportError as exc:
        raise OtpVerifierUnavailable("firebase_admin is not installed") from exc

    try:
        return firebase_admin.get_app()
    except ValueError:
        pass

    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise OtpVerifierUnavailable("FIREBASE_SERVICE_ACCOUNT_JSON is not set")
    try:
        cred = credentials.Certificate(json.loads(raw))
    except (ValueError, TypeError):
        # Deliberately not chaining the message: it can quote parts of the credential.
        raise OtpVerifierUnavailable("FIREBASE_SERVICE_ACCOUNT_JSON is not a valid service account") from None
    try:
        return firebase_admin.initialize_app(cred)
    except ValueError:
        # Another request initialised the default app between get_app and here.
        return firebase_admin.get_app()


def verify_phone_id_token(id_token: str) -> str:
    """Return the verified E.164 phone number of a fresh Firebase phone sign-in."""
    app = _app()
    from firebase_admin import auth

    try:
        claims = auth.verify_id_token(id_token, app=app)
    except auth.CertificateFetchError as exc:
        raise OtpVerifierUnavailable("could not fetch Google signing certificates") from exc
    except (auth.InvalidIdTokenError, ValueError) as exc:
        # ExpiredIdTokenError and RevokedIdTokenError subclass InvalidIdTokenError.
        raise InvalidOtpToken(str(exc)) from exc

    if (claims.get("firebase") or {}).get("sign_in_provider") != "phone":
        raise InvalidOtpToken("not a phone sign-in")
    phone = claims.get("phone_number")
    if not phone or not isinstance(phone, str):
        raise InvalidOtpToken("no phone number in token")
    auth_time = claims.get("auth_time")
    if not isinstance(auth_time, (int, float)) or _now() - auth_time > OTP_TOKEN_MAX_AGE_SECONDS:
        raise InvalidOtpToken("sign-in is too old")
    return phone
