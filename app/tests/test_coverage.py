from datetime import datetime

import pytest

from app.models import Beat, Employee, Order, Retailer, Warehouse
from app.models.enums import EmployeeRole, OrderStatus
from app.services.coverage import _parse_bound, get_coverage


def _warehouse(db, name: str) -> Warehouse:
    warehouse = Warehouse(name=name)
    db.add(warehouse)
    db.flush()
    return warehouse


def _salesman(db, name: str) -> Employee:
    employee = Employee(
        name=name,
        email=f"{name.lower().replace(' ', '.')}@example.com",
        role=EmployeeRole.SALESMAN,
    )
    db.add(employee)
    db.flush()
    return employee


def _beat(db, name: str, warehouse: Warehouse) -> Beat:
    beat = Beat(name=name, warehouse_id=warehouse.id)
    db.add(beat)
    db.flush()
    return beat


def _retailer(
    db,
    name: str,
    *,
    beat: Beat | None = None,
    salesman: Employee | None = None,
) -> Retailer:
    retailer = Retailer(
        name=name,
        beat_id=beat.id if beat else None,
        assigned_salesman_id=salesman.id if salesman else None,
    )
    db.add(retailer)
    db.flush()
    return retailer


def _order(
    db,
    retailer: Retailer,
    warehouse: Warehouse,
    *,
    created_at: datetime,
    status: OrderStatus = OrderStatus.PENDING,
    salesman: Employee | None = None,
) -> Order:
    order = Order(
        from_entity_type="WAREHOUSE",
        from_entity_id=warehouse.id,
        to_entity_type="RETAILER",
        to_entity_id=retailer.id,
        status=status,
        salesman_id=salesman.id if salesman else None,
        created_at=created_at,
    )
    db.add(order)
    return order


def test_parse_bound_builds_half_open_range_and_rejects_bad_input():
    assert _parse_bound(None, is_end=False) is None
    assert _parse_bound("", is_end=True) is None
    assert _parse_bound("2026-02-03", is_end=False) == datetime(2026, 2, 3)
    assert _parse_bound("2026-02-03", is_end=True) == datetime(2026, 2, 4)

    with pytest.raises(ValueError, match="Invalid date 'not-a-date'.*YYYY-MM-DD"):
        _parse_bound("not-a-date", is_end=False)


def test_empty_planned_set_has_undefined_ratio_and_visible_excluded_count(db):
    _retailer(db, "Outside Plan")
    db.commit()

    result = get_coverage(db)

    assert result == {
        "from_date": None,
        "to_date": None,
        "planned": 0,
        "billed": 0,
        "coverage_percent": None,
        "retailers_without_beat": 1,
        "by_beat": [],
        "by_salesman": [],
    }


def test_coverage_uses_one_planned_set_for_totals_and_breakdowns(db):
    warehouse = _warehouse(db, "Central")
    alpha = _beat(db, "Alpha", warehouse)
    beta = _beat(db, "Beta", warehouse)
    alice = _salesman(db, "Alice Seller")
    bob = _salesman(db, "Bob Seller")

    alice_billed = _retailer(db, "Alice Billed", beat=alpha, salesman=alice)
    alice_cancelled = _retailer(db, "Alice Cancelled", beat=alpha, salesman=alice)
    bob_pending = _retailer(db, "Bob Pending", beat=beta, salesman=bob)
    unassigned = _retailer(db, "Unassigned Planned", beat=beta)
    outside_plan = _retailer(db, "Outside Plan", salesman=alice)

    for hour in (9, 10, 11):
        _order(
            db,
            alice_billed,
            warehouse,
            created_at=datetime(2026, 2, 3, hour),
            status=OrderStatus.DELIVERED,
            salesman=bob,
        )
    _order(
        db,
        alice_cancelled,
        warehouse,
        created_at=datetime(2026, 2, 3, 12),
        status=OrderStatus.CANCELLED,
    )
    _order(
        db,
        bob_pending,
        warehouse,
        created_at=datetime(2026, 2, 3, 13),
        status=OrderStatus.PENDING,
    )
    _order(
        db,
        outside_plan,
        warehouse,
        created_at=datetime(2026, 2, 3, 14),
        status=OrderStatus.DELIVERED,
    )
    db.commit()

    result = get_coverage(db, from_date="2026-02-03", to_date="2026-02-03")

    assert result["planned"] == 4
    assert result["billed"] == 2
    assert result["coverage_percent"] == 50.0
    assert result["retailers_without_beat"] == 1
    assert result["from_date"] == "2026-02-03"
    assert result["to_date"] == "2026-02-03"
    assert result["by_beat"] == [
        {
            "beat_id": alpha.id,
            "beat_name": "Alpha",
            "warehouse_id": warehouse.id,
            "planned": 2,
            "billed": 1,
            "coverage_percent": 50.0,
        },
        {
            "beat_id": beta.id,
            "beat_name": "Beta",
            "warehouse_id": warehouse.id,
            "planned": 2,
            "billed": 1,
            "coverage_percent": 50.0,
        },
    ]
    assert result["by_salesman"] == [
        {
            "salesman_id": alice.id,
            "salesman_name": "Alice Seller",
            "planned": 2,
            "billed": 1,
            "coverage_percent": 50.0,
        },
        {
            "salesman_id": bob.id,
            "salesman_name": "Bob Seller",
            "planned": 1,
            "billed": 1,
            "coverage_percent": 100.0,
        },
        {
            "salesman_id": None,
            "salesman_name": "Unassigned",
            "planned": 1,
            "billed": 0,
            "coverage_percent": 0.0,
        },
    ]


def test_date_range_includes_both_named_days_and_excludes_next_midnight(db):
    warehouse = _warehouse(db, "Boundary Warehouse")
    beat = _beat(db, "Boundary Beat", warehouse)
    on_start = _retailer(db, "On Start", beat=beat)
    on_end = _retailer(db, "On End", beat=beat)
    after_end = _retailer(db, "After End", beat=beat)
    _order(db, on_start, warehouse, created_at=datetime(2026, 2, 3, 0, 0))
    _order(db, on_end, warehouse, created_at=datetime(2026, 2, 4, 23, 59, 59))
    _order(db, after_end, warehouse, created_at=datetime(2026, 2, 5, 0, 0))
    db.commit()

    result = get_coverage(db, from_date="2026-02-03", to_date="2026-02-04")

    assert result["planned"] == 3
    assert result["billed"] == 2
    assert result["coverage_percent"] == 66.7


def test_filters_narrow_the_planned_set_through_retailer_membership(db):
    north = _warehouse(db, "North")
    south = _warehouse(db, "South")
    north_a = _beat(db, "North A", north)
    north_b = _beat(db, "North B", north)
    south_a = _beat(db, "South A", south)
    alice = _salesman(db, "Alice Filter")
    bob = _salesman(db, "Bob Filter")
    north_alice = _retailer(db, "North Alice", beat=north_a, salesman=alice)
    north_bob = _retailer(db, "North Bob", beat=north_b, salesman=bob)
    south_alice = _retailer(db, "South Alice", beat=south_a, salesman=alice)

    _order(db, north_alice, south, created_at=datetime(2026, 2, 3), salesman=bob)
    _order(db, north_bob, north, created_at=datetime(2026, 2, 3), salesman=alice)
    _order(db, south_alice, north, created_at=datetime(2026, 2, 3), salesman=bob)
    db.commit()

    north_result = get_coverage(db, warehouse_id=north.id)
    beat_result = get_coverage(db, beat_id=north_b.id)
    alice_result = get_coverage(db, salesman_id=alice.id)
    combined_result = get_coverage(db, warehouse_id=north.id, salesman_id=alice.id)

    assert (north_result["planned"], north_result["billed"]) == (2, 2)
    assert (beat_result["planned"], beat_result["billed"]) == (1, 1)
    assert (alice_result["planned"], alice_result["billed"]) == (2, 2)
    assert (combined_result["planned"], combined_result["billed"]) == (1, 1)
