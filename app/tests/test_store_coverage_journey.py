"""Phase 9 (09-11): a storefront order is counted by the real assigned-salesman coverage report.

08-09 proved the store order carries the shop's assigned salesman, and test_coverage.py proves the
aggregator on hand-built orders. This module joins them: a real POST /store/orders, then the real
app.services.coverage.get_coverage, with no staff-placed order and no invoice. Nothing here mocks
the aggregator, and the database is the conftest per-test in-memory SQLite (drop_all/create_all).
"""
from app.models import Beat, Employee, Invoice, Order, Retailer
from app.models.enums import EmployeeRole, OrderStatus
from app.services.coverage import get_coverage
from app.tests.store_helpers import (
    auth_headers,
    make_brand,
    make_category,
    make_storefront,
    make_user,
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


def _cancel(client, admin, order_id):
    response = client.patch(
        f"/orders/{order_id}/status", json={"status": "CANCELLED"}, headers=auth_headers(admin)
    )
    assert response.status_code == 200, response.text


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


def test_repeat_orders_count_one_retailer_and_cancelled_only_is_excluded(client, db):
    warehouse, sku = _store(db)
    salesman = _salesman(db, "Asha Rao")
    beat = _beat(db, "Karol Bagh", warehouse)
    user, retailer, address = _shop(db, salesman=salesman, beat=beat)
    admin = make_user(db, EmployeeRole.ADMIN)
    filters = dict(salesman_id=salesman.id)

    first = _place(client, user, sku, address)
    second = _place(client, user, sku, address)
    assert db.query(Order).count() == 2

    # Two orders, one distinct retailer.
    result = _coverage(db, first, **filters)
    assert (result["planned"], result["billed"]) == (1, 1)

    # Cancelling one order leaves the retailer billed through the other.
    _cancel(client, admin, first)
    db.expire_all()
    assert db.query(Order).filter(Order.id == first).one().status == OrderStatus.CANCELLED
    assert _coverage(db, second, **filters)["billed"] == 1

    # Cancelling every order the retailer placed drops it from billed but not from planned.
    _cancel(client, admin, second)
    db.expire_all()
    result = _coverage(db, second, **filters)
    assert (result["planned"], result["billed"]) == (1, 0)
    assert result["coverage_percent"] == 0.0
    assert result["by_beat"][0]["billed"] == 0
    assert result["by_salesman"][0]["billed"] == 0


def test_membership_boundaries_follow_current_assignment(client, db):
    warehouse, sku = _store(db)
    asha = _salesman(db, "Asha Rao")
    bilal = _salesman(db, "Bilal Khan")
    beat = _beat(db, "Karol Bagh", warehouse)
    other_beat = _beat(db, "Lajpat Nagar", warehouse)

    asha_user, asha_shop, asha_addr = _shop(db, salesman=asha, beat=beat)
    bilal_user, bilal_shop, bilal_addr = _shop(db, salesman=bilal, beat=beat)
    # Same salesman as the first shop but no beat: ordered, yet never part of the planned set.
    nobeat_user, nobeat_shop, nobeat_addr = _shop(db, salesman=asha)
    # A shop on another beat with no orders at all.
    _shop(db, salesman=bilal, beat=other_beat)

    asha_order = _place(client, asha_user, sku, asha_addr)
    bilal_order = _place(client, bilal_user, sku, bilal_addr)
    nobeat_order = _place(client, nobeat_user, sku, nobeat_addr)
    # An order for a no-beat shop is still attributed to its salesman on the order itself.
    assert db.query(Order).filter(Order.id == nobeat_order).one().salesman_id == asha.id

    # Salesman filter: Asha's planned set is just her beat-linked shop, so the no-beat order
    # does not count and Bilal's order does not leak in.
    asha_result = _coverage(db, asha_order, salesman_id=asha.id)
    assert (asha_result["planned"], asha_result["billed"]) == (1, 1)
    assert [row["salesman_id"] for row in asha_result["by_salesman"]] == [asha.id]

    # Beat filter: both salesmen's shops sit on the beat and both were billed.
    beat_result = _coverage(db, asha_order, beat_id=beat.id)
    assert (beat_result["planned"], beat_result["billed"]) == (2, 2)
    assert {row["salesman_id"]: row["billed"] for row in beat_result["by_salesman"]} == {
        asha.id: 1,
        bilal.id: 1,
    }

    # Whole warehouse: three beat-linked shops, two billed; the no-beat shop is only reported
    # as excluded, and its order adds nothing to billed.
    all_result = _coverage(db, bilal_order, warehouse_id=warehouse.id)
    assert (all_result["planned"], all_result["billed"]) == (3, 2)
    assert all_result["retailers_without_beat"] == 1
    assert {row["beat_id"]: (row["planned"], row["billed"]) for row in all_result["by_beat"]} == {
        beat.id: (2, 2),
        other_beat.id: (1, 0),
    }

    # Reassigning the shop moves it: coverage follows the CURRENT assignment, not the salesman
    # stamped on the order at checkout.
    db.query(Retailer).filter(Retailer.id == asha_shop.id).update({"assigned_salesman_id": bilal.id})
    db.commit()
    moved_from = _coverage(db, asha_order, salesman_id=asha.id)
    assert (moved_from["planned"], moved_from["billed"]) == (0, 0)
    moved_to = _coverage(db, asha_order, salesman_id=bilal.id)
    assert (moved_to["planned"], moved_to["billed"]) == (3, 2)
