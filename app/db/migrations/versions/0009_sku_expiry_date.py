"""add expiry date to sku"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0009_sku_expiry_date"
down_revision = "0008_sku_weight_dimensions"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("skus", sa.Column("expiry_date", sa.Date(), nullable=True))


def downgrade():
    op.drop_column("skus", "expiry_date")
