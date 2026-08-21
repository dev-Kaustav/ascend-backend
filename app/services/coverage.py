"""Compute billed-over-planned coverage from retailer beat membership.

The third term reserved by 05-CONTEXT.md D1 belongs to RPT-05 and remains blocked. The
denominator here is the set of retailers assigned to beats, never the whole retailer table.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import Beat, Employee, Order, Retailer
from app.models.enums import OrderStatus


def _parse_bound(value: str | None, is_end: bool) -> datetime | None:
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid date '{value}' - expected YYYY-MM-DD") from exc
    if is_end:
        parsed += timedelta(days=1)
    return datetime.combine(parsed, datetime.min.time())


def _ratio(billed: int, planned: int) -> float | None:
    if planned == 0:
        return None
    return round(billed / planned * 100, 1)


def get_coverage(
    db: Session,
    from_date: str | None = None,
    to_date: str | None = None,
    warehouse_id: int | None = None,
    beat_id: int | None = None,
    salesman_id: int | None = None,
) -> dict:
    from_dt = _parse_bound(from_date, is_end=False)
    to_dt = _parse_bound(to_date, is_end=True)

    planned_query = (
        db.query(
            Retailer.id.label("retailer_id"),
            Retailer.beat_id.label("beat_id"),
            Beat.name.label("beat_name"),
            Beat.warehouse_id.label("warehouse_id"),
            Retailer.assigned_salesman_id.label("assigned_salesman_id"),
        )
        .join(Beat, Retailer.beat_id == Beat.id)
    )
    if warehouse_id is not None:
        planned_query = planned_query.filter(Beat.warehouse_id == warehouse_id)
    if beat_id is not None:
        planned_query = planned_query.filter(Retailer.beat_id == beat_id)
    if salesman_id is not None:
        planned_query = planned_query.filter(Retailer.assigned_salesman_id == salesman_id)

    planned_rows = planned_query.all()
    planned_retailer_ids = [row.retailer_id for row in planned_rows]

    billed_retailer_ids: set[int] = set()
    if planned_retailer_ids:
        billed_query = db.query(Order.to_entity_id).filter(
            Order.from_entity_type == "WAREHOUSE",
            Order.to_entity_type == "RETAILER",
            Order.to_entity_id.in_(planned_retailer_ids),
            Order.status != OrderStatus.CANCELLED,
        )
        if from_dt is not None:
            billed_query = billed_query.filter(Order.created_at >= from_dt)
        if to_dt is not None:
            billed_query = billed_query.filter(Order.created_at < to_dt)
        billed_retailer_ids = {row[0] for row in billed_query.distinct().all()}

    beat_groups = defaultdict(list)
    salesman_groups = defaultdict(list)
    for row in planned_rows:
        beat_groups[row.beat_id].append(row)
        # Ownership must match on both sides; order attribution cannot move an outlet.
        salesman_groups[row.assigned_salesman_id].append(row)

    by_beat = []
    for rows in beat_groups.values():
        planned_count = len(rows)
        billed_count = sum(row.retailer_id in billed_retailer_ids for row in rows)
        first = rows[0]
        by_beat.append(
            {
                "beat_id": first.beat_id,
                "beat_name": first.beat_name,
                "warehouse_id": first.warehouse_id,
                "planned": planned_count,
                "billed": billed_count,
                "coverage_percent": _ratio(billed_count, planned_count),
            }
        )
    by_beat.sort(key=lambda row: row["beat_name"])

    assigned_ids = {key for key in salesman_groups if key is not None}
    employee_names = {}
    if assigned_ids:
        employee_names = {
            employee.id: employee.name
            for employee in db.query(Employee).filter(Employee.id.in_(assigned_ids)).all()
        }

    by_salesman = []
    for assigned_id, rows in salesman_groups.items():
        planned_count = len(rows)
        billed_count = sum(row.retailer_id in billed_retailer_ids for row in rows)
        by_salesman.append(
            {
                "salesman_id": assigned_id,
                "salesman_name": employee_names.get(assigned_id, "Unassigned"),
                "planned": planned_count,
                "billed": billed_count,
                "coverage_percent": _ratio(billed_count, planned_count),
            }
        )
    by_salesman.sort(key=lambda row: (-row["planned"], row["salesman_name"]))

    planned_count = len(planned_rows)
    billed_count = len(billed_retailer_ids)
    return {
        "from_date": from_date or None,
        "to_date": to_date or None,
        "planned": planned_count,
        "billed": billed_count,
        "coverage_percent": _ratio(billed_count, planned_count),
        "retailers_without_beat": db.query(Retailer).filter(Retailer.beat_id.is_(None)).count(),
        "by_beat": by_beat,
        "by_salesman": by_salesman,
    }
