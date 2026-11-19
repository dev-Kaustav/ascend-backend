"""The receivables report: non-cancelled orders not fully paid, one row each. Which orders count,
and every total, come from app/services/metrics.py; the summary is metrics.outstanding_summary."""
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Employee, Order, Retailer
from app.services import metrics
from app.services.finance import calculate_order_outstanding, calculate_order_totals


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
    query = metrics.outstanding_orders_query(db, warehouse_id, from_date, to_date, salesman_id, retailer_id)
    total = query.count()
    orders = query.order_by(Order.created_at.desc()).limit(limit).offset(offset).all()

    retailer_names = metrics.names_by_id(db, Retailer, (o.to_entity_id for o in orders))
    salesman_names = metrics.names_by_id(db, Employee, (o.salesman_id for o in orders))
    items = []
    for order in orders:
        outstanding = calculate_order_outstanding(order)
        if outstanding <= 0:
            continue
        totals = calculate_order_totals(order)
        payments_total = sum(p.amount for p in order.payments)
        credit_total = metrics.credit_notes_total(order)

        payment_modes = {}
        for p in order.payments:
            mode = p.payment_mode.value if hasattr(p.payment_mode, "value") and p.payment_mode else "OTHER"
            payment_modes[mode] = payment_modes.get(mode, 0) + p.amount

        items.append({
            "order_id": order.id,
            "invoice_number": order.invoice_number,
            "retailer_name": retailer_names.get(order.to_entity_id),
            "retailer_id": order.to_entity_id,
            "salesman_name": salesman_names.get(order.salesman_id),
            "salesman_id": order.salesman_id,
            "order_date": order.created_at,
            "grand_total": totals["grand_total"],
            "payments_total": round(payments_total, 2),
            "payment_modes": payment_modes,
            "credit_note_total": round(credit_total, 2),
            "outstanding": outstanding,
            "days_old": (datetime.utcnow() - metrics.naive_utc(order.created_at)).days if order.created_at else 0,
        })
    return items, total


def get_outstanding_summary(
    db: Session,
    warehouse_id: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
):
    return metrics.outstanding_summary(db, warehouse_id=warehouse_id, from_date=from_date, to_date=to_date)
