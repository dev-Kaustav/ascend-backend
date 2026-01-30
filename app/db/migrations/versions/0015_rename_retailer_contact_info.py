"""rename retailer contact info to mobile number"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0015_rename_retailer_contact_info"
down_revision = "0014_add_retailer_address_gst"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("alembic_version", "version_num", type_=sa.String(length=64))
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("retailers")}
    if "contact_info" in columns and "mobile_number" not in columns:
        op.alter_column("retailers", "contact_info", new_column_name="mobile_number")


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("retailers")}
    if "mobile_number" in columns and "contact_info" not in columns:
        op.alter_column("retailers", "mobile_number", new_column_name="contact_info")
