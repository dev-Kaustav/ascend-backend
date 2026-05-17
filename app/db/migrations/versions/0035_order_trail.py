"""order trail lifecycle tracking

Revision ID: 0035_order_trail
Revises: 0034_delivery_payment_tracking
Create Date: 2026-05-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0035_order_trail"
down_revision = "0034_delivery_payment_tracking"
branch_labels = None
depends_on = None

order_status_enum = postgresql.ENUM(
    "PENDING", "READY_TO_SHIP", "OUT_FOR_DELIVERY",
    "DELIVERED", "RETURNED", "CANCELLED",
    name="order_status", create_type=False
)


def upgrade():
    op.create_table(
        "order_trails",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False, index=True),
        sa.Column("order_status", order_status_enum, nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("changed_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("order_trails")
