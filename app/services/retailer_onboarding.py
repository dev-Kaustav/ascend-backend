"""Sign-in / sign-up bookkeeping for retailers who log in with a phone OTP (08-03, D-18, D-20).

The canonical identity is the integer of the 10 digits after +91, compared by exact equality
with users.phone_number and retailers.mobile_number. Legacy 12-digit (91XXXXXXXXXX) retailer
rows therefore never match; the audit script reports how many exist.
"""

import logging
import re
import secrets

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import get_password_hash
from app.models import Retailer, User
from app.models.enums import EmployeeRole

logger = logging.getLogger("ascend.auth")

STORE_EMAIL_DOMAIN = "store.invalid"  # RFC 2606 reserved TLD: never routable

_INDIAN_MOBILE = re.compile(r"\+91([6-9][0-9]{9})")


class UnsupportedMobile(ValueError):
    """The verified number is not a +91 Indian mobile."""


def normalize_indian_mobile(e164: str) -> int:
    match = _INDIAN_MOBILE.fullmatch(e164 or "")
    if not match:
        raise UnsupportedMobile("Only Indian (+91) mobile numbers can sign in.")
    return int(match.group(1))


def _mask(mobile: int) -> str:
    return "******" + str(mobile)[-4:]


def get_or_create_otp_user(db: Session, mobile: int) -> User:
    """Return the user for this number, creating a RETAILER instantly if there is none (D-18).

    The password hash is a valid bcrypt of a random secret nobody sees, so /auth/login can
    never succeed for this user and verify_password never raises (research Pitfall 7).
    """
    user = db.query(User).filter(User.phone_number == mobile).first()
    if user:
        return user
    user = User(
        email=f"{mobile}@{STORE_EMAIL_DOMAIN}",
        password_hash=get_password_hash(secrets.token_urlsafe(32)),
        role=EmployeeRole.RETAILER,
        phone_number=mobile,
        retailer_id=None,
        is_active=True,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # Lost a race with a concurrent first login for the same number: it is a login now.
        db.rollback()
        existing = db.query(User).filter(User.phone_number == mobile).first()
        if existing is None:
            raise
        return existing
    return user


def link_existing_retailer(db: Session, user: User) -> Retailer | None:
    """Link a user with no shop to the one retailer record carrying their number (D-20).

    Only an exact single match links; anything ambiguous is treated as a new shop so that
    one shop's name, address and GSTIN are never shown to the owner of another.
    """
    if user.retailer_id is not None or user.shop_confirmed_at is not None or user.phone_number is None:
        return None
    matches = db.query(func.count(Retailer.id)).filter(Retailer.mobile_number == user.phone_number).scalar()
    if matches == 0:
        return None
    if matches > 1:
        logger.warning("ambiguous_mobile mobile=%s matches=%d", _mask(user.phone_number), matches)
        return None
    retailer = db.query(Retailer).filter(Retailer.mobile_number == user.phone_number).first()
    user.retailer_id = retailer.id
    db.commit()
    return retailer


def onboarding_state(user: User) -> str:
    if user.retailer_id is None:
        return "new"
    if user.shop_confirmed_at is None:
        return "confirm"
    return "ready"


def shop_prefill(retailer: Retailer) -> dict:
    return {
        "shop_name": retailer.name,
        "address_line1": retailer.address_line1,
        "address_line2": retailer.address_line2,
        "city": retailer.city,
        "state": retailer.state,
        "pincode": retailer.pincode,
        "gst_number": retailer.gst_number,
        "latitude": retailer.latitude,
        "longitude": retailer.longitude,
    }
