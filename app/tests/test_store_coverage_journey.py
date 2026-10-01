"""Phase 9 (09-11): a storefront order is counted by the real assigned-salesman coverage report.

08-09 proved the store order carries the shop's assigned salesman, and test_coverage.py proves the
aggregator on hand-built orders. This module joins them: a real POST /store/orders, then the real
app.services.coverage.get_coverage, with no staff-placed order and no invoice. Nothing here mocks
the aggregator, and the database is the conftest per-test in-memory SQLite (drop_all/create_all).
"""
from app.models import Beat, Employee, Invoice, Order
from app.models.enums import EmployeeRole, OrderStatus
from app.services.coverage import get_coverage
from app.tests.store_helpers import (
    auth_headers,
    make_brand,
    make_category,
    make_storefront,
    ready_retailer,
    stocked_sku,
)


def _salesman(db, name):
    employee = Employee(
        name=name, email=f"{name.lower().replace(' ', '.')}@example.com", role=EmployeeRole.SALESMAN
    )
    db.add(employee)
    db.commit()
    return employee


def _beat(db, name, warehouse):
    beat = Beat(name=name, warehouse_id=warehouse.id)
    db.add(beat)
    db.commit()
    return beat


def _shop(db, *, salesman=None, beat=None):
    """A store-ready retailer, optionally assigned to a salesman and placed on a beat."""
    user, retailer, address = ready_retailer(
        db, assigned_salesman_id=salesman.id if salesman else None
    )
    if beat is not None:
        retailer.beat_id = beat.id
        db.commit()
    return user, retailer, address


def _store(db):
    warehouse = make_storefront(db)
    sku = stocked_sku(
        db,
        warehouse=warehouse,
        brand=make_brand(db),
        category=make_category(db, "Chips"),
        name="Masala Chips",
        code="CHN-MSL-23g",
        mrp="22.5",
        amount="18.0",
        qty=40,
    )
    return warehouse, sku


def _place(client, user, sku, address):
    response = client.post(
        "/store/orders",
        json={"items": [{"sku_id": sku.id, "quantity": 3}], "address_id": address.id},
        headers=auth_headers(user),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _coverage(db, order_id, **filters):
    """Real aggregator over the order's own created_at day, so a midnight rollover cannot flake."""
    order = db.query(Order).filter(Order.id == order_id).one()
    day = order.created_at.date().isoformat()
    return get_coverage(db, from_date=day, to_date=day, **filters)


def test_store_created_pending_order_counts_for_assigned_salesman(client, db):
    warehouse, sku = _store(db)
    salesman = _salesman(db, "Asha Rao")
    beat = _beat(db, "Karol Bagh", warehouse)
    user, retailer, address = _shop(db, salesman=salesman, beat=beat)
    filters = dict(warehouse_id=warehouse.id, beat_id=beat.id, salesman_id=salesman.id)

    before = get_coverage(db, **filters)
    assert (before["planned"], before["billed"]) == (1, 0)

    order_id = _place(client, user, sku, address)

    order = db.query(Order).one()
    assert order.id == order_id
    assert order.status == OrderStatus.PENDING
    assert order.to_entity_id == retailer.id
    assert order.salesman_id == salesman.id
    # Counted without any staff action: no staff-placed order and no invoice exist.
    assert db.query(Invoice).count() == 0

    after = _coverage(db, order_id, **filters)
    assert (after["planned"], after["billed"]) == (1, 1)
    assert after["coverage_percent"] == 100.0
    (beat_row,) = after["by_beat"]
    assert (beat_row["beat_id"], beat_row["planned"], beat_row["billed"]) == (beat.id, 1, 1)
    (salesman_row,) = after["by_salesman"]
    assert (salesman_row["salesman_id"], salesman_row["planned"], salesman_row["billed"]) == (
        salesman.id,
        1,
        1,
    )
