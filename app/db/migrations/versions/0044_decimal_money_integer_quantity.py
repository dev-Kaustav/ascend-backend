"""retype money columns to numeric(12,2) and saleable-quantity columns to integer"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0044_decimal_money_integer_quantity"
down_revision = "0043_state_check_constraint"
branch_labels = None
depends_on = None


# (table, column) pairs. Money -> NUMERIC(12,2) (D-05, CORE-01). Quantity -> Integer,
# whole saleable units only (D-04, CORE-02). Kept in sync with app/models/*.py.
MONEY_COLUMNS = [
    ("skus", "distributor_landing_price"),
    ("skus", "mrp"),
    ("skus", "discount_amount"),
    ("skus", "discount_percent"),
    ("skus", "rate"),
    ("skus", "sgst_percent"),
    ("skus", "sgst_amount"),
    ("skus", "cgst_percent"),
    ("skus", "cgst_amount"),
    ("skus", "igst_percent"),
    ("skus", "igst_amount"),
    ("skus", "amount"),
    ("order_items", "unit_price"),
    ("order_items", "discount_amount"),
    ("order_item_taxes", "rate"),
    ("accounts", "amount"),
    ("credit_note_items", "unit_price"),
]

QUANTITY_COLUMNS = [
    ("inventory", "total_quantity"),
    ("inventory", "reserved_quantity"),
    ("sku_batches", "quantity_received"),
    ("sku_batches", "remaining_quantity"),
    ("sku_batches", "reserved_quantity"),
    ("order_items", "quantity"),
    ("credit_note_items", "quantity"),
    ("order_item_batches", "quantity"),
    ("inventory_transactions", "quantity"),
]

# Identifying column(s) to name in the guard's sample-row output. Every affected
# table has a single-column "id" PK except `inventory`, which is keyed on
# (sku_id, warehouse_id).
ID_COLUMNS = {
    "inventory": ["sku_id", "warehouse_id"],
}


def _id_cols(table: str) -> list[str]:
    return ID_COLUMNS.get(table, ["id"])


def _guard_fractional_quantities(bind):
    """Abort before any ALTER if a quantity column holds a fractional value.

    PostgreSQL's `double precision -> integer` cast rounds (half to even)
    rather than failing, so without this guard a batch recorded as 2.5 units
    would silently become 2 units with no error and no way to recover the
    original value. D-04: fractional quantities are not a thing Ascend
    sells; if any exist, a human decides what they meant, not the migration.
    """
    for table, col in QUANTITY_COLUMNS:
        id_cols = _id_cols(table)
        id_select = ", ".join(id_cols)
        result = bind.execute(
            sa.text(
                f'SELECT count(*) FROM "{table}" '
                f'WHERE "{col}" IS NOT NULL AND "{col}" <> trunc("{col}")'
            )
        )
        count = result.scalar()
        if count:
            sample = bind.execute(
                sa.text(
                    f'SELECT {id_select}, "{col}" FROM "{table}" '
                    f'WHERE "{col}" IS NOT NULL AND "{col}" <> trunc("{col}") '
                    f"LIMIT 5"
                )
            ).fetchall()
            raise RuntimeError(
                f"Migration 0044 aborted: {table}.{col} holds {count} fractional "
                f"value(s), which cannot be losslessly converted to Integer. "
                f"Sample rows ({id_select}, {col}): {sample}. "
                f"Resolve these rows manually before re-running this migration."
            )


def _guard_money_overflow(bind):
    """Abort if a money value would overflow NUMERIC(12,2).

    numeric(12,2) tops out at 9,999,999,999.99; an overflow would otherwise
    abort the ALTER mid-run with an opaque error. Sub-paise precision loss on
    the way to 2dp is accepted (D-02, D-03) and is not guarded here.
    """
    for table, col in MONEY_COLUMNS:
        id_cols = _id_cols(table)
        id_select = ", ".join(id_cols)
        result = bind.execute(
            sa.text(
                f'SELECT count(*) FROM "{table}" '
                f'WHERE "{col}" IS NOT NULL AND abs("{col}") >= 10000000000'
            )
        )
        count = result.scalar()
        if count:
            sample = bind.execute(
                sa.text(
                    f'SELECT {id_select}, "{col}" FROM "{table}" '
                    f'WHERE "{col}" IS NOT NULL AND abs("{col}") >= 10000000000 '
                    f"LIMIT 5"
                )
            ).fetchall()
            raise RuntimeError(
                f"Migration 0044 aborted: {table}.{col} holds {count} value(s) "
                f">= 10^10, which overflows NUMERIC(12,2) (max "
                f"9,999,999,999.99). Sample rows ({id_select}, {col}): {sample}. "
                f"Resolve these rows manually before re-running this migration."
            )


# existing_nullable / existing_server_default per column, sourced from a direct
# information_schema probe against the dev database at planning time (the
# planning evidence in 01-02-PLAN.md named only 4 of these; the live schema
# has 6 server_default="0" survivors, per 0032_orders_invoices_reservations.py).
_NULLABLE = {
    ("skus", "distributor_landing_price"): True,
    ("skus", "mrp"): True,
    ("skus", "discount_amount"): True,
    ("skus", "discount_percent"): True,
    ("skus", "rate"): True,
    ("skus", "sgst_percent"): True,
    ("skus", "sgst_amount"): True,
    ("skus", "cgst_percent"): True,
    ("skus", "cgst_amount"): True,
    ("skus", "igst_percent"): True,
    ("skus", "igst_amount"): True,
    ("skus", "amount"): True,
    ("order_items", "unit_price"): False,
    ("order_items", "discount_amount"): False,
    ("order_item_taxes", "rate"): False,
    ("accounts", "amount"): False,
    ("credit_note_items", "unit_price"): False,
    ("inventory", "total_quantity"): False,
    ("inventory", "reserved_quantity"): False,
    ("sku_batches", "quantity_received"): False,
    ("sku_batches", "remaining_quantity"): False,
    ("sku_batches", "reserved_quantity"): False,
    ("order_items", "quantity"): False,
    ("credit_note_items", "quantity"): False,
    ("order_item_batches", "quantity"): False,
    ("inventory_transactions", "quantity"): False,
}

# Columns that carry server_default="0" and must keep it through the type change.
_SERVER_DEFAULT_ZERO = {
    ("skus", "discount_amount"),
    ("skus", "discount_percent"),
    ("order_items", "discount_amount"),
    ("inventory", "total_quantity"),
    ("inventory", "reserved_quantity"),
    ("sku_batches", "reserved_quantity"),
}


def upgrade():
    bind = op.get_bind()

    _guard_fractional_quantities(bind)
    _guard_money_overflow(bind)

    for table, col in MONEY_COLUMNS:
        kwargs = dict(
            type_=sa.Numeric(12, 2),
            postgresql_using=f'"{col}"::numeric(12,2)',
            existing_type=sa.Float(),
            existing_nullable=_NULLABLE[(table, col)],
        )
        if (table, col) in _SERVER_DEFAULT_ZERO:
            kwargs["existing_server_default"] = sa.text("0")
        op.alter_column(table, col, **kwargs)

    for table, col in QUANTITY_COLUMNS:
        kwargs = dict(
            type_=sa.Integer(),
            postgresql_using=f'"{col}"::integer',
            existing_type=sa.Float(),
            existing_nullable=_NULLABLE[(table, col)],
        )
        if (table, col) in _SERVER_DEFAULT_ZERO:
            kwargs["existing_server_default"] = sa.text("0")
        op.alter_column(table, col, **kwargs)


def downgrade():
    # Reverses every column back to double precision. This cannot restore
    # precision lost on the way down (e.g. sub-paise rounding applied during
    # upgrade()) -- accepted per D-02, dev data only, no backup/rollback
    # rehearsal is built for this phase.
    for table, col in QUANTITY_COLUMNS:
        kwargs = dict(
            type_=sa.Float(),
            postgresql_using=f'"{col}"::double precision',
            existing_type=sa.Integer(),
            existing_nullable=_NULLABLE[(table, col)],
        )
        if (table, col) in _SERVER_DEFAULT_ZERO:
            kwargs["existing_server_default"] = sa.text("0")
        op.alter_column(table, col, **kwargs)

    for table, col in MONEY_COLUMNS:
        kwargs = dict(
            type_=sa.Float(),
            postgresql_using=f'"{col}"::double precision',
            existing_type=sa.Numeric(12, 2),
            existing_nullable=_NULLABLE[(table, col)],
        )
        if (table, col) in _SERVER_DEFAULT_ZERO:
            kwargs["existing_server_default"] = sa.text("0")
        op.alter_column(table, col, **kwargs)
