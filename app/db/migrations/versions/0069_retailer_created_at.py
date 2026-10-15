"""Record when retailers are added, leaving unknown historical dates empty."""

from alembic import op
import sqlalchemy as sa

revision = "0069_retailer_created_at"
down_revision = "0068_sku_ladi_backfill"
branch_labels = None
depends_on = None


def upgrade():
    # Add the default separately so existing retailers are not dated to this migration.
    op.add_column("retailers", sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column("retailers", "created_at", server_default=sa.func.now())


def downgrade():
    op.drop_column("retailers", "created_at")
