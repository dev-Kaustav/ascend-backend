"""Viewer selection for the public storefront catalogue (08-04, D-10 / D-24)."""
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.deps import get_current_active_user, get_current_user, get_role_value, security
from app.db.session import get_db


def get_optional_retailer(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
):
    """Return the signed-in retailer, or None for the anonymous view.

    - No Authorization header: None (anonymous).
    - A header that does not authenticate (malformed, expired, wrong token type, stale session,
      deleted or inactive user): the 401 propagates so the client refreshes its token.
    - A valid user who is not a RETAILER with a shop (staff, or a retailer user with no
      retailer_id yet): None, i.e. exactly the anonymous shape.

    Deliberately not require_roles("RETAILER"): that lets ADMIN through.
    """
    if credentials is None:
        return None
    user = get_current_active_user(get_current_user(credentials, db))
    if get_role_value(user) != "RETAILER" or user.retailer_id is None:
        return None
    return user


def get_store_user(user=Depends(get_current_active_user)):
    """A signed-in RETAILER, whether or not their shop is set up yet (08-08).

    Never require_roles("RETAILER"): that lets ADMIN through (research Pattern 1).
    """
    if get_role_value(user) != "RETAILER":
        raise HTTPException(status_code=403, detail="Retailer account required")
    return user


def get_store_retailer(user=Depends(get_store_user)):
    """A RETAILER whose shop is confirmed: the guard for everything that acts on a shop."""
    if user.retailer_id is None or user.shop_confirmed_at is None:
        raise HTTPException(status_code=409, detail="Finish setting up your shop first")
    return user
