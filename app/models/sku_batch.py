from sqlalchemy import CheckConstraint, Column, Integer, Date, ForeignKey
from sqlalchemy.orm import relationship

from app.db.base import Base

class SKUBatch(Base):
    """One physical batch. INVT-03: see docs/inventory-invariants.md for invariants
    I1-I5 and the full write-path citations — the comments below must match that
    document word for word."""
    __tablename__ = "sku_batches"
    # INVT-02 / D4: the database's own floors on stock quantities — not a restatement of
    # the service-layer guards in app/services/order.py, which are bypassable by raw SQL,
    # an import script, or a future service that forgets them.
    __table_args__ = (
        CheckConstraint("quantity_received >= 0", name="ck_sku_batches_quantity_received_non_negative"),
        CheckConstraint("remaining_quantity >= 0", name="ck_sku_batches_remaining_quantity_non_negative"),
        CheckConstraint("reserved_quantity >= 0", name="ck_sku_batches_reserved_quantity_non_negative"),
        CheckConstraint("reserved_quantity <= remaining_quantity", name="ck_sku_batches_reserved_not_over_remaining"),
    )

    id = Column(Integer, primary_key=True, index=True)
    sku_id = Column(Integer, ForeignKey("skus.id"), nullable=False)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    mfg_date = Column(Date)
    expiry_date = Column(Date)
    # Units this batch arrived with. Never decremented.
    quantity_received = Column(Integer, nullable=False)
    # Physical units of this batch on hand right now. This meaning does not change with
    # order status.
    remaining_quantity = Column(Integer, nullable=False)
    # Subset of remaining_quantity claimed by orders not yet dispatched.
    reserved_quantity = Column(Integer, default=0, nullable=False)

    order_item_batches = relationship("OrderItemBatch", back_populates="batch")
