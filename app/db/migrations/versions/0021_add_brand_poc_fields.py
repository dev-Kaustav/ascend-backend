"""add brand poc fields"""

from alembic import op
import sqlalchemy as sa

revision = "0021_add_brand_poc_fields"
down_revision = "0020_add_user_deleted_at"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("brands", sa.Column("poc_name", sa.String(), nullable=True))
    op.add_column("brands", sa.Column("poc_phone_number", sa.BigInteger(), nullable=True))
    op.add_column("brands", sa.Column("poc_email", sa.String(), nullable=True))


def downgrade():
    op.drop_column("brands", "poc_email")
    op.drop_column("brands", "poc_phone_number")
    op.drop_column("brands", "poc_name")
