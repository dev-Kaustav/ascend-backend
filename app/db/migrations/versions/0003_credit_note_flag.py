"""add credit note outstanding flag

Revision ID: 0003_credit_note_flag
Revises: 0002_add_indexes
Create Date: 2026-01-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_credit_note_flag"
down_revision = "0002_add_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "credit_notes",
        sa.Column("applies_to_outstanding", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column("credit_notes", "applies_to_outstanding", server_default=None)


def downgrade() -> None:
    op.drop_column("credit_notes", "applies_to_outstanding")
