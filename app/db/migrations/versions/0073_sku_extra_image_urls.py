"""Images 2-4 for the storefront product carousel.

skus.image_url stays the cover (and the only image list, cart and order thumbnails use);
extra_image_urls holds the remaining up-to-three https links, in display order. Nullable and
empty for every existing SKU, so nothing is backfilled.
"""

from alembic import op
import sqlalchemy as sa

revision = "0073_sku_extra_image_urls"
down_revision = "0072_ladi_price_96_displayed"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("skus", sa.Column("extra_image_urls", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("skus", "extra_image_urls")
