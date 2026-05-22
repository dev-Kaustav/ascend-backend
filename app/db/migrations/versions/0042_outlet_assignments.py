"""outlet assignments (driver jobs)

Revision ID: 0042_outlet_assignments
Revises: 0041_outlet_deliveries
Create Date: 2026-05-22
"""

from alembic import op
import sqlalchemy as sa

revision = "0042_outlet_assignments"
down_revision = "0041_outlet_deliveries"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "outlet_assignments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("driver_employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("assigned_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="PENDING"),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_outlet_assignments_driver_status",
        "outlet_assignments",
        ["driver_employee_id", "status"],
    )

    op.create_table(
        "outlet_assignment_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "assignment_id",
            sa.Integer(),
            sa.ForeignKey("outlet_assignments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("retailer_id", sa.Integer(), sa.ForeignKey("retailers.id"), nullable=False),
    )
    op.create_index(
        "ix_outlet_assignment_items_assignment",
        "outlet_assignment_items",
        ["assignment_id"],
    )


def downgrade():
    op.drop_index("ix_outlet_assignment_items_assignment", table_name="outlet_assignment_items")
    op.drop_table("outlet_assignment_items")
    op.drop_index("ix_outlet_assignments_driver_status", table_name="outlet_assignments")
    op.drop_table("outlet_assignments")
