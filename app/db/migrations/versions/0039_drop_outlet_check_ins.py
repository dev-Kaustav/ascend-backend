"""drop outlet_check_ins table

Revision ID: 0039_drop_outlet_check_ins
Revises: 0038_employee_locations
Create Date: 2026-05-18
"""

from alembic import op
import sqlalchemy as sa

revision = "0039_drop_outlet_check_ins"
down_revision = "0038_employee_locations"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_index("ix_outlet_checkins_user_date", table_name="outlet_check_ins")
    op.drop_table("outlet_check_ins")


def downgrade():
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
