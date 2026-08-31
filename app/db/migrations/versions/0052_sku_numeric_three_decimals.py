"""widen sku price columns to 3 decimal places

Revision ID: 0052_sku_numeric_three_decimals
Revises: 0051_drop_permission_tables
Create Date: 2026-08-31

The SKU form derives rate as amount / (1 + total GST%), which is rarely exact:
PTR 8.00 at 5% GST is 7.619, not 7.62. NUMERIC(12, 2) rounded that away on save,
so the stored rate disagreed with the pricing sheet by up to half a paisa per unit.

The upgrade only widens the scale, so existing values survive unchanged. The
downgrade narrows it back and DOES round every stored value to 2 decimals — the
third decimal is discarded and cannot be recovered.
"""

from alembic import op
import sqlalchemy as sa


revision = "0052_sku_numeric_three_decimals"
down_revision = "0051_drop_permission_tables"
branch_labels = None
depends_on = None


COLUMNS = (
    "distributor_landing_price",
    "mrp",
    "discount_amount",
    "discount_percent",
    "rate",
    "sgst_percent",
    "sgst_amount",
    "cgst_percent",
    "cgst_amount",
    "igst_percent",
    "igst_amount",
    "amount",
)


def upgrade():
    for column in COLUMNS:
        op.alter_column(
            "skus",
            column,
            existing_type=sa.Numeric(12, 2),
            type_=sa.Numeric(12, 3),
            existing_nullable=True,
        )


def downgrade():
    for column in COLUMNS:
        op.alter_column(
            "skus",
            column,
            existing_type=sa.Numeric(12, 3),
            type_=sa.Numeric(12, 2),
            existing_nullable=True,
        )
