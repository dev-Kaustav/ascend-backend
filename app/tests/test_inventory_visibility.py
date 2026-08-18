"""Pins D1's visibility half (INVT-05): `GET /admin/inventory` reports
`expired_quantity` per (sku, warehouse), derived from the same
`expired_batch_criterion` the allocator refuses on (app/services/inventory.py).

Every test fixes the date explicitly via the `as_of` fixture, which monkeypatches
`app.services.inventory.current_business_date`. No test here reads the wall
clock directly.
"""
from datetime import date, timedelta

import pytest
from sqlalchemy import event

from app.services.admin import get_inventory
from app.services.order import create_outgoing_order, InsufficientStockError
from app.services import inventory as inventory_service
from app.schemas.order import OrderCreate, OrderItemCreate
from app.models import SKU, SKUBatch, Inventory, User, Brand, Retailer, Warehouse, Permission
from app.models.enums import EmployeeRole


FIXED_DATE = date(2026, 6, 15)


@pytest.fixture
def as_of(monkeypatch):
    """Freeze app.services.inventory.current_business_date() to FIXED_DATE.

    Callers must reach the seam module-qualified (inventory_service.current_business_date());
    a from-import bound at import time elsewhere would silently ignore this patch.
    """
    monkeypatch.setattr(inventory_service, "current_business_date", lambda: FIXED_DATE)
    return FIXED_DATE


def _seed(db, batches, warehouse=None, sku_suffix=""):
    """Build the house fixture graph and return (sku, warehouse, retailer, user, batches).

    batches: list of (expiry_date, quantity, reserved_quantity) tuples.
    Inventory.total_quantity is set to the sum of seeded batch quantities.
    """
    brand = Brand(name=f"Brand{sku_suffix}")
    if warehouse is None:
        warehouse = Warehouse(name=f"Visibility WH{sku_suffix}", location="Delhi", state="Delhi")
        db.add(warehouse)
    retailer = Retailer(name=f"Visibility Retailer{sku_suffix}", state="Delhi")
    db.add_all([brand, retailer])
    db.commit()
    sku = SKU(name=f"Visibility SKU{sku_suffix}", brand_id=brand.id)
    db.add(sku)
    db.commit()
    db_batches = []
    total_qty = 0
    total_reserved = 0
    for expiry_date, quantity, reserved in batches:
        batch = SKUBatch(
            sku_id=sku.id,
            warehouse_id=warehouse.id,
            expiry_date=expiry_date,
            quantity_received=quantity,
            remaining_quantity=quantity,
            reserved_quantity=reserved,
        )
        db.add(batch)
        db_batches.append(batch)
        total_qty += quantity
        total_reserved += reserved
    db.commit()
    inventory = Inventory(
        sku_id=sku.id,
        warehouse_id=warehouse.id,
        total_quantity=total_qty,
        reserved_quantity=total_reserved,
    )
    db.add(inventory)
    db.commit()
    user = User(email=f"admin-{id(batches)}{sku_suffix}@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add(user)
    db.commit()
    return sku, warehouse, retailer, user, db_batches


def _row_for(items, sku_id, warehouse_id):
    for item in items:
        if item["sku_id"] == sku_id and item["warehouse_id"] == warehouse_id:
            return item
    raise AssertionError(f"no row for sku_id={sku_id} warehouse_id={warehouse_id}")


def test_expired_quantity_sums_only_expired_batches(db, as_of):
    sku, warehouse, retailer, user, (expired, today, later) = _seed(
        db,
        [
            (as_of - timedelta(days=1), 5, 0),
            (as_of, 3, 0),
            (as_of + timedelta(days=30), 2, 0),
        ],
    )
    items, total = get_inventory(db)
    row = _row_for(items, sku.id, warehouse.id)
    assert row["expired_quantity"] == 5


def test_expired_quantity_is_zero_when_nothing_is_expired(db, as_of):
    sku, warehouse, retailer, user, (batch,) = _seed(
        db, [(as_of + timedelta(days=30), 5, 0)]
    )
    items, total = get_inventory(db)
    row = _row_for(items, sku.id, warehouse.id)
    assert row["expired_quantity"] == 0
    assert row["expired_quantity"] is not None


def test_batches_without_expiry_never_count_as_expired(db, as_of):
    sku, warehouse, retailer, user, (batch,) = _seed(db, [(None, 5, 0)])
    items, total = get_inventory(db)
    row = _row_for(items, sku.id, warehouse.id)
    assert row["expired_quantity"] == 0


def test_expired_quantity_counts_physical_units_including_reserved(db, as_of):
    sku, warehouse, retailer, user, (batch,) = _seed(
        db, [(as_of - timedelta(days=1), 10, 4)]
    )
    items, total = get_inventory(db)
    row = _row_for(items, sku.id, warehouse.id)
    assert row["expired_quantity"] == 10


def test_warehouse_filter_narrows_the_expired_figure(db, as_of):
    warehouse_a = Warehouse(name="WH A", location="Delhi", state="Delhi")
    warehouse_b = Warehouse(name="WH B", location="Mumbai", state="Maharashtra")
    db.add_all([warehouse_a, warehouse_b])
    db.commit()

    sku_a, _, _, _, _ = _seed(db, [(as_of - timedelta(days=1), 6, 0)], warehouse=warehouse_a, sku_suffix="-A")
    sku_b, _, _, _, _ = _seed(db, [(as_of - timedelta(days=1), 9, 0)], warehouse=warehouse_b, sku_suffix="-B")

    items, total = get_inventory(db, warehouse_id=warehouse_a.id)
    assert len(items) == 1
    row = _row_for(items, sku_a.id, warehouse_a.id)
    assert row["expired_quantity"] == 6


def test_reported_expired_stock_is_exactly_what_allocation_refuses(db, as_of):
    sku, warehouse, retailer, user, (batch,) = _seed(
        db, [(as_of - timedelta(days=1), 8, 0)]
    )
    items, total = get_inventory(db)
    row = _row_for(items, sku.id, warehouse.id)
    assert row["expired_quantity"] == row["total_quantity"]

    order = OrderCreate(
        retailer_id=retailer.id,
        warehouse_id=warehouse.id,
        items=[OrderItemCreate(sku_id=sku.id, quantity=1, unit_price=100, discount_amount=0)],
    )
    with pytest.raises(InsufficientStockError):
        create_outgoing_order(db, order, user)


def test_inventory_endpoint_query_count_does_not_grow_with_rows(db, as_of):
    warehouse = Warehouse(name="Query Count WH", location="Delhi", state="Delhi")
    db.add(warehouse)
    db.commit()
    _seed(db, [(as_of - timedelta(days=1), 5, 0)], warehouse=warehouse, sku_suffix="-qc0")

    db.expire_all()
    engine = db.get_bind()
    counter = {"n": 0}

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    event.listen(engine, "before_cursor_execute", _before_cursor_execute)
    try:
        get_inventory(db, warehouse_id=warehouse.id)
    finally:
        event.remove(engine, "before_cursor_execute", _before_cursor_execute)
    first_count = counter["n"]

    for i in range(5):
        _seed(db, [(as_of - timedelta(days=1), 5, 0)], warehouse=warehouse, sku_suffix=f"-qc{i + 1}")

    db.expire_all()
    counter = {"n": 0}
    event.listen(engine, "before_cursor_execute", _before_cursor_execute)
    try:
        get_inventory(db, warehouse_id=warehouse.id)
    finally:
        event.remove(engine, "before_cursor_execute", _before_cursor_execute)
    second_count = counter["n"]

    assert first_count == second_count


def test_inventory_endpoint_serializes_expired_quantity_over_http(db, client, as_of):
    permission = Permission(code="inventory.view", description="View Inventory")
    db.add(permission)
    db.commit()

    from app.core.security import create_access_token, get_password_hash

    admin = User(
        email="admin-http@ascend.com",
        password_hash=get_password_hash("password"),
        role=EmployeeRole.ADMIN,
    )
    db.add(admin)
    db.commit()
    token = create_access_token({"user_id": admin.id, "role": "ADMIN"})
    headers = {"Authorization": f"Bearer {token}"}

    sku, warehouse, retailer, user, (batch,) = _seed(
        db, [(as_of - timedelta(days=1), 4, 0)], sku_suffix="-http"
    )

    response = client.get("/admin/inventory", headers=headers)
    assert response.status_code == 200
    body = response.json()
    row = _row_for(body["items"], sku.id, warehouse.id)
    assert row["expired_quantity"] == 4
