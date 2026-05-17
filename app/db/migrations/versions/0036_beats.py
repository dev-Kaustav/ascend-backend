"""beats table and beat_id on orders

Revision ID: 0036_beats
Revises: 0035_order_trail
Create Date: 2026-05-04
"""

from alembic import op
import sqlalchemy as sa

revision = "0036_beats"
down_revision = "0035_order_trail"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "beats",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(), nullable=False, unique=True, index=True),
        sa.Column("warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.add_column("orders", sa.Column("beat_id", sa.Integer(), sa.ForeignKey("beats.id"), nullable=True))


def downgrade():
    op.drop_column("orders", "beat_id")
    op.drop_table("beats")
