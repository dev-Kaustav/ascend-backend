"""Pins D1 (expiry refusal) and INVT-05 (FEFO/exact-Integer correctness) to an
injected business date.

Every test in this module fixes the date explicitly via the `as_of` fixture,
which uses pytest's built-in `monkeypatch` to replace
`app.services.inventory.current_business_date`. `monkeypatch` is a pytest
builtin, not a mocking library — it does not fake the database, so this does
not run afoul of `codebase/TESTING.md`'s "no mocks" note. No test here reads
the wall clock directly, and no test carries an absolute year literal outside
the `as_of` fixture, so none of this can rot as fixture data ages.
"""
from datetime import date, timedelta

import pytest

from app.services.order import (
    create_outgoing_order,
    update_order_status,
    InsufficientStockError,
)
from app.services import inventory as inventory_service
from app.schemas.order import OrderCreate, OrderItemCreate, StatusUpdate
from app.models import SKU, SKUBatch, Inventory, OrderItemBatch, User, Brand, Retailer, Warehouse
from app.models.enums import EmployeeRole


FIXED_DATE = date(2026, 6, 15)


@pytest.fixture
def as_of(monkeypatch):
    """Freeze app.services.inventory.current_business_date() to FIXED_DATE.

    Callers must reach the seam module-qualified (inventory_service.current_business_date()),
    which is exactly what this monkeypatch relies on — a from-import bound at
    import time elsewhere would silently ignore this patch.
    """
    monkeypatch.setattr(inventory_service, "current_business_date", lambda: FIXED_DATE)
    return FIXED_DATE


def _seed(db, batches):
    """Build the house fixture graph and return (sku, warehouse, retailer, user, batches).

    batches: list of (expiry_date, quantity) tuples. Inventory.total_quantity is
    set to the sum of seeded batch quantities so the aggregate pre-check does
    not mask what each test is trying to observe.
    """
    brand = Brand(name="Brand")
    warehouse = Warehouse(name="Expiry WH", location="Delhi", state="Delhi")
    retailer = Retailer(name="Expiry Retailer", state="Delhi")
    db.add_all([brand, warehouse, retailer])
    db.commit()
    sku = SKU(name="Expiry SKU", brand_id=brand.id)
    db.add(sku)
    db.commit()
    db_batches = []
    total_qty = 0
    for expiry_date, quantity in batches:
        batch = SKUBatch(
            sku_id=sku.id,
            warehouse_id=warehouse.id,
            expiry_date=expiry_date,
            quantity_received=quantity,
            remaining_quantity=quantity,
        )
        db.add(batch)
        db_batches.append(batch)
        total_qty += quantity
    db.commit()
    inventory = Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=total_qty)
    db.add(inventory)
    db.commit()
    user = User(email=f"admin-{id(batches)}@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add(user)
    db.commit()
    return sku, warehouse, retailer, user, db_batches


def _order(retailer, warehouse, sku, quantity):
    return OrderCreate(
        retailer_id=retailer.id,
        warehouse_id=warehouse.id,
        items=[OrderItemCreate(sku_id=sku.id, quantity=quantity, unit_price=100, discount_amount=0)],
    )


def test_expired_batch_is_skipped_in_favour_of_fresh_stock(db, as_of):
    sku, warehouse, retailer, user, (expired, fresh) = _seed(
        db,
        [
            (as_of - timedelta(days=1), 5),
            (as_of + timedelta(days=365), 5),
        ],
    )
    result = create_outgoing_order(db, _order(retailer, warehouse, sku, 5), user)
    assert result is not None

    refreshed_expired = db.query(SKUBatch).filter(SKUBatch.id == expired.id).first()
    refreshed_fresh = db.query(SKUBatch).filter(SKUBatch.id == fresh.id).first()
    assert refreshed_expired.reserved_quantity == 0
    assert refreshed_fresh.reserved_quantity == 5

    batch_ids = {b.batch_id for b in db.query(OrderItemBatch).all()}
    assert expired.id not in batch_ids
    assert fresh.id in batch_ids


def test_batch_expiring_exactly_on_the_business_date_is_allocatable(db, as_of):
    sku, warehouse, retailer, user, (batch,) = _seed(db, [(as_of, 5)])
    result = create_outgoing_order(db, _order(retailer, warehouse, sku, 5), user)
    assert result is not None
    refreshed = db.query(SKUBatch).filter(SKUBatch.id == batch.id).first()
    assert refreshed.reserved_quantity == 5


def test_batch_expired_the_previous_day_is_not_allocatable(db, as_of):
    sku, warehouse, retailer, user, (batch,) = _seed(db, [(as_of - timedelta(days=1), 5)])
    with pytest.raises(InsufficientStockError):
        create_outgoing_order(db, _order(retailer, warehouse, sku, 5), user)


def test_batch_without_expiry_date_is_allocatable(db, as_of):
    sku, warehouse, retailer, user, (batch,) = _seed(db, [(None, 5)])
    result = create_outgoing_order(db, _order(retailer, warehouse, sku, 5), user)
    assert result is not None
    refreshed = db.query(SKUBatch).filter(SKUBatch.id == batch.id).first()
    assert refreshed.reserved_quantity == 5


def test_order_fails_and_reserves_nothing_when_only_expired_stock_remains(db, as_of):
    sku, warehouse, retailer, user, (batch_a, batch_b) = _seed(
        db,
        [
            (as_of - timedelta(days=1), 10),
            (as_of - timedelta(days=30), 10),
        ],
    )
    with pytest.raises(InsufficientStockError):
        create_outgoing_order(db, _order(retailer, warehouse, sku, 5), user)

    # The allocator's transactional_session rolls back on raise; assert the
    # undo explicitly rather than assuming it.
    db.rollback()

    refreshed_a = db.query(SKUBatch).filter(SKUBatch.id == batch_a.id).first()
    refreshed_b = db.query(SKUBatch).filter(SKUBatch.id == batch_b.id).first()
    refreshed_inventory = db.query(Inventory).filter(Inventory.sku_id == sku.id).first()
    assert refreshed_a.reserved_quantity == 0
    assert refreshed_b.reserved_quantity == 0
    assert refreshed_inventory.reserved_quantity == 0
    assert db.query(OrderItemBatch).count() == 0


def test_fefo_order_is_preserved_among_non_expired_batches(db, as_of):
    sku, warehouse, retailer, user, (expired, soon, later, no_expiry) = _seed(
        db,
        [
            (as_of - timedelta(days=1), 5),
            (as_of + timedelta(days=10), 3),
            (as_of + timedelta(days=20), 3),
            (None, 3),
        ],
    )
    result = create_outgoing_order(db, _order(retailer, warehouse, sku, 5), user)
    assert result is not None

    refreshed_expired = db.query(SKUBatch).filter(SKUBatch.id == expired.id).first()
    refreshed_soon = db.query(SKUBatch).filter(SKUBatch.id == soon.id).first()
    refreshed_later = db.query(SKUBatch).filter(SKUBatch.id == later.id).first()
    refreshed_no_expiry = db.query(SKUBatch).filter(SKUBatch.id == no_expiry.id).first()

    assert refreshed_expired.reserved_quantity == 0
    assert refreshed_soon.reserved_quantity == 3
    assert refreshed_later.reserved_quantity == 2
    assert refreshed_no_expiry.reserved_quantity == 0


def test_allocated_quantities_are_exact_integers(db, as_of):
    sku, warehouse, retailer, user, (batch,) = _seed(db, [(as_of + timedelta(days=10), 7)])
    result = create_outgoing_order(db, _order(retailer, warehouse, sku, 5), user)
    assert result is not None

    order_item_batch = db.query(OrderItemBatch).first()
    refreshed_batch = db.query(SKUBatch).filter(SKUBatch.id == batch.id).first()
    refreshed_inventory = db.query(Inventory).filter(Inventory.sku_id == sku.id).first()

    assert isinstance(order_item_batch.quantity, int)
    assert order_item_batch.quantity == 5
    assert isinstance(refreshed_batch.reserved_quantity, int)
    assert refreshed_batch.reserved_quantity == 5
    assert isinstance(refreshed_inventory.reserved_quantity, int)
    assert refreshed_inventory.reserved_quantity == 5


def test_dispatch_refuses_a_batch_that_expired_after_reservation(db, as_of, monkeypatch):
    sku, warehouse, retailer, user, (batch,) = _seed(db, [(as_of + timedelta(days=5), 5)])
    result = create_outgoing_order(db, _order(retailer, warehouse, sku, 5), user)
    assert result is not None

    result.delivery_driver_id = 1
    db.commit()
    update_order_status(db, result.id, StatusUpdate(status="READY_TO_SHIP"), user)

    # Move the seam forward past the batch's expiry, then dispatch.
    monkeypatch.setattr(
        inventory_service, "current_business_date", lambda: as_of + timedelta(days=10)
    )
    with pytest.raises(InsufficientStockError):
        update_order_status(db, result.id, StatusUpdate(status="OUT_FOR_DELIVERY"), user)
