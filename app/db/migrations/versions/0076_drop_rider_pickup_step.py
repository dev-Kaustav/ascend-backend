"""Drop the rider-accept/pickup step: orders waiting in DRIVER_ASSIGNED go back to READY_TO_SHIP.

The office now picks the rider and sends the order out in one step (READY_TO_SHIP ->
OUT_FOR_DELIVERY), and nothing moves an order into DRIVER_ASSIGNED any more. Orders already waiting
there are moved back to READY_TO_SHIP, keeping their delivery_driver_id so the office screen offers
the same rider and one tap sends it out. Their stock is still reserved (DRIVER_ASSIGNED never
released it) and no invoice exists yet, so nothing else changes. Each moved order gets a trail line.

Sending them straight out for delivery instead would mean dispatching stock and issuing invoices
from a migration; that stays an action someone takes in the app.

The DRIVER_ASSIGNED enum value stays: past order_trails rows carry it. The ids moved are kept in
orders_rider_step_0076 so the downgrade restores exactly those orders.
"""

from alembic import op

revision = "0076_drop_rider_pickup_step"
down_revision = "0075_sku_ladi_backfill_later_skus"
branch_labels = None
depends_on = None

NOTE = "Back to accepted: the rider pickup step was removed. Send out with the rider to dispatch."


def upgrade():
    op.execute("CREATE TABLE orders_rider_step_0076 AS SELECT id FROM orders WHERE status = 'DRIVER_ASSIGNED'")
    op.execute(
        "INSERT INTO order_trails (order_id, order_status, description) "
        f"SELECT id, 'READY_TO_SHIP', '{NOTE}' FROM orders_rider_step_0076"
    )
    op.execute("UPDATE orders SET status = 'READY_TO_SHIP' WHERE id IN (SELECT id FROM orders_rider_step_0076)")


def downgrade():
    op.execute("UPDATE orders SET status = 'DRIVER_ASSIGNED' WHERE id IN (SELECT id FROM orders_rider_step_0076)")
    op.execute(
        "DELETE FROM order_trails "
        f"WHERE description = '{NOTE}' AND order_id IN (SELECT id FROM orders_rider_step_0076)"
    )
    op.execute("DROP TABLE orders_rider_step_0076")
