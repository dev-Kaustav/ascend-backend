"""Single definition of "expired" for inventory, shared by allocation
(app/services/order.py) and the inventory read path (app/services/admin.py).

D1: a batch is expired when it has an expiry_date and that date is strictly
before the current business date. A batch expiring today is still good today
and remains allocatable. A batch with no expiry_date is not expired and
remains allocatable (existing FEFO ordering already sorts it last).

expired_batch_criterion and allocatable_batch_criterion are exact complements
over every SKUBatch row, including expiry_date IS NULL. A gap between them
would produce stock that is neither allocatable nor reported as expired —
invisible stock, which is precisely what D1 exists to prevent.

This is a leaf module: it imports from app.models and sqlalchemy only, never
from another service.
"""
from datetime import date

from sqlalchemy import and_, or_

from app.models import SKUBatch


def current_business_date() -> date:
    # Named seam so tests can substitute a fixed date. Every caller MUST reach
    # this as an attribute of this module (e.g. `inventory_service.current_business_date()`)
    # rather than binding it via `from app.services.inventory import current_business_date`
    # at import time — a from-import binds the original function object, so
    # monkeypatching this attribute later silently does nothing and the test
    # becomes a wall-clock test.
    return date.today()


def expired_batch_criterion(as_of: date):
    """A batch is expired when it has an expiry_date strictly before as_of."""
    return and_(SKUBatch.expiry_date.isnot(None), SKUBatch.expiry_date < as_of)


def allocatable_batch_criterion(as_of: date):
    """Exact complement of expired_batch_criterion over every row, including
    expiry_date IS NULL (not expired) and expiry_date == as_of (still good today)."""
    return or_(SKUBatch.expiry_date.is_(None), SKUBatch.expiry_date >= as_of)
