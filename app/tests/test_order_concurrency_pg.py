"""PostgreSQL-only concurrency test for ORD-03 — the double-decrement race on
`_restore_dispatched_inventory` (`app/services/order.py:218-238`).

Why this module exists and cannot be a normal SQLite test: the default suite runs against
SQLite in-memory through a single shared `StaticPool` connection (`app/tests/conftest.py`).
SQLAlchemy's SQLite dialect emits no `FOR UPDATE`, and a single shared connection cannot
express two independent transactions blocking on each other. A race that cannot race is not
evidence of anything. This module opens two genuinely independent connections to the real
dev PostgreSQL database, releases two threads together with a `threading.Barrier`, and lets
them actually contend for the same row.

ORD-03 was originally worded "cancel + return"; `RETURNED` is unreachable after D1
(03-CONTEXT.md), so the race this module proves is cancel-vs-cancel and cancel-vs-deliver,
both from `OUT_FOR_DELIVERY` — see 03-CONTEXT.md's "Requirement Note — ORD-03 needs
rewording".

D5 fence: this module never calls `update_order_status(..., "OUT_FOR_DELIVERY")`, because
that path calls `issue_invoice_for_order`, which mints a permanent, undeletable `ISSUED`
invoice row (enforced by an ORM guard and a DB trigger — no bypass) and burns an
`invoice_number_seq` value, every single run. Instead this module reaches
`OUT_FOR_DELIVERY` by calling `_dispatch_reserved_inventory` directly and setting
`order.status`, which reproduces the exact race precondition — dispatched stock,
`order_item_batches` rows, status `OUT_FOR_DELIVERY` — with no invoice anywhere near it.

If PostgreSQL is unreachable, this module fails loudly by default (see `_connect_or_skip`
in `pg_utils`-pattern below, copied from `test_invoice_sequence_pg.py`). A silently skipping
concurrency test is a green tick over an unproven guarantee (T-03-17). Set
ASCEND_ALLOW_PG_SKIP=1 to allow a skip instead.
"""

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.tests.pg_utils import pg_engine
from app.models import (
    Brand,
    Employee,
    Inventory,
    InventoryTransaction,
    Order,
    OrderTrail,
    Retailer,
    SKU,
    SKUBatch,
    User,
    Warehouse,
)
from app.models.enums import EmployeeRole, OrderStatus, TransactionType
from app.schemas.order import OrderCreate, OrderItemCreate, StatusUpdate
from app.services.order import _dispatch_reserved_inventory, create_outgoing_order, update_order_status


def _connect_or_skip():
    engine = pg_engine()
    try:
        conn = engine.connect()
        conn.close()
    except Exception:
        if os.getenv("ASCEND_ALLOW_PG_SKIP") == "1":
            pytest.skip("PostgreSQL unreachable and ASCEND_ALLOW_PG_SKIP=1")
        raise
    return engine


@pytest.fixture(scope="module")
def engine():
    return _connect_or_skip()


@pytest.fixture(scope="module")
def SessionLocal(engine):
    # Each thread opens its own Session on its own connection (NullPool, see pg_utils.py) —
    # a shared/pooled connection would serialize the threads for us and defeat the point of
    # this module entirely.
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _seed_dispatched_order(SessionLocal, quantity=5):
    """Create a real Brand/Warehouse/Retailer/SKU/driver/admin, a PENDING order for
    `quantity` units, then dispatch it WITHOUT going through `update_order_status` (D5 —
    see module docstring). Returns a plain dict of ids plus the post-dispatch baselines,
    asserted here so a seeding mistake fails as a seeding mistake, not as a phantom race
    result.
    """
    suffix = uuid.uuid4().hex[:8]
    session = SessionLocal()
    try:
        brand = Brand(name=f"Concurrency Brand {suffix}")
        warehouse = Warehouse(name=f"Concurrency WH {suffix}", location="Delhi", state="Delhi")
        retailer = Retailer(name=f"Concurrency Retailer {suffix}", state="Delhi")
        session.add_all([brand, warehouse, retailer])
        session.commit()

        sku = SKU(name=f"Concurrency SKU {suffix}", brand_id=brand.id)
        session.add(sku)
        session.commit()

        batch = SKUBatch(
            sku_id=sku.id,
            warehouse_id=warehouse.id,
            quantity_received=50,
            remaining_quantity=50,
        )
        inventory = Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=50)
        driver = Employee(
            name=f"Concurrency Driver {suffix}",
            email=f"concurrency-driver-{suffix}@ascend.com",
            role=EmployeeRole.DRIVER,
        )
        admin = User(
            email=f"concurrency-admin-{suffix}@ascend.com",
            password_hash="x",
            role=EmployeeRole.ADMIN,
        )
        session.add_all([batch, inventory, driver, admin])
        session.commit()

        order = create_outgoing_order(
            session,
            OrderCreate(
                retailer_id=retailer.id,
                warehouse_id=warehouse.id,
                items=[OrderItemCreate(sku_id=sku.id, quantity=quantity, unit_price=100, discount_amount=0)],
            ),
            admin,
        )

        order.delivery_driver_id = driver.id
        session.commit()

        _dispatch_reserved_inventory(session, order)
        order.status = OrderStatus.OUT_FOR_DELIVERY
        session.commit()

        session.refresh(inventory)
        session.refresh(batch)
        assert inventory.total_quantity == 45, "seeding baseline: dispatch must drop total_quantity to 45"
        assert batch.remaining_quantity == 45, "seeding baseline: dispatch must drop remaining_quantity to 45"

        return {
            "order_id": order.id,
            "sku_id": sku.id,
            "warehouse_id": warehouse.id,
            "batch_id": batch.id,
            "admin_id": admin.id,
            "driver_id": driver.id,
            "retailer_id": retailer.id,
            "brand_id": brand.id,
            "dispatched_quantity": quantity,
        }
    finally:
        session.close()


def _purge(SessionLocal, ids):
    """Delete every row this module seeded, by id, in FK-safe order. Each table gets its own
    DELETE so a missing ORM cascade cannot leave an orphan. Run from a try/finally so it
    fires on failure too — a leaked order can wedge a later `alembic upgrade` (Phase 2's
    verification)."""
    order_id = ids["order_id"]
    session = SessionLocal()
    try:
        session.execute(
            text("DELETE FROM order_trails WHERE order_id = :order_id"), {"order_id": order_id}
        )
        session.execute(
            text(
                "DELETE FROM order_item_batches WHERE order_item_id IN "
                "(SELECT id FROM order_items WHERE order_id = :order_id)"
            ),
            {"order_id": order_id},
        )
        session.execute(
            text(
                "DELETE FROM order_item_taxes WHERE order_item_id IN "
                "(SELECT id FROM order_items WHERE order_id = :order_id)"
            ),
            {"order_id": order_id},
        )
        session.execute(text("DELETE FROM order_items WHERE order_id = :order_id"), {"order_id": order_id})
        session.execute(
            text("DELETE FROM inventory_transactions WHERE sku_id = :sku_id"), {"sku_id": ids["sku_id"]}
        )
        session.execute(text("DELETE FROM orders WHERE id = :order_id"), {"order_id": order_id})
        session.execute(text("DELETE FROM inventory WHERE sku_id = :sku_id"), {"sku_id": ids["sku_id"]})
        session.execute(text("DELETE FROM sku_batches WHERE sku_id = :sku_id"), {"sku_id": ids["sku_id"]})
        session.execute(text("DELETE FROM skus WHERE id = :sku_id"), {"sku_id": ids["sku_id"]})
        session.execute(text("DELETE FROM users WHERE id = :admin_id"), {"admin_id": ids["admin_id"]})
        session.execute(text("DELETE FROM employees WHERE id = :driver_id"), {"driver_id": ids["driver_id"]})
        session.execute(text("DELETE FROM retailers WHERE id = :retailer_id"), {"retailer_id": ids["retailer_id"]})
        session.execute(text("DELETE FROM warehouses WHERE id = :warehouse_id"), {"warehouse_id": ids["warehouse_id"]})
        session.execute(text("DELETE FROM brands WHERE id = :brand_id"), {"brand_id": ids["brand_id"]})
        session.commit()

        order_count = session.execute(
            text("SELECT count(*) FROM orders WHERE id = :order_id"), {"order_id": order_id}
        ).scalar()
        invoice_count = session.execute(
            text("SELECT count(*) FROM invoices WHERE order_id = :order_id"), {"order_id": order_id}
        ).scalar()
        assert order_count == 0, "teardown must leave no leaked order row"
        # D5 guard: proves this run minted nothing undeletable.
        assert invoice_count == 0, "this module must never mint an invoice (D5)"
    finally:
        session.close()


def test_two_concurrent_cancels_restore_inventory_once(SessionLocal):
    """T-03-11 / T-03-12: two genuinely concurrent cancels of one OUT_FOR_DELIVERY order
    must restore inventory exactly once, not twice."""
    ids = _seed_dispatched_order(SessionLocal)
    try:
        session_a = SessionLocal()
        session_b = SessionLocal()
        try:
            admin_a = session_a.query(User).filter(User.id == ids["admin_id"]).first()
            admin_b = session_b.query(User).filter(User.id == ids["admin_id"]).first()

            barrier = threading.Barrier(2)

            def worker(session, admin):
                barrier.wait()
                try:
                    update_order_status(
                        session, ids["order_id"], StatusUpdate(status="CANCELLED"), admin
                    )
                    return None
                except Exception as exc:  # noqa: BLE001 - collected, not raised, from the thread
                    return exc

            with ThreadPoolExecutor(max_workers=2) as pool:
                future_a = pool.submit(worker, session_a, admin_a)
                future_b = pool.submit(worker, session_b, admin_b)
                outcome_a = future_a.result()
                outcome_b = future_b.result()
        finally:
            session_a.close()
            session_b.close()

        outcomes = [outcome_a, outcome_b]
        successes = [o for o in outcomes if o is None]
        failures = [o for o in outcomes if o is not None]
        assert len(successes) == 1, f"exactly one worker should succeed, got outcomes: {outcomes}"
        assert len(failures) == 1, f"exactly one worker should fail, got outcomes: {outcomes}"
        assert isinstance(failures[0], ValueError)
        assert str(failures[0]) == "Invalid status transition"

        verify = SessionLocal()
        try:
            order = verify.query(Order).filter(Order.id == ids["order_id"]).first()
            inventory = (
                verify.query(Inventory)
                .filter(Inventory.sku_id == ids["sku_id"], Inventory.warehouse_id == ids["warehouse_id"])
                .first()
            )
            batch = verify.query(SKUBatch).filter(SKUBatch.id == ids["batch_id"]).first()
            returns = (
                verify.query(InventoryTransaction)
                .filter(
                    InventoryTransaction.sku_id == ids["sku_id"],
                    InventoryTransaction.warehouse_id == ids["warehouse_id"],
                    InventoryTransaction.transaction_type == TransactionType.RETURN,
                )
                .all()
            )
            cancelled_trails = (
                verify.query(OrderTrail)
                .filter(OrderTrail.order_id == ids["order_id"], OrderTrail.order_status == OrderStatus.CANCELLED)
                .all()
            )

            assert order.status is OrderStatus.CANCELLED
            assert inventory.total_quantity == 50, (
                f"expected 45 + {ids['dispatched_quantity']} == 50 (one restoration), "
                f"got {inventory.total_quantity}"
            )
            assert batch.remaining_quantity == 50
            assert len(returns) == 1, f"expected exactly one RETURN transaction, got {len(returns)}"
            assert returns[0].quantity == ids["dispatched_quantity"]
            assert len(cancelled_trails) == 1
        finally:
            verify.close()
    finally:
        _purge(SessionLocal, ids)


def test_cancel_racing_deliver_adjusts_inventory_once(SessionLocal):
    """T-03-11 / T-03-12: cancel racing deliver, both from OUT_FOR_DELIVERY. The outcome is
    genuinely non-deterministic and both orderings are valid — asserting a specific winner
    would make this test flaky, which is worse than not having it at all:

    - If CANCELLED commits first, DELIVERED's locked re-read sees CANCELLED, which has no
      legal successor (D1), so it raises ValueError("Invalid status transition"). Final
      status is CANCELLED; one restoration happened.
    - If DELIVERED commits first, CANCELLED's locked re-read sees DELIVERED. Per D2,
      DELIVERED -> CANCELLED is legal (admin only), so the cancel succeeds and restores
      stock once.

    Either ordering: final status is CANCELLED, inventory is adjusted exactly once, and at
    most one worker raises. Do not tighten these assertions to pin an ordering — that would
    make a correct, order-independent guarantee look flaky.
    """
    ids = _seed_dispatched_order(SessionLocal)
    try:
        session_a = SessionLocal()
        session_b = SessionLocal()
        try:
            admin_a = session_a.query(User).filter(User.id == ids["admin_id"]).first()
            admin_b = session_b.query(User).filter(User.id == ids["admin_id"]).first()

            barrier = threading.Barrier(2)

            def worker(session, admin, target_status):
                barrier.wait()
                try:
                    update_order_status(
                        session, ids["order_id"], StatusUpdate(status=target_status), admin
                    )
                    return None
                except Exception as exc:  # noqa: BLE001 - collected, not raised, from the thread
                    return exc

            with ThreadPoolExecutor(max_workers=2) as pool:
                future_cancel = pool.submit(worker, session_a, admin_a, "CANCELLED")
                future_deliver = pool.submit(worker, session_b, admin_b, "DELIVERED")
                outcome_cancel = future_cancel.result()
                outcome_deliver = future_deliver.result()
        finally:
            session_a.close()
            session_b.close()

        outcomes = [outcome_cancel, outcome_deliver]
        failures = [o for o in outcomes if o is not None]
        assert len(failures) <= 1, f"at most one worker should fail, got outcomes: {outcomes}"
        for failure in failures:
            assert isinstance(failure, ValueError)
            assert str(failure) == "Invalid status transition"

        verify = SessionLocal()
        try:
            order = verify.query(Order).filter(Order.id == ids["order_id"]).first()
            inventory = (
                verify.query(Inventory)
                .filter(Inventory.sku_id == ids["sku_id"], Inventory.warehouse_id == ids["warehouse_id"])
                .first()
            )
            batch = verify.query(SKUBatch).filter(SKUBatch.id == ids["batch_id"]).first()
            returns = (
                verify.query(InventoryTransaction)
                .filter(
                    InventoryTransaction.sku_id == ids["sku_id"],
                    InventoryTransaction.warehouse_id == ids["warehouse_id"],
                    InventoryTransaction.transaction_type == TransactionType.RETURN,
                )
                .all()
            )

            # Both orderings land here: DELIVERED->CANCELLED is legal for admin (D2), and
            # CANCELLED beats DELIVERED outright when it commits first.
            assert order.status is OrderStatus.CANCELLED
            assert inventory.total_quantity == 50, (
                f"expected exactly one net adjustment back to 50, got {inventory.total_quantity}"
            )
            assert batch.remaining_quantity == 50
            assert len(returns) == 1, f"expected exactly one RETURN transaction, got {len(returns)}"
        finally:
            verify.close()
    finally:
        _purge(SessionLocal, ids)
