"""outlet_deliveries audit table

Revision ID: 0041_outlet_deliveries
Revises: 0040_retailer_external_id_unique
Create Date: 2026-05-20
"""

from alembic import op
import sqlalchemy as sa

revision = "0041_outlet_deliveries"
down_revision = "0040_retailer_external_id_unique"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "outlet_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("retailer_id", sa.Integer(), sa.ForeignKey("retailers.id"), nullable=False),
        sa.Column("driver_latitude", sa.Float(), nullable=False),
        sa.Column("driver_longitude", sa.Float(), nullable=False),
        sa.Column("driver_accuracy_m", sa.Float(), nullable=True),
        sa.Column("distance_m", sa.Float(), nullable=True),
        sa.Column("stored_lat_before", sa.Float(), nullable=True),
        sa.Column("stored_lng_before", sa.Float(), nullable=True),
        sa.Column("retailer_updated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("outer_limit_overridden", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_outlet_deliveries_user_date", "outlet_deliveries", ["user_id", "created_at"])
    op.create_index("ix_outlet_deliveries_retailer_date", "outlet_deliveries", ["retailer_id", "created_at"])


def downgrade():
    op.drop_index("ix_outlet_deliveries_retailer_date", table_name="outlet_deliveries")
    op.drop_index("ix_outlet_deliveries_user_date", table_name="outlet_deliveries")
    op.drop_table("outlet_deliveries")
