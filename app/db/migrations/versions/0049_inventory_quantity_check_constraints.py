"""inventory quantity check constraints

Revision ID: 0049_inventory_quantity_check_constraints
Revises: 0048_credit_note_invoice_link
Create Date: 2026-08-18

INVT-02 / D4: `inventory`, `sku_batches` and `order_item_batches` today carry only primary
and foreign keys — nothing at the database level stops a negative quantity or a reservation
larger than the stock it claims. Every guard against that today lives in
app/services/order.py, which is application-level and bypassable by raw SQL, a data fix, an
import script, or the next service that forgets. This migration adds eight named CHECK
constraints so the database itself refuses those writes.

`inventory`, `sku_batches` and `order_item_batches` are all empty in the dev database at
migration time (0 rows each, confirmed during phase planning), so no backfill and no
cleanup are needed before these constraints can hold.

The eight constraint names below are duplicated from the corresponding models'
`__table_args__` (app/models/inventory.py, app/models/sku_batch.py,
app/models/order_item_batch.py) on purpose. They must match exactly — a name drifting
between the model and this migration would make a future autogenerate try to drop and
recreate the constraint. The SQLite test suite proves the model half (rejection against the
schema `create_all` builds); this migration is what makes real PostgreSQL enforce the same
rule.

R6 (coordinator ruling, 04-CONTEXT.md): also fold in `sku_batches.warehouse_id` NOT NULL.
The model has always declared `nullable=False`, but the real PostgreSQL column is nullable —
a NULL-warehouse batch would be invisible to allocation, which is exactly this phase's
subject. `sku_batches` is empty, so the constraint is trivially satisfiable with no backfill.
"""

from alembic import op
import sqlalchemy as sa

revision = "0049_inventory_quantity_check_constraints"
down_revision = "0048_credit_note_invoice_link"
branch_labels = None
depends_on = None


def upgrade():
    op.create_check_constraint(
        "ck_inventory_total_quantity_non_negative",
        "inventory",
        "total_quantity >= 0",
    )
    op.create_check_constraint(
        "ck_inventory_reserved_quantity_non_negative",
        "inventory",
        "reserved_quantity >= 0",
    )
    op.create_check_constraint(
        "ck_inventory_reserved_not_over_total",
        "inventory",
        "reserved_quantity <= total_quantity",
    )
    op.create_check_constraint(
        "ck_sku_batches_quantity_received_non_negative",
        "sku_batches",
        "quantity_received >= 0",
    )
    op.create_check_constraint(
        "ck_sku_batches_remaining_quantity_non_negative",
        "sku_batches",
        "remaining_quantity >= 0",
    )
    op.create_check_constraint(
        "ck_sku_batches_reserved_quantity_non_negative",
        "sku_batches",
        "reserved_quantity >= 0",
    )
    op.create_check_constraint(
        "ck_sku_batches_reserved_not_over_remaining",
        "sku_batches",
        "reserved_quantity <= remaining_quantity",
    )
    op.create_check_constraint(
        "ck_order_item_batches_quantity_positive",
        "order_item_batches",
        "quantity > 0",
    )
    # R6: close the nullable/nullable=False drift on sku_batches.warehouse_id. Table is
    # empty, so no backfill is required before the NOT NULL can be applied.
    op.alter_column("sku_batches", "warehouse_id", nullable=False)


def downgrade():
    op.alter_column("sku_batches", "warehouse_id", nullable=True)
    op.drop_constraint("ck_order_item_batches_quantity_positive", "order_item_batches", type_="check")
    op.drop_constraint("ck_sku_batches_reserved_not_over_remaining", "sku_batches", type_="check")
    op.drop_constraint("ck_sku_batches_reserved_quantity_non_negative", "sku_batches", type_="check")
    op.drop_constraint("ck_sku_batches_remaining_quantity_non_negative", "sku_batches", type_="check")
    op.drop_constraint("ck_sku_batches_quantity_received_non_negative", "sku_batches", type_="check")
    op.drop_constraint("ck_inventory_reserved_not_over_total", "inventory", type_="check")
    op.drop_constraint("ck_inventory_reserved_quantity_non_negative", "inventory", type_="check")
    op.drop_constraint("ck_inventory_total_quantity_non_negative", "inventory", type_="check")
