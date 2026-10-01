"""Delivery charge on orders (08-11, STORE-11).

orders.delivery_charge is the GST-inclusive charge the server added below the storefront's minimum
cart value; orders.delivery_charge_gst_rate is the rate it is taxed at (highest per-item rate, D-33).
Server defaults of 0 mean every existing order, and every salesman/admin order from now on, reads as
"no charge", so their totals and invoices are unchanged.
"""

from alembic import op
import sqlalchemy as sa

revision = "0066_order_delivery_charge"
down_revision = "0065_invoice_ship_to"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "orders",
        sa.Column("delivery_charge", sa.Numeric(12, 2), nullable=False, server_default="0"),
    )
    op.add_column(
        "orders",
        sa.Column("delivery_charge_gst_rate", sa.Numeric(5, 2), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_orders_delivery_charge_nonnegative", "orders", "delivery_charge >= 0"
    )


def downgrade():
    op.drop_constraint("ck_orders_delivery_charge_nonnegative", "orders", type_="check")
    op.drop_column("orders", "delivery_charge_gst_rate")
    op.drop_column("orders", "delivery_charge")
