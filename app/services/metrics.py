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
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import and_, func
from sqlalchemy.orm import Session, joinedload

from app.models import SKU, Account, CreditNote, Employee, Inventory, InventoryTransaction, Invoice, Order, OrderItem, Retailer, SKUBatch, Warehouse
from app.models.enums import OrderStatus
from app.services.finance import calculate_credit_note_totals, calculate_order_outstanding, calculate_order_totals

IST = timedelta(hours=5, minutes=30)
_IST_TZ = timezone(IST)

LIVE_ORDER = and_(
    Order.from_entity_type == "WAREHOUSE",
    Order.to_entity_type == "RETAILER",
    Order.status != OrderStatus.CANCELLED,
)

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

def payment_due_query(query):
    """A positive balance is authoritative, even if a legacy payment-status label is stale."""
    orders = query.options(
        joinedload(Order.payments),
        joinedload(Order.credit_notes).joinedload(CreditNote.items),
        joinedload(Order.items).joinedload(OrderItem.taxes),
    ).all()
    ids = [order.id for order in orders if calculate_order_outstanding(order) > 0]
    return query.filter(Order.id.in_(ids))


def _balance_orders_query(
    db: Session,
    warehouse_id: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    salesman_id: int | None = None,
    retailer_id: int | None = None,
):
    """Live orders and their recorded payments, scoped consistently for balances."""
    query = (
        live_orders(db)
        .options(joinedload(Order.payments), joinedload(Order.credit_notes).joinedload(CreditNote.items), joinedload(Order.items).joinedload(OrderItem.taxes))
    )
    if warehouse_id:
        query = query.filter(Order.from_entity_id == warehouse_id)
    query = in_period(query, Order.created_at, from_date, to_date)
    if salesman_id:
        query = query.filter(Order.salesman_id == salesman_id)
    if retailer_id:
        query = query.filter(Order.to_entity_id == retailer_id)
    return query


def outstanding_orders_query(db: Session, warehouse_id=None, from_date=None, to_date=None, salesman_id=None, retailer_id=None):
    """Every live order with a positive balance, whatever its fulfilment status."""
    return payment_due_query(_balance_orders_query(db, warehouse_id, from_date, to_date, salesman_id, retailer_id))


def naive_utc(value: datetime) -> datetime:
    """PostgreSQL returns `created_at` timezone-aware, SQLite naive (already UTC). Ages are
    measured against the naive `datetime.utcnow()`, so bring aware values to naive UTC first."""
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def names_by_id(db: Session, model, ids) -> dict:
    """{id: name} for every id in one query, for loops that label rows with a retailer or employee
    name. Querying per row made these endpoints cost one round trip per order."""
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return dict(db.query(model.id, model.name).filter(model.id.in_(ids)).all())


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
    orders = _balance_orders_query(db, warehouse_id, from_date, to_date).all()
    salesman_names = names_by_id(db, Employee, (o.salesman_id for o in orders))
    retailer_names = names_by_id(db, Retailer, (o.to_entity_id for o in orders))

    reconciliation = {"order_value": 0, "collected": 0, "credits": 0, "excess_payments": 0}
    stages = {
        "dispatched": {"key": "dispatched", "label": "Delivered / out for delivery", "amount": 0, "orders": 0},
        "pending": {"key": "pending", "label": "Pending orders", "amount": 0, "orders": 0},
        "preparing": {"key": "preparing", "label": "Accepted / awaiting rider", "amount": 0, "orders": 0},
        "returned": {"key": "returned", "label": "Returned orders", "amount": 0, "orders": 0},
    }
    total_outstanding = 0
    total_recovered = 0
    total_credit_notes = 0
    aging = {"0_7": 0, "7_15": 0, "15_30": 0, "30_plus": 0}
    by_salesman = {}
    by_retailer = {}
    now = datetime.utcnow()

    for order in orders:
        totals = calculate_order_totals(order)
        payments_total = sum(p.amount for p in order.payments)
        credit_total = credit_notes_total(order)
        reconciliation["order_value"] += totals["grand_total"]
        reconciliation["collected"] += payments_total
        reconciliation["credits"] += credit_total
        reconciliation["excess_payments"] += max(payments_total + credit_total - totals["grand_total"], 0)
        outstanding = calculate_order_outstanding(order)
        if outstanding <= 0:
            continue
        if order.status in (OrderStatus.DELIVERED, OrderStatus.OUT_FOR_DELIVERY):
            stage = "dispatched"
        elif order.status == OrderStatus.PENDING:
            stage = "pending"
        elif order.status == OrderStatus.RETURNED:
            stage = "returned"
        else:
            stage = "preparing"
        stages[stage]["amount"] += outstanding
        stages[stage]["orders"] += 1
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
            by_salesman[sid] = {
                "salesman_id": sid,
                "salesman_name": salesman_names.get(sid, "Unassigned"),
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
            by_retailer[rid] = {
                "retailer_id": rid,
                "retailer_name": retailer_names.get(rid, "Unknown"),
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
        "breakdown": [{**row, "amount": round(row["amount"], 2)} for key, row in stages.items() if key in ("dispatched", "pending") or row["amount"] > 0],
        "reconciliation": {key: round(value, 2) for key, value in reconciliation.items()},
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

def _stock_amount(unit_cost, units):
    if unit_cost is None:
        return None
    return float((Decimal(str(unit_cost)) * Decimal(str(units or 0))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


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
    value = round(sum(_stock_amount(r[3], r[4]) for r in priced), 2) if priced else None
    lowest_rows = sorted(rows, key=lambda r: (float(r[4] or 0), r[1] or ""))[:lowest]
    return {
        "inventory_value": value,
        "unpriced_skus": len(rows) - len(priced),
        "lowest": [
            {"sku_id": r[0], "name": r[1], "code": r[2], "units": float(r[4] or 0)}
            for r in lowest_rows
        ],
    }


def retailer_activity(db: Session, from_date=None, to_date=None, warehouse_id=None, beat_id=None, salesman_id=None):
    """Per-shop order figures, including GST, for the Coverage screen."""
    query = live_orders(db).options(joinedload(Order.items)).join(Retailer, Retailer.id == Order.to_entity_id)
    query = in_period(query, Order.created_at, from_date, to_date)
    if warehouse_id:
        query = query.filter(Order.from_entity_id == warehouse_id)
    if beat_id:
        query = query.filter(Retailer.beat_id == beat_id)
    if salesman_id:
        query = query.filter(Retailer.assigned_salesman_id == salesman_id)
    rows = {}
    months = {}
    for order in query.all():
        row = rows.setdefault(order.to_entity_id, {"id": order.to_entity_id, "orders": 0, "total": 0, "last_billed": None})
        row["orders"] += 1
        row["total"] += calculate_order_totals(order)["grand_total"]
        when = naive_utc(order.created_at) if order.created_at else None
        if when and (row["last_billed"] is None or when > row["last_billed"]):
            row["last_billed"] = when
        if when:
            month = ist_date(when).strftime("%Y-%m")
            months.setdefault(month, set()).add(order.to_entity_id)
    names = dict(db.query(Retailer.id, Retailer.name).filter(Retailer.id.in_(rows))) if rows else {}
    for row in rows.values():
        row["name"] = names.get(row["id"], "Unknown shop")
        row["total"] = round(float(row["total"]), 2)
    return {
        "retailers": sorted(rows.values(), key=lambda row: -row["total"]),
        "monthly": [{"label": month, "value": len(ids)} for month, ids in sorted(months.items())],
    }


def payment_days(db: Session, from_date=None, to_date=None):
    by_day = {}
    query = in_period(live_payments(db), PAYMENT_DATE, from_date, to_date)
    for when, amount in query.with_entities(PAYMENT_DATE, Account.amount):
        day = ist_date(when)
        if day:
            by_day[day] = by_day.get(day, 0) + float(amount or 0)
    return [{"date": day.isoformat(), "amount": round(amount, 2)} for day, amount in sorted(by_day.items())]


def stock_overview(db: Session, warehouse_id=None, from_date=None, to_date=None):
    """Current allocator stock and batches; receipts and dispatches follow the period."""
    # Local imports keep the existing inventory and ledger services' delegation acyclic.
    from app.services.admin import get_inventory
    from app.services.stock_ledger import _summary

    today = today_ist()
    inventory, _ = get_inventory(db, limit=db.query(Inventory).count(), warehouse_id=warehouse_id)
    movements = db.query(InventoryTransaction)
    batches = db.query(SKUBatch).filter(SKUBatch.remaining_quantity > 0)
    if warehouse_id:
        movements = movements.filter(InventoryTransaction.warehouse_id == warehouse_id)
        batches = batches.filter(SKUBatch.warehouse_id == warehouse_id)
    first, last = movements.with_entities(func.min(InventoryTransaction.created_at), func.max(InventoryTransaction.created_at)).one()
    start = _iso_day(from_date) or (ist_date(first) if first else today)
    end = _iso_day(to_date) or today
    period_days = max(1, (end - start).days + 1)
    flow = _summary(db, parse_date(from_date, False), parse_date(to_date, True), warehouse_id, None, None)
    warehouse_names = dict(db.query(Warehouse.id, Warehouse.name))
    rows = {sku.id: {
        "sku_id": sku.id, "name": sku.name, "code": sku.code, "image_url": sku.image_url,
        "on_hand": 0, "reserved": 0, "available": 0, "expired": 0,
        "received": 0, "dispatched": 0, "returned": 0,
        "unit_cost": float(sku.distributor_landing_price) if sku.distributor_landing_price is not None else None,
        "earliest_expiry": None, "batches": [], "warehouses": [],
    } for sku in db.query(SKU)}
    for item in inventory:
        row = rows[item["sku_id"]]
        row["on_hand"] += item["total_quantity"]
        row["reserved"] += item["reserved_quantity"]
        row["available"] += item["available_quantity"]
        row["expired"] += item["expired_quantity"]
        row["warehouses"].append({**item, "name": warehouse_names.get(item["warehouse_id"])})
    for item in flow.values():
        for key in ("received", "dispatched", "returned"):
            rows[item["sku_id"]][key] += item[key]
    for batch in batches.order_by(SKUBatch.expiry_date.asc().nullslast(), SKUBatch.id):
        row = rows[batch.sku_id]
        row["batches"].append({"id": batch.id, "warehouse_name": warehouse_names.get(batch.warehouse_id),
            "expiry_date": batch.expiry_date, "remaining": batch.remaining_quantity, "reserved": batch.reserved_quantity})
        if batch.expiry_date and (row["earliest_expiry"] is None or batch.expiry_date < row["earliest_expiry"]):
            row["earliest_expiry"] = batch.expiry_date
    for row in rows.values():
        row["value"] = _stock_amount(row["unit_cost"], row["on_hand"])
        row["days_left"] = round(row["available"] / (row["dispatched"] / period_days), 1) if row["dispatched"] else None
        row["running_low"] = row["available"] > 0 and row["days_left"] is not None and row["days_left"] < 14
        row["out_of_stock"] = row["available"] <= 0
        row["expiring"] = any(b["expiry_date"] and today <= b["expiry_date"] <= today + timedelta(days=60) for b in row["batches"])
    values = list(rows.values())
    return {
        "first_movement_at": first, "last_movement_at": last, "period_days": period_days,
        "totals": {**{key: sum(row[key] for row in values) for key in ("on_hand", "reserved", "available", "expired", "received", "dispatched", "returned")},
            "value": round(sum(row["value"] or 0 for row in values), 2) if any(row["value"] is not None for row in values) else None,
            "unpriced_skus": sum(row["unit_cost"] is None for row in values)},
        "counts": {"all": len(values), **{key: sum(row[key] for row in values) for key in ("running_low", "out_of_stock", "expiring")}},
        "skus": sorted(values, key=lambda row: (not row["out_of_stock"], row["days_left"] if row["days_left"] is not None else float("inf"), row["name"])),
    }
