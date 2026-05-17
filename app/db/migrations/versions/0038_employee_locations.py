"""outlet_check_ins table for visit tracking

Revision ID: 0038_employee_locations
Revises: 0037_retailer_lat_lng
Create Date: 2026-05-10
"""

from alembic import op
import sqlalchemy as sa

revision = "0038_employee_locations"
down_revision = "0037_retailer_lat_lng"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "outlet_check_ins",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("retailer_id", sa.Integer(), sa.ForeignKey("retailers.id"), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_outlet_checkins_user_date", "outlet_check_ins", ["user_id", "created_at"])


def downgrade():
    op.drop_index("ix_outlet_checkins_user_date", table_name="outlet_check_ins")
    op.drop_table("outlet_check_ins")
