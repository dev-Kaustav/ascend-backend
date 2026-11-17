"""DRIVER_ASSIGNED order status: rider chosen, waiting for the rider to accept.

READY_TO_SHIP now means the warehouse has accepted the order; the rider is assigned as a
separate step and must accept before the order goes out. In-flight READY_TO_SHIP orders that
already have a driver are exactly that state, so they move to DRIVER_ASSIGNED and their driver
sees the same accept tap they had before.
"""

from alembic import op

revision = "0074_driver_assigned_status"
down_revision = "0073_sku_extra_image_urls"
branch_labels = None
depends_on = None


def upgrade():
    # A new enum value must be committed before any statement can use it.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE order_status ADD VALUE IF NOT EXISTS 'DRIVER_ASSIGNED' AFTER 'READY_TO_SHIP'")

    op.execute(
        """
        UPDATE orders
        SET status = 'DRIVER_ASSIGNED'
        WHERE status = 'READY_TO_SHIP'
          AND delivery_driver_id IS NOT NULL
          AND from_entity_type = 'WAREHOUSE'
          AND to_entity_type = 'RETAILER'
        """
    )


def downgrade():
    # Postgres cannot drop an enum value; fold the state back into READY_TO_SHIP instead.
    op.execute("UPDATE orders SET status = 'READY_TO_SHIP' WHERE status = 'DRIVER_ASSIGNED'")
    op.execute("UPDATE order_trails SET order_status = 'READY_TO_SHIP' WHERE order_status = 'DRIVER_ASSIGNED'")
