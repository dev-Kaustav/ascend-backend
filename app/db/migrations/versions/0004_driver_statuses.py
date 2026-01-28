"""add driver and order statuses

Revision ID: 0004_driver_statuses
Revises: 0003_credit_note_flag
Create Date: 2026-01-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_driver_statuses"
down_revision = "0003_credit_note_flag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE employee_role ADD VALUE IF NOT EXISTS 'DRIVER'")
    op.execute("ALTER TYPE order_status ADD VALUE IF NOT EXISTS 'OUT_FOR_DELIVERY'")
    op.execute("ALTER TYPE order_status ADD VALUE IF NOT EXISTS 'RETURNED'")

    op.add_column("orders", sa.Column("delivery_driver_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_orders_delivery_driver_id_employees",
        "orders",
        "employees",
        ["delivery_driver_id"],
        ["id"],
    )
    op.create_index("ix_orders_delivery_driver_id", "orders", ["delivery_driver_id"])


def downgrade() -> None:
    op.drop_index("ix_orders_delivery_driver_id", table_name="orders")
    op.drop_constraint("fk_orders_delivery_driver_id_employees", "orders", type_="foreignkey")
    op.drop_column("orders", "delivery_driver_id")
