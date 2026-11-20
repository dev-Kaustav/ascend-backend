"""The shops screens: a page of the shop list, and one shop with its figures (metrics.retailer_report).

Scoped like the order form: the office sees any shop; a salesman only the shops assigned to them.
"""
from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import Session

from app.core.deps import get_role_value
from app.models import Beat, Employee, Order, Retailer
from app.services import metrics


class RetailerNotFoundError(ValueError):
    pass


class RetailerScopeError(Exception):
    pass


def _salesman_scope(current_user) -> int | None:
    """The salesman whose shops this user may see, or None for the office (every shop)."""
    role = get_role_value(current_user)
    if role in {"ADMIN", "ACCOUNTANT"}:
        return None
    if role == "SALESMAN" and getattr(current_user, "employee_id", None):
        return current_user.employee_id
    raise RetailerScopeError("Shops are not available for this role")


def list_retailers_page(
    db: Session,
    current_user,
    search: str | None = None,
    salesman_id: int | None = None,
    beat_id: str | None = None,
    limit: int = 30,
    offset: int = 0,
) -> dict:
    """One page of shops with their salesman, beat, last order and what they owe; the shop that
    ordered last comes first, and shops that never ordered come last, by name. `beat_id` "none" is
    the shops on no beat. Every word of `search` must match the name, address, city, phone or GST."""
    last_orders = (
        metrics.live_orders(db)
        .with_entities(Order.to_entity_id.label("retailer_id"), func.max(Order.created_at).label("last_order_at"))
        .group_by(Order.to_entity_id)
        .subquery()
    )
    query = db.query(Retailer, last_orders.c.last_order_at).outerjoin(last_orders, last_orders.c.retailer_id == Retailer.id)
    own = _salesman_scope(current_user)
    if own is not None:
        query = query.filter(Retailer.assigned_salesman_id == own)
    if salesman_id:
        query = query.filter(Retailer.assigned_salesman_id == salesman_id)
    if beat_id == "none":
        query = query.filter(Retailer.beat_id.is_(None))
    elif beat_id:
        query = query.filter(Retailer.beat_id == int(beat_id))
    fields = (Retailer.name, Retailer.address_line1, Retailer.city, cast(Retailer.mobile_number, String), Retailer.gst_number)
    for word in (search or "").split():
        query = query.filter(or_(*(field.ilike(f"%{word}%") for field in fields)))

    total = query.count()
    rows = query.order_by(last_orders.c.last_order_at.desc().nulls_last(), Retailer.name.asc(), Retailer.id.asc()).limit(limit).offset(offset).all()
    retailers = [retailer for retailer, _ in rows]
    owed = metrics.outstanding_by_retailer(db, [r.id for r in retailers])
    salesmen = metrics.names_by_id(db, Employee, (r.assigned_salesman_id for r in retailers))
    beats = metrics.names_by_id(db, Beat, (r.beat_id for r in retailers))
    items = [
        {
            **_fields(r),
            "salesman_name": salesmen.get(r.assigned_salesman_id),
            "beat_name": beats.get(r.beat_id),
            "last_order_at": last_order_at,
            "outstanding": owed.get(r.id, 0.0),
        }
        for r, last_order_at in rows
    ]
    return {"items": items, "total": total}


def _fields(retailer) -> dict:
    return {
        "id": retailer.id,
        "name": retailer.name,
        "mobile_number": retailer.mobile_number,
        "address_line1": retailer.address_line1,
        "address_line2": retailer.address_line2,
        "city": retailer.city,
        "state": retailer.state,
        "pincode": retailer.pincode,
        "gst_number": retailer.gst_number,
        "latitude": retailer.latitude,
        "longitude": retailer.longitude,
        "assigned_salesman_id": retailer.assigned_salesman_id,
        "beat_id": retailer.beat_id,
    }


def get_retailer_overview(db: Session, retailer_id: int, current_user) -> dict:
    retailer = db.get(Retailer, retailer_id)
    if retailer is None:
        raise RetailerNotFoundError("Retailer not found")
    own = _salesman_scope(current_user)
    if own is not None and retailer.assigned_salesman_id != own:
        raise RetailerScopeError("This shop is not assigned to you")

    salesman = db.get(Employee, retailer.assigned_salesman_id) if retailer.assigned_salesman_id else None
    beat = db.get(Beat, retailer.beat_id) if retailer.beat_id else None
    return {
        **_fields(retailer),
        "salesman_name": salesman.name if salesman else None,
        "beat_name": beat.name if beat else None,
        "report": metrics.retailer_report(db, retailer.id),
    }
