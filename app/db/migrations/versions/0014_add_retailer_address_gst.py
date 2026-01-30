"""add retailer address and gst"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0014_add_retailer_address_gst"
down_revision = "0013_merge_heads"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("retailers", sa.Column("address_line1", sa.String(), nullable=True))
    op.add_column("retailers", sa.Column("address_line2", sa.String(), nullable=True))
    op.add_column("retailers", sa.Column("city", sa.String(), nullable=True))
    op.add_column("retailers", sa.Column("state", sa.String(), nullable=True))
    op.add_column("retailers", sa.Column("pincode", sa.String(), nullable=True))
    op.add_column("retailers", sa.Column("gst_number", sa.String(), nullable=True))


def downgrade():
    op.drop_column("retailers", "gst_number")
    op.drop_column("retailers", "pincode")
    op.drop_column("retailers", "state")
    op.drop_column("retailers", "city")
    op.drop_column("retailers", "address_line2")
    op.drop_column("retailers", "address_line1")
