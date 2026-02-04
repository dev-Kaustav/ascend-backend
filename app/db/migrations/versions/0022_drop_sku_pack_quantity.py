"""drop sku pack quantity"""

from alembic import op
import sqlalchemy as sa

revision = "0022_drop_sku_pack_quantity"
down_revision = "0021_add_brand_poc_fields"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column("skus", "pack_quantity")


def downgrade():
    op.add_column("skus", sa.Column("pack_quantity", sa.Float(), nullable=True))
