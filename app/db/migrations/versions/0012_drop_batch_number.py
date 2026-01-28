"""drop batch number from sku batches"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0012_drop_batch_number"
down_revision = "0011_drop_order_type"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column("sku_batches", "batch_number")


def downgrade():
    op.add_column(
        "sku_batches",
        sa.Column("batch_number", sa.String(), nullable=False, server_default=""),
    )
    op.alter_column("sku_batches", "batch_number", server_default=None)
