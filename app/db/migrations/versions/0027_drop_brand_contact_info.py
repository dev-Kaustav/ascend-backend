"""drop brand contact info column"""

from alembic import op
import sqlalchemy as sa

revision = "0027_drop_brand_contact_info"
down_revision = "0026_add_brands_access_rule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col["name"] for col in inspector.get_columns("brands")]
    if "contact_info" in columns:
        op.drop_column("brands", "contact_info")


def downgrade() -> None:
    op.add_column("brands", sa.Column("contact_info", sa.Text(), nullable=True))
