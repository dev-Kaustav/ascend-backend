"""Sell the storefront snacks by the ladi: fill pack_type/units_per_pack from MRP.

MRP 10 hangs 12 pieces to a ladi, MRP 30 hangs 10 and MRP 60 hangs 6. Only SKUs with no pack set
are touched, so an admin's own value is never overwritten. Quantities stay in pieces everywhere.
"""

from alembic import op

revision = "0068_sku_ladi_backfill"
down_revision = "0067_order_channel"
branch_labels = None
depends_on = None

LADI_PIECES_BY_MRP = {10: 12, 30: 10, 60: 6}


def upgrade():
    for mrp, pieces in LADI_PIECES_BY_MRP.items():
        op.execute(
            f"UPDATE skus SET pack_type = 'ladi', units_per_pack = {pieces} "
            f"WHERE mrp = {mrp} AND pack_type IS NULL AND units_per_pack IS NULL"
        )


def downgrade():
    for mrp, pieces in LADI_PIECES_BY_MRP.items():
        op.execute(
            f"UPDATE skus SET pack_type = NULL, units_per_pack = NULL "
            f"WHERE mrp = {mrp} AND pack_type = 'ladi' AND units_per_pack = {pieces}"
        )
