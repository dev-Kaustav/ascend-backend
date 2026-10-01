"""Ship-to snapshot on orders (08-09, D-29).

orders.delivery_address_id points at the saved address a storefront order was placed to (SET NULL
when that address is deleted); the ship_to_* columns copy its fields at order time so a later edit
or deletion never changes where an existing order goes. All NULL for salesman orders, which keep
delivering to the retailer's registered address.
"""

from alembic import op
import sqlalchemy as sa

revision = "0064_order_ship_to"
down_revision = "0063_retailer_addresses"
branch_labels = None
depends_on = None

_SHIP_TO_COLUMNS = [
    sa.Column("ship_to_label", sa.String(length=40), nullable=True),
    sa.Column("ship_to_line1", sa.String(length=200), nullable=True),
    sa.Column("ship_to_line2", sa.String(length=200), nullable=True),
    sa.Column("ship_to_landmark", sa.String(length=120), nullable=True),
    sa.Column("ship_to_city", sa.String(length=80), nullable=True),
    sa.Column("ship_to_state", sa.String(), nullable=True),
    sa.Column("ship_to_pincode", sa.Integer(), nullable=True),
    sa.Column("ship_to_latitude", sa.Float(), nullable=True),
    sa.Column("ship_to_longitude", sa.Float(), nullable=True),
]


def upgrade():
    # Imported inside the function so read-only alembic commands (heads, history) keep working
    # without env.py's sys.path setup (Phase 01 convention).
    from app.models.enums import INDIAN_STATES

    op.add_column("orders", sa.Column("delivery_address_id", sa.Integer(), nullable=True))
    for column in _SHIP_TO_COLUMNS:
        op.add_column("orders", column.copy())
    op.create_foreign_key(
        "fk_orders_delivery_address_id_retailer_addresses",
        "orders",
        "retailer_addresses",
        ["delivery_address_id"],
        ["id"],
        ondelete="SET NULL",
    )
    states_list = ", ".join(f"'{s}'" for s in INDIAN_STATES)
    op.create_check_constraint(
        "ck_orders_ship_to_state",
        "orders",
        f"ship_to_state IS NULL OR ship_to_state IN ({states_list})",
    )


def downgrade():
    op.drop_constraint("ck_orders_ship_to_state", "orders", type_="check")
    op.drop_constraint(
        "fk_orders_delivery_address_id_retailer_addresses", "orders", type_="foreignkey"
    )
    for column in reversed(_SHIP_TO_COLUMNS):
        op.drop_column("orders", column.name)
    op.drop_column("orders", "delivery_address_id")
