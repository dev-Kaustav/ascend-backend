"""merge igst and batch number heads"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0013_merge_heads"
down_revision = ("0011_add_sku_igst", "0012_drop_batch_number")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
