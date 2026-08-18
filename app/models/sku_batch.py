from sqlalchemy import CheckConstraint, Column, Integer, Date, ForeignKey
from sqlalchemy.orm import relationship

from app.db.base import Base

class SKUBatch(Base):
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
    quantity_received = Column(Integer, nullable=False)
    remaining_quantity = Column(Integer, nullable=False)
    reserved_quantity = Column(Integer, default=0, nullable=False)

    order_item_batches = relationship("OrderItemBatch", back_populates="batch")
