"""orders invoices reservations

Revision ID: 0032_orders_invoices_reservations
Revises: 0031_bill_labels_metadata_only
Create Date: 2026-04-25
"""

from alembic import op
import sqlalchemy as sa


revision = "0032_orders_invoices_reservations"
down_revision = "0031_bill_labels_metadata_only"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE order_status ADD VALUE IF NOT EXISTS 'BOOKED'")
        op.execute("ALTER TYPE order_status ADD VALUE IF NOT EXISTS 'READY_TO_SHIP'")
        op.execute("ALTER TYPE payment_status ADD VALUE IF NOT EXISTS 'CREDIT'")

    op.add_column("skus", sa.Column("distributor_landing_price", sa.Float(), nullable=True))
    op.add_column("inventory", sa.Column("reserved_quantity", sa.Float(), nullable=False, server_default="0"))
    op.add_column("sku_batches", sa.Column("reserved_quantity", sa.Float(), nullable=False, server_default="0"))
    op.alter_column("orders", "status", server_default="BOOKED")
    op.alter_column("orders", "payment_status", server_default="CREDIT")

    op.create_table(
        "company_profile",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("legal_name", sa.String(), nullable=False),
        sa.Column("gstin", sa.String(), nullable=True),
        sa.Column("address_line1", sa.String(), nullable=True),
        sa.Column("address_line2", sa.String(), nullable=True),
        sa.Column("city", sa.String(), nullable=True),
        sa.Column("state", sa.String(), nullable=True),
        sa.Column("pincode", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("invoice_prefix", sa.String(), nullable=False),
        sa.Column("invoice_next_number", sa.Integer(), nullable=False),
        sa.Column("invoice_footer", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_company_profile_id"), "company_profile", ["id"], unique=False)

    op.execute(
        """
        INSERT INTO company_profile (id, legal_name, invoice_prefix, invoice_next_number)
        VALUES (1, 'Ascend Foods', 'ASC', 1)
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE orders
        SET status = 'BOOKED'
        WHERE status = 'PENDING'
          AND from_entity_type = 'WAREHOUSE'
          AND to_entity_type = 'RETAILER'
        """
    )
    op.execute(
        """
        UPDATE orders
        SET status = 'READY_TO_SHIP'
        WHERE status = 'CONFIRMED'
          AND from_entity_type = 'WAREHOUSE'
          AND to_entity_type = 'RETAILER'
        """
    )
    op.execute("UPDATE orders SET payment_status = 'CREDIT' WHERE payment_status = 'UNPAID'")
    op.execute(
        """
        UPDATE orders
        SET invoice_number = 'ASC' || lpad(id::text, 6, '0')
        WHERE invoice_number IS NULL
          AND from_entity_type = 'WAREHOUSE'
          AND to_entity_type = 'RETAILER'
        """
    )
    op.execute(
        """
        UPDATE company_profile
        SET invoice_next_number = COALESCE((SELECT max(id) + 1 FROM orders), 1)
        WHERE id = 1
        """
    )


def downgrade():
    op.drop_index(op.f("ix_company_profile_id"), table_name="company_profile")
    op.drop_table("company_profile")
    op.drop_column("sku_batches", "reserved_quantity")
    op.drop_column("inventory", "reserved_quantity")
    op.drop_column("skus", "distributor_landing_price")
    op.alter_column("orders", "status", server_default="PENDING")
    op.alter_column("orders", "payment_status", server_default="UNPAID")
