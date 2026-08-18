"""Executable proof of INVT-03's invariants (docs/inventory-invariants.md, I1-I5).

Prose rots; this file is the half that does not. `assert_inventory_invariants(db)` checks
I3, I4 and I5 for the current state of the session — I1 and I2 are already database-enforced
by migration `0049` (plan 04-01) and are not re-checked here. It is walked through order
creation, dispatch, release, restore, inventory receipt and a two-order interleaving, and it
is deliberately made to fail once, on purpose, by the one write path known to break it:
`create_credit_note(restock=True)`.

Seeding style follows `app/tests/test_order_transitions.py`'s `_seed_dispatchable_order` /
`_dispatch` pattern, duplicated rather than imported (this module keeps its own
`itertools.count()` seam for unique emails/names, per this plan's "do not import test
helpers across modules" instruction).
"""
import itertools

import pytest

from app.models import (
    Brand,
    Employee,
    Inventory,
    Order,
    OrderItem,
    OrderItemBatch,
    Retailer,
    SKU,
    SKUBatch,
    User,
    Warehouse,
)
from app.models.enums import EmployeeRole, OrderStatus
from app.schemas.accounting import CreditNoteCreate, CreditNoteItemCreate
from app.schemas.admin import InventoryReceiptCreate, InventoryReceiptItem
from app.schemas.order import OrderCreate, OrderItemCreate, StatusUpdate
from app.services.accounting import create_credit_note
from app.services.admin import add_inventory_receipt
from app.services.order import create_outgoing_order, update_order_status


def assert_inventory_invariants(db):
    """Executable half of INVT-03 (docs/inventory-invariants.md).

    - I3: Inventory.total_quantity == SUM(SKUBatch.remaining_quantity) per (sku, warehouse).
    - I4: Inventory.reserved_quantity == SUM(SKUBatch.reserved_quantity) per (sku, warehouse).
    - I5: SKUBatch.reserved_quantity == SUM(OrderItemBatch.quantity) over order lines whose
      order is still PENDING or READY_TO_SHIP.

    Raises AssertionError naming the (sku, warehouse) or batch and both sides of the failing
    comparison — a bare `assert a == b` here would be unreadable at the point of failure.
    """
    for inv in db.query(Inventory).all():
        batches = (
            db.query(SKUBatch)
            .filter(SKUBatch.sku_id == inv.sku_id, SKUBatch.warehouse_id == inv.warehouse_id)
            .all()
        )
        batch_remaining_sum = sum(b.remaining_quantity or 0 for b in batches)
        batch_reserved_sum = sum(b.reserved_quantity or 0 for b in batches)

        if inv.total_quantity != batch_remaining_sum:
            raise AssertionError(
                f"I3 broken for (sku_id={inv.sku_id}, warehouse_id={inv.warehouse_id}): "
                f"Inventory.total_quantity={inv.total_quantity} != "
                f"SUM(SKUBatch.remaining_quantity)={batch_remaining_sum}"
            )
        if inv.reserved_quantity != batch_reserved_sum:
            raise AssertionError(
                f"I4 broken for (sku_id={inv.sku_id}, warehouse_id={inv.warehouse_id}): "
                f"Inventory.reserved_quantity={inv.reserved_quantity} != "
                f"SUM(SKUBatch.reserved_quantity)={batch_reserved_sum}"
            )

    for batch in db.query(SKUBatch).all():
        allocated_to_open_orders = (
            db.query(OrderItemBatch)
            .join(OrderItem, OrderItemBatch.order_item_id == OrderItem.id)
            .join(Order, OrderItem.order_id == Order.id)
            .filter(
                OrderItemBatch.batch_id == batch.id,
                Order.status.in_([OrderStatus.PENDING, OrderStatus.READY_TO_SHIP]),
            )
            .all()
        )
        allocated_sum = sum(oib.quantity or 0 for oib in allocated_to_open_orders)
        if (batch.reserved_quantity or 0) != allocated_sum:
            raise AssertionError(
                f"I5 broken for batch_id={batch.id}: SKUBatch.reserved_quantity="
                f"{batch.reserved_quantity} != SUM(OrderItemBatch.quantity over "
                f"PENDING/READY_TO_SHIP orders)={allocated_sum}"
            )


# ---------------------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------------------

_seed_counter = itertools.count()


def _seed_warehouse_and_sku(db):
    n = next(_seed_counter)
    brand = Brand(name=f"Invariants Brand {n}")
    warehouse = Warehouse(name=f"Invariants WH {n}", location="Delhi", state="Delhi")
    db.add_all([brand, warehouse])
    db.commit()
    sku = SKU(name=f"Invariants SKU {n}", brand_id=brand.id)
    db.add(sku)
    db.commit()
    return brand, warehouse, sku


def _seed_order(db, quantity=6, batch_quantity=15):
    """One Brand/Warehouse/SKU/Retailer/driver/admin and one PENDING order against a single
    batch, reserved via the real order-creation path."""
    brand, warehouse, sku = _seed_warehouse_and_sku(db)
    n = next(_seed_counter)
    retailer = Retailer(name=f"Invariants Retailer {n}", state="Delhi")
    db.add(retailer)
    db.commit()

    batch = SKUBatch(
        sku_id=sku.id,
        warehouse_id=warehouse.id,
        quantity_received=batch_quantity,
        remaining_quantity=batch_quantity,
    )
    db.add(batch)
    inventory = Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=batch_quantity)
    db.add(inventory)
    driver = Employee(
        name=f"Invariants Driver {n}",
        email=f"invariants-driver-{n}@ascend.com",
        role=EmployeeRole.DRIVER,
    )
    admin = User(email=f"invariants-admin-{n}@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add_all([driver, admin])
    db.commit()

    order = create_outgoing_order(
        db,
        OrderCreate(
            retailer_id=retailer.id,
            warehouse_id=warehouse.id,
            items=[OrderItemCreate(sku_id=sku.id, quantity=quantity, unit_price=100, discount_amount=0)],
        ),
        admin,
    )
    return order, sku, warehouse, brand, driver, admin


def _dispatch(db, order, driver):
    order.delivery_driver_id = driver.id
    db.commit()
    update_order_status(db, order.id, StatusUpdate(status="READY_TO_SHIP"))
    update_order_status(db, order.id, StatusUpdate(status="OUT_FOR_DELIVERY"))
    db.refresh(order)


def _deliver(db, order):
    update_order_status(db, order.id, StatusUpdate(status="DELIVERED"))
    db.refresh(order)


# ---------------------------------------------------------------------------------------
# The invariant holds across every lifecycle write path
# ---------------------------------------------------------------------------------------


def test_invariants_hold_after_order_creation(db):
    order, sku, warehouse, brand, driver, admin = _seed_order(db)
    assert_inventory_invariants(db)


def test_invariants_hold_after_dispatch(db):
    order, sku, warehouse, brand, driver, admin = _seed_order(db)
    _dispatch(db, order, driver)
    assert_inventory_invariants(db)


def test_invariants_hold_after_release_from_pending(db):
    order, sku, warehouse, brand, driver, admin = _seed_order(db)
    update_order_status(db, order.id, StatusUpdate(status="CANCELLED"))
    assert_inventory_invariants(db)


def test_invariants_hold_after_restore_from_out_for_delivery(db):
    order, sku, warehouse, brand, driver, admin = _seed_order(db)
    _dispatch(db, order, driver)
    update_order_status(db, order.id, StatusUpdate(status="CANCELLED"))
    assert_inventory_invariants(db)


def test_invariants_hold_after_inventory_receipt(db):
    """add_inventory_receipt (admin.py:111-161) is the only path that creates a batch
    outside the order flow."""
    brand, warehouse, sku = _seed_warehouse_and_sku(db)
    add_inventory_receipt(
        db,
        InventoryReceiptCreate(
            brand_id=brand.id,
            warehouse_id=warehouse.id,
            items=[InventoryReceiptItem(sku_id=sku.id, quantity=20, mfg_date=None, expiry_date=None)],
        ),
    )
    assert_inventory_invariants(db)


def test_invariants_hold_across_two_orders_on_one_sku(db):
    """Two orders against the same (sku, warehouse), one dispatched and one cancelled.
    Multi-order interleaving is where a per-batch counter (SKUBatch.reserved_quantity) and
    a per-sku counter (Inventory.reserved_quantity) can drift apart, and no existing test
    covers it."""
    brand, warehouse, sku = _seed_warehouse_and_sku(db)
    n = next(_seed_counter)
    retailer_a = Retailer(name=f"Invariants Retailer A {n}", state="Delhi")
    retailer_b = Retailer(name=f"Invariants Retailer B {n}", state="Delhi")
    db.add_all([retailer_a, retailer_b])
    db.commit()

    batch = SKUBatch(sku_id=sku.id, warehouse_id=warehouse.id, quantity_received=30, remaining_quantity=30)
    db.add(batch)
    inventory = Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=30)
    db.add(inventory)
    driver = Employee(
        name=f"Invariants Driver {n}",
        email=f"invariants-driver-{n}@ascend.com",
        role=EmployeeRole.DRIVER,
    )
    admin = User(email=f"invariants-admin-{n}@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add_all([driver, admin])
    db.commit()

    order_a = create_outgoing_order(
        db,
        OrderCreate(
            retailer_id=retailer_a.id,
            warehouse_id=warehouse.id,
            items=[OrderItemCreate(sku_id=sku.id, quantity=10, unit_price=100, discount_amount=0)],
        ),
        admin,
    )
    assert_inventory_invariants(db)

    order_b = create_outgoing_order(
        db,
        OrderCreate(
            retailer_id=retailer_b.id,
            warehouse_id=warehouse.id,
            items=[OrderItemCreate(sku_id=sku.id, quantity=8, unit_price=100, discount_amount=0)],
        ),
        admin,
    )
    assert_inventory_invariants(db)

    _dispatch(db, order_a, driver)
    assert_inventory_invariants(db)

    update_order_status(db, order_b.id, StatusUpdate(status="CANCELLED"))
    assert_inventory_invariants(db)


# ---------------------------------------------------------------------------------------
# The one exception, pinned rather than fixed (D5, docs/inventory-invariants.md section 3)
# ---------------------------------------------------------------------------------------


def test_credit_note_restock_breaks_the_aggregate_invariant(db):
    """Pins `create_credit_note(restock=True)` (`accounting.py:167-186`) as a known,
    deliberately unfixed deviation (`#todo INVT-06`, docs/inventory-invariants.md section 3).

    It increments `Inventory.total_quantity` without creating a `SKUBatch`, so the restocked
    units are invisible to FEFO allocation (`order.py:155-168` reads only `sku_batches`) —
    the inventory screen shows stock that no order can actually use. `restock` defaults to
    `False` (`schemas/accounting.py:64`) and nothing else in this codebase sets it to `True`;
    this test is the only thing exercising that branch.

    A test asserting that something is broken is uncomfortable and correct: it converts an
    invisible inconsistency into a documented one that fails loudly the day somebody fixes
    it — at which point they delete this test on purpose.
    """
    order, sku, warehouse, brand, driver, admin = _seed_order(db, quantity=6, batch_quantity=15)
    _dispatch(db, order, driver)
    _deliver(db, order)
    assert_inventory_invariants(db)

    restock_quantity = 4
    create_credit_note(
        db,
        CreditNoteCreate(
            order_id=order.id,
            items=[CreditNoteItemCreate(sku_id=sku.id, quantity=restock_quantity, unit_price=100)],
            restock=True,
        ),
    )

    with pytest.raises(AssertionError, match="I3 broken"):
        assert_inventory_invariants(db)

    inventory = (
        db.query(Inventory)
        .filter(Inventory.sku_id == sku.id, Inventory.warehouse_id == warehouse.id)
        .first()
    )
    batch_remaining_sum = sum(
        b.remaining_quantity or 0
        for b in db.query(SKUBatch).filter(SKUBatch.sku_id == sku.id, SKUBatch.warehouse_id == warehouse.id).all()
    )
    # The exact shape of the deviation: total_quantity exceeds the batches' remaining
    # sum by precisely the restocked quantity — no batch was created to absorb it.
    assert inventory.total_quantity - batch_remaining_sum == restock_quantity
