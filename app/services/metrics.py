"""Every business figure the app shows is calculated here, and only here: sales, money collected,
orders, what shops owe, and stock. Screens and other services ask this module; none of them adds
orders or payments up on its own, so no two screens can disagree.

The rules, written once:
- A business order goes from a warehouse to a retailer and is not cancelled (`LIVE_ORDER`). A
  cancelled order counts for nothing: not as an order, a sale, money collected or money owed.
  Whatever was recorded against cancelled orders is reported by `cancelled_money`, so it can be
  put right rather than silently dropped.
- Per-order arithmetic (line totals, GST, what one order still owes) lives in
  app/services/finance.py. This module only adds orders up.
- A day is a day in India. `parse_date` turns an ISO date into the instant of that India
  midnight and `ist_date` turns a stored instant into its India date, so a SQL range filter and a
  Python bucket put every record on the same day.
- Balances (outstanding, stock on hand) are as of now. Flows (sales, collected, orders, stock in
  and out) belong to a period; no period means all time.
"""

from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import and_, func
from sqlalchemy.orm import Session, joinedload

from app.models import SKU, Account, Employee, Inventory, Invoice, Order, OrderItem, Retailer
from app.models.enums import OrderStatus, PaymentStatus
from app.services.finance import calculate_credit_note_totals, calculate_order_outstanding, calculate_order_totals

IST = timedelta(hours=5, minutes=30)
_IST_TZ = timezone(IST)

LIVE_ORDER = and_(
    Order.from_entity_type == "WAREHOUSE",
    Order.to_entity_type == "RETAILER",
    Order.status != OrderStatus.CANCELLED,
)

# An order still owes money in any of these payment states.
PAYMENT_DUE_STATUSES = (PaymentStatus.CREDIT, PaymentStatus.PARTIAL, PaymentStatus.UNPAID)

# When a payment counts as collected: the day it was collected, else the day it was recorded.
PAYMENT_DATE = func.coalesce(Account.collection_date, Account.created_at)


# ---- Days and periods -------------------------------------------------------------------------

def parse_date(value: str | None, is_end: bool) -> datetime | None:
    """The India midnight that starts `value` (or, for an end date, the one after it), as a UTC
    instant. None for a blank or malformed date, which means "no limit"."""
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    if is_end:
        parsed += timedelta(days=1)
    return datetime.combine(parsed, time.min, tzinfo=_IST_TZ).astimezone(timezone.utc)


def ist_date(value) -> date | None:
    """The India date of a stored instant. Naive values are UTC (SQLite stores them that way)."""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.replace(tzinfo=None) - value.utcoffset()
    return (value + IST).date()


def today_ist() -> date:
    return ist_date(datetime.now(timezone.utc))


def in_period(query, column, from_date: str | None = None, to_date: str | None = None):
    from_dt = parse_date(from_date, False)
    to_dt = parse_date(to_date, True)
    if from_dt:
        query = query.filter(column >= from_dt)
    if to_dt:
        query = query.filter(column < to_dt)
    return query


def _within(day: date | None, start: date | None, end: date | None) -> bool:
    return day is not None and (start is None or day >= start) and (end is None or day <= end)


def _iso_day(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


# ---- Orders -----------------------------------------------------------------------------------

def live_orders(db: Session):
    return db.query(Order).filter(LIVE_ORDER)


def order_total_subquery(db: Session):
    """Each order's GST-inclusive line total, for lists that sort and sum in SQL. Must equal
    finance.calculate_order_totals()["grand_total"] once the delivery charge is added
    (`order_total_expr`); app/tests/test_metrics_consistency.py holds the two together."""
    line = (OrderItem.quantity * OrderItem.unit_price) - OrderItem.discount_amount
    return (
        db.query(OrderItem.order_id.label("order_id"), func.sum(line).label("lines_total"))
        .group_by(OrderItem.order_id)
        .subquery()
    )


def order_total_expr(subquery):
    # The delivery charge is GST-inclusive like the item lines, so it adds to the total as is.
    return func.coalesce(subquery.c.lines_total, 0) + func.coalesce(Order.delivery_charge, 0)


def orders_value(db: Session, query) -> float:
    """What the orders in `query` are worth."""
    sub = order_total_subquery(db)
    value = query.outerjoin(sub, sub.c.order_id == Order.id).with_entities(func.coalesce(func.sum(order_total_expr(sub)), 0)).scalar()
    return round(float(value or 0), 2)


def orders_by_payment_status(db: Session, from_date: str | None = None, to_date: str | None = None) -> dict:
    query = in_period(
        db.query(Order.payment_status, func.count(Order.id)).filter(LIVE_ORDER), Order.created_at, from_date, to_date
    )
    return {getattr(status, "value", status): count for status, count in query.group_by(Order.payment_status).all()}


# ---- Money in: sales, collected, orders placed --------------------------------------------------

def _dated(db: Session, kind: str, warehouse_id: int | None = None) -> list[tuple[date, float]]:
    """(India date, value) of every sale, payment or order placed on a live order. The tables are
    small, so periods and buckets are worked out from these rows in Python, one way for all."""
    if kind == "sales":
        query = db.query(Invoice.invoice_date, Invoice.grand_total).join(Order, Order.id == Invoice.order_id)
    elif kind == "collected":
        query = db.query(PAYMENT_DATE, Account.amount).join(Order, Order.id == Account.order_id)
    else:
        query = db.query(Order.created_at, Order.id)
    query = query.filter(LIVE_ORDER)
    if warehouse_id:
        query = query.filter(Order.from_entity_id == warehouse_id)
    if kind == "orders":
        return [(ist_date(when), 1.0) for when, _ in query]
    return [(ist_date(when), float(amount or 0)) for when, amount in query]


def _sum(rows, start: date | None, end: date | None) -> float:
    return round(sum(value for day, value in rows if _within(day, start, end)), 2)


def sales(db: Session, from_date: str | None = None, to_date: str | None = None, warehouse_id: int | None = None) -> float:
    """Invoiced sales, by invoice date. Invoices are issued at dispatch."""
    return _sum(_dated(db, "sales", warehouse_id), _iso_day(from_date), _iso_day(to_date))


def collected(db: Session, from_date: str | None = None, to_date: str | None = None, warehouse_id: int | None = None) -> float:
    """Money collected from shops, by collection date."""
    return _sum(_dated(db, "collected", warehouse_id), _iso_day(from_date), _iso_day(to_date))


def live_payments(db: Session):
    """Payments recorded against live orders: the payment log and its total use this."""
    return db.query(Account).join(Order, Order.id == Account.order_id).filter(LIVE_ORDER)


def money_series(db: Session, from_date: str | None = None, to_date: str | None = None, today: date | None = None):
    """Sales, collected and orders placed for a period, as totals and as weekly buckets (monthly
    when the period runs past 120 days). All time starts at the first record. A bounded period
    is also compared with the same number of days just before it."""
    today = today or today_ist()
    series = {kind: _dated(db, kind) for kind in ("sales", "collected", "orders")}
    end = _iso_day(to_date) or today
    start = _iso_day(from_date)
    if start is None:
        first = [day for rows in series.values() for day, _ in rows if day]
        start = min(first) if first else end
    start = min(start, end)
    span = (end - start).days + 1
    bucket = "week" if span <= 120 else "month"

    def window(a, b):
        return {kind: _sum(rows, a, b) for kind, rows in series.items()}

    buckets = []
    cursor = start - timedelta(days=start.weekday()) if bucket == "week" else start.replace(day=1)
    while cursor <= end:
        if bucket == "week":
            following = cursor + timedelta(days=7)
        else:
            following = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        buckets.append({"start": cursor.isoformat(), **window(max(cursor, start), min(following - timedelta(days=1), end))})
        cursor = following

    previous = None
    if from_date:
        previous_end = start - timedelta(days=1)
        previous = window(previous_end - timedelta(days=span - 1), previous_end)

    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "bucket": bucket,
        "buckets": buckets,
        "totals": window(start, end),
        "previous": previous,
    }


# ---- Money owed -------------------------------------------------------------------------------

def outstanding_orders_query(
    db: Session,
    warehouse_id: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    salesman_id: int | None = None,
    retailer_id: int | None = None,
):
    """Delivered orders not fully paid. Only a delivered order can owe money."""
    query = (
        live_orders(db)
        .options(joinedload(Order.payments), joinedload(Order.credit_notes), joinedload(Order.items))
        .filter(Order.status == OrderStatus.DELIVERED)
        .filter(Order.payment_status.in_(PAYMENT_DUE_STATUSES))
    )
    if warehouse_id:
        query = query.filter(Order.from_entity_id == warehouse_id)
    query = in_period(query, Order.created_at, from_date, to_date)
    if salesman_id:
        query = query.filter(Order.salesman_id == salesman_id)
    if retailer_id:
        query = query.filter(Order.to_entity_id == retailer_id)
    return query


def naive_utc(value: datetime) -> datetime:
    """PostgreSQL returns `created_at` timezone-aware, SQLite naive (already UTC). Ages are
    measured against the naive `datetime.utcnow()`, so bring aware values to naive UTC first."""
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def credit_notes_total(order) -> float:
    return sum(
        calculate_credit_note_totals(cn)["grand_total"] for cn in order.credit_notes if getattr(cn, "applies_to_outstanding", True)
    )


def outstanding_summary(
    db: Session,
    warehouse_id: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
):
    """Total outstanding, with its age and who owes it. Every screen that shows what shops owe
    reads this."""
    orders = outstanding_orders_query(db, warehouse_id, from_date, to_date).all()

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
        credit_total = credit_notes_total(order)
        total_outstanding += outstanding
        total_recovered += payments_total
        total_credit_notes += credit_total

        days = (now - naive_utc(order.created_at)).days if order.created_at else 0
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


def cancelled_money(db: Session) -> dict:
    """Money recorded against cancelled orders. It counts in no total, so it is listed here to be
    refunded or moved to the right order."""
    cancelled = and_(Order.to_entity_type == "RETAILER", Order.status == OrderStatus.CANCELLED)
    payments_count, payments_total = (
        db.query(func.count(Account.id), func.coalesce(func.sum(Account.amount), 0))
        .join(Order, Order.id == Account.order_id)
        .filter(cancelled)
        .one()
    )
    orders_with_payments = (
        db.query(func.count(func.distinct(Account.order_id))).join(Order, Order.id == Account.order_id).filter(cancelled).scalar() or 0
    )
    invoices = db.query(func.count(Invoice.id)).join(Order, Order.id == Invoice.order_id).filter(cancelled).scalar() or 0
    return {
        "orders": orders_with_payments,
        "payments": payments_count or 0,
        "payments_total": round(float(payments_total or 0), 2),
        "invoices": invoices,
    }


# ---- Stock ------------------------------------------------------------------------------------

def stock_value(db: Session, lowest: int = 5):
    """What the stock on hand cost us (units x distributor landing price), and the SKUs with the
    fewest units left across all warehouses."""
    rows = (
        db.query(SKU.id, SKU.name, SKU.code, SKU.distributor_landing_price, func.coalesce(func.sum(Inventory.total_quantity), 0))
        .outerjoin(Inventory, Inventory.sku_id == SKU.id)
        .group_by(SKU.id, SKU.name, SKU.code, SKU.distributor_landing_price)
        .all()
    )
    priced = [r for r in rows if r[3] is not None]
    value = round(sum(float(r[3]) * float(r[4] or 0) for r in priced), 2) if priced else None
    lowest_rows = sorted(rows, key=lambda r: (float(r[4] or 0), r[1] or ""))[:lowest]
    return {
        "inventory_value": value,
        "unpriced_skus": len(rows) - len(priced),
        "lowest": [
            {"sku_id": r[0], "name": r[1], "code": r[2], "units": float(r[4] or 0)}
            for r in lowest_rows
        ],
    }
