from sqlalchemy import Column, Integer, Date, ForeignKey
from sqlalchemy.orm import relationship

from app.db.base import Base

class SKUBatch(Base):
    __tablename__ = "sku_batches"

    id = Column(Integer, primary_key=True, index=True)
    sku_id = Column(Integer, ForeignKey("skus.id"), nullable=False)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    mfg_date = Column(Date)
    expiry_date = Column(Date)
    quantity_received = Column(Integer, nullable=False)
    remaining_quantity = Column(Integer, nullable=False)
    reserved_quantity = Column(Integer, default=0, nullable=False)

    order_item_batches = relationship("OrderItemBatch", back_populates="batch")
