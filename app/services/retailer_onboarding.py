"""Sign-in / sign-up bookkeeping for retailers who log in with a phone OTP (08-03, D-18, D-20).

The canonical identity is the integer of the 10 digits after +91, compared by exact equality
with users.phone_number and retailers.mobile_number. Legacy 12-digit (91XXXXXXXXXX) retailer
rows therefore never match; the audit script reports how many exist.
"""

import logging
import re
import secrets
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import get_password_hash
from app.models import Retailer, RetailerAddress, User
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


class ShopAddressRequired(ValueError):
    """A shop submission needs a delivery location the first time (D-26)."""


def _apply_address(target, address) -> None:
    """Copy a submitted address onto a Retailer's registered-address columns."""
    target.address_line1 = address.line1
    target.address_line2 = address.line2
    target.city = address.city
    target.state = address.state
    target.pincode = address.pincode
    target.latitude = address.latitude
    target.longitude = address.longitude


def _new_saved_address(retailer_id: int, address) -> RetailerAddress:
    return RetailerAddress(
        retailer_id=retailer_id,
        label=address.label,
        line1=address.line1,
        line2=address.line2,
        landmark=address.landmark,
        city=address.city,
        state=address.state,
        pincode=address.pincode,
        latitude=address.latitude,
        longitude=address.longitude,
    )


def complete_shop(db: Session, user: User, payload) -> User:
    """Finish onboarding (D-18, D-20, D-26, D-28): one transaction, safe to submit twice.

    The user row is locked and re-read first so two concurrent first submissions cannot both
    see state "new" and create two shops (a no-op on SQLite, which cannot exercise the race).
    """
    locked = (
        db.query(User).filter(User.id == user.id).populate_existing().with_for_update().one()
    )
    state = onboarding_state(locked)
    now = datetime.now(timezone.utc)

    if state == "new":
        if payload.address is None:
            raise ShopAddressRequired()
        address = payload.address
        retailer = Retailer(
            name=payload.shop_name,
            mobile_number=locked.phone_number,
            gst_number=payload.gst_number,
            signup_source="STORE",
        )
        _apply_address(retailer, address)
        db.add(retailer)
        db.flush()
        db.add(_new_saved_address(retailer.id, address))
        locked.retailer_id = retailer.id
        locked.shop_confirmed_at = now
    elif state == "confirm":
        if payload.address is None:
            raise ShopAddressRequired()
        address = payload.address
        retailer = db.get(Retailer, locked.retailer_id)
        retailer.name = payload.shop_name
        retailer.gst_number = payload.gst_number
        _apply_address(retailer, address)
        has_saved = (
            db.query(func.count(RetailerAddress.id))
            .filter(RetailerAddress.retailer_id == retailer.id)
            .scalar()
        )
        if not has_saved:
            db.add(_new_saved_address(retailer.id, address))
        locked.shop_confirmed_at = now
    else:
        retailer = db.get(Retailer, locked.retailer_id)
        retailer.name = payload.shop_name
        if payload.gst_number is not None:
            retailer.gst_number = payload.gst_number

    db.commit()
    return locked


def build_store_me(db: Session, user: User) -> dict:
    state = onboarding_state(user)
    retailer = db.get(Retailer, user.retailer_id) if user.retailer_id is not None else None
    shop = None
    prefill = None
    addresses = []
    if retailer is not None:
        if state == "confirm":
            prefill = shop_prefill(retailer)
        else:
            shop = {
                "retailer_id": retailer.id,
                "name": retailer.name,
                "gst_number": retailer.gst_number,
                "address_line1": retailer.address_line1,
                "address_line2": retailer.address_line2,
                "city": retailer.city,
                "state": retailer.state,
                "pincode": retailer.pincode,
            }
        addresses = list_addresses(db, retailer.id)
    return {
        "mobile": user.phone_number,
        "onboarding": state,
        "shop": shop,
        "prefill": prefill,
        "addresses": addresses,
    }


def list_addresses(db: Session, retailer_id: int) -> list[RetailerAddress]:
    return (
        db.query(RetailerAddress)
        .filter(RetailerAddress.retailer_id == retailer_id)
        .order_by(RetailerAddress.id.desc())
        .all()
    )


class AddressNotFound(Exception):
    """The address does not exist or belongs to another shop (indistinguishable on purpose)."""


class LastAddressError(Exception):
    """A shop must keep at least one delivery address."""


def _get_address(db: Session, retailer_id: int, address_id: int) -> RetailerAddress:
    address = (
        db.query(RetailerAddress)
        .filter(RetailerAddress.id == address_id, RetailerAddress.retailer_id == retailer_id)
        .first()
    )
    if address is None:
        raise AddressNotFound()
    return address


def create_address(db: Session, retailer_id: int, payload) -> RetailerAddress:
    address = _new_saved_address(retailer_id, payload)
    db.add(address)
    db.commit()
    return address


def update_address(db: Session, retailer_id: int, address_id: int, payload) -> RetailerAddress:
    address = _get_address(db, retailer_id, address_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(address, field, value)
    db.commit()
    return address


def delete_address(db: Session, retailer_id: int, address_id: int) -> None:
    address = _get_address(db, retailer_id, address_id)
    remaining = (
        db.query(func.count(RetailerAddress.id))
        .filter(RetailerAddress.retailer_id == retailer_id)
        .scalar()
    )
    if remaining <= 1:
        raise LastAddressError()
    db.delete(address)
    db.commit()
