"""Proves the *model* half of D4 (INVT-02): the CHECK constraints declared in
`__table_args__` on Inventory, SKUBatch and OrderItemBatch are picked up by
`Base.metadata.create_all` and are enforced by SQLite — a real database rejection, not an
application-level guard. Each test constructs a row that violates exactly one named
constraint and asserts the commit raises `IntegrityError` naming that constraint.

`test_inventory_constraints_pg.py` proves the migration half of the same guarantee against
real PostgreSQL: that `alembic upgrade head` actually installed the same eight constraints,
independent of whatever `create_all` builds for the test suite.

Also settles INVT-04 (D3): `inventory`'s composite primary key is asserted as already
present, not re-added. And pins D2 / #todo INVT-04b: `order_item_batches` deliberately
carries no uniqueness over (order_item_id, batch_id).
"""

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Brand, Warehouse, SKU, SKUBatch, Inventory, Order, OrderItem, OrderItemBatch
from app.models.enums import OrderStatus


def _seed(db):
    """Valid foreign keys for every violating row below — otherwise a foreign-key failure
    would fire before the CHECK constraint under test, and prove the wrong thing."""
    brand = Brand(name="Brand")
    db.add(brand)
    db.flush()
    warehouse = Warehouse(name="Warehouse", state="Delhi")
    db.add(warehouse)
    db.flush()
    sku = SKU(name="SKU", brand_id=brand.id)
    db.add(sku)
    db.flush()
    return brand.id, warehouse.id, sku.id


def _seed_order_item(db, warehouse_id, sku_id):
    """Extra fixture only the order_item_batches case needs: a real Order + OrderItem for
    OrderItemBatch.order_item_id to point at."""
    order = Order(
        from_entity_type="WAREHOUSE",
        from_entity_id=warehouse_id,
        to_entity_type="RETAILER",
        to_entity_id=1,
        status=OrderStatus.PENDING,
    )
    db.add(order)
    db.flush()
    item = OrderItem(order_id=order.id, sku_id=sku_id, quantity=1, unit_price=10)
    db.add(item)
    db.flush()
    return item.id


def _seed_batch(db, warehouse_id, sku_id, quantity_received=5, remaining_quantity=5, reserved_quantity=0):
    batch = SKUBatch(
        sku_id=sku_id,
        warehouse_id=warehouse_id,
        quantity_received=quantity_received,
        remaining_quantity=remaining_quantity,
        reserved_quantity=reserved_quantity,
    )
    db.add(batch)
    db.flush()
    return batch.id


def test_inventory_total_quantity_non_negative_is_rejected(db):
    _, warehouse_id, sku_id = _seed(db)
    db.add(Inventory(sku_id=sku_id, warehouse_id=warehouse_id, total_quantity=-1, reserved_quantity=0))
    with pytest.raises(IntegrityError) as exc:
        db.commit()
    assert "ck_inventory_total_quantity_non_negative" in str(exc.value)
    db.rollback()


def test_inventory_reserved_quantity_non_negative_is_rejected(db):
    _, warehouse_id, sku_id = _seed(db)
    db.add(Inventory(sku_id=sku_id, warehouse_id=warehouse_id, total_quantity=5, reserved_quantity=-1))
    with pytest.raises(IntegrityError) as exc:
        db.commit()
    assert "ck_inventory_reserved_quantity_non_negative" in str(exc.value)
    db.rollback()


def test_inventory_reserved_not_over_total_is_rejected(db):
    _, warehouse_id, sku_id = _seed(db)
    db.add(Inventory(sku_id=sku_id, warehouse_id=warehouse_id, total_quantity=5, reserved_quantity=6))
    with pytest.raises(IntegrityError) as exc:
        db.commit()
    assert "ck_inventory_reserved_not_over_total" in str(exc.value)
    db.rollback()


def test_sku_batches_quantity_received_non_negative_is_rejected(db):
    _, warehouse_id, sku_id = _seed(db)
    db.add(SKUBatch(sku_id=sku_id, warehouse_id=warehouse_id, quantity_received=-1, remaining_quantity=0, reserved_quantity=0))
    with pytest.raises(IntegrityError) as exc:
        db.commit()
    assert "ck_sku_batches_quantity_received_non_negative" in str(exc.value)
    db.rollback()


def test_sku_batches_remaining_quantity_non_negative_is_rejected(db):
    _, warehouse_id, sku_id = _seed(db)
    db.add(SKUBatch(sku_id=sku_id, warehouse_id=warehouse_id, quantity_received=5, remaining_quantity=-1, reserved_quantity=0))
    with pytest.raises(IntegrityError) as exc:
        db.commit()
    assert "ck_sku_batches_remaining_quantity_non_negative" in str(exc.value)
    db.rollback()


def test_sku_batches_reserved_quantity_non_negative_is_rejected(db):
    _, warehouse_id, sku_id = _seed(db)
    db.add(SKUBatch(sku_id=sku_id, warehouse_id=warehouse_id, quantity_received=5, remaining_quantity=5, reserved_quantity=-1))
    with pytest.raises(IntegrityError) as exc:
        db.commit()
    assert "ck_sku_batches_reserved_quantity_non_negative" in str(exc.value)
    db.rollback()


def test_sku_batches_reserved_not_over_remaining_is_rejected(db):
    _, warehouse_id, sku_id = _seed(db)
    db.add(SKUBatch(sku_id=sku_id, warehouse_id=warehouse_id, quantity_received=5, remaining_quantity=5, reserved_quantity=6))
    with pytest.raises(IntegrityError) as exc:
        db.commit()
    assert "ck_sku_batches_reserved_not_over_remaining" in str(exc.value)
    db.rollback()


def test_order_item_batches_quantity_positive_is_rejected(db):
    _, warehouse_id, sku_id = _seed(db)
    order_item_id = _seed_order_item(db, warehouse_id, sku_id)
    batch_id = _seed_batch(db, warehouse_id, sku_id)
    db.add(OrderItemBatch(order_item_id=order_item_id, batch_id=batch_id, quantity=0))
    with pytest.raises(IntegrityError) as exc:
        db.commit()
    assert "ck_order_item_batches_quantity_positive" in str(exc.value)
    db.rollback()


def test_boundary_values_are_accepted(db):
    """The constraints are inclusive at the boundary, not strict. reserved == total is what
    _dispatch_reserved_inventory legitimately produces when it reserves the whole of a
    batch; all-zero is the resting state of a freshly created row. A future reader tightening
    these to strict `<` would break real dispatch arithmetic — this test pins that they must
    not."""
    _, warehouse_id, sku_id = _seed(db)
    db.add(Inventory(sku_id=sku_id, warehouse_id=warehouse_id, total_quantity=5, reserved_quantity=5))
    db.add(SKUBatch(sku_id=sku_id, warehouse_id=warehouse_id, quantity_received=0, remaining_quantity=0, reserved_quantity=0))
    db.commit()


def test_inventory_has_composite_primary_key(db):
    """INVT-04, D3: CONCERNS.md:278 listed inventory's composite uniqueness as missing. It
    was wrong — `inventory` has carried PRIMARY KEY (sku_id, warehouse_id) all along,
    confirmed directly in pg_constraint as `inventory_pkey` at planning time. This
    requirement closes on the assertion below; nothing is added."""
    assert {c.name for c in Inventory.__table__.primary_key.columns} == {"sku_id", "warehouse_id"}


def test_order_item_batches_pair_uniqueness_is_deliberately_absent(db):
    """D2 / #todo INVT-04b: (order_item_id, batch_id) uniqueness on order_item_batches is a
    user decision to defer, made 2026-08-18, tracked in REQUIREMENTS.md Deferred. Adding it
    requires first proving that the reserve-then-dispatch re-entry path
    (_dispatch_reserved_inventory calling _reserve_inventory_for_order a second time,
    app/services/order.py:177) never legitimately tops up an existing pair. This test pins
    the absence so that adding the constraint later is a deliberate act that turns this test
    red, not a drive-by change made while working on something else."""
    table = OrderItemBatch.__table__
    pair = {"order_item_id", "batch_id"}
    assert not any(
        {c.name for c in uc.columns} == pair for uc in table.constraints
        if uc.__class__.__name__ == "UniqueConstraint"
    )
    assert not any(
        {c.name for c in idx.columns} == pair and idx.unique for idx in table.indexes
    )
