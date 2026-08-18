from sqlalchemy import CheckConstraint, Column, Integer, ForeignKey
from sqlalchemy.orm import relationship

from app.db.base import Base

class OrderItemBatch(Base):
    """One allocation of one batch to one order line. INVT-03: see
    docs/inventory-invariants.md for invariants I1-I5 and the full write-path citations —
    the comment below must match that document word for word. Release (cancel from
    PENDING/READY_TO_SHIP) deletes these rows because the reservation they record never
    became a real stock movement. Restore (cancel after dispatch) keeps them because a
    real stock movement happened and the row is the allocation history of that movement."""
    __tablename__ = "order_item_batches"
    # INVT-02 / D4: quantity is the multiplier _restore_dispatched_inventory adds back to
    # stock (app/services/order.py:219-240) — a database floor, not a restatement of the
    # service-layer guard that already refuses to write a zero allocation.
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_item_batches_quantity_positive"),
    )

    id = Column(Integer, primary_key=True, index=True)
    order_item_id = Column(Integer, ForeignKey("order_items.id"), nullable=False)
    batch_id = Column(Integer, ForeignKey("sku_batches.id"), nullable=False)
    # Units of one batch allocated to one order line.
    quantity = Column(Integer, nullable=False)

    order_item = relationship("OrderItem", back_populates="order_item_batches")
    batch = relationship("SKUBatch", back_populates="order_item_batches")
