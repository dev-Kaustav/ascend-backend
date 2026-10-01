"""Viewer selection for the public storefront catalogue (08-04, D-10 / D-24)."""
from fastapi import Depends
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
