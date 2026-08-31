"""add sku code

Revision ID: 0053_sku_code
Revises: 0052_sku_numeric_three_decimals
Create Date: 2026-08-31

The code printed on physical stock count sheets (CHN-HG-25g, DF-CSBP-18g). Added
nullable because every existing SKU predates the column and has none; a NOT NULL
column would have no value to backfill with. The unique index still admits those
rows, since Postgres treats NULLs as distinct under a unique constraint.
"""

from alembic import op
import sqlalchemy as sa


revision = "0053_sku_code"
down_revision = "0052_sku_numeric_three_decimals"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("skus", sa.Column("code", sa.String(), nullable=True))
    op.create_index(op.f("ix_skus_code"), "skus", ["code"], unique=True)


def downgrade():
    op.drop_index(op.f("ix_skus_code"), table_name="skus")
    op.drop_column("skus", "code")
