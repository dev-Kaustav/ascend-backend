"""add deleted_at to users"""

from alembic import op
import sqlalchemy as sa

revision = "0020_add_user_deleted_at"
down_revision = "0019_drop_sku_deprecated_fields"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("users", "deleted_at")
