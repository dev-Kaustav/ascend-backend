"""Stock is reserved when an admin marks an order Ready to Ship, not when it is booked.

Allocation tests (FEFO, expiry, exact quantities) only care about the reservation, so they book
the order and reserve in one step here instead of walking each order through a driver and a
status change.
"""

from app.services.order import _reserve_inventory_for_order, create_outgoing_order


def create_and_reserve(db, payload, user, **kwargs):
    order = create_outgoing_order(db, payload, user, **kwargs)
    _reserve_inventory_for_order(db, order)
    db.commit()
    return order
