"""add weight and dimensions to sku"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0008_sku_weight_dimensions"
down_revision = "0007_sku_attributes"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("skus", sa.Column("weight", sa.Float(), nullable=True))
    op.add_column("skus", sa.Column("length_cm", sa.Float(), nullable=True))
    op.add_column("skus", sa.Column("width_cm", sa.Float(), nullable=True))
    op.add_column("skus", sa.Column("height_cm", sa.Float(), nullable=True))


def downgrade():
    op.drop_column("skus", "height_cm")
    op.drop_column("skus", "width_cm")
    op.drop_column("skus", "length_cm")
    op.drop_column("skus", "weight")
