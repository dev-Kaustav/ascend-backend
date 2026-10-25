"""Sell the ladi at 96 instead of 98.04, matching the price the storefront shows.

0071 matched ROUND(amount * units_per_pack, 2) = 98.04 and so found nothing: the stored amounts
are 8.166 / 8.167, and 98.04 is what the storefront displays after rounding the per-piece price to
8.17 first (components/store/Catalogue.jsx PriceBlock). This matches that displayed price instead
and applies the same back-calculation as 0071:
  amount           = 96 / units_per_pack
  rate             scales by new_amount / old_amount (rate is amount net of GST)
  *gst_amount      = rate x *gst_percent / 100, or scaled like rate when no percent is set
  discount_amount  = mrp - amount
  discount_percent = discount_amount / mrp x 100
MRP, the GST percents and distributor_landing_price are untouched. Existing orders keep the prices
they were booked at.

The previous values are kept in skus_ladi_price_backup_0072 so the downgrade restores them exactly.
"""

from alembic import op

revision = "0072_ladi_price_96_displayed"
down_revision = "0071_ladi_price_96"
branch_labels = None
depends_on = None

OLD_LADI_PRICE = "98.04"
NEW_LADI_PRICE = "96"

MATCH = (
    "pack_type = 'ladi' AND units_per_pack > 0 AND amount > 0 "
    f"AND ROUND(ROUND(amount, 2) * units_per_pack, 2) = {OLD_LADI_PRICE}"
)
PRICE_FIELDS = (
    "amount", "rate", "discount_amount", "discount_percent", "sgst_amount", "cgst_amount", "igst_amount"
)


def upgrade():
    fields = ", ".join(PRICE_FIELDS)
    op.execute(
        f"CREATE TABLE skus_ladi_price_backup_0072 AS SELECT id, {fields} FROM skus WHERE {MATCH}"
    )
    new_amount = f"({NEW_LADI_PRICE}.0 / units_per_pack)"
    scale = f"({new_amount} / amount)"
    # Every right-hand side reads the row's pre-update values, so amount here is the old amount.
    op.execute(
        f"""
        UPDATE skus SET
            amount = ROUND({new_amount}, 3),
            rate = ROUND(rate * {scale}, 3),
            sgst_amount = CASE WHEN sgst_percent IS NULL THEN ROUND(sgst_amount * {scale}, 3)
                              ELSE ROUND(ROUND(rate * {scale}, 3) * sgst_percent / 100, 3) END,
            cgst_amount = CASE WHEN cgst_percent IS NULL THEN ROUND(cgst_amount * {scale}, 3)
                              ELSE ROUND(ROUND(rate * {scale}, 3) * cgst_percent / 100, 3) END,
            igst_amount = CASE WHEN igst_percent IS NULL THEN ROUND(igst_amount * {scale}, 3)
                              ELSE ROUND(ROUND(rate * {scale}, 3) * igst_percent / 100, 3) END,
            discount_amount = CASE WHEN mrp IS NULL THEN discount_amount
                                   ELSE ROUND(mrp - {new_amount}, 3) END,
            discount_percent = CASE WHEN mrp IS NULL OR mrp = 0 THEN discount_percent
                                    ELSE ROUND((mrp - {new_amount}) / mrp * 100, 3) END
        WHERE {MATCH}
        """
    )


def downgrade():
    assignments = ", ".join(
        f"{field} = (SELECT b.{field} FROM skus_ladi_price_backup_0072 b WHERE b.id = skus.id)"
        for field in PRICE_FIELDS
    )
    op.execute(
        f"UPDATE skus SET {assignments} WHERE id IN (SELECT id FROM skus_ladi_price_backup_0072)"
    )
    op.execute("DROP TABLE skus_ladi_price_backup_0072")
