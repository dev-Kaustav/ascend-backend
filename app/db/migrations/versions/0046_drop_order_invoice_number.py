"""drop orders.invoice_number and company_profile.invoice_next_number

Revision ID: 0046_drop_order_invoice_number
Revises: 0045_invoice_tables
Create Date: 2026-08-15

D-01/D-05: invoice_number leaves Order entirely — it is now a read-only Python
property resolving through the Invoice relationship. D-04: the read-modify-write
counter on company_profile is retired in favour of invoice_number_seq (0045).

`orders` has zero rows in the dev database (confirmed below before dropping), so this
is a clean drop with nothing to migrate forward. The downgrade path is not lossy: it
backfills orders.invoice_number from invoices by order_id, for parity with any rows
that exist by the time someone downgrades.
"""

from alembic import op
import sqlalchemy as sa

revision = "0046_drop_order_invoice_number"
down_revision = "0045_invoice_tables"
branch_labels = None
depends_on = None


def upgrade():
    # orders has zero rows in dev today — confirm rather than assume, exactly as 0044 did.
    conn = op.get_bind()
    count = conn.execute(sa.text("SELECT count(*) FROM orders")).scalar()
    assert count == 0, f"orders has {count} rows; this drop was written assuming zero"

    # Postgres names an inline `unique=True` column constraint <table>_<column>_key by
    # default (set in 0001_initial.py's op.create_table). Drop by that name if present;
    # IF EXISTS makes this safe against a differently-named constraint or none at all.
    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_invoice_number_key")
    op.drop_column("orders", "invoice_number")
    op.drop_column("company_profile", "invoice_next_number")


def downgrade():
    op.add_column("orders", sa.Column("invoice_number", sa.String(), nullable=True, unique=True))
    op.add_column(
        "company_profile",
        sa.Column("invoice_next_number", sa.Integer(), nullable=False, server_default="1"),
    )

    # Not lossy: backfill orders.invoice_number from invoices by order_id.
    op.execute(
        """
        UPDATE orders
        SET invoice_number = invoices.invoice_number
        FROM invoices
        WHERE invoices.order_id = orders.id
        """
    )
