"""drop deprecated sku fields"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0019_drop_sku_deprecated_fields"
down_revision = "0018_numeric_phone_pincode"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column("skus", "expiry_date")
    op.drop_column("skus", "margin_percent")
    op.drop_column("skus", "basis_rate")


def downgrade():
    op.add_column("skus", sa.Column("basis_rate", sa.Float(), nullable=True))
    op.add_column(
        "skus",
        sa.Column("margin_percent", sa.Float(), nullable=True, server_default="0"),
    )
    op.add_column("skus", sa.Column("expiry_date", sa.Date(), nullable=True))
    op.alter_column("skus", "margin_percent", server_default=None)
