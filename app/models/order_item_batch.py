from sqlalchemy import Column, Integer, Float, ForeignKey
from sqlalchemy.orm import relationship

from app.db.base import Base

class OrderItemBatch(Base):
    __tablename__ = "order_item_batches"

    id = Column(Integer, primary_key=True, index=True)
    order_item_id = Column(Integer, ForeignKey("order_items.id"), nullable=False)
    batch_id = Column(Integer, ForeignKey("sku_batches.id"), nullable=False)
    quantity = Column(Float, nullable=False)

    order_item = relationship("OrderItem", back_populates="order_item_batches")
    batch = relationship("SKUBatch", back_populates="order_item_batches")
