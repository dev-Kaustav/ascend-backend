"""initial schema

Revision ID: 0001_initial
Revises: 
Create Date: 2024-01-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    employee_role = postgresql.ENUM(
        "ADMIN",
        "SALESMAN",
        "ACCOUNTANT",
        "WAREHOUSE_MANAGER",
        "RETAILER",
        "BRAND",
        name="employee_role",
        create_type=False,
    )
    order_type = postgresql.ENUM("INCOMING", "OUTGOING", name="order_type", create_type=False)
    order_status = postgresql.ENUM(
        "PENDING",
        "CONFIRMED",
        "DELIVERED",
        "CANCELLED",
        name="order_status",
        create_type=False,
    )
    transaction_type = postgresql.ENUM("IN", "OUT", "RETURN", name="transaction_type", create_type=False)
    payment_status = postgresql.ENUM("UNPAID", "PARTIAL", "PAID", name="payment_status", create_type=False)

    employee_role.create(op.get_bind(), checkfirst=True)
    order_type.create(op.get_bind(), checkfirst=True)
    order_status.create(op.get_bind(), checkfirst=True)
    transaction_type.create(op.get_bind(), checkfirst=True)
    payment_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "brands",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("contact_info", sa.Text(), nullable=True),
    )
    op.create_table(
        "warehouses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("location", sa.String(), nullable=True),
    )
    op.create_table(
        "employees",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column("role", employee_role, nullable=False),
        sa.Column("warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id"), nullable=True),
    )
    op.create_table(
        "retailers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("contact_info", sa.Text(), nullable=True),
        sa.Column("assigned_salesman_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=True),
    )
    op.create_table(
        "skus",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("brand_id", sa.Integer(), sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("unit", sa.String(), nullable=True),
    )
    op.create_table(
        "sku_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sku_id", sa.Integer(), sa.ForeignKey("skus.id"), nullable=False),
        sa.Column("batch_number", sa.String(), nullable=False),
        sa.Column("mfg_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("quantity_received", sa.Float(), nullable=False),
        sa.Column("remaining_quantity", sa.Float(), nullable=False),
    )
    op.create_table(
        "inventory",
        sa.Column("sku_id", sa.Integer(), sa.ForeignKey("skus.id"), primary_key=True),
        sa.Column("warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id"), primary_key=True),
        sa.Column("total_quantity", sa.Float(), nullable=False, server_default="0"),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_type", order_type, nullable=False),
        sa.Column("from_entity_type", sa.String(), nullable=False),
        sa.Column("from_entity_id", sa.Integer(), nullable=False),
        sa.Column("to_entity_type", sa.String(), nullable=False),
        sa.Column("to_entity_id", sa.Integer(), nullable=False),
        sa.Column("status", order_status, nullable=False, server_default="PENDING"),
        sa.Column("payment_status", payment_status, nullable=False, server_default="UNPAID"),
        sa.Column("invoice_number", sa.String(), unique=True, nullable=True),
        sa.Column("salesman_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_table(
        "order_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("sku_id", sa.Integer(), sa.ForeignKey("skus.id"), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("unit_price", sa.Float(), nullable=False),
        sa.Column("discount_amount", sa.Float(), nullable=False, server_default="0"),
    )
    op.create_table(
        "order_item_taxes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_item_id", sa.Integer(), sa.ForeignKey("order_items.id"), nullable=False),
        sa.Column("tax_type", sa.String(), nullable=False),
        sa.Column("rate", sa.Float(), nullable=False),
    )
    op.create_table(
        "order_item_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_item_id", sa.Integer(), sa.ForeignKey("order_items.id"), nullable=False),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("sku_batches.id"), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
    )
    op.create_table(
        "inventory_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sku_id", sa.Integer(), sa.ForeignKey("skus.id"), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id"), nullable=False),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("sku_batches.id"), nullable=True),
        sa.Column("transaction_type", transaction_type, nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("transaction_reference", sa.String(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_table(
        "credit_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("credit_note_number", sa.String(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_table(
        "credit_note_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("credit_note_id", sa.Integer(), sa.ForeignKey("credit_notes.id"), nullable=False),
        sa.Column("sku_id", sa.Integer(), sa.ForeignKey("skus.id"), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("unit_price", sa.Float(), nullable=False),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=True),
        sa.Column("role", employee_role, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("users")
    op.drop_table("credit_note_items")
    op.drop_table("credit_notes")
    op.drop_table("accounts")
    op.drop_table("inventory_transactions")
    op.drop_table("order_item_batches")
    op.drop_table("order_item_taxes")
    op.drop_table("order_items")
    op.drop_table("orders")
    op.drop_table("inventory")
    op.drop_table("sku_batches")
    op.drop_table("skus")
    op.drop_table("retailers")
    op.drop_table("employees")
    op.drop_table("warehouses")
    op.drop_table("brands")

    op.execute("DROP TYPE IF EXISTS payment_status")
    op.execute("DROP TYPE IF EXISTS transaction_type")
    op.execute("DROP TYPE IF EXISTS order_status")
    op.execute("DROP TYPE IF EXISTS order_type")
    op.execute("DROP TYPE IF EXISTS employee_role")
