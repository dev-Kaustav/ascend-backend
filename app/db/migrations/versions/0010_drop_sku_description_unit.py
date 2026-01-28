"""drop sku description and unit"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0010_drop_sku_description_unit"
down_revision = "0009_sku_expiry_date"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column("skus", "description")
    op.drop_column("skus", "unit")


def downgrade():
    op.add_column("skus", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("skus", sa.Column("unit", sa.String(), nullable=True))
