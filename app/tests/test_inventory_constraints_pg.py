"""PostgreSQL-only proof of the migration half of D4 (INVT-02): that `alembic upgrade
head` actually installed the eight CHECK constraints into the real database, and that real
PostgreSQL rejects raw-SQL writes that violate them — independent of the ORM and of
whatever `Base.metadata.create_all` builds for the SQLite suite.

`test_inventory_constraints.py` proves the model half (the schema `create_all` builds,
which the whole suite runs against). This module closes the gap that module cannot: a
migration with a typo in a table name, or one that silently no-ops, would leave the
production path unprotected while the SQLite suite stays green. Task 3 of 04-01-PLAN.md
asserts the eight names directly in `pg_constraint`, then proves rejection against raw SQL.

If the dev PostgreSQL database is unreachable, these tests fail loudly by default. Set
ASCEND_ALLOW_PG_SKIP=1 to allow a skip instead — a silently-skipping test is not evidence.
"""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.tests.pg_utils import pg_engine

_CONSTRAINT_NAMES = {
    "ck_inventory_total_quantity_non_negative",
    "ck_inventory_reserved_quantity_non_negative",
    "ck_inventory_reserved_not_over_total",
    "ck_sku_batches_quantity_received_non_negative",
    "ck_sku_batches_remaining_quantity_non_negative",
    "ck_sku_batches_reserved_quantity_non_negative",
    "ck_sku_batches_reserved_not_over_remaining",
    "ck_order_item_batches_quantity_positive",
}


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


def test_check_constraints_exist_in_pg_catalog(engine):
    """Did the migration actually run? Independent of the ORM entirely — reads the
    PostgreSQL system catalog directly."""
    with engine.connect() as conn:
        have = {
            row[0]
            for row in conn.execute(
                text(
                    "select conname from pg_constraint where contype = 'c' "
                    "and conrelid in ("
                    "'inventory'::regclass, 'sku_batches'::regclass, 'order_item_batches'::regclass"
                    ")"
                )
            )
        }
    missing = _CONSTRAINT_NAMES - have
    assert not missing, f"migration did not install: {', '.join(sorted(missing))}"


def test_postgres_rejects_violating_writes(engine):
    """Raw SQL, real PostgreSQL, nothing left behind. One outer transaction rolled back in
    a finally so the dev database ends at the same row counts it started with. Each
    violating statement runs inside its own SAVEPOINT — PostgreSQL aborts the surrounding
    transaction on any error, so without per-statement savepoints only the first case here
    would be testable."""
    conn = engine.connect()
    trans = conn.begin()
    try:
        warehouse_id = conn.execute(text("select id from warehouses limit 1")).scalar()
        assert warehouse_id is not None, "seed data expected: at least one warehouse"

        brand_id = conn.execute(
            text("INSERT INTO brands (name) VALUES ('PG Constraint Test Brand') RETURNING id")
        ).scalar()
        sku_id = conn.execute(
            text("INSERT INTO skus (name, brand_id) VALUES ('PG Constraint Test SKU', :brand_id) RETURNING id"),
            {"brand_id": brand_id},
        ).scalar()

        # inventory: negative total_quantity. Note: reserved_quantity <= total_quantity and
        # reserved_quantity >= 0 together already imply total_quantity >= 0, so any row
        # with a negative total necessarily double-violates — there is no (total, reserved)
        # pair that fails *only* ck_inventory_total_quantity_non_negative. Confirmed by
        # direct experiment against this database: PostgreSQL evaluates its violated CHECK
        # constraints in an order that is not declaration order, so which of the two
        # simultaneously-violated names comes back depends on the row's exact values.
        # reserved_quantity=-5 keeps reserved_quantity <= total_quantity satisfied (both
        # negative), isolating the failure to the two non-negativity constraints.
        savepoint = conn.begin_nested()
        with pytest.raises(IntegrityError) as exc:
            conn.execute(
                text(
                    "INSERT INTO inventory (sku_id, warehouse_id, total_quantity, reserved_quantity) "
                    "VALUES (:sku_id, :warehouse_id, -1, -5)"
                ),
                {"sku_id": sku_id, "warehouse_id": warehouse_id},
            )
        savepoint.rollback()
        assert (
            "ck_inventory_total_quantity_non_negative" in str(exc.value)
            or "ck_inventory_reserved_quantity_non_negative" in str(exc.value)
        )

        # inventory: reserved above total
        savepoint = conn.begin_nested()
        with pytest.raises(IntegrityError) as exc:
            conn.execute(
                text(
                    "INSERT INTO inventory (sku_id, warehouse_id, total_quantity, reserved_quantity) "
                    "VALUES (:sku_id, :warehouse_id, 5, 6)"
                ),
                {"sku_id": sku_id, "warehouse_id": warehouse_id},
            )
        savepoint.rollback()
        assert "ck_inventory_reserved_not_over_total" in str(exc.value)

        # sku_batches: negative remaining_quantity. Same redundancy as inventory above
        # (reserved <= remaining and reserved >= 0 imply remaining >= 0), so
        # reserved_quantity=-5 keeps reserved <= remaining satisfied and isolates the
        # failure to the two non-negativity constraints.
        savepoint = conn.begin_nested()
        with pytest.raises(IntegrityError) as exc:
            conn.execute(
                text(
                    "INSERT INTO sku_batches (sku_id, warehouse_id, quantity_received, remaining_quantity, reserved_quantity) "
                    "VALUES (:sku_id, :warehouse_id, 5, -1, -5)"
                ),
                {"sku_id": sku_id, "warehouse_id": warehouse_id},
            )
        savepoint.rollback()
        assert (
            "ck_sku_batches_remaining_quantity_non_negative" in str(exc.value)
            or "ck_sku_batches_reserved_quantity_non_negative" in str(exc.value)
        )

        # sku_batches: reserved above remaining
        savepoint = conn.begin_nested()
        with pytest.raises(IntegrityError) as exc:
            conn.execute(
                text(
                    "INSERT INTO sku_batches (sku_id, warehouse_id, quantity_received, remaining_quantity, reserved_quantity) "
                    "VALUES (:sku_id, :warehouse_id, 5, 5, 6)"
                ),
                {"sku_id": sku_id, "warehouse_id": warehouse_id},
            )
        savepoint.rollback()
        assert "ck_sku_batches_reserved_not_over_remaining" in str(exc.value)

        # order_item_batches: non-positive quantity. Needs a real order + order_item + a
        # valid batch to point at, or the FK fires first and proves nothing.
        order_id = conn.execute(
            text(
                "INSERT INTO orders (from_entity_type, from_entity_id, to_entity_type, to_entity_id, status, payment_status) "
                "VALUES ('WAREHOUSE', :warehouse_id, 'RETAILER', 1, 'PENDING', 'CREDIT') RETURNING id"
            ),
            {"warehouse_id": warehouse_id},
        ).scalar()
        order_item_id = conn.execute(
            text(
                "INSERT INTO order_items (order_id, sku_id, quantity, unit_price) "
                "VALUES (:order_id, :sku_id, 1, 10) RETURNING id"
            ),
            {"order_id": order_id, "sku_id": sku_id},
        ).scalar()
        batch_id = conn.execute(
            text(
                "INSERT INTO sku_batches (sku_id, warehouse_id, quantity_received, remaining_quantity) "
                "VALUES (:sku_id, :warehouse_id, 5, 5) RETURNING id"
            ),
            {"sku_id": sku_id, "warehouse_id": warehouse_id},
        ).scalar()
        savepoint = conn.begin_nested()
        with pytest.raises(IntegrityError) as exc:
            conn.execute(
                text(
                    "INSERT INTO order_item_batches (order_item_id, batch_id, quantity) "
                    "VALUES (:order_item_id, :batch_id, 0)"
                ),
                {"order_item_id": order_item_id, "batch_id": batch_id},
            )
        savepoint.rollback()
        assert "ck_order_item_batches_quantity_positive" in str(exc.value)
    finally:
        trans.rollback()
        conn.close()
