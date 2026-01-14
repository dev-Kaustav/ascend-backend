from sqlalchemy import Column, Integer, String, Float, ForeignKey
from sqlalchemy.orm import relationship

from app.db.base import Base

class OrderItemTax(Base):
    __tablename__ = "order_item_taxes"

    id = Column(Integer, primary_key=True, index=True)
    order_item_id = Column(Integer, ForeignKey("order_items.id"), nullable=False)
    tax_type = Column(String, nullable=False)
    rate = Column(Float, nullable=False)

    order_item = relationship("OrderItem", back_populates="taxes")
