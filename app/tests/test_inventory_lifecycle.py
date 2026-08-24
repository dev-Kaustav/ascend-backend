"""INVT-01's deliverable: a per-status matrix pinning every one of the four inventory
functions — `_reserve_inventory_for_order`, `_dispatch_reserved_inventory`,
`_release_reserved_inventory`, `_restore_dispatched_inventory` (`app/services/order.py`) —
at every order status where it actually runs, plus the two statuses where none of them runs
at all.

Trigger table (04-CONTEXT.md `<baseline>`, confirmed by reading `update_order_status` and
`create_outgoing_order`):

| Trigger                              | Function                        |
|---------------------------------------|----------------------------------|
| order creation (order lands PENDING)  | `_reserve_inventory_for_order`   |
| PENDING -> READY_TO_SHIP              | (none — status write only)       |
| READY_TO_SHIP -> OUT_FOR_DELIVERY     | `_dispatch_reserved_inventory`   |
| OUT_FOR_DELIVERY -> DELIVERED         | (none)                           |
| PENDING -> CANCELLED                  | `_release_reserved_inventory`    |
| READY_TO_SHIP -> CANCELLED            | `_release_reserved_inventory`    |
| OUT_FOR_DELIVERY -> CANCELLED         | `_restore_dispatched_inventory`  |
| DELIVERED -> CANCELLED                | `_restore_dispatched_inventory`  |

**Correcting `CONCERNS.md:190`:** it claims `OrderItemBatch` "is only populated during
PENDING->READY_TO_SHIP and OUT_FOR_DELIVERY transitions". That is wrong. The rows are
written at **order creation** (`create_outgoing_order` -> `_reserve_inventory_for_order`),
and `PENDING` -> `READY_TO_SHIP` moves no inventory and touches no `OrderItemBatch` row at
all — it is a status write and a delivery-driver check, nothing else. Tests 1 and 2 below
pin the real behaviour.

**The release/restore asymmetry (CONTEXT finding 5) is intentional, not a leak.**
`_release_reserved_inventory` deletes its `OrderItemBatch` rows because a reservation is a
claim on stock that never physically moved; dropping the claim leaves nothing for those rows
to describe. `_restore_dispatched_inventory` keeps its rows because the goods really left the
warehouse and came back, and those rows are the allocation history of a movement that really
happened — the `InventoryTransaction(RETURN)` written alongside them references the same
`batch_id`. This module does not harmonise the two; test 9 asserts both directions in one
place so a future "cleanup" that merges the two behaviours turns red instead of silently
destroying an audit trail.

Follows the house style used by `test_order.py` / `test_order_transitions.py`: flat pytest
functions, direct model construction, the `db` fixture, no mocking, exact integer equality.
"""
import itertools

from app.models import (
    Brand,
    Employee,
    Inventory,
    InventoryTransaction,
    Order,
    OrderItem,
    OrderItemBatch,
    Retailer,
    SKU,
    SKUBatch,
    User,
    Warehouse,
)
from app.models.enums import EmployeeRole, OrderStatus, TransactionType
from app.schemas.order import OrderCreate, OrderItemCreate, StatusUpdate
from app.services import inventory as inventory_service
from app.services.order import (
    _dispatch_reserved_inventory,
    _release_reserved_inventory,
    _restore_dispatched_inventory,
    create_outgoing_order,
    update_order_status,
)

_seed_counter = itertools.count()


def _seed(db, batch_quantity=10):
    """Create a real Brand/Warehouse/Retailer/SKU, one SKUBatch, one Inventory row, an
    ADMIN User and a DRIVER Employee. Expiry is derived from the injected business-date
    seam plus a year, never an absolute literal (04-02's rule) — a past literal would make
    the batch unallocatable and silently invert every assertion in this module."""
    n = next(_seed_counter)
    brand = Brand(name=f"Lifecycle Brand {n}")
    warehouse = Warehouse(name=f"Lifecycle WH {n}", location="Delhi", state="Delhi")
    retailer = Retailer(name=f"Lifecycle Retailer {n}", state="Delhi")
    db.add_all([brand, warehouse, retailer])
    db.commit()

    sku = SKU(name=f"Lifecycle SKU {n}", brand_id=brand.id)
    db.add(sku)
    db.commit()

    today = inventory_service.current_business_date()
    batch = SKUBatch(
        sku_id=sku.id,
        warehouse_id=warehouse.id,
        expiry_date=today.replace(year=today.year + 1),
        quantity_received=batch_quantity,
        remaining_quantity=batch_quantity,
    )
    db.add(batch)
    inventory = Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=batch_quantity)
    db.add(inventory)
    driver = Employee(name=f"Lifecycle Driver {n}", email=f"lifecycle-driver-{n}@ascend.com", role=EmployeeRole.DRIVER)
    admin = User(email=f"lifecycle-admin-{n}@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add_all([driver, admin])
    db.commit()

    return {
        "brand_id": brand.id,
        "warehouse_id": warehouse.id,
        "retailer_id": retailer.id,
        "sku_id": sku.id,
        "batch_id": batch.id,
        "driver": driver,
        "admin": admin,
    }


def _snapshot(db, sku_id, warehouse_id, order_id):
    """A plain dict of everything a status transition might move. Every test asserts
    against this so a failure names exactly which counter moved instead of a bare
    True/False."""
    inventory = (
        db.query(Inventory)
        .filter(Inventory.sku_id == sku_id, Inventory.warehouse_id == warehouse_id)
        .first()
    )
    batch = (
        db.query(SKUBatch)
        .filter(SKUBatch.sku_id == sku_id, SKUBatch.warehouse_id == warehouse_id)
        .first()
    )
    order_item_batch_count = (
        db.query(OrderItemBatch)
        .join(OrderItem, OrderItem.id == OrderItemBatch.order_item_id)
        .filter(OrderItem.order_id == order_id)
        .count()
    )
    transaction_counts = {
        t.value: db.query(InventoryTransaction)
        .filter(InventoryTransaction.sku_id == sku_id, InventoryTransaction.transaction_type == t)
        .count()
        for t in TransactionType
    }
    return {
        "inventory_total": inventory.total_quantity,
        "inventory_reserved": inventory.reserved_quantity,
        "batch_remaining": batch.remaining_quantity,
        "batch_reserved": batch.reserved_quantity,
        "order_item_batch_count": order_item_batch_count,
        "transaction_counts": transaction_counts,
    }


def _order_item_batches(db, order_id):
    return (
        db.query(OrderItemBatch)
        .join(OrderItem, OrderItem.id == OrderItemBatch.order_item_id)
        .filter(OrderItem.order_id == order_id)
        .all()
    )


def _place_order(db, ids, quantity=4):
    return create_outgoing_order(
        db,
        OrderCreate(
            retailer_id=ids["retailer_id"],
            warehouse_id=ids["warehouse_id"],
            items=[OrderItemCreate(sku_id=ids["sku_id"], quantity=quantity, unit_price=100, discount_amount=0)],
        ),
        ids["admin"],
    )


def _ready_to_ship(db, order, driver, current_user):
    order.delivery_driver_id = driver.id
    db.commit()
    update_order_status(db, order.id, StatusUpdate(status="READY_TO_SHIP"), current_user)
    db.refresh(order)


def _dispatch(db, order, current_user):
    update_order_status(db, order.id, StatusUpdate(status="OUT_FOR_DELIVERY"), current_user)
    db.refresh(order)


def _deliver(db, order, current_user):
    update_order_status(db, order.id, StatusUpdate(status="DELIVERED"), current_user)
    db.refresh(order)


def _cancel(db, order, current_user):
    update_order_status(db, order.id, StatusUpdate(status="CANCELLED"), current_user)
    db.refresh(order)


def test_reserve_on_order_creation(db):
    """Correction to CONCERNS.md:190: the OrderItemBatch row is written right here, at
    order creation, not at PENDING -> READY_TO_SHIP."""
    ids = _seed(db, batch_quantity=10)
    order = _place_order(db, ids, quantity=4)

    inventory = db.query(Inventory).filter(Inventory.sku_id == ids["sku_id"]).first()
    batch = db.query(SKUBatch).filter(SKUBatch.id == ids["batch_id"]).first()
    assert inventory.reserved_quantity == 4
    assert batch.reserved_quantity == 4
    assert inventory.total_quantity == 10
    assert batch.remaining_quantity == 10

    rows = _order_item_batches(db, order.id)
    assert len(rows) == 1
    assert rows[0].quantity == 4
    assert db.query(InventoryTransaction).filter(InventoryTransaction.sku_id == ids["sku_id"]).count() == 0


def test_ready_to_ship_moves_no_inventory(db):
    """The other half of the CONCERNS.md:190 correction: this transition is a status write
    and a delivery-driver check, nothing more."""
    ids = _seed(db, batch_quantity=10)
    order = _place_order(db, ids, quantity=4)

    before = _snapshot(db, ids["sku_id"], ids["warehouse_id"], order.id)
    _ready_to_ship(db, order, ids["driver"], ids["admin"])
    after = _snapshot(db, ids["sku_id"], ids["warehouse_id"], order.id)
    assert before == after


def test_dispatch_converts_reservation_into_a_shipment(db):
    ids = _seed(db, batch_quantity=10)
    order = _place_order(db, ids, quantity=4)
    _ready_to_ship(db, order, ids["driver"], ids["admin"])
    _dispatch(db, order, ids["admin"])

    inventory = db.query(Inventory).filter(Inventory.sku_id == ids["sku_id"]).first()
    batch = db.query(SKUBatch).filter(SKUBatch.id == ids["batch_id"]).first()
    assert inventory.reserved_quantity == 0
    assert batch.reserved_quantity == 0
    assert inventory.total_quantity == 6
    assert batch.remaining_quantity == 6

    out_txns = (
        db.query(InventoryTransaction)
        .filter(InventoryTransaction.sku_id == ids["sku_id"], InventoryTransaction.transaction_type == TransactionType.OUT)
        .all()
    )
    assert len(out_txns) == 1
    assert out_txns[0].quantity == 4

    rows = _order_item_batches(db, order.id)
    assert len(rows) == 1, "dispatch must not touch the OrderItemBatch rows"


def test_delivery_moves_no_inventory(db):
    ids = _seed(db, batch_quantity=10)
    order = _place_order(db, ids, quantity=4)
    _ready_to_ship(db, order, ids["driver"], ids["admin"])
    _dispatch(db, order, ids["admin"])

    before = _snapshot(db, ids["sku_id"], ids["warehouse_id"], order.id)
    _deliver(db, order, ids["admin"])
    after = _snapshot(db, ids["sku_id"], ids["warehouse_id"], order.id)
    assert before == after


def test_release_from_pending_returns_the_reservation_and_drops_the_allocation_rows(db):
    ids = _seed(db, batch_quantity=10)
    order = _place_order(db, ids, quantity=4)
    _cancel(db, order, ids["admin"])

    inventory = db.query(Inventory).filter(Inventory.sku_id == ids["sku_id"]).first()
    batch = db.query(SKUBatch).filter(SKUBatch.id == ids["batch_id"]).first()
    assert inventory.reserved_quantity == 0
    assert batch.reserved_quantity == 0
    assert inventory.total_quantity == 10
    assert batch.remaining_quantity == 10

    assert len(_order_item_batches(db, order.id)) == 0
    assert db.query(InventoryTransaction).filter(InventoryTransaction.sku_id == ids["sku_id"]).count() == 0


def test_release_from_ready_to_ship_behaves_identically(db):
    ids = _seed(db, batch_quantity=10)
    order = _place_order(db, ids, quantity=4)
    _ready_to_ship(db, order, ids["driver"], ids["admin"])
    _cancel(db, order, ids["admin"])

    inventory = db.query(Inventory).filter(Inventory.sku_id == ids["sku_id"]).first()
    batch = db.query(SKUBatch).filter(SKUBatch.id == ids["batch_id"]).first()
    assert inventory.reserved_quantity == 0
    assert batch.reserved_quantity == 0
    assert inventory.total_quantity == 10
    assert batch.remaining_quantity == 10

    assert len(_order_item_batches(db, order.id)) == 0
    assert db.query(InventoryTransaction).filter(InventoryTransaction.sku_id == ids["sku_id"]).count() == 0


def test_restore_from_out_for_delivery_returns_the_goods_and_keeps_the_allocation_rows(db):
    ids = _seed(db, batch_quantity=10)
    order = _place_order(db, ids, quantity=4)
    _ready_to_ship(db, order, ids["driver"], ids["admin"])
    _dispatch(db, order, ids["admin"])
    _cancel(db, order, ids["admin"])

    inventory = db.query(Inventory).filter(Inventory.sku_id == ids["sku_id"]).first()
    batch = db.query(SKUBatch).filter(SKUBatch.id == ids["batch_id"]).first()
    assert inventory.total_quantity == 10
    assert batch.remaining_quantity == 10
    # Dispatch already zeroed reserved_quantity; restore must not re-inflate it.
    assert inventory.reserved_quantity == 0
    assert batch.reserved_quantity == 0

    return_txns = (
        db.query(InventoryTransaction)
        .filter(InventoryTransaction.sku_id == ids["sku_id"], InventoryTransaction.transaction_type == TransactionType.RETURN)
        .all()
    )
    assert len(return_txns) == 1
    assert return_txns[0].quantity == 4

    rows = _order_item_batches(db, order.id)
    assert len(rows) == 1, "restore must keep its OrderItemBatch rows — they record a real shipment"


def test_restore_from_delivered_behaves_identically(db):
    ids = _seed(db, batch_quantity=10)
    order = _place_order(db, ids, quantity=4)
    _ready_to_ship(db, order, ids["driver"], ids["admin"])
    _dispatch(db, order, ids["admin"])
    _deliver(db, order, ids["admin"])
    _cancel(db, order, ids["admin"])

    inventory = db.query(Inventory).filter(Inventory.sku_id == ids["sku_id"]).first()
    batch = db.query(SKUBatch).filter(SKUBatch.id == ids["batch_id"]).first()
    assert inventory.total_quantity == 10
    assert batch.remaining_quantity == 10
    assert inventory.reserved_quantity == 0
    assert batch.reserved_quantity == 0

    return_txns = (
        db.query(InventoryTransaction)
        .filter(InventoryTransaction.sku_id == ids["sku_id"], InventoryTransaction.transaction_type == TransactionType.RETURN)
        .all()
    )
    assert len(return_txns) == 1
    assert return_txns[0].quantity == 4

    rows = _order_item_batches(db, order.id)
    assert len(rows) == 1


def test_release_and_restore_treat_allocation_rows_differently_on_purpose(db):
    """The verdict on CONTEXT finding 5, made executable. A dropped reservation describes
    nothing that ever happened, so release deletes its rows. A returned shipment describes
    a movement that really happened, and its InventoryTransaction(RETURN) references the
    same batch_id, so restore keeps its rows as the audit trail of that movement. This test
    exists so a future "harmonisation" of the two paths turns red instead of silently
    destroying that trail."""
    released_ids = _seed(db, batch_quantity=10)
    released_order = _place_order(db, released_ids, quantity=4)
    _cancel(db, released_order, released_ids["admin"])

    restored_ids = _seed(db, batch_quantity=10)
    restored_order = _place_order(db, restored_ids, quantity=4)
    _ready_to_ship(db, restored_order, restored_ids["driver"], restored_ids["admin"])
    _dispatch(db, restored_order, restored_ids["admin"])
    _cancel(db, restored_order, restored_ids["admin"])

    assert len(_order_item_batches(db, released_order.id)) == 0, (
        "release: a dropped reservation describes nothing — the rows must be gone"
    )
    assert len(_order_item_batches(db, restored_order.id)) == 1, (
        "restore: the goods really moved — the allocation row is the audit trail and must survive"
    )


def test_non_outgoing_order_is_a_no_op_for_dispatch_release_and_restore(db):
    """`_is_outgoing_order` is the guard on all three post-reservation functions. Pin it
    directly against the BRAND -> WAREHOUSE shape `add_inventory_receipt` creates."""
    ids = _seed(db, batch_quantity=10)
    order = Order(
        from_entity_type="BRAND",
        from_entity_id=ids["brand_id"],
        to_entity_type="WAREHOUSE",
        to_entity_id=ids["warehouse_id"],
        status=OrderStatus.DELIVERED,
    )
    db.add(order)
    db.flush()
    item = OrderItem(order_id=order.id, sku_id=ids["sku_id"], quantity=4, unit_price=100, discount_amount=0)
    db.add(item)
    db.commit()
    db.refresh(order)

    before = _snapshot(db, ids["sku_id"], ids["warehouse_id"], order.id)
    _dispatch_reserved_inventory(db, order)
    _release_reserved_inventory(db, order)
    _restore_dispatched_inventory(db, order)
    after = _snapshot(db, ids["sku_id"], ids["warehouse_id"], order.id)
    assert before == after


def test_dispatch_reentry_does_not_duplicate_allocation_rows(db):
    """_dispatch_reserved_inventory re-enters _reserve_inventory_for_order at :177. For a
    fully-allocated order that re-entry's top-up branch never fires (allocated_qty already
    equals item.quantity), so the OrderItemBatch row count must not change. The top-up
    branch itself is the open question behind #todo INVT-04b (D2) and is deliberately not
    exercised here."""
    ids = _seed(db, batch_quantity=10)
    order = _place_order(db, ids, quantity=4)
    before_count = len(_order_item_batches(db, order.id))

    _ready_to_ship(db, order, ids["driver"], ids["admin"])
    _dispatch(db, order, ids["admin"])
    after_count = len(_order_item_batches(db, order.id))

    assert before_count == 1
    assert after_count == 1
