"""add warehouse address fields"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0016_add_warehouse_address"
down_revision = "0015_rename_retailer_contact_info"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("warehouses", sa.Column("address_line1", sa.String(), nullable=True))
    op.add_column("warehouses", sa.Column("address_line2", sa.String(), nullable=True))
    op.add_column("warehouses", sa.Column("city", sa.String(), nullable=True))
    op.add_column("warehouses", sa.Column("state", sa.String(), nullable=True))
    op.add_column("warehouses", sa.Column("pincode", sa.String(), nullable=True))


def downgrade():
    op.drop_column("warehouses", "pincode")
    op.drop_column("warehouses", "state")
    op.drop_column("warehouses", "city")
    op.drop_column("warehouses", "address_line2")
    op.drop_column("warehouses", "address_line1")
