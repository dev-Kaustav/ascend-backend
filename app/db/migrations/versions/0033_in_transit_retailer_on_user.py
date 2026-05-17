"""in transit status and retailer_id on users

Revision ID: 0033_in_transit_retailer_on_user
Revises: 0032_orders_invoices_reservations
Create Date: 2026-04-27
"""

from alembic import op
import sqlalchemy as sa

revision = "0033_in_transit_retailer_on_user"
down_revision = "0032_orders_invoices_reservations"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE order_status ADD VALUE IF NOT EXISTS 'IN_TRANSIT'")

    op.add_column(
        "users",
        sa.Column("retailer_id", sa.Integer(), sa.ForeignKey("retailers.id"), nullable=True),
    )


def downgrade():
    op.drop_column("users", "retailer_id")
