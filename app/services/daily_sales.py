from datetime import datetime, date, timedelta
from collections import defaultdict
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models import Order, Employee
from app.models.enums import OrderStatus
from app.services.finance import calculate_order_totals


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


def get_daily_sales_summary(
    db: Session,
    warehouse_id: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
):
    query = (
        db.query(Order)
        .options(joinedload(Order.items), joinedload(Order.payments))
        .filter(Order.from_entity_type == "WAREHOUSE")
    )
    if warehouse_id:
        query = query.filter(Order.from_entity_id == warehouse_id)
    from_dt = _parse_date(from_date, False)
    to_dt = _parse_date(to_date, True)
    if from_dt:
        query = query.filter(Order.created_at >= from_dt)
    if to_dt:
        query = query.filter(Order.created_at < to_dt)

    orders = query.order_by(Order.created_at.desc()).all()

    daily = defaultdict(lambda: {
        "date": None,
        "total_orders": 0,
        "delivered": 0,
        "cancelled": 0,
        "total_amount": 0,
        "collected": 0,
        "by_salesman": defaultdict(lambda: {
            "salesman_id": None,
            "salesman_name": None,
            "orders": 0,
            "delivered": 0,
            "cancelled": 0,
            "amount": 0,
        }),
    })

    salesman_cache = {}
    total_orders = 0
    total_delivered = 0
    total_cancelled = 0
    total_amount = 0
    total_collected = 0

    issue_counts = defaultdict(int)

    for order in orders:
        if not order.created_at:
            continue
        day_key = order.created_at.date().isoformat()
        day = daily[day_key]
        day["date"] = day_key

        totals = calculate_order_totals(order)
        amount = totals["grand_total"]
        collected = sum(p.amount for p in order.payments)

        day["total_orders"] += 1
        day["total_amount"] += amount
        day["collected"] += collected
        total_orders += 1
        total_amount += amount
        total_collected += collected

        if order.status == OrderStatus.DELIVERED:
            day["delivered"] += 1
            total_delivered += 1
        elif order.status == OrderStatus.CANCELLED:
            day["cancelled"] += 1
            total_cancelled += 1
            if order.issue_category:
                cat = order.issue_category.value if hasattr(order.issue_category, "value") else order.issue_category
                issue_counts[cat] += 1

        sid = order.salesman_id or 0
        if sid not in salesman_cache:
            emp = db.query(Employee).filter(Employee.id == sid).first() if sid else None
            salesman_cache[sid] = emp.name if emp else "Unassigned"
        sm = day["by_salesman"][sid]
        sm["salesman_id"] = sid
        sm["salesman_name"] = salesman_cache[sid]
        sm["orders"] += 1
        sm["amount"] += amount
        if order.status == OrderStatus.DELIVERED:
            sm["delivered"] += 1
        elif order.status == OrderStatus.CANCELLED:
            sm["cancelled"] += 1

    days_list = []
    for day_key in sorted(daily.keys(), reverse=True):
        d = daily[day_key]
        d["by_salesman"] = sorted(d["by_salesman"].values(), key=lambda x: x["amount"], reverse=True)
        d["total_amount"] = round(d["total_amount"], 2)
        d["collected"] = round(d["collected"], 2)
        days_list.append(d)

    cancel_rate = round(total_cancelled / total_orders * 100, 1) if total_orders > 0 else 0

    return {
        "total_orders": total_orders,
        "total_delivered": total_delivered,
        "total_cancelled": total_cancelled,
        "total_amount": round(total_amount, 2),
        "total_collected": round(total_collected, 2),
        "cancel_rate": cancel_rate,
        "issue_breakdown": dict(issue_counts),
        "days": days_list,
    }


def get_salesman_performance(
    db: Session,
    warehouse_id: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
):
    query = (
        db.query(Order)
        .options(joinedload(Order.items))
        .filter(Order.from_entity_type == "WAREHOUSE")
    )
    if warehouse_id:
        query = query.filter(Order.from_entity_id == warehouse_id)
    from_dt = _parse_date(from_date, False)
    to_dt = _parse_date(to_date, True)
    if from_dt:
        query = query.filter(Order.created_at >= from_dt)
    if to_dt:
        query = query.filter(Order.created_at < to_dt)

    orders = query.all()
    by_salesman = defaultdict(lambda: {
        "salesman_id": None,
        "salesman_name": None,
        "total_orders": 0,
        "delivered": 0,
        "cancelled": 0,
        "delivered_amount": 0,
        "cancelled_amount": 0,
    })
    salesman_cache = {}

    for order in orders:
        sid = order.salesman_id or 0
        if sid not in salesman_cache:
            emp = db.query(Employee).filter(Employee.id == sid).first() if sid else None
            salesman_cache[sid] = emp.name if emp else "Unassigned"

        totals = calculate_order_totals(order)
        amount = totals["grand_total"]
        sm = by_salesman[sid]
        sm["salesman_id"] = sid
        sm["salesman_name"] = salesman_cache[sid]
        sm["total_orders"] += 1
        if order.status == OrderStatus.DELIVERED:
            sm["delivered"] += 1
            sm["delivered_amount"] += amount
        elif order.status == OrderStatus.CANCELLED:
            sm["cancelled"] += 1
            sm["cancelled_amount"] += amount

    result = []
    for sm in by_salesman.values():
        sm["cancel_rate"] = round(sm["cancelled"] / sm["total_orders"] * 100, 1) if sm["total_orders"] > 0 else 0
        sm["delivered_amount"] = round(sm["delivered_amount"], 2)
        sm["cancelled_amount"] = round(sm["cancelled_amount"], 2)
        result.append(sm)

    return sorted(result, key=lambda x: x["delivered_amount"], reverse=True)
