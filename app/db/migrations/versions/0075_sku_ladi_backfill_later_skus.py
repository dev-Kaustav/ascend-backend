"""Sell the SKUs added after 0068 by the ladi too (Crax was the case): same rule, run again.

0068 filled pack_type/units_per_pack from MRP for the SKUs that existed then: MRP 10 hangs 12
pieces to a ladi, MRP 30 hangs 10 and MRP 60 hangs 6. Crax's SKUs were added later with no pack,
so the order screen, store and cart counted them by the piece ("5 pc") while Jabsons went by the
ladi. Only SKUs with no pack set are touched. Quantities stay in pieces everywhere, so existing
orders are unchanged.

The ids changed are kept in skus_ladi_backfill_0075 so the downgrade reverts exactly those rows.
"""

from alembic import op

revision = "0075_sku_ladi_backfill_later_skus"
down_revision = "0074_driver_assigned_status"
branch_labels = None
depends_on = None

LADI_PIECES_BY_MRP = {10: 12, 30: 10, 60: 6}

MATCH = f"pack_type IS NULL AND units_per_pack IS NULL AND mrp IN ({', '.join(map(str, LADI_PIECES_BY_MRP))})"


def upgrade():
    op.execute(f"CREATE TABLE skus_ladi_backfill_0075 AS SELECT id FROM skus WHERE {MATCH}")
    for mrp, pieces in LADI_PIECES_BY_MRP.items():
        op.execute(
            f"UPDATE skus SET pack_type = 'ladi', units_per_pack = {pieces} "
            f"WHERE mrp = {mrp} AND pack_type IS NULL AND units_per_pack IS NULL"
        )


def downgrade():
    op.execute(
        "UPDATE skus SET pack_type = NULL, units_per_pack = NULL "
        "WHERE id IN (SELECT id FROM skus_ladi_backfill_0075)"
    )
    op.execute("DROP TABLE skus_ladi_backfill_0075")
