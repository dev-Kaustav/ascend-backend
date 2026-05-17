"""delivery tracking, payment mode, issue category fields

Revision ID: 0034_delivery_payment_tracking
Revises: 0033_in_transit_retailer_on_user
Create Date: 2026-05-03
"""

from alembic import op
import sqlalchemy as sa

revision = "0034_delivery_payment_tracking"
down_revision = "0033_in_transit_retailer_on_user"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("DO $$ BEGIN CREATE TYPE payment_mode AS ENUM ('CASH', 'UPI', 'CHEQUE', 'ONLINE'); EXCEPTION WHEN duplicate_object THEN NULL; END $$")
    op.execute("DO $$ BEGIN CREATE TYPE issue_category AS ENUM ('STOCK_SHORTAGE', 'EXPIRY', 'RETURN', 'GST_ISSUE', 'SALESMAN_ISSUE', 'LOW_AMOUNT', 'SHOP_CLOSED', 'OTHER'); EXCEPTION WHEN duplicate_object THEN NULL; END $$")

    # Account: payment tracking fields
    op.add_column("accounts", sa.Column("payment_mode", sa.Enum("CASH", "UPI", "CHEQUE", "ONLINE", name="payment_mode"), nullable=True))
    op.add_column("accounts", sa.Column("collected_by_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=True))
    op.add_column("accounts", sa.Column("collection_date", sa.DateTime(timezone=True), nullable=True))
    op.add_column("accounts", sa.Column("cheque_number", sa.String(), nullable=True))
    op.add_column("accounts", sa.Column("cheque_name", sa.String(), nullable=True))

    # Order: delivery and issue tracking
    op.add_column("orders", sa.Column("delivery_date", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders", sa.Column("panel_status", sa.String(), nullable=True))
    op.add_column("orders", sa.Column("issue_category", sa.Enum("STOCK_SHORTAGE", "EXPIRY", "RETURN", "GST_ISSUE", "SALESMAN_ISSUE", "LOW_AMOUNT", "SHOP_CLOSED", "OTHER", name="issue_category"), nullable=True))
    op.add_column("orders", sa.Column("description", sa.Text(), nullable=True))

    # Retailer: external outlet ID
    op.add_column("retailers", sa.Column("external_id", sa.String(), nullable=True))
    op.create_index("ix_retailers_external_id", "retailers", ["external_id"])


def downgrade():
    op.drop_index("ix_retailers_external_id", table_name="retailers")
    op.drop_column("retailers", "external_id")

    op.drop_column("orders", "description")
    op.drop_column("orders", "issue_category")
    op.drop_column("orders", "panel_status")
    op.drop_column("orders", "delivery_date")

    op.drop_column("accounts", "cheque_name")
    op.drop_column("accounts", "cheque_number")
    op.drop_column("accounts", "collection_date")
    op.drop_column("accounts", "collected_by_id")
    op.drop_column("accounts", "payment_mode")

    sa.Enum(name="issue_category").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="payment_mode").drop(op.get_bind(), checkfirst=True)
