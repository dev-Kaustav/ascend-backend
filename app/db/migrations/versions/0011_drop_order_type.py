"""drop order type column"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0011_drop_order_type"
down_revision = "0010_drop_sku_description_unit"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_index("ix_orders_order_type", table_name="orders")
    op.drop_column("orders", "order_type")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS order_type")


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        order_type = postgresql.ENUM("INCOMING", "OUTGOING", name="order_type", create_type=False)
        order_type.create(bind, checkfirst=True)
    else:
        order_type = sa.Enum("INCOMING", "OUTGOING", name="order_type")

    op.add_column(
        "orders",
        sa.Column("order_type", order_type, nullable=False, server_default="OUTGOING"),
    )
    op.alter_column("orders", "order_type", server_default=None)
    op.create_index("ix_orders_order_type", "orders", ["order_type"])
