"""add employee phone number"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0017_add_employee_phone_number"
down_revision = "0016_add_warehouse_address"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("employees", sa.Column("phone_number", sa.String(), nullable=True))


def downgrade():
    op.drop_column("employees", "phone_number")
