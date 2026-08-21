from datetime import datetime, date, timedelta
from sqlalchemy import func, case, and_
from sqlalchemy.orm import Session, joinedload

from app.models import Order, Account, Retailer, Employee, CreditNote
from app.models.enums import OrderStatus, PaymentStatus
from app.services.finance import calculate_order_outstanding, calculate_order_totals, calculate_credit_note_totals


def _naive_utc(value: datetime) -> datetime:
    """`Order.created_at` is `DateTime(timezone=True)`: PostgreSQL returns it timezone-aware,
    SQLite returns it naive, and both represent the same UTC instant (rows are always written
    through `server_default=func.now()` or an explicit UTC value). `datetime.utcnow()` is
    always naive, so subtracting an aware `created_at` from it raises
    `TypeError: can't subtract offset-naive and offset-aware datetimes` the moment this runs
    against real PostgreSQL (#todo RPT-06 — fixed here per Phase 5 coordinator ruling R5,
    05-CONTEXT.md). Stripping tzinfo, rather than converting, leaves SQLite's naive values
    untouched and makes PostgreSQL's aware ones comparable, with no change to what "now" means.
    """
    return value.replace(tzinfo=None) if value.tzinfo else value


def _parse_date(value: str | None, is_end: bool) -> datetime | None:
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    if is_end:
        return datetime.combine(parsed + timedelta(days=1), datetime.min.time())
    return datetime.combine(parsed, datetime.min.time())


def _base_outstanding_query(db: Session, warehouse_id: int | None, from_date: str | None, to_date: str | None, salesman_id: int | None, retailer_id: int | None):
    query = (
        db.query(Order)
        .options(joinedload(Order.payments), joinedload(Order.credit_notes), joinedload(Order.items))
        .filter(Order.status == OrderStatus.DELIVERED)
        .filter(Order.payment_status.in_([PaymentStatus.CREDIT, PaymentStatus.PARTIAL]))
    )
    if warehouse_id:
        query = query.filter(Order.from_entity_id == warehouse_id)
    from_dt = _parse_date(from_date, False)
    to_dt = _parse_date(to_date, True)
    if from_dt:
        query = query.filter(Order.created_at >= from_dt)
    if to_dt:
        query = query.filter(Order.created_at < to_dt)
    if salesman_id:
        query = query.filter(Order.salesman_id == salesman_id)
    if retailer_id:
        query = query.filter(Order.to_entity_id == retailer_id)
    return query


def get_outstanding_orders(
    db: Session,
    warehouse_id: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    salesman_id: int | None = None,
    retailer_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
):
    query = _base_outstanding_query(db, warehouse_id, from_date, to_date, salesman_id, retailer_id)
    total = query.count()
    orders = query.order_by(Order.created_at.desc()).limit(limit).offset(offset).all()

    items = []
    for order in orders:
        outstanding = calculate_order_outstanding(order)
        if outstanding <= 0:
            continue
        totals = calculate_order_totals(order)
        payments_total = sum(p.amount for p in order.payments)
        credit_total = sum(
            calculate_credit_note_totals(cn)["grand_total"]
            for cn in order.credit_notes
            if getattr(cn, "applies_to_outstanding", True)
        )
        retailer = db.query(Retailer).filter(Retailer.id == order.to_entity_id).first()
        salesman = db.query(Employee).filter(Employee.id == order.salesman_id).first() if order.salesman_id else None

        payment_modes = {}
        for p in order.payments:
            mode = p.payment_mode.value if hasattr(p.payment_mode, "value") and p.payment_mode else "OTHER"
            payment_modes[mode] = payment_modes.get(mode, 0) + p.amount

        items.append({
            "order_id": order.id,
            "invoice_number": order.invoice_number,
            "retailer_name": retailer.name if retailer else None,
            "retailer_id": order.to_entity_id,
            "salesman_name": salesman.name if salesman else None,
            "salesman_id": order.salesman_id,
            "order_date": order.created_at,
            "grand_total": totals["grand_total"],
            "payments_total": round(payments_total, 2),
            "payment_modes": payment_modes,
            "credit_note_total": round(credit_total, 2),
            "outstanding": outstanding,
            "days_old": (datetime.utcnow() - _naive_utc(order.created_at)).days if order.created_at else 0,
        })
    return items, total


def get_outstanding_summary(
    db: Session,
    warehouse_id: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
):
    query = _base_outstanding_query(db, warehouse_id, from_date, to_date, None, None)
    orders = query.all()

    total_outstanding = 0
    total_recovered = 0
    total_credit_notes = 0
    aging = {"0_7": 0, "7_15": 0, "15_30": 0, "30_plus": 0}
    by_salesman = {}
    by_retailer = {}
    now = datetime.utcnow()

    for order in orders:
        outstanding = calculate_order_outstanding(order)
        if outstanding <= 0:
            continue
        totals = calculate_order_totals(order)
        payments_total = sum(p.amount for p in order.payments)
        credit_total = sum(
            calculate_credit_note_totals(cn)["grand_total"]
            for cn in order.credit_notes
            if getattr(cn, "applies_to_outstanding", True)
        )
        total_outstanding += outstanding
        total_recovered += payments_total
        total_credit_notes += credit_total

        days = (now - _naive_utc(order.created_at)).days if order.created_at else 0
        if days <= 7:
            aging["0_7"] += outstanding
        elif days <= 15:
            aging["7_15"] += outstanding
        elif days <= 30:
            aging["15_30"] += outstanding
        else:
            aging["30_plus"] += outstanding

        sid = order.salesman_id or 0
        if sid not in by_salesman:
            salesman = db.query(Employee).filter(Employee.id == sid).first() if sid else None
            by_salesman[sid] = {
                "salesman_id": sid,
                "salesman_name": salesman.name if salesman else "Unassigned",
                "total_credit": 0,
                "recovered": 0,
                "pending": 0,
                "bills": 0,
            }
        by_salesman[sid]["total_credit"] += totals["grand_total"]
        by_salesman[sid]["recovered"] += payments_total
        by_salesman[sid]["pending"] += outstanding
        by_salesman[sid]["bills"] += 1

        rid = order.to_entity_id
        if rid not in by_retailer:
            retailer = db.query(Retailer).filter(Retailer.id == rid).first()
            by_retailer[rid] = {
                "retailer_id": rid,
                "retailer_name": retailer.name if retailer else "Unknown",
                "total_credit": 0,
                "recovered": 0,
                "pending": 0,
                "bills": 0,
            }
        by_retailer[rid]["total_credit"] += totals["grand_total"]
        by_retailer[rid]["recovered"] += payments_total
        by_retailer[rid]["pending"] += outstanding
        by_retailer[rid]["bills"] += 1

    recovery_rate = round(total_recovered / (total_outstanding + total_recovered) * 100, 1) if (total_outstanding + total_recovered) > 0 else 0

    return {
        "total_outstanding": round(total_outstanding, 2),
        "total_recovered": round(total_recovered, 2),
        "total_credit_notes": round(total_credit_notes, 2),
        "recovery_rate": recovery_rate,
        "aging": {k: round(v, 2) for k, v in aging.items()},
        "by_salesman": sorted(by_salesman.values(), key=lambda x: x["pending"], reverse=True),
        "by_retailer": sorted(by_retailer.values(), key=lambda x: x["pending"], reverse=True),
    }
