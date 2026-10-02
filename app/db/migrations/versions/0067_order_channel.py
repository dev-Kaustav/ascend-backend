"""Mark new orders as online or offline, without historical channel inference."""

from alembic import op
import sqlalchemy as sa

revision = "0067_order_channel"
down_revision = "0066_order_delivery_charge"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "orders",
        sa.Column("channel", sa.String(8), nullable=False, server_default="OFFLINE"),
    )
    op.create_check_constraint(
        "ck_orders_channel", "orders", "channel IN ('ONLINE', 'OFFLINE')"
    )


def downgrade():
    op.drop_constraint("ck_orders_channel", "orders", type_="check")
    op.drop_column("orders", "channel")
