"""add igst fields to skus"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0011_add_sku_igst"
down_revision = "0010_drop_sku_description_unit"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("skus", sa.Column("igst_percent", sa.Float(), nullable=True))
    op.add_column("skus", sa.Column("igst_amount", sa.Float(), nullable=True))


def downgrade():
    op.drop_column("skus", "igst_amount")
    op.drop_column("skus", "igst_percent")
